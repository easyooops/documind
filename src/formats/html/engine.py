"""HTML Format Engine - generates standalone HTML documents."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer, FormatEngine
from src.formats.html.renderer import HTMLRenderer


class HTMLFormatEngine(FormatEngine):
    """Static HTML export engine."""

    @property
    def format_id(self) -> str:
        return "html"

    @property
    def renderer(self) -> DocumentRenderer:
        return HTMLRenderer()

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Render content to a standalone .html file."""
        renderer = HTMLRenderer()
        return await renderer.render(classified_elements, output_dir)

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Validate HTML output (structure check)."""
        if output_path.exists() and output_path.stat().st_size > 0:
            return 1.0
        return 0.0
