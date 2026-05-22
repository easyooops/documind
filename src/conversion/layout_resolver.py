"""Layout Resolver — DEPRECATED.

This module is no longer used in the OOXML-DSL architecture.
Previously used Playwright to extract element positions from rendered HTML.
With DSL, positions are defined explicitly in the schema, eliminating the need
for browser-based layout extraction.
"""

from __future__ import annotations


class LayoutResolver:
    """DEPRECATED: Playwright-based layout extraction."""

    async def extract(self, html: str) -> list[dict]:
        raise NotImplementedError(
            "LayoutResolver is deprecated. OOXML-DSL architecture defines positions directly in the schema."
        )
