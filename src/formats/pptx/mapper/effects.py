"""Effects Mapper — gradient, shadow, border → OOXML XML generation."""

from __future__ import annotations

import math
import re
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)

PX_TO_EMU = 9525
PT_TO_EMU = 12700
DEGREES_TO_60K = 60000
GRADIENT_POS_TO_PERMILLE = 1000


def apply_gradient_fill(pptx_shape, gradient_css: str) -> None:
    """Apply CSS linear-gradient to a shape as OOXML gradFill."""
    from pptx.oxml.ns import qn
    from lxml import etree

    parsed = parse_gradient(gradient_css)
    if not parsed or len(parsed["stops"]) < 2:
        return

    spPr = pptx_shape._element.spPr

    for tag in ("a:solidFill", "a:gradFill", "a:noFill"):
        existing = spPr.find(qn(tag))
        if existing is not None:
            spPr.remove(existing)

    gradFill = etree.SubElement(spPr, qn("a:gradFill"))
    gsLst = etree.SubElement(gradFill, qn("a:gsLst"))

    for stop in parsed["stops"]:
        gs = etree.SubElement(gsLst, qn("a:gs"))
        gs.set("pos", str(int(stop["position"] * GRADIENT_POS_TO_PERMILLE)))
        srgbClr = etree.SubElement(gs, qn("a:srgbClr"))
        srgbClr.set("val", stop["color"])

    lin = etree.SubElement(gradFill, qn("a:lin"))
    lin.set("ang", str(int(parsed["angle"] * DEGREES_TO_60K)))
    lin.set("scaled", "1")


def apply_shadow(pptx_shape, shadow_css: str) -> None:
    """Apply CSS box-shadow to a shape as OOXML outerShdw."""
    from pptx.oxml.ns import qn
    from lxml import etree

    parsed = parse_box_shadow(shadow_css)
    if not parsed:
        return

    spPr = pptx_shape._element.spPr
    effectLst = spPr.find(qn("a:effectLst"))
    if effectLst is None:
        effectLst = etree.SubElement(spPr, qn("a:effectLst"))

    offset_x = parsed["offset_x"]
    offset_y = parsed["offset_y"]
    blur = parsed["blur"]
    color = parsed["color"]
    alpha = parsed["alpha"]

    dist = int(math.hypot(offset_x, offset_y) * PX_TO_EMU)
    direction = 0
    if dist > 0:
        direction = int(math.degrees(math.atan2(offset_y, offset_x)) * DEGREES_TO_60K)
        if direction < 0:
            direction += 360 * DEGREES_TO_60K

    outerShdw = etree.SubElement(effectLst, qn("a:outerShdw"))
    outerShdw.set("blurRad", str(int(blur * PX_TO_EMU)))
    outerShdw.set("dist", str(dist))
    outerShdw.set("dir", str(direction))
    outerShdw.set("rotWithShape", "0")

    srgbClr = etree.SubElement(outerShdw, qn("a:srgbClr"))
    srgbClr.set("val", color)
    alpha_elem = etree.SubElement(srgbClr, qn("a:alpha"))
    alpha_elem.set("val", str(int(alpha * 100000)))


def apply_border(pptx_shape, width_px: float, color: str, style: str = "solid") -> None:
    """Apply border to a shape line."""
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    line = pptx_shape.line
    line.color.rgb = RGBColor.from_string(color)
    line.width = Pt(width_px)
    if style == "dashed":
        line.dash_style = 2  # msoLineDash
    elif style == "dotted":
        line.dash_style = 3  # msoLineDot


def apply_corner_radius(pptx_shape, radius_px: float, width_emu: int, height_emu: int) -> None:
    """Set corner rounding on a rounded rectangle."""
    try:
        min_dim = min(width_emu, height_emu)
        if min_dim <= 0:
            return
        max_radius_emu = min_dim // 2
        radius_emu = min(int(radius_px * PX_TO_EMU), max_radius_emu)
        ratio = int((radius_emu / max_radius_emu) * 50000) if max_radius_emu > 0 else 0
        ratio = min(ratio, 50000)
        if len(pptx_shape.adjustments) > 0:
            pptx_shape.adjustments[0] = ratio / 100000.0
    except (IndexError, TypeError, ValueError):
        pass


# ─── CSS Parsers ───────────────────────────────────────────────────────────────

_GRADIENT_RE = re.compile(
    r"linear-gradient\(\s*([\d.]+)deg\s*,\s*(.+)\)", re.IGNORECASE
)
_GRADIENT_KEYWORD_RE = re.compile(
    r"linear-gradient\(\s*to\s+([\w\s]+?)\s*,\s*(.+)\)", re.IGNORECASE
)
_GRADIENT_STOP_RE = re.compile(
    r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))\s*([\d.]+%)?",
)

_DIRECTION_TO_ANGLE = {
    "top": 0, "right": 90, "bottom": 180, "left": 270,
    "top right": 45, "right top": 45,
    "bottom right": 135, "right bottom": 135,
    "bottom left": 225, "left bottom": 225,
    "top left": 315, "left top": 315,
}

_SHADOW_RE = re.compile(
    r"([-\d.]+)px\s+([-\d.]+)px\s+([-\d.]+)px\s+"
    r"(?:([-\d.]+)px\s+)?"
    r"(rgba?\([^)]+\)|#[0-9a-fA-F]{3,8})",
)


def parse_gradient(css: str) -> dict | None:
    """Parse CSS linear-gradient into angle + stops."""
    match = _GRADIENT_RE.search(css)
    if match:
        angle = float(match.group(1))
        stops_str = match.group(2)
    else:
        kw_match = _GRADIENT_KEYWORD_RE.search(css)
        if not kw_match:
            return None
        direction = kw_match.group(1).strip().lower()
        angle = _DIRECTION_TO_ANGLE.get(direction, 180)
        stops_str = kw_match.group(2)

    stops = []
    parts = [s.strip() for s in stops_str.split(",")]

    for i, part in enumerate(parts):
        color_hex = _css_color_to_hex(part.split()[0] if part.split() else part)
        if not color_hex:
            continue
        pos_match = re.search(r"([\d.]+)%", part)
        if pos_match:
            position = float(pos_match.group(1))
        else:
            position = (i / max(len(parts) - 1, 1)) * 100
        stops.append({"color": color_hex, "position": position})

    if len(stops) < 2:
        return None

    return {"angle": angle, "stops": stops}


def parse_box_shadow(css: str) -> dict | None:
    """Parse CSS box-shadow into components."""
    match = _SHADOW_RE.search(css)
    if not match:
        return None

    offset_x = float(match.group(1))
    offset_y = float(match.group(2))
    blur = float(match.group(3))
    color_str = match.group(5) if match.group(5) else match.group(4)

    color_hex = "000000"
    alpha = 0.15
    if color_str:
        rgba_match = re.match(
            r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)",
            color_str,
        )
        if rgba_match:
            r, g, b = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3))
            color_hex = f"{r:02x}{g:02x}{b:02x}"
            alpha = float(rgba_match.group(4)) if rgba_match.group(4) else 1.0
        else:
            color_hex = _css_color_to_hex(color_str) or "000000"

    return {
        "offset_x": offset_x,
        "offset_y": offset_y,
        "blur": blur,
        "color": color_hex,
        "alpha": alpha,
    }


def parse_border(css: str) -> dict | None:
    """Parse CSS border shorthand."""
    match = re.match(r"([\d.]+)px\s+(solid|dashed|dotted)\s+(#[0-9a-fA-F]{3,8})", css.strip())
    if not match:
        return None
    return {
        "width": float(match.group(1)),
        "style": match.group(2),
        "color": _css_color_to_hex(match.group(3)) or "000000",
    }


def _css_color_to_hex(color: str) -> str | None:
    """Convert CSS color to 6-char hex without #."""
    color = color.strip()
    if color.startswith("#"):
        hex_val = color[1:]
        if len(hex_val) == 3:
            return "".join(c * 2 for c in hex_val).lower()
        if len(hex_val) in (6, 8):
            return hex_val[:6].lower()
    rgba_match = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", color)
    if rgba_match:
        r, g, b = int(rgba_match.group(1)), int(rgba_match.group(2)), int(rgba_match.group(3))
        return f"{r:02x}{g:02x}{b:02x}"
    return None
