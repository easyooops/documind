"""PDF Format Engine - PDF document generation (Phase 3)."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer, FormatEngine
from src.formats.pdf.renderer import PDFRenderer


class PDFFormatEngine(FormatEngine):
    """PDF generation engine (Phase 3 - stub)."""

    @property
    def format_id(self) -> str:
        return "pdf"

    @property
    def renderer(self) -> DocumentRenderer:
        return PDFRenderer()

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Render content to a .pdf file."""
        renderer = PDFRenderer()
        return await renderer.render(classified_elements, output_dir)

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Validate PDF output (Phase 3)."""
        return 1.0
