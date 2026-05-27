"""XLSX format engine."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import DocumentRenderer, FormatEngine
from src.formats.xlsx.renderer import XLSXRenderer


class XLSXFormatEngine(FormatEngine):
    @property
    def format_id(self) -> str:
        return "xlsx"

    @property
    def renderer(self) -> DocumentRenderer:
        return XLSXRenderer()

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        return await self.renderer.render(classified_elements, output_dir)

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        return 1.0 if output_path.exists() and output_path.stat().st_size else 0.0

