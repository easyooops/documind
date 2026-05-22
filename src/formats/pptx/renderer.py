"""PPTX Renderer — DEPRECATED.

This module is replaced by `src.formats.pptx.dsl.pptx_builder.DSLtoPPTXBuilder`.
The old renderer converted Playwright-extracted layout data to python-pptx calls.
The new DSL builder converts OOXML-DSL directly to PPTX with zero information loss.
"""

from __future__ import annotations

from pathlib import Path


class PPTXRenderer:
    """DEPRECATED: Use DSLtoPPTXBuilder instead."""

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        raise NotImplementedError(
            "PPTXRenderer is deprecated. Use src.formats.pptx.dsl.pptx_builder.DSLtoPPTXBuilder."
        )
