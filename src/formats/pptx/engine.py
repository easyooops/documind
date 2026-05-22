"""PPTX Format Engine — wraps DSL builder and QA for PowerPoint generation.

With the OOXML-DSL architecture, the engine delegates to DSLtoPPTXBuilder.
The old ConversionPipeline, Playwright layout extraction, and CSS classification
are no longer used.
"""

from __future__ import annotations

from pathlib import Path

from src.formats.base import FormatEngine
from src.formats.pptx.dsl.pptx_builder import DSLtoPPTXBuilder
from src.formats.pptx.dsl.schema import PresentationDSL


class PPTXFormatEngine(FormatEngine):
    """Complete PPTX generation engine using OOXML-DSL."""

    def __init__(self):
        self._builder = DSLtoPPTXBuilder()

    @property
    def format_id(self) -> str:
        return "pptx"

    @property
    def renderer(self):
        return None

    async def render_dsl(self, dsl: PresentationDSL, output_dir: Path) -> Path:
        """Render PresentationDSL to a .pptx file (lossless)."""
        return self._builder.build(dsl, output_dir)

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Legacy interface — not used in OOXML-DSL architecture."""
        raise NotImplementedError(
            "PPTXFormatEngine.render() is deprecated. Use render_dsl() with OOXML-DSL."
        )

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Validate PPTX output quality."""
        from src.formats.pptx.qa import PPTXQualityAssurance
        qa = PPTXQualityAssurance()
        return await qa.evaluate(str(output_path), reference_html)
