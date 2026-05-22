"""PDF Renderer - PDF document generation (Phase 3 stub)."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer
from src.core.logging import get_logger

logger = get_logger(__name__)


class PDFRenderer(DocumentRenderer):
    """Renders content as a PDF document (Phase 3)."""

    @property
    def format_name(self) -> str:
        return "pdf"

    @property
    def mime_type(self) -> str:
        return "application/pdf"

    @property
    def file_extension(self) -> str:
        return ".pdf"

    async def render(self, slides_data: list[dict], output_dir: Path) -> Path:
        """Convert content to a .pdf file (Phase 3)."""
        raise NotImplementedError(
            "PDF rendering is planned for Phase 3. "
            "Currently only PPTX format is fully implemented."
        )
