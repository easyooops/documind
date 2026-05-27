"""XLSX format engine - designed analytical workbook generation."""

from src.formats.registry import register_format
from src.formats.xlsx.engine import XLSXFormatEngine

register_format(XLSXFormatEngine)

__all__ = ["XLSXFormatEngine"]

