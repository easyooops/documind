"""HTML format engine - static HTML document export."""

from src.formats.html.engine import HTMLFormatEngine
from src.formats.registry import register_format

register_format(HTMLFormatEngine)

__all__ = ["HTMLFormatEngine"]
