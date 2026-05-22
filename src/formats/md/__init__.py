"""Markdown format engine - Markdown document generation."""

from src.formats.md.engine import MarkdownFormatEngine
from src.formats.registry import register_format

register_format(MarkdownFormatEngine)

__all__ = ["MarkdownFormatEngine"]
