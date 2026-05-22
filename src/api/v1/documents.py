"""Document generation endpoints — create, status, download."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import GenerationJob, JobStatus
from src.schemas.api import GenerateRequest, JobStatusResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


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

    return JobStatusResponse(
        id=job.id,
        status=job.status,
        phase=job.phase,
        progress=job.progress or 0.0,
        fidelity_score=job.file.fidelity_score if job.file else None,
        slide_count=job.file.slide_count if job.file else None,
        error=job.error,
        download_url=download_url,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/download")
async def download_document(
    job_id: str,
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
    if not job.file:
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=job.file.storage_path,
        filename=job.file.filename,
        media_type=job.file.mime_type,
    )


@router.get("/{job_id}/versions")
async def get_document_versions(
    job_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Get all versions of a generated document."""
    from sqlalchemy import select

    from src.infrastructure.models import DocumentVersion
    from src.schemas.api import DocumentVersionResponse

    result = await db.execute(select(GenerationJob).where(GenerationJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    versions_result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == job_id)
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
            slide_count=None,
            download_url=f"/api/v1/documents/{job_id}/download?version={v.version_number}",
            created_at=v.created_at,
        )
        for v in versions
    ]


@router.get("/{job_id}/preview")
async def preview_document(
    job_id: str,
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

    if job.format in ("html", "md"):
        from src.infrastructure.storage import create_storage_backend

        storage = create_storage_backend()
        if job.file:
            content = await storage.load(job.file.storage_path)
            return HTMLResponse(content=content)

    slides = job.slides or []
    sorted_slides = sorted(slides, key=lambda s: s.slide_number)

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
            "</script>",
            "</head><body><div class='slides-wrapper'>",
        ]
        for s in sorted_slides:
            html_parts.append(
                f'<div class="slide-frame">'
                f'<div class="slide-inner">{s.html_content or ""}</div>'
                f'</div>'
            )
        html_parts.append("</div></body></html>")
        return HTMLResponse(content="".join(html_parts))

    return HTMLResponse(
        content="<html><body><p>Preview is not available.</p></body></html>"
    )


async def _run_pipeline(job_id: str, request: GenerateRequest) -> None:
    """Execute the full agent pipeline in the background."""
    from src.engine import _get_format_pipeline
    from src.infrastructure.database import get_session_factory
    from src.schemas.agents import DocuMindState

    logger.info("pipeline.start", job_id=job_id)

    initial_state: DocuMindState = {
        "user_query": request.query,
        "session_id": request.session_id or "",
        "template_id": request.template_id,
        "conversation_history": [],
        "document_format": request.format,
        "needs_research": True,
        "template_provided": request.template_id is not None,
        "current_phase": "planning",
        "errors": [],
        "retry_count": 0,
        "qa_iterations": 0,
    }

    try:
        pipeline = _get_format_pipeline(request.format)
        result = await pipeline.ainvoke(initial_state, config={"recursion_limit": 80})

        async with get_session_factory()() as db:
            from sqlalchemy import select

            stmt = select(GenerationJob).where(GenerationJob.id == job_id)
            job_result = await db.execute(stmt)
            job = job_result.scalar_one()
            job.status = JobStatus.COMPLETED.value
            job.phase = "done"
            job.progress = 1.0
            job.completed_at = datetime.utcnow()
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
