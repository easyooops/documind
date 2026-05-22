"""HTML Renderer - exports content as a standalone HTML document."""

from __future__ import annotations

import uuid
from pathlib import Path

from src.formats.base import DocumentRenderer
from src.core.logging import get_logger

logger = get_logger(__name__)


class HTMLRenderer(DocumentRenderer):
    """Renders content as a standalone HTML document with embedded CSS."""

    @property
    def format_name(self) -> str:
        return "html"

    @property
    def mime_type(self) -> str:
        return "text/html"

    @property
    def file_extension(self) -> str:
        return ".html"

    async def render(self, slides_data: list[dict], output_dir: Path) -> Path:
        """Export slides as a self-contained HTML file.

        Each slide becomes a section within the HTML document.
        """
        raise NotImplementedError(
            "HTML export is planned for a future phase. "
            "Currently only PPTX format is fully implemented."
        )
