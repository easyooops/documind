"""Layer 0: Master Context — slide canvas, grid, object catalog, and template parsing.

This module provides the foundational constraints and element registry that all
downstream phases (planning, HTML generation, PPTX conversion) reference.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)

# ─── Slide Canvas ─────────────────────────────────────────────────────────────

SLIDE_CANVAS = {
    "width": 960,
    "height": 540,
    "ratio": "16:9",
    "width_emu": 9_144_000,
    "height_emu": 5_143_500,
}

SAFE_AREA = {
    "top": 48,
    "bottom": 48,
    "left": 58,
    "right": 58,
}

GRID = {
    "columns": 12,
    "gutter": 14.4,
    "column_width": (960 - 58 * 2 - 14.4 * 11) / 12,
}

# ─── Default Master Layout ─────────────────────────────────────────────────────

DEFAULT_MASTER = {
    "canvas": SLIDE_CANVAS,
    "safe_area": SAFE_AREA,
    "grid": GRID,
    "regions": {
        "header": {"x": 58, "y": 36, "w": 844, "h": 76},
        "body": {"x": 58, "y": 128, "w": 844, "h": 356},
        "footer": {"x": 58, "y": 500, "w": 844, "h": 26},
    },
    "anchors": {
        "title": {"x": 58, "y": 38, "w": 820, "h": 66, "max_lines": 2},
        "subtitle": {"x": 58, "y": 92, "w": 760, "h": 24, "max_lines": 1},
        "footer_source": {"x": 58, "y": 506, "w": 650, "h": 16},
        "footer_page": {"x": 828, "y": 506, "w": 72, "h": 16},
    },
    "default_layouts": [
        "title_slide",
        "section_header",
        "content_with_header",
        "two_column",
        "blank_with_header",
    ],
}

# ─── Object Catalog ────────────────────────────────────────────────────────────

OBJECT_CATALOG = {
    "shapes": {
        "basic": [
            "rect", "rounded_rect", "oval", "triangle", "diamond",
            "pentagon", "hexagon", "octagon", "parallelogram", "trapezoid",
        ],
        "arrows": [
            "right_arrow", "left_arrow", "up_arrow", "down_arrow",
            "chevron", "bent_arrow", "circular_arrow", "u_turn_arrow",
            "striped_right_arrow", "notched_right_arrow",
        ],
        "callouts": [
            "cloud", "thought_bubble", "rounded_callout",
            "wedge_rect_callout", "wedge_ellipse_callout",
        ],
        "stars_banners": [
            "star_4", "star_5", "star_6", "star_8",
            "ribbon", "ribbon_2", "explosion_1", "explosion_2",
        ],
        "flowchart": [
            "flowchart_process", "flowchart_decision", "flowchart_terminator",
            "flowchart_data", "flowchart_document", "flowchart_connector",
            "flowchart_merge", "flowchart_delay",
        ],
        "block": [
            "block_arc", "donut", "bevel", "frame", "half_frame",
            "corner", "fold_corner", "smiley_face",
        ],
    },
    "text": ["textbox", "placeholder"],
    "data_viz": {
        "table": ["table_banded", "table_plain"],
        "chart": ["bar", "column", "line", "pie", "doughnut", "area", "scatter", "radar"],
        "smartart": ["list", "process", "cycle", "hierarchy", "relationship"],
    },
    "media": ["picture", "icon"],
    "decorative": {
        "lines": ["straight_line", "elbow_connector", "curved_connector"],
        "groups": ["group"],
        "fills": ["gradient_fill", "pattern_fill", "picture_fill"],
    },
    "effects": ["shadow_outer", "shadow_inner", "glow", "soft_edge", "reflection", "3d_rotation"],
}

# ─── Design Directions (palette seeds) ─────────────────────────────────────────

DESIGN_DIRECTIONS = [
    {
        "name": "obsidian_lime",
        "primary": "#111827", "secondary": "#334155", "accent": "#A3E635",
        "background": "#F8FAFC", "surface": "#FFFFFF", "tint": "#ECFCCB",
    },
    {
        "name": "ink_coral",
        "primary": "#1E1B4B", "secondary": "#4338CA", "accent": "#F97316",
        "background": "#FAFAF9", "surface": "#FFFFFF", "tint": "#FFF7ED",
    },
    {
        "name": "forest_gold",
        "primary": "#12372A", "secondary": "#436850", "accent": "#D6A84F",
        "background": "#F7F8F3", "surface": "#FFFFFF", "tint": "#F8EBC9",
    },
    {
        "name": "plum_cyan",
        "primary": "#3B0764", "secondary": "#7E22CE", "accent": "#06B6D4",
        "background": "#F8FAFC", "surface": "#FFFFFF", "tint": "#ECFEFF",
    },
    {
        "name": "graphite_rose",
        "primary": "#27272A", "secondary": "#52525B", "accent": "#E11D48",
        "background": "#F9FAFB", "surface": "#FFFFFF", "tint": "#FFF1F2",
    },
    {
        "name": "ocean_amber",
        "primary": "#0C4A6E", "secondary": "#0369A1", "accent": "#F59E0B",
        "background": "#F0F9FF", "surface": "#FFFFFF", "tint": "#FFFBEB",
    },
    {
        "name": "slate_emerald",
        "primary": "#1E293B", "secondary": "#475569", "accent": "#10B981",
        "background": "#F8FAFC", "surface": "#FFFFFF", "tint": "#ECFDF5",
    },
]


def select_design_direction(seed: str) -> dict:
    """Deterministically select a design direction from a seed string."""
    import hashlib
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
    return DESIGN_DIRECTIONS[int(digest[:8], 16) % len(DESIGN_DIRECTIONS)]


# ─── Template Parser ───────────────────────────────────────────────────────────

EMU_PER_PX = 9525


def parse_template(template_bytes: bytes, filename: str = "template.pptx") -> dict:
    """Parse a .pptx template and extract master context for downstream agents.

    Returns a dict with: theme, layouts, used_elements, design_patterns.
    """
    from pptx import Presentation

    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".pptx", delete=False) as tmp:
        tmp.write(template_bytes)
        tmp_path = Path(tmp.name)

    try:
        prs = Presentation(str(tmp_path))
        return _extract_template_context(prs)
    finally:
        tmp_path.unlink(missing_ok=True)


def _extract_template_context(prs) -> dict:
    """Extract comprehensive context from a parsed Presentation object."""
    colors: Counter[str] = Counter()
    fonts: Counter[str] = Counter()
    font_sizes: Counter[int] = Counter()

    layouts = []
    for idx, layout in enumerate(prs.slide_layouts, 1):
        placeholders = []
        for shape in layout.shapes:
            _collect_style_info(shape, colors, fonts, font_sizes)
            if shape.is_placeholder:
                placeholders.append({
                    "type": str(getattr(shape.placeholder_format, "type", "")),
                    "idx": getattr(shape.placeholder_format, "idx", None),
                    "position": _shape_position(shape),
                })
        layouts.append({
            "index": idx,
            "name": getattr(layout, "name", f"Layout {idx}"),
            "placeholders": placeholders,
        })

    used_elements = []
    for slide in prs.slides:
        for shape in slide.shapes:
            _collect_style_info(shape, colors, fonts, font_sizes)
            elem = _classify_element(shape)
            if elem:
                used_elements.append(elem)

    palette = [f"#{c}" for c, _ in colors.most_common(10)]
    font_names = [f for f, _ in fonts.most_common(4)]

    return {
        "slide_master": {
            "width_px": _emu_to_px(prs.slide_width),
            "height_px": _emu_to_px(prs.slide_height),
            "layouts": layouts[:12],
        },
        "theme": {
            "colors": _palette_to_theme(palette),
            "fonts": {
                "major": font_names[0] if font_names else "Pretendard",
                "minor": font_names[1] if len(font_names) > 1 else "Pretendard",
                "observed": font_names,
            },
            "font_sizes": [s for s, _ in font_sizes.most_common(6)],
        },
        "used_elements": used_elements[:30],
        "design_patterns": _infer_patterns(used_elements),
    }


def _shape_position(shape) -> dict:
    return {
        "x": _emu_to_px(shape.left),
        "y": _emu_to_px(shape.top),
        "w": _emu_to_px(shape.width),
        "h": _emu_to_px(shape.height),
    }


def _emu_to_px(value) -> int:
    return round((value or 0) / EMU_PER_PX)


def _collect_style_info(shape, colors: Counter, fonts: Counter, font_sizes: Counter) -> None:
    try:
        if shape.fill and shape.fill.fore_color and shape.fill.fore_color.rgb:
            colors[str(shape.fill.fore_color.rgb)] += 1
    except (AttributeError, TypeError):
        pass

    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for para in shape.text_frame.paragraphs[:4]:
            for run in para.runs[:4]:
                if run.font.name:
                    fonts[run.font.name] += 1
                if run.font.size:
                    font_sizes[round(run.font.size / 12700)] += 1
                try:
                    if run.font.color and run.font.color.rgb:
                        colors[str(run.font.color.rgb)] += 1
                except (AttributeError, TypeError):
                    pass


def _classify_element(shape) -> dict | None:
    """Classify a shape into our element catalog structure."""
    pos = _shape_position(shape)
    if pos["w"] < 10 and pos["h"] < 10:
        return None

    shape_type = str(getattr(shape, "shape_type", ""))
    has_text = getattr(shape, "has_text_frame", False) and shape.has_text_frame

    fill_info = None
    try:
        if shape.fill and shape.fill.fore_color and shape.fill.fore_color.rgb:
            fill_info = str(shape.fill.fore_color.rgb)
    except (AttributeError, TypeError):
        pass

    return {
        "type": shape_type,
        "position": pos,
        "has_text": has_text,
        "fill_color": fill_info,
    }


def _palette_to_theme(palette: list[str]) -> dict:
    colors = palette or ["#17324d", "#2f5f8f", "#2fb7c8", "#f5f7fa", "#111827"]
    return {
        "primary": colors[0],
        "secondary": colors[1] if len(colors) > 1 else colors[0],
        "accent": colors[2] if len(colors) > 2 else colors[0],
        "background": colors[3] if len(colors) > 3 else "#f5f7fa",
        "text": colors[4] if len(colors) > 4 else "#111827",
    }


def _infer_patterns(elements: list[dict]) -> list[str]:
    """Infer reusable design patterns from element usage."""
    patterns = set()
    for elem in elements:
        pos = elem.get("position", {})
        if pos.get("w", 0) > 800 and pos.get("h", 0) < 10:
            patterns.add("full_width_divider")
        if pos.get("w", 0) > 800 and pos.get("h", 0) > 400:
            patterns.add("full_bleed_background")
        if elem.get("fill_color") and pos.get("w", 0) < 300:
            patterns.add("accent_card")
    return list(patterns)[:10]


# ─── Build Master Context ──────────────────────────────────────────────────────

def build_master_context(
    template_bytes: bytes | None = None,
    template_filename: str = "template.pptx",
    seed: str = "",
) -> dict:
    """Build the complete master context for the pipeline.

    If template_bytes is provided, parses it and uses the template's theme.
    Otherwise uses Default Master + a design direction selected by seed.
    """
    from src.formats.pptx.css_spec import CONSTRAINED_CSS_SPEC

    if template_bytes:
        template_context = parse_template(template_bytes, template_filename)
        return {
            "source": "template",
            "master": DEFAULT_MASTER,
            "template": template_context,
            "object_catalog": OBJECT_CATALOG,
            "css_spec": CONSTRAINED_CSS_SPEC,
            "design_direction": None,
        }

    direction = select_design_direction(seed)
    return {
        "source": "default",
        "master": DEFAULT_MASTER,
        "template": None,
        "object_catalog": OBJECT_CATALOG,
        "css_spec": CONSTRAINED_CSS_SPEC,
        "design_direction": direction,
    }
