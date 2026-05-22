"""DOCX Renderer - Word document generation using python-docx (Phase 2 stub)."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer
from src.core.logging import get_logger

logger = get_logger(__name__)


class DOCXRenderer(DocumentRenderer):
    """Renders HTML elements as a Word document (Phase 2)."""

    @property
    def format_name(self) -> str:
        return "docx"

    @property
    def mime_type(self) -> str:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    @property
    def file_extension(self) -> str:
        return ".docx"

    async def render(self, slides_data: list[dict], output_dir: Path) -> Path:
        """Convert content data to a .docx file (Phase 2)."""
        raise NotImplementedError(
            "DOCX rendering is planned for Phase 2. "
            "Currently only PPTX format is fully implemented."
        )
