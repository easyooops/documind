"""DOCX format engine - Word document generation."""

from src.formats.docx.engine import DOCXFormatEngine
from src.formats.registry import register_format

register_format(DOCXFormatEngine)

__all__ = ["DOCXFormatEngine"]
