"""Unit conversion utilities for OOXML (EMU, points, etc.)."""

from __future__ import annotations

DPI = 96
PX_TO_EMU = 914400 // DPI  # 9525
PT_TO_EMU = 12700
INCHES_TO_EMU = 914400
CM_TO_EMU = 360000


def px_to_emu(px: float) -> int:
    """Convert pixels to English Metric Units."""
    return round(px * PX_TO_EMU)


def emu_to_px(emu: int) -> float:
    """Convert EMU to pixels."""
    return emu / PX_TO_EMU


def pt_to_emu(pt: float) -> int:
    """Convert points to EMU."""
    return round(pt * PT_TO_EMU)


def emu_to_pt(emu: int) -> float:
    """Convert EMU to points."""
    return emu / PT_TO_EMU


def inches_to_emu(inches: float) -> int:
    """Convert inches to EMU."""
    return round(inches * INCHES_TO_EMU)


def cm_to_emu(cm: float) -> int:
    """Convert centimeters to EMU."""
    return round(cm * CM_TO_EMU)


def deg_to_ooxml_angle(degrees: float) -> int:
    """Convert degrees to OOXML angle units (60000ths of a degree)."""
    return round(degrees * 60000)


def px_to_half_points(px: float) -> int:
    """Convert pixels to half-points (for font sizes in OOXML)."""
    pt = px * 0.75
    return round(pt * 100)


def css_opacity_to_ooxml_alpha(opacity: float) -> int:
    """Convert CSS opacity (0-1) to OOXML alpha (0-100000)."""
    return round(opacity * 100000)
