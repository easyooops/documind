"""SDK PPTX render node without browser screenshots or visual QA artifacts."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger
from src.formats.pptx.mapper.engine import CSStoOOXMLEngine
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

PPTX_TEXT_ALIGNMENT_PREVIEW_CSS = """
[data-pptx-type="textbox"][data-pptx-text-align="left"],
[data-pptx-type="shape"][data-pptx-text-align="left"]{text-align:left}
[data-pptx-type="textbox"][data-pptx-text-align="center"],
[data-pptx-type="shape"][data-pptx-text-align="center"]{text-align:center}
[data-pptx-type="textbox"][data-pptx-text-align="right"],
[data-pptx-type="shape"][data-pptx-text-align="right"]{text-align:right}
[data-pptx-type="textbox"][data-pptx-text-valign="middle"],
[data-pptx-type="shape"][data-pptx-text-valign="middle"]{display:flex;flex-direction:column;justify-content:center}
[data-pptx-type="textbox"][data-pptx-text-valign="bottom"],
[data-pptx-type="shape"][data-pptx-text-valign="bottom"]{display:flex;flex-direction:column;justify-content:flex-end}
""".strip()


async def render_and_convert(state: DocuMindState) -> dict:
    """Build a PPTX directly from generated HTML.

    The SDK intentionally skips Playwright HTML screenshots, LibreOffice/PyMuPDF
    PPTX rasterization, and VLM QA inputs. The deterministic OOXML mapper still
    creates the native PowerPoint file.
    """
    logger.info("render_convert.sdk_start")

    slides_html = _normalize_slide_icon_layouts(state.get("slides_html", []))
    title = state.get("title", "Presentation")
    if not slides_html:
        return {"errors": ["No HTML slides to convert"], "current_phase": "error"}

    _cleanup_file(state.get("output_path"))

    output_dir = Path(settings.storage_local_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = CSStoOOXMLEngine().build_presentation(
        slides_html,
        output_dir,
        title=title,
        template_bytes=state.get("_template_bytes"),
    )
    html_preview_path = _save_html_preview(slides_html, output_dir)

    logger.info("render_convert.sdk_complete", output=str(output_path), slides=len(slides_html))
    return {
        "output_path": str(output_path),
        "html_preview_path": html_preview_path,
        "html_screenshots": [],
        "pptx_screenshots": [],
        "pptx_render_info": {"renderer": "sdk_no_raster", "true_render": False, "paths": []},
        "screenshots_count": 0,
        "current_phase": "converting",
    }


async def _capture_slides(slides_html: list[dict]) -> list[str]:
    """Compatibility no-op; SDK builds do not depend on Playwright."""
    return []


def _normalize_slide_icon_layouts(slides_html: list[dict]) -> list[dict]:
    normalized = []
    for slide in slides_html:
        copied = dict(slide)
        copied["html"] = _normalize_slide_html(str(slide.get("html", "")))
        normalized.append(copied)
    return normalized


def _normalize_slide_html(slide_html: str, *, slide_type: str = "content") -> str:
    """Keep the SDK path lightweight while preserving hook compatibility."""
    return _normalize_legacy_icon_nodes(slide_html)


def _normalize_legacy_icon_nodes(slide_html: str) -> str:
    """Leave icon markup in place; SDK icon handling is file-cache based."""
    return slide_html


def _save_html_preview(slides_html: list[dict], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / f"preview_{uuid.uuid4().hex[:8]}.html"
    slides_content = "\n".join(slide.get("html", "") for slide in slides_html)
    html = f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"/>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#1a1a2e; display:flex; flex-direction:column; align-items:center; gap:24px; padding:24px; font-family:system-ui,sans-serif; }}
[data-slide] {{ border-radius:8px; box-shadow:0 4px 20px rgba(0,0,0,0.3); }}
{PPTX_TEXT_ALIGNMENT_PREVIEW_CSS}
</style>
<script>
function renderTables(){{document.querySelectorAll('[data-pptx-table-data]').forEach(function(el){{try{{var d=JSON.parse(el.getAttribute('data-pptx-table-data'));if(!d)return;var h=d.headers||[],rows=d.rows||[];var t='<table style="width:100%;height:100%;border-collapse:collapse;font-size:11px;font-family:system-ui,sans-serif">';if(h.length){{t+='<tr>';h.forEach(function(c){{t+='<th style="background:#1e293b;color:#fff;padding:6px 8px;text-align:center;font-weight:600">'+c+'</th>';}});t+='</tr>';}}rows.forEach(function(r,i){{t+='<tr>';(Array.isArray(r)?r:Object.values(r)).forEach(function(c){{t+='<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;background:'+(i%2?'#f9fafb':'#fff')+'">'+c+'</td>';}});t+='</tr>';}});t+='</table>';el.innerHTML=t;}}catch(e){{}}}});}}
document.addEventListener('DOMContentLoaded',renderTables);
</script>
</head><body>
{slides_content}
</body></html>"""
    preview_path.write_text(html, encoding="utf-8")
    return str(preview_path)


def _cleanup_file(path: str | None) -> None:
    if not path:
        return
    try:
        file_path = Path(path)
        if file_path.exists():
            file_path.unlink()
    except OSError:
        pass


def _public_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
