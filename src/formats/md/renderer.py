"""Markdown Renderer - converts structured content to .md format."""

from __future__ import annotations

import uuid
from pathlib import Path

from src.formats.base import DocumentRenderer
from src.core.logging import get_logger

logger = get_logger(__name__)


class MarkdownRenderer(DocumentRenderer):
    """Renders content as a structured Markdown document."""

    @property
    def format_name(self) -> str:
        return "md"

    @property
    def mime_type(self) -> str:
        return "text/markdown"

    @property
    def file_extension(self) -> str:
        return ".md"

    async def render(self, slides_data: list[dict], output_dir: Path) -> Path:
        """Convert structured content to a .md file.

        For Markdown, 'slides' are treated as sections/chapters.
        """
        raise NotImplementedError(
            "Markdown rendering is planned for a future phase. "
            "Currently only PPTX format is fully implemented."
        )
