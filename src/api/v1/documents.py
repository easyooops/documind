"""Document generation endpoints — create, status, download."""

# ruff: noqa: E501

from __future__ import annotations

import ast
import base64
import uuid
from datetime import datetime
from html import escape
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import DocumentVersion, GeneratedFile, GenerationJob, JobStatus
from src.schemas.api import GenerateRequest, JobStatusResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _mime_type(format_id: str) -> str:
    return {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "md": "text/markdown",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "hwp": "application/hwp+zip",
    }.get(format_id, "application/octet-stream")


@router.post("/generate", response_model=JobStatusResponse)
async def generate_document(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
):
    """Start a new document generation job."""
    job_id = str(uuid.uuid4())

    job = GenerationJob(
        id=job_id,
        session_id=request.session_id,
        template_id=request.template_id,
        query=request.query,
        format=request.format,
        options=request.options,
        status=JobStatus.QUEUED.value,
        created_at=datetime.utcnow(),
    )
    db.add(job)
    await db.flush()

    background_tasks.add_task(_run_pipeline, job_id, request)

    return JobStatusResponse(
        id=job_id,
        status=JobStatus.QUEUED.value,
        phase=None,
        progress=0.0,
        created_at=job.created_at,
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Get the current status of a generation job."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .options(selectinload(GenerationJob.file))
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    download_url = None
    if job.status == JobStatus.COMPLETED.value and job.file:
        download_url = f"/api/v1/documents/{job_id}/download"

    version_result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == job_id)
        .options(selectinload(DocumentVersion.slide_versions))
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    latest_version = version_result.scalar_one_or_none()

    latest_count = None
    if latest_version:
        latest_count = len(latest_version.slide_versions)
        spec = (latest_version.pipeline_data or {}).get("document_spec") or {}
        if not latest_count and isinstance(spec, dict):
            latest_count = len(spec.get("sections", []))

    return JobStatusResponse(
        id=job.id,
        status=job.status,
        phase=job.phase,
        progress=job.progress or 0.0,
        fidelity_score=latest_version.fidelity_score if latest_version else (
            job.file.fidelity_score if job.file else None
        ),
        slide_count=latest_count if latest_version else (
            job.file.slide_count if job.file else None
        ),
        error=job.error,
        download_url=download_url,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/download")
async def download_document(
    job_id: str,
    version: int | None = None,
    db: AsyncSession = Depends(get_session),
):
    """Download the generated document file."""
    from fastapi.responses import FileResponse
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .options(selectinload(GenerationJob.file))
    )
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="Document not ready")
    version_stmt = select(DocumentVersion).where(DocumentVersion.document_id == job_id)
    if version is None:
        version_stmt = version_stmt.order_by(DocumentVersion.version_number.desc()).limit(1)
    else:
        version_stmt = version_stmt.where(DocumentVersion.version_number == version)
    version_result = await db.execute(version_stmt)
    selected_version = version_result.scalar_one_or_none()
    if version is not None and not selected_version:
        raise HTTPException(status_code=404, detail="Document version not found")

    path = selected_version.file_path if selected_version else (
        job.file.storage_path if job.file else None
    )
    if not path:
        raise HTTPException(status_code=404, detail="File not found")
    filename = job.file.filename if job.file else "document.pptx"
    if selected_version:
        filename = f"{Path(filename).stem}-v{selected_version.version_number}{Path(filename).suffix}"

    return FileResponse(
        path=path,
        filename=filename,
        media_type=job.file.mime_type if job.file else _mime_type(job.format),
    )


@router.get("/{job_id}/versions")
async def get_document_versions(
    job_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Get all versions of a generated document."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.schemas.api import DocumentVersionResponse

    result = await db.execute(select(GenerationJob).where(GenerationJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    versions_result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == job_id)
        .options(selectinload(DocumentVersion.slide_versions))
        .order_by(DocumentVersion.version_number.desc())
    )
    versions = versions_result.scalars().all()

    return [
        DocumentVersionResponse(
            id=v.id,
            version_number=v.version_number,
            trigger=v.trigger,
            user_instruction=v.user_instruction,
            fidelity_score=v.fidelity_score,
            slide_count=(
                len(v.slide_versions)
                or len(((v.pipeline_data or {}).get("document_spec") or {}).get("sections", []))
            ),
            download_url=f"/api/v1/documents/{job_id}/download?version={v.version_number}",
            created_at=v.created_at,
            is_latest=v.version_number == versions[0].version_number,
        )
        for v in versions
    ]


@router.get("/{job_id}/preview")
async def preview_document(
    job_id: str,
    version: int | None = None,
    db: AsyncSession = Depends(get_session),
):
    """Return an HTML preview page for the generated document."""
    from fastapi.responses import HTMLResponse
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .options(selectinload(GenerationJob.file))
        .options(selectinload(GenerationJob.slides))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="Document not ready")

    version_stmt = (
        select(DocumentVersion)
        .where(DocumentVersion.document_id == job_id)
        .options(selectinload(DocumentVersion.slide_versions))
    )
    if version is None:
        version_stmt = version_stmt.order_by(DocumentVersion.version_number.desc()).limit(1)
    else:
        version_stmt = version_stmt.where(DocumentVersion.version_number == version)
    version_result = await db.execute(version_stmt)
    selected_version = version_result.scalar_one_or_none()
    if version is not None and not selected_version:
        raise HTTPException(status_code=404, detail="Document version not found")
    if job.format != "pptx":
        selected_path = selected_version.file_path if selected_version else (
            job.file.storage_path if job.file else None
        )
        pipeline_data = selected_version.pipeline_data or {} if selected_version else {}
        spec = pipeline_data.get("document_spec") or {}
        design = pipeline_data.get("template_profile") or {}
        if job.format == "pdf" and selected_path:
            from src.core.config import settings
            from src.formats.docx.preview import render_pdf_images

            pages = await render_pdf_images(
                Path(selected_path),
                Path(settings.storage_local_path) / "previews",
            )
            if pages:
                return HTMLResponse(content=_rendered_pages_preview(pages, "Rendered PDF page"))
        if job.format == "docx" and selected_path:
            from src.core.config import settings
            from src.formats.docx.preview import render_docx_images

            pages = await render_docx_images(
                Path(selected_path),
                Path(settings.storage_local_path) / "previews",
            )
            if pages:
                return HTMLResponse(content=_rendered_pages_preview(pages, "Rendered DOCX page"))
        if job.format == "xlsx" and selected_path:
            from src.core.config import settings
            from src.formats.docx.preview import render_docx_images

            pages = await render_docx_images(
                Path(selected_path),
                Path(settings.storage_local_path) / "previews",
            )
            if pages:
                return HTMLResponse(content=_rendered_pages_preview(pages, "Rendered XLSX page"))
        if job.format == "md" and selected_path:
            return HTMLResponse(content=_markdown_preview(Path(selected_path)))
        if job.format == "hwp" and selected_path:
            from src.core.config import settings

            pages = await _hwpx_visual_preview(
                Path(selected_path),
                spec,
                design,
                Path(settings.storage_local_path) / "previews",
            )
            if pages:
                return HTMLResponse(content=_rendered_pages_preview(pages, "Rendered HWPX preview page"))
        return HTMLResponse(content=_native_preview(spec, design, job.format))
    if selected_version:
        sorted_slides = sorted(selected_version.slide_versions, key=lambda s: s.slide_index)
        slide_html = [slide.html for slide in sorted_slides]
        pipeline_data = selected_version.pipeline_data or {}
        screenshot_paths = pipeline_data.get("pptx_screenshots", [])
        if pipeline_data.get("native_template_output") and screenshot_paths:
            embedded_slides = []
            for screenshot_path in screenshot_paths:
                try:
                    encoded = base64.b64encode(Path(screenshot_path).read_bytes()).decode("ascii")
                    embedded_slides.append(
                        f'<div class="slide-frame"><img src="data:image/png;base64,{encoded}" '
                        'alt="Rendered PPTX slide"/></div>'
                    )
                except OSError:
                    embedded_slides = []
                    break
            if embedded_slides:
                return HTMLResponse(
                    content=(
                        "<html><head><meta charset='utf-8'><style>"
                        "*{box-sizing:border-box;margin:0;padding:0}"
                        "body{background:#1a1a2e;padding:12px}"
                        ".slide-frame{margin:0 auto 12px;max-width:100%;"
                        "border-radius:4px;overflow:hidden;"
                        "box-shadow:0 4px 24px rgba(0,0,0,.3)}"
                        ".slide-frame img{display:block;width:100%;height:auto}"
                        "</style></head><body>"
                        + "".join(embedded_slides)
                        + "</body></html>"
                    )
                )
    else:
        sorted_slides = sorted(job.slides or [], key=lambda s: s.slide_number)
        slide_html = [slide.html_content or "" for slide in sorted_slides]

    if sorted_slides:
        html_parts = [
            '<html><head><meta charset="utf-8"><style>',
            "*{box-sizing:border-box;margin:0;padding:0}",
            "body{font-family:'Pretendard',system-ui,-apple-system,sans-serif;",
            "margin:0;padding:0;background:#1a1a2e;overflow-x:hidden;overflow-y:auto}",
            ".slides-wrapper{padding:12px;width:100%}",
            ".slide-frame{width:100%;position:relative;margin:0 auto 12px;",
            "padding-bottom:56.25%;border-radius:4px;box-shadow:0 4px 24px rgba(0,0,0,.3);overflow:hidden;background:#fff}",
            ".slide-inner{position:absolute;top:0;left:0;width:960px;height:540px;",
            "transform-origin:top left}",
            "</style>",
            "<script>",
            "function resizeSlides(){",
            "document.querySelectorAll('.slide-frame').forEach(function(f){",
            "var s=f.querySelector('.slide-inner');",
            "if(s){s.style.transform='scale('+f.offsetWidth/960+')';}",
            "});}",
            "window.addEventListener('resize',resizeSlides);",
            "document.addEventListener('DOMContentLoaded',resizeSlides);",
            "setTimeout(resizeSlides,100);",
            "new ResizeObserver(resizeSlides).observe(document.documentElement);",
            # Render data-pptx-table-data as visual HTML tables
            "function renderTables(){",
            "document.querySelectorAll('[data-pptx-table-data]').forEach(function(el){",
            "try{var d=JSON.parse(el.getAttribute('data-pptx-table-data'));",
            "if(!d)return;var h=d.headers||[],rows=d.rows||[];",
            "var t='<table style=\"width:100%;height:100%;border-collapse:collapse;font-size:11px;font-family:Pretendard,sans-serif\">';",
            "if(h.length){t+='<tr>';h.forEach(function(c){t+='<th style=\"background:#1e293b;color:#fff;padding:6px 8px;text-align:center;font-weight:600\">'+c+'</th>';});t+='</tr>';}",
            "rows.forEach(function(r,i){t+='<tr>';(Array.isArray(r)?r:Object.values(r)).forEach(function(c){t+='<td style=\"padding:5px 8px;border-bottom:1px solid #e5e7eb;background:'+(i%2?'#f9fafb':'#fff')+'\">'+c+'</td>';});t+='</tr>';});",
            "t+='</table>';el.innerHTML=t;}catch(e){}});}",
            # Render data-pptx-chart-data as simple bar visualization
            "function renderCharts(){",
            "document.querySelectorAll('[data-pptx-chart-data]').forEach(function(el){",
            "try{var d=JSON.parse(el.getAttribute('data-pptx-chart-data'));",
            "if(!d||!d.length)return;var max=Math.max.apply(null,d.map(function(i){return parseFloat(i.value)||0;}));",
            "var type=el.getAttribute('data-pptx-chart-type')||'bar';",
            "var html='<div style=\"display:flex;flex-direction:column;justify-content:flex-end;align-items:stretch;height:100%;padding:8px;gap:4px;font-family:Pretendard,sans-serif;font-size:10px\">';",
            "d.forEach(function(item){var pct=max>0?((parseFloat(item.value)||0)/max*100):0;",
            "html+='<div style=\"display:flex;align-items:center;gap:6px\"><span style=\"min-width:60px;text-align:right;color:#64748b\">'+item.label+'</span><div style=\"flex:1;background:#e2e8f0;border-radius:3px;height:18px;position:relative\"><div style=\"width:'+pct+'%;height:100%;background:#3b82f6;border-radius:3px\"></div></div><span style=\"min-width:36px;color:#1e293b;font-weight:500\">'+item.value+'</span></div>';});",
            "html+='</div>';el.innerHTML=html;}catch(e){}});}",
            "document.addEventListener('DOMContentLoaded',function(){renderTables();renderCharts();});",
            "</script>",
            "</head><body><div class='slides-wrapper'>",
        ]
        for html in slide_html:
            html_parts.append(
                f'<div class="slide-frame">'
                f'<div class="slide-inner">{html}</div>'
                f'</div>'
            )
        html_parts.append("</div></body></html>")
        return HTMLResponse(content="".join(html_parts))

    return HTMLResponse(
        content="<html><body><p>Preview is not available.</p></body></html>"
    )


def _rendered_pages_preview(paths: list[Path], label: str) -> str:
    pages = []
    for index, path in enumerate(paths, 1):
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        pages.append(
            f'<img class="page" src="data:image/png;base64,{encoded}" '
            f'alt="{escape(label)} {index}"/>'
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "*{box-sizing:border-box}body{margin:0;padding:18px;background:#e5e7eb;"
        "display:flex;flex-direction:column;align-items:center;gap:18px}"
        ".page{display:block;max-width:100%;width:min(920px,100%);height:auto;"
        "background:white;box-shadow:0 2px 12px rgba(0,0,0,.18)}"
        "</style></head><body>"
        + "".join(pages)
        + "</body></html>"
    )


def _markdown_preview(path: Path) -> str:
    import re

    from markdown_it import MarkdownIt

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2].lstrip()
    renderer = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")
    body = renderer.render(text)
    body = re.sub(
        r'<pre><code class="language-mermaid">([\s\S]*?)</code></pre>',
        r'<div class="mermaid">\1</div>',
        body,
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "*{box-sizing:border-box}body{margin:0;padding:28px;background:#e8edf2;"
        "color:#152536;font-family:'Malgun Gothic',Arial,sans-serif}"
        "article{max-width:900px;min-height:1100px;margin:auto;background:#fff;padding:54px 66px;"
        "box-shadow:0 4px 18px rgba(20,37,52,.14)}"
        "h1{font-size:38px;color:#153b64;margin:0 0 22px;border-bottom:3px solid #0e7490;padding-bottom:20px}"
        "h2{font-size:25px;color:#153b64;margin:42px 0 16px;border-bottom:1px solid #b9dce2;padding-bottom:9px}"
        "h3{font-size:18px;color:#245474}p,li{line-height:1.72;color:#253746}"
        "blockquote{margin:20px 0;padding:15px 20px;border-left:5px solid #0e7490;background:#f2f8f8}"
        "table{width:100%;max-width:100%;table-layout:fixed;border-collapse:collapse;margin:20px 0;font-size:14px}"
        "th{background:#153b64;color:#fff;text-align:left;padding:11px;overflow-wrap:anywhere}"
        "td{border-bottom:1px solid #d7e0e5;padding:10px;overflow-wrap:anywhere;word-break:break-word}"
        "tbody tr:nth-child(even){background:#f6f9fa}pre{overflow:auto;background:#152536;color:#e5edf2;"
        "padding:18px 20px;border-radius:7px;line-height:1.55}code{font-family:Consolas,monospace}"
        ".mermaid{display:flex;justify-content:center;margin:22px 0;padding:18px;background:#fbfdfe;"
        "border:1px solid #d8e6ec;border-radius:8px}img{max-width:100%;border-radius:6px}"
        "a{color:#0e7490}</style>"
        "<script type='module'>import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';"
        "mermaid.initialize({startOnLoad:true,theme:'neutral',securityLevel:'strict'});</script>"
        "</head><body><article>"
        + body
        + "</article></body></html>"
    )


async def _hwpx_visual_preview(source_path: Path, spec: dict, design: dict, output_dir: Path) -> list[Path]:
    from src.formats.docx.preview import render_pdf_images
    from src.formats.pdf.renderer import PDFRenderer

    output_dir.mkdir(parents=True, exist_ok=True)
    preview_pdf = output_dir / f"{source_path.stem}_visual.pdf"
    if not (
        preview_pdf.exists()
        and preview_pdf.stat().st_mtime >= source_path.stat().st_mtime
    ):
        rendered = await PDFRenderer().render(spec, output_dir, design_system=design)
        rendered.replace(preview_pdf)
    return await render_pdf_images(preview_pdf, output_dir)


def _preview_value(value: object) -> str:
    value = _preview_action_data(value) or value
    if not isinstance(value, dict):
        return str(value)
    text = str(
        value.get("action")
        or value.get("task")
        or value.get("title")
        or value.get("description")
        or value.get("text")
        or ""
    )
    details = [
        str(value.get(key, ""))
        for key in ("owner", "due_date", "deadline", "priority", "status")
        if value.get(key)
    ]
    return " | ".join([text, *details]) if text else " | ".join(details)


def _preview_action_data(value: object) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    try:
        candidate = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _preview_action_table(items: list, korean: bool) -> str:
    headers = (
        ["\uc5c5\ubb34\ub0b4\uc6a9", "\ub2f4\ub2f9\uc790", "\uc644\ub8cc\uae30\ud55c", "\uc6b0\uc120\uc21c\uc704"]
        if korean
        else ["Action", "Owner", "Due Date", "Priority"]
    )
    rows = []
    for item in items:
        data = _preview_action_data(item) or {"action": str(item)}
        rows.append(
            [
                data.get("action") or data.get("task") or data.get("title") or data.get("text") or "",
                data.get("owner", ""),
                data.get("due_date") or data.get("deadline") or "",
                data.get("priority") or data.get("status") or "",
            ]
        )
    header = "".join(f"<th>{escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _native_preview(spec: dict, design: dict, format_id: str) -> str:
    """Render a safe browser projection while the deliverable remains a native file."""
    primary = escape(str(design.get("primary", "#12304A")))
    accent = escape(str(design.get("accent", "#17A2A4")))
    title = escape(str(spec.get("title", "Generated document")))
    subtitle = escape(str(spec.get("subtitle", "")))
    format_label = "HWPX" if format_id == "hwp" else format_id.upper()
    korean = spec.get("language", "ko_mixed") != "en"
    summary_label = "\ubcf4\uace0 \uc694\uc57d" if korean else "Executive Summary"
    cards = "".join(
        f"<div class='meta'><label>{escape(str(item.get('label', '')))}</label>"
        f"<strong>{escape(str(item.get('value', '')))}</strong></div>"
        for item in spec.get("metadata", [])[:4]
    )
    sections = []
    for section in spec.get("sections", []):
        blocks = []
        for block in section.get("blocks", []):
            kind = block.get("type")
            if kind == "kpi_grid":
                items = "".join(
                    f"<div class='kpi'><small>{escape(str(item.get('label', '')))}</small>"
                    f"<b>{escape(str(item.get('value', '')))}</b>"
                    f"<span>{escape(str(item.get('context', '')))}</span></div>"
                    for item in block.get("items", [])
                )
                blocks.append(f"<div class='kpis'>{items}</div>")
            elif kind == "table":
                header = "".join(f"<th>{escape(_preview_value(value))}</th>" for value in block.get("headers", []))
                rows = "".join(
                    "<tr>" + "".join(f"<td>{escape(_preview_value(value))}</td>" for value in row) + "</tr>"
                    for row in block.get("rows", [])
                )
                blocks.append(f"<table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>")
            elif kind in {"callout", "quote"}:
                blocks.append(
                    f"<aside><strong>{escape(str(block.get('title', 'Insight')))}</strong>"
                    f"<p>{escape(str(block.get('text', '')))}</p></aside>"
                )
            elif kind == "action_items" and any(_preview_action_data(item) for item in block.get("items", [])):
                blocks.append(_preview_action_table(block.get("items", []), korean))
            elif kind in {"bullet_list", "timeline", "action_items"}:
                items = "".join(f"<li>{escape(_preview_value(value))}</li>" for value in block.get("items", []))
                blocks.append(f"<ul>{items}</ul>")
            else:
                blocks.append(f"<p>{escape(str(block.get('text', '')))}</p>")
        sections.append(
            f"<section><h2>{escape(str(section.get('title', '')))}</h2>{''.join(blocks)}</section>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}} body{{margin:0;background:#edf1f4;color:#17252f;font-family:Arial,'Malgun Gothic',sans-serif}}
.page{{max-width:820px;margin:22px auto;background:#fff;min-height:1080px;box-shadow:0 8px 35px rgba(18,35,52,.12);padding:58px 62px}}
.tag{{color:{accent};font-weight:700;letter-spacing:1.4px;font-size:12px}} h1{{color:{primary};font-size:35px;margin:20px 0 8px}}
.subtitle{{color:#627483;margin-bottom:34px}} .meta-grid,.kpis{{display:flex;gap:12px;margin:22px 0;flex-wrap:wrap}}
.meta,.kpi{{flex:1;min-width:135px;background:#f3f6f8;border-top:3px solid {accent};padding:13px}}
label,small{{display:block;color:#637481;font-size:11px;font-weight:bold;text-transform:uppercase}} .meta strong,.kpi b{{display:block;color:{primary};font-size:18px;margin-top:6px}}
.kpi span{{font-size:11px;color:#637481}} .summary,aside{{border-left:5px solid {accent};background:#f3f6f8;padding:16px 18px;margin:25px 0}}
h2{{color:{primary};border-bottom:2px solid {accent};padding-bottom:9px;margin-top:34px}} table{{width:100%;border-collapse:collapse;margin:16px 0}}
th{{background:{primary};color:#fff;text-align:left;padding:10px}} td{{padding:10px;border-bottom:1px solid #d9e1e6}} tr:nth-child(even) td{{background:#f5f7f8}}
li{{margin:8px 0}} p{{line-height:1.6}}</style></head><body><main class="page">
<div class="tag">{format_label} / DESIGNED NATIVE DOCUMENT</div><h1>{title}</h1><div class="subtitle">{subtitle}</div>
<div class="meta-grid">{cards}</div><div class="summary"><strong>{summary_label}</strong><p>{escape(str(spec.get("executive_summary", "")))}</p></div>
{''.join(sections)}</main></body></html>"""


async def _run_pipeline(job_id: str, request: GenerateRequest) -> None:
    """Execute the full agent pipeline in the background."""
    from sqlalchemy import select

    from src.engine import _get_format_pipeline
    from src.agents.research_intent import analyze_research_intent
    from src.infrastructure.database import get_session_factory
    from src.infrastructure.models import Template
    from src.infrastructure.storage import create_storage_backend
    from src.schemas.agents import DocuMindState
    from src.utils.language import detect_output_language

    logger.info("pipeline.start", job_id=job_id)

    template_bytes = None
    template_filename = ""
    template_analysis: dict = {}
    if request.template_id:
        async with get_session_factory()() as db:
            template_result = await db.execute(
                select(Template).where(Template.id == request.template_id)
            )
            template = template_result.scalar_one_or_none()
            if template:
                template_bytes = await create_storage_backend().load(template.file_path)
                template_filename = template.filename
                template_analysis = template.analysis or {}
                if Path(template.filename).suffix.lower() in {".docx", ".hwpx", ".xlsx", ".md", ".pdf"}:
                    from src.formats.rich_document.template_analysis import analyze_template

                    template_analysis = {
                        **template_analysis,
                        **analyze_template(template_bytes, template.filename),
                    }

    research_intent = await analyze_research_intent(request.query)
    logger.info(
        "documents.research_intent",
        needs_research=research_intent.needs_research,
        intent=research_intent.intent_label,
        reason=research_intent.reason,
    )
    initial_state: DocuMindState = {
        "user_query": request.query,
        "session_id": request.session_id or "",
        "template_id": request.template_id,
        "conversation_history": [],
        "document_format": request.format,
        "locale": str(request.options.get("locale", "ko")),
        "output_language": detect_output_language(request.query),
        "needs_research": research_intent.needs_research,
        "template_provided": request.template_id is not None,
        "current_phase": "planning",
        "errors": [],
        "retry_count": 0,
        "qa_iterations": 0,
        "_template_bytes": template_bytes,
        "_template_filename": template_filename,
        "_template_analysis": template_analysis,
    }

    try:
        pipeline = _get_format_pipeline(request.format)
        result = await pipeline.ainvoke(initial_state, config={"recursion_limit": 80})

        async with get_session_factory()() as db:
            import os

            from sqlalchemy import select

            stmt = select(GenerationJob).where(GenerationJob.id == job_id)
            job_result = await db.execute(stmt)
            job = job_result.scalar_one()
            job.status = JobStatus.COMPLETED.value
            job.phase = "done"
            job.progress = 1.0
            job.completed_at = datetime.utcnow()
            output_path = result.get("output_path")
            if output_path:
                db.add(
                    GeneratedFile(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        filename=Path(output_path).name,
                        storage_backend="local",
                        storage_path=output_path,
                        size_bytes=os.path.getsize(output_path),
                        mime_type=_mime_type(request.format),
                        fidelity_score=result.get("fidelity_score"),
                        slide_count=len(
                            result.get("slides_html", []) or result.get("section_blueprints", [])
                        ),
                        created_at=datetime.utcnow(),
                    )
                )
                db.add(
                    DocumentVersion(
                        id=str(uuid.uuid4()),
                        document_id=job_id,
                        version_number=1,
                        trigger="created",
                        user_instruction=request.query,
                        slide_plan=(
                            result.get("slide_blueprints") or result.get("section_blueprints")
                        ),
                        design_system=result.get("design_system"),
                        pipeline_data={
                            "master_context": result.get("master_context"),
                            "document_spec": result.get("document_spec"),
                            "document_intent": result.get("document_intent"),
                            "template_profile": result.get("template_profile"),
                            "template_references": result.get("template_references", []),
                            "format_rules": result.get("format_rules"),
                            "qa_feedback": result.get("qa_feedback"),
                            "pptx_screenshots": result.get("pptx_screenshots", []),
                        },
                        file_path=output_path,
                        fidelity_score=result.get("fidelity_score"),
                        created_at=datetime.utcnow(),
                    )
                )
            await db.commit()

        logger.info("pipeline.complete", job_id=job_id)

    except Exception as e:
        logger.error("pipeline.failed", job_id=job_id, error=str(e))

        async with get_session_factory()() as db:
            from sqlalchemy import select

            stmt = select(GenerationJob).where(GenerationJob.id == job_id)
            job_result = await db.execute(stmt)
            job = job_result.scalar_one()
            job.status = JobStatus.FAILED.value
            job.error = {"message": str(e), "type": type(e).__name__}
            await db.commit()
