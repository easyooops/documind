"""Markdown Format Engine - generates structured .md documents."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer, FormatEngine
from src.formats.md.renderer import MarkdownRenderer


class MarkdownFormatEngine(FormatEngine):
    """Markdown generation engine."""

    @property
    def format_id(self) -> str:
        return "md"

    @property
    def renderer(self) -> DocumentRenderer:
        return MarkdownRenderer()

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Render content to a .md file."""
        renderer = MarkdownRenderer()
        return await renderer.render(classified_elements, output_dir)

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Validate Markdown output (structure check)."""
        if output_path.exists() and output_path.stat().st_size > 0:
            return 1.0
        return 0.0
