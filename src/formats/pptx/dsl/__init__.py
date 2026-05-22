"""OOXML-DSL package — defines the intermediate representation and renderers."""

from src.formats.pptx.dsl.schema import (
    PresentationDSL,
    SlideDSL,
    Shape,
    ShapePosition,
    SolidFill,
    GradientFill,
    GradientStop,
    NoFill,
    Shadow,
    Border,
    TextRun,
    TextParagraph,
)
from src.formats.pptx.dsl.pptx_builder import DSLtoPPTXBuilder
from src.formats.pptx.dsl.html_renderer import DSLtoHTMLRenderer

__all__ = [
    "PresentationDSL",
    "SlideDSL",
    "Shape",
    "ShapePosition",
    "SolidFill",
    "GradientFill",
    "GradientStop",
    "NoFill",
    "Shadow",
    "Border",
    "TextRun",
    "TextParagraph",
    "DSLtoPPTXBuilder",
    "DSLtoHTMLRenderer",
]
