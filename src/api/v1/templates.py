"""Template management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database import get_session
from src.infrastructure.models import Template
from src.infrastructure.storage import create_storage_backend
from src.schemas.api import TemplateAnalysisResponse, TemplateUploadResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])


@router.post("/upload", response_model=TemplateUploadResponse)
async def upload_template(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    """Upload a native document template for visual/style analysis."""
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    supported = {".pptx", ".potx", ".docx", ".pdf", ".md", ".xlsx", ".hwpx"}
    if not filename or extension not in supported:
        raise HTTPException(
            status_code=400,
            detail="Supported templates: .pptx, .potx, .docx, .pdf, .md, .xlsx, .hwpx",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded template is empty")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    try:
        if extension in {".pptx", ".potx"}:
            from src.formats.pptx.master_context import parse_template

            analysis = parse_template(content, filename)
        else:
            from src.formats.rich_document.template_analysis import analyze_template

            analysis = analyze_template(content, filename)
    except Exception as exc:
        logger.warning("template.analysis_failed", filename=filename, error=str(exc)[:200])
        raise HTTPException(status_code=400, detail="Invalid document template file") from exc

    if extension in {".pptx", ".potx"}:
        analysis["visual_analysis"] = {
            "status": "pending",
            "summary": "Rendered-slide visual analysis will run before slide design planning.",
        }

    template_id = str(uuid.uuid4())
    storage = create_storage_backend()
    storage_path = f"templates/{template_id}/{filename}"
    await storage.save(content, storage_path, file.content_type or "application/octet-stream")

    template = Template(
        id=template_id,
        name=filename.rsplit(".", 1)[0],
        filename=filename,
        file_path=storage_path,
        size_bytes=len(content),
        analysis=analysis,
        status="analyzed",
        created_at=datetime.utcnow(),
    )
    db.add(template)

    return TemplateUploadResponse(
        id=template_id,
        name=template.name,
        filename=template.filename,
        status="analyzed",
        size_bytes=len(content),
        created_at=template.created_at,
    )


@router.get("/{template_id}", response_model=TemplateAnalysisResponse)
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Get template details and analysis results."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    template = result.scalar_one_or_none()

    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return TemplateAnalysisResponse(
        id=template.id,
        status=template.status,
        analysis=template.analysis,
    )


@router.get("/")
async def list_templates(
    db: AsyncSession = Depends(get_session),
):
    """List all uploaded templates."""
    result = await db.execute(select(Template).order_by(Template.created_at.desc()))
    templates = result.scalars().all()

    return [
        {
            "id": t.id,
            "name": t.name,
            "filename": t.filename,
            "status": t.status,
            "size_bytes": t.size_bytes,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in templates
    ]
