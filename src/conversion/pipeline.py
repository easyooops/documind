"""Conversion pipeline — DEPRECATED.

With the OOXML-DSL architecture, conversion is handled directly by
`src.formats.pptx.dsl.pptx_builder.DSLtoPPTXBuilder` in the conversion node.
Playwright layout extraction and CSS classification are no longer needed.
"""

from __future__ import annotations

from src.core.logging import get_logger

logger = get_logger(__name__)


class ConversionPipeline:
    """DEPRECATED: Use DSLtoPPTXBuilder directly via conversion_node.

    This class is retained only to avoid import errors in any remaining
    references. All new code should use the OOXML-DSL pipeline.
    """

    def __init__(self, document_format: str = "pptx"):
        self.format = document_format

    async def convert(self, slides_html: list[dict]) -> str:
        raise NotImplementedError(
            "ConversionPipeline is deprecated. "
            "The OOXML-DSL architecture uses DSLtoPPTXBuilder directly."
        )
