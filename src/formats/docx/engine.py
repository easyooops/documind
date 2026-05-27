"""DOCX format engine for designed native Word documents."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer, FormatEngine
from src.formats.docx.renderer import DOCXRenderer


class DOCXFormatEngine(FormatEngine):
    """DOCX generation engine backed by the template-led native renderer."""

    @property
    def format_id(self) -> str:
        return "docx"

    @property
    def renderer(self) -> DocumentRenderer:
        return DOCXRenderer()

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Render classified HTML elements to a .docx file."""
        renderer = DOCXRenderer()
        return await renderer.render(classified_elements, output_dir)

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Basic artifact validation for the legacy renderer interface."""
        return 1.0 if output_path.exists() and output_path.stat().st_size else 0.0
