"""PDF format engine - PDF document generation."""

from src.formats.pdf.engine import PDFFormatEngine
from src.formats.registry import register_format

register_format(PDFFormatEngine)

__all__ = ["PDFFormatEngine"]
