"""PPTX format engine - PowerPoint document generation."""

from src.formats.pptx.engine import PPTXFormatEngine
from src.formats.registry import register_format

register_format(PPTXFormatEngine)

__all__ = ["PPTXFormatEngine"]
