"""OOXML-DSL Schema — Pydantic models that map 1:1 to DrawingML.

Unit conventions (all stored in px, converted at render time):
- Position/Size: px → EMU = px * 9525
- Font size: px → hundredths-of-point = px * 75
- Gradient angle: CSS degrees → 60000ths = degrees * 60000
- Shadow blur: px → EMU = px * 12700
- Gradient stop position: 0-100 → per-mille = pos * 1000
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ShapePosition(BaseModel):
    x: int = Field(ge=0, description="Left offset in px")
    y: int = Field(ge=0, description="Top offset in px")
    w: int = Field(gt=0, description="Width in px")
    h: int = Field(gt=0, description="Height in px")


class SolidFill(BaseModel):
    type: Literal["solid"] = "solid"
    color: str = Field(pattern=r"^[0-9a-fA-F]{6}$", description="6-char hex color without #")


class GradientStop(BaseModel):
    position: int = Field(ge=0, le=100, description="Stop position 0-100")
    color: str = Field(pattern=r"^[0-9a-fA-F]{6}$", description="6-char hex color without #")


class GradientFill(BaseModel):
    type: Literal["gradient"] = "gradient"
    angle: int = Field(ge=0, lt=360, description="Angle in CSS degrees (0=top-to-bottom, 90=left-to-right)")
    stops: list[GradientStop] = Field(min_length=2, max_length=6)


class NoFill(BaseModel):
    type: Literal["none"] = "none"


FillType = SolidFill | GradientFill | NoFill


class Shadow(BaseModel):
    offset_x: int = Field(default=0, description="Horizontal offset in px")
    offset_y: int = Field(default=4, description="Vertical offset in px")
    blur: int = Field(ge=0, default=12, description="Blur radius in px")
    color: str = Field(default="000000", pattern=r"^[0-9a-fA-F]{6}$")
    opacity: float = Field(ge=0.0, le=1.0, default=0.15)


class Border(BaseModel):
    width: int = Field(ge=1, default=1, description="Border width in px")
    color: str = Field(pattern=r"^[0-9a-fA-F]{6}$", description="6-char hex")
    style: Literal["solid", "dashed", "dotted"] = "solid"


class TextRun(BaseModel):
    text: str
    font_size: int = Field(ge=8, le=120, default=16, description="Font size in px")
    font_weight: Literal[100, 200, 300, 400, 500, 600, 700, 800, 900] = 400
    font_family: str = "Pretendard"
    color: str = Field(default="000000", pattern=r"^[0-9a-fA-F]{6}$")
    italic: bool = False
    letter_spacing: float = Field(default=0, description="Letter spacing in px")


class TextParagraph(BaseModel):
    runs: list[TextRun] = Field(min_length=1)
    align: Literal["left", "center", "right", "justify"] = "left"
    line_height: float = Field(ge=0.8, le=3.0, default=1.5)
    spacing_before: int = Field(ge=0, default=0, description="Space before paragraph in px")


class Shape(BaseModel):
    id: str = Field(min_length=1, description="Unique shape identifier")
    role: Literal["title", "subtitle", "body", "decorative", "chart", "image", "badge", "kpi", "label"] = "body"
    position: ShapePosition
    z_index: int = Field(default=0, description="Stacking order")
    fill: FillType | None = None
    border_radius: int = Field(ge=0, default=0, description="Corner radius in px")
    shadow: Shadow | None = None
    opacity: float = Field(ge=0.0, le=1.0, default=1.0)
    border: Border | None = None
    text: list[TextParagraph] | None = None

    @field_validator("id")
    @classmethod
    def id_no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("Shape id must not contain spaces")
        return v


class SlideDSL(BaseModel):
    index: int = Field(ge=1)
    slide_type: Literal["cover", "toc", "content", "data", "comparison", "summary", "cta", "section"] = "content"
    shapes: list[Shape] = Field(min_length=1)


class PresentationDSL(BaseModel):
    title: str
    slides: list[SlideDSL] = Field(min_length=1)
    viewport_width: int = 960
    viewport_height: int = 540


# ─── Unit Conversion Constants ────────────────────────────────────────────────

PX_TO_EMU = 9525
PT_TO_EMU = 12700
FONT_PX_TO_HUNDREDTHS_PT = 75
DEGREES_TO_60K = 60000
GRADIENT_POS_TO_PERMILLE = 1000
