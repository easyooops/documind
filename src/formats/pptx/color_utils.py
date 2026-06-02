"""Color helpers shared by PPTX generation, validation, and OOXML mapping."""

from __future__ import annotations

import colorsys
import re


def extract_colors(value: str) -> list[str]:
    """Extract 6-character lowercase hex colors from a CSS color string."""
    text = str(value or "").strip()
    if not text or text.lower() in {"transparent", "none", "inherit", "initial"}:
        return []
    colors: list[str] = []
    for hex_value in re.findall(r"#([0-9a-fA-F]{3,8})", text):
        if len(hex_value) == 3:
            colors.append("".join(char * 2 for char in hex_value).lower())
        elif len(hex_value) >= 6:
            colors.append(hex_value[:6].lower())
    for rgba in re.findall(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text):
        r, g, b = (max(0, min(255, int(channel))) for channel in rgba)
        colors.append(f"{r:02x}{g:02x}{b:02x}")
    return colors


def normalize_hex(value: str) -> str | None:
    colors = extract_colors(value)
    if colors:
        return colors[0]
    text = str(value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", text):
        return "".join(char * 2 for char in text).lower()
    if re.fullmatch(r"[0-9a-fA-F]{6,8}", text):
        return text[:6].lower()
    return None


def contrast_ratio(color_a: str, color_b: str) -> float:
    lighter = max(relative_luminance(color_a), relative_luminance(color_b))
    darker = min(relative_luminance(color_a), relative_luminance(color_b))
    return (lighter + 0.05) / (darker + 0.05)


def relative_luminance(hex_color: str) -> float:
    try:
        channels = [
            int(str(hex_color).lstrip("#")[i:i + 2], 16) / 255
            for i in (0, 2, 4)
        ]
    except (ValueError, IndexError):
        return 1.0

    def linear(channel: float) -> float:
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_dark_color(hex_color: str) -> bool:
    color = normalize_hex(hex_color)
    if not color:
        return False
    return relative_luminance(color) < 0.32


def contrast_threshold(font_size_px: float, bold: bool) -> float:
    return 3.0 if font_size_px >= 24 or (bold and font_size_px >= 18.5) else 4.5


def choose_legible_text_color(
    foreground: str | None,
    backgrounds: str | list[str],
    *,
    font_size_px: float = 16,
    bold: bool = False,
) -> str:
    """Pick a readable text color using theme-family contrast rules.

    Light or near-white backgrounds get a dark color derived from the same hue.
    Dark backgrounds get white. An existing foreground is preserved when it
    already clears the contrast gate.
    """
    bg_colors = _normalize_backgrounds(backgrounds)
    if not bg_colors:
        return (normalize_hex(foreground or "") or "111827").upper()

    threshold = contrast_threshold(font_size_px, bold)
    current = normalize_hex(foreground or "")
    if current and all(contrast_ratio(current, bg) >= threshold for bg in bg_colors):
        return current.upper()

    preferred_candidates = []
    for bg in bg_colors:
        if is_dark_color(bg):
            preferred_candidates.append("ffffff")
            preferred_candidates.append(_near_white_from_hue(bg))
        else:
            preferred_candidates.extend(_dark_text_candidates_from_hue(bg))
    for candidate in _dedupe(preferred_candidates):
        if all(contrast_ratio(candidate, bg) >= threshold for bg in bg_colors):
            return candidate.upper()

    candidates = list(preferred_candidates)
    candidates.extend(["111827", "ffffff"])

    best = max(
        _dedupe(candidates),
        key=lambda color: min(contrast_ratio(color, bg) for bg in bg_colors),
    )
    return best.upper()


def _normalize_backgrounds(backgrounds: str | list[str]) -> list[str]:
    raw = [backgrounds] if isinstance(backgrounds, str) else backgrounds
    colors: list[str] = []
    for value in raw:
        color = normalize_hex(str(value or ""))
        if color:
            colors.append(color)
    return colors


def _dark_text_candidates_from_hue(background: str) -> list[str]:
    r, g, b = _hex_to_rgb(background)
    h, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if saturation < 0.08:
        return ["111827", "1f2937", "0f172a"]
    sat = max(0.45, min(0.78, saturation + 0.28))
    lightnesses = [0.12, 0.16, 0.20, 0.24]
    if lightness < 0.82:
        lightnesses = [0.10, 0.14, 0.18]
    return [_hls_to_hex(h, l, sat) for l in lightnesses]


def _near_white_from_hue(background: str) -> str:
    r, g, b = _hex_to_rgb(background)
    h, _lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if saturation < 0.08:
        return "ffffff"
    return _hls_to_hex(h, 0.96, min(0.45, saturation))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    color = normalize_hex(hex_color) or "ffffff"
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _hls_to_hex(hue: float, lightness: float, saturation: float) -> str:
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        color = normalize_hex(value)
        if not color or color in seen:
            continue
        seen.add(color)
        result.append(color)
    return result
