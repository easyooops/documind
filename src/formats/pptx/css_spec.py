"""Constrained CSS Subset — defines exactly which CSS properties can map to OOXML.

This serves as:
1. A constraint specification for the HTML Generator LLM
2. A mapping reference for the Deterministic CSS→OOXML Mapper
3. Documentation of what's supported vs forbidden
"""

from __future__ import annotations

# ─── Allowed CSS Properties ────────────────────────────────────────────────────

ALLOWED_PROPERTIES = {
    # Positioning (absolute only, px units)
    "position": {"values": ["absolute"], "unit": None},
    "left": {"values": None, "unit": "px"},
    "top": {"values": None, "unit": "px"},
    "width": {"values": None, "unit": "px"},
    "height": {"values": None, "unit": "px"},

    # Background
    "background-color": {"values": None, "unit": "color"},
    "background": {"values": None, "unit": "gradient_or_color"},

    # Border
    "border": {"values": None, "unit": "shorthand"},
    "border-width": {"values": None, "unit": "px"},
    "border-color": {"values": None, "unit": "color"},
    "border-style": {"values": ["solid", "dashed", "dotted", "none"], "unit": None},
    "border-radius": {"values": None, "unit": "px"},

    # Typography
    "font-size": {"values": None, "unit": "px"},
    "font-weight": {"values": ["100", "200", "300", "400", "500", "600", "700", "800", "900", "normal", "bold"], "unit": None},
    "font-family": {"values": None, "unit": "string"},
    "color": {"values": None, "unit": "color"},
    "text-align": {"values": ["left", "center", "right", "justify"], "unit": None},
    "line-height": {"values": None, "unit": "number"},
    "letter-spacing": {"values": None, "unit": "px"},
    "vertical-align": {"values": ["top", "middle", "bottom"], "unit": None},

    # Effects
    "opacity": {"values": None, "unit": "number"},
    "box-shadow": {"values": None, "unit": "shadow_shorthand"},
    "transform": {"values": None, "unit": "rotate_only"},

    # Layout helpers (limited)
    "padding": {"values": None, "unit": "px"},
    "padding-top": {"values": None, "unit": "px"},
    "padding-right": {"values": None, "unit": "px"},
    "padding-bottom": {"values": None, "unit": "px"},
    "padding-left": {"values": None, "unit": "px"},
}

# ─── Forbidden CSS Properties ──────────────────────────────────────────────────

FORBIDDEN_PROPERTIES = [
    "display",        # No flexbox/grid — use absolute positioning
    "flex",
    "flex-direction",
    "flex-wrap",
    "justify-content",
    "align-items",
    "align-self",
    "grid",
    "grid-template-columns",
    "grid-template-rows",
    "gap",
    "overflow",
    "overflow-x",
    "overflow-y",
    "z-index",        # Stacking via DOM order + data-pptx-z attribute
    "clip-path",
    "backdrop-filter",
    "filter",
    "mix-blend-mode",
    "animation",
    "transition",
    "cursor",
    "pointer-events",
    "user-select",
    "margin",         # Use absolute positioning instead
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "float",
    "clear",
    "max-width",
    "max-height",
    "min-width",
    "min-height",
    "object-fit",
    "text-decoration",
    "text-transform",
    "white-space",
    "word-break",
    "text-overflow",
    "outline",
    "box-sizing",
]

# ─── Data Attributes ───────────────────────────────────────────────────────────

DATA_ATTRIBUTES = {
    "data-pptx-type": {
        "description": "PPTX element type",
        "values": ["shape", "textbox", "table", "chart", "image", "connector", "group"],
        "required": True,
    },
    "data-pptx-shape": {
        "description": "Preset shape geometry",
        "values": [
            "rect", "rounded_rect", "oval", "triangle", "diamond",
            "pentagon", "hexagon", "chevron", "right_arrow", "left_arrow",
            "up_arrow", "down_arrow", "cloud", "star_5", "star_4",
            "ribbon", "explosion_1", "flowchart_process", "flowchart_decision",
            "flowchart_terminator", "heart", "lightning_bolt", "sun", "moon",
            "block_arc", "donut", "frame", "bevel", "fold_corner",
            "parallelogram", "trapezoid", "cross", "plus",
        ],
        "required": False,
    },
    "data-pptx-fill": {
        "description": "Fill type override",
        "values": ["solid", "gradient", "pattern", "none"],
        "required": False,
    },
    "data-pptx-shadow": {
        "description": "Shadow preset",
        "values": ["outer_soft", "outer_hard", "inner", "none"],
        "required": False,
    },
    "data-pptx-z": {
        "description": "Z-order (stacking order as integer)",
        "values": "integer",
        "required": False,
    },
    "data-pptx-rotate": {
        "description": "Rotation in degrees",
        "values": "number",
        "required": False,
    },
    "data-pptx-chart-type": {
        "description": "Chart type for data-pptx-type=chart",
        "values": ["bar", "column", "line", "pie", "doughnut", "area"],
        "required": False,
    },
    "data-pptx-chart-data": {
        "description": "JSON-encoded chart data",
        "values": "json_string",
        "required": False,
    },
    "data-pptx-chart-options": {
        "description": "JSON-encoded OOXML chart formatting options",
        "values": "json_object",
        "required": False,
    },
    "data-pptx-table-data": {
        "description": "JSON-encoded table data (headers + rows)",
        "values": "json_string",
        "required": False,
    },
    "data-pptx-table-options": {
        "description": "JSON-encoded OOXML table formatting options",
        "values": "json_object",
        "required": False,
    },
    "data-pptx-connector-type": {
        "description": "Connector line type",
        "values": ["straight", "elbow", "curved"],
        "required": False,
    },
    "data-pptx-icon": {
        "description": "Semantic Iconify icon name. Prefer on data-pptx-type='icon' elements.",
        "values": "approved_icon_name",
        "required": False,
    },
    "data-pptx-icon-placement": {
        "description": "Standard icon placement rule ID from icon_layouts.json",
        "values": "icon_placement_id",
        "required": False,
    },
    "data-pptx-icon-layout": {
        "description": "Reserved icon slot inside the element",
        "values": ["top-left", "inline-left", "badge-top-right", "metric-left"],
        "required": False,
    },
    "data-pptx-icon-size": {
        "description": "Icon slot size in pixels, normally 24-44",
        "values": "integer_px",
        "required": False,
    },
    "data-pptx-shape-options": {
        "description": "JSON-encoded OOXML shape formatting options",
        "values": "json_object",
        "required": False,
    },
}

# ─── CSS → OOXML Mapping Rules ─────────────────────────────────────────────────

CSS_TO_OOXML_MAP = {
    "position_size": {
        "css": "left, top, width, height (px)",
        "ooxml": "a:off/@x, a:off/@y, a:ext/@cx, a:ext/@cy",
        "formula": "px * 9525 = EMU",
    },
    "background_solid": {
        "css": "background-color: #RRGGBB",
        "ooxml": "a:solidFill/a:srgbClr/@val",
        "formula": "strip # prefix",
    },
    "background_gradient": {
        "css": "background: linear-gradient(Xdeg, #color1 pos1%, #color2 pos2%)",
        "ooxml": "a:gradFill/a:gsLst/a:gs + a:lin/@ang",
        "formula": "angle * 60000, position * 1000",
    },
    "border_radius": {
        "css": "border-radius: Npx",
        "ooxml": "Rounded Rectangle preset + avLst adjustment",
        "formula": "radius / (min(w,h)/2) * 50000 (capped at 50000)",
    },
    "font_size": {
        "css": "font-size: Npx",
        "ooxml": "a:rPr/@sz",
        "formula": "px * 75 (hundredths of a point)",
    },
    "font_weight": {
        "css": "font-weight: 700",
        "ooxml": "a:rPr/@b = 1",
        "formula": ">= 700 → bold",
    },
    "font_color": {
        "css": "color: #RRGGBB",
        "ooxml": "a:solidFill/a:srgbClr/@val inside a:rPr",
        "formula": "strip # prefix",
    },
    "opacity": {
        "css": "opacity: 0.0-1.0",
        "ooxml": "a:alpha/@val inside fill color",
        "formula": "opacity * 100000",
    },
    "box_shadow": {
        "css": "box-shadow: Xpx Ypx Bpx rgba(R,G,B,A)",
        "ooxml": "a:effectLst/a:outerShdw",
        "formula": "blur*12700=blurRad, dist=hypot(x,y)*12700, dir=atan2(y,x)*60000",
    },
    "transform_rotate": {
        "css": "transform: rotate(Xdeg)",
        "ooxml": "a:xfrm/@rot",
        "formula": "degrees * 60000",
    },
    "border": {
        "css": "border: Wpx style #color",
        "ooxml": "a:ln/@w + a:solidFill",
        "formula": "width_px * 12700 = EMU line width",
    },
    "text_align": {
        "css": "text-align: left|center|right|justify",
        "ooxml": "a:pPr/@algn = l|ctr|r|just",
        "formula": "direct mapping",
    },
    "line_height": {
        "css": "line-height: 1.5",
        "ooxml": "a:lnSpc/a:spcPct/@val",
        "formula": "value * 100000",
    },
    "vertical_align": {
        "css": "vertical-align: top|middle|bottom",
        "ooxml": "a:bodyPr/@anchor = t|ctr|b",
        "formula": "direct mapping",
    },
}

# ─── Compiled Spec for Prompt Injection ────────────────────────────────────────

CONSTRAINED_CSS_SPEC = {
    "allowed_properties": list(ALLOWED_PROPERTIES.keys()),
    "forbidden_properties": FORBIDDEN_PROPERTIES,
    "data_attributes": DATA_ATTRIBUTES,
    "mapping_rules": CSS_TO_OOXML_MAP,
    "canvas": {"width": 960, "height": 540},
    "unit": "px only (no em, rem, %, vh, vw)",
    "positioning": "absolute only (no relative, fixed, sticky)",
    "colors": "hex (#RRGGBB or #RGB), rgba() for shadows only",
    "fonts": ["Pretendard", "Noto Sans KR", "Inter", "Aptos", "Segoe UI"],
}


def generate_css_spec_prompt() -> str:
    """Generate the CSS constraint specification for the HTML Generator prompt."""
    allowed = "\n".join(f"  - {prop}" for prop in sorted(ALLOWED_PROPERTIES.keys()))
    forbidden = "\n".join(f"  - {prop}" for prop in FORBIDDEN_PROPERTIES[:20])

    attrs = []
    for attr, info in DATA_ATTRIBUTES.items():
        req = " (REQUIRED)" if info.get("required") else ""
        vals = info["values"] if isinstance(info["values"], list) else info["values"]
        attrs.append(f"  - {attr}{req}: {vals}")
    attrs_str = "\n".join(attrs)

    return f"""## Constrained CSS Subset (PPTX-Mappable)

Canvas: exactly 960px × 540px (16:9)
Safe area: 48px padding on all sides
All positioning: position:absolute with px values ONLY

### ALLOWED CSS Properties:
{allowed}

### FORBIDDEN CSS (will break PPTX conversion):
{forbidden}
  ... (no flexbox, grid, overflow, z-index, clip-path, filters, animations)

### Required Data Attributes:
{attrs_str}

### Rules:
1. Every element MUST have data-pptx-type attribute
2. Use ONLY px units (no em, rem, %, vh, vw)
3. Colors in #RRGGBB format (rgba only for box-shadow)
4. No nested absolute positioning deeper than 2 levels
5. Table/chart data goes in data-pptx-table-data / data-pptx-chart-data as JSON
6. Stacking order via data-pptx-z attribute (integer), NOT z-index CSS
7. Icons should be independent data-pptx-type="icon" elements with data-pptx-icon and data-pptx-icon-placement; HTML geometry maps 1:1 to PPTX
8. OOXML object formatting goes in data-pptx-chart-options, data-pptx-table-options, or data-pptx-shape-options
9. Use premium fonts: Pretendard, Noto Sans KR, Inter, Aptos, Segoe UI"""
