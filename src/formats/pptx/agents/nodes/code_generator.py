"""PPTX Code Agent — generates OOXML-DSL JSON for each slide (parallel execution)."""

from __future__ import annotations

import asyncio
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.agents.loader import get_llm_for_agent, load_agent_config, load_agent_prompt
from src.core.logging import get_logger
from src.formats.pptx.dsl.html_renderer import DSLtoHTMLRenderer
from src.formats.pptx.dsl.schema import SlideDSL
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "code_generator"
FORMAT_ID = "pptx"

_html_renderer = DSLtoHTMLRenderer()

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{3,8}$")
_RGBA_RE = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*[\d.]+)?\s*\)")
_COLOR_KEYS = {
    "color",
    "header_fill",
    "header_text_color",
    "row_fill",
    "alternate_row_fill",
    "border_color",
}
_ALLOWED_SLIDE_TYPES = {
    "cover",
    "toc",
    "content",
    "data",
    "comparison",
    "summary",
    "cta",
    "section",
    "problem",
    "solution",
}
_SLIDE_TYPE_ALIASES = {
    "closing": "cta",
    "close": "cta",
    "agenda": "toc",
    "divider": "section",
    "title": "cover",
}
_ALLOWED_SHAPE_ROLES = {
    "title",
    "subtitle",
    "body",
    "decorative",
    "chart",
    "image",
    "badge",
    "kpi",
    "label",
    "table",
    "diagram",
    "line",
    "arrow",
    "callout",
}
_ROLE_ALIASES = {
    "heading": "title",
    "headline": "title",
    "caption": "label",
    "note": "label",
    "card": "callout",
    "panel": "callout",
    "box": "callout",
    "rectangle": "decorative",
    "rect": "decorative",
    "connector": "arrow",
}
_ALLOWED_CHART_TYPES = {"bar", "column", "line", "pie", "donut"}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _repair_json(raw: str) -> str:
    """Attempt to repair common LLM JSON errors.

    Handles:
    - Trailing commas before } or ]
    - Unescaped newlines inside strings
    - Truncated JSON (attempts to close brackets)
    - Single quotes used instead of double quotes (partial)
    """
    s = raw.strip()

    # Remove trailing commas before closing brackets
    s = re.sub(r",\s*([}\]])", r"\1", s)

    # Try to fix unescaped newlines within string values
    # Strategy: replace actual newlines between quotes with \\n
    lines = s.split("\n")
    repaired_lines = []
    in_string = False
    for line in lines:
        quote_count = len(re.findall(r'(?<!\\)"', line))
        if in_string:
            # We're continuing a string from the previous line
            repaired_lines[-1] += "\\n" + line
            # Check if this line closes the string
            if quote_count % 2 == 1:
                in_string = False
        else:
            repaired_lines.append(line)
            # If odd number of unescaped quotes, we have an unclosed string
            if quote_count % 2 == 1:
                in_string = True

    s = "\n".join(repaired_lines)

    # If JSON appears truncated (no final }), try to close it
    if s.count("{") > s.count("}"):
        diff = s.count("{") - s.count("}")
        # Find the last valid position (last complete key-value or array item)
        # Remove any trailing incomplete content after the last comma or bracket
        last_complete = max(s.rfind(","), s.rfind("}"), s.rfind("]"))
        if last_complete > 0:
            s = s[: last_complete + 1]
            # Remove trailing comma if present
            s = re.sub(r",\s*$", "", s)
        s += "}" * diff

    if s.count("[") > s.count("]"):
        diff = s.count("[") - s.count("]")
        s = re.sub(r",\s*$", "", s)
        s += "]" * diff

    # Final trailing comma cleanup
    s = re.sub(r",\s*([}\]])", r"\1", s)

    return s


def _extract_json(raw: str) -> str:
    """Strip markdown fences if present and return raw JSON string."""
    m = _JSON_FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    raw = raw.strip()
    if raw.startswith("{"):
        return raw
    start = raw.find("{")
    if start != -1:
        return raw[start:]
    return raw


def _parse_and_validate(
    raw: str,
    slide_index: int,
    layout_spec: dict | None = None,
    design_system: dict | None = None,
    slide_content: dict | None = None,
) -> SlideDSL:
    """Parse raw LLM output → SlideDSL. Tries repair on failure."""
    data = parse_llm_json(_extract_json(raw))

    if not isinstance(data, dict):
        data = {"shapes": _as_list(data)}
    if "index" not in data:
        data["index"] = slide_index
    data = _sanitize_slide_data(
        data,
        slide_index=slide_index,
        layout_spec=layout_spec or {},
        design_system=design_system or {},
        slide_content=slide_content or {},
    )
    return SlideDSL.model_validate(data)


def _sanitize_slide_data(
    data: object,
    *,
    slide_index: int,
    layout_spec: dict,
    design_system: dict,
    slide_content: dict,
) -> dict:
    """Repair model-shaped JSON before Pydantic validation."""
    slide = _as_dict(_sanitize_colors(data, design_system))
    slide["index"] = int(slide.get("index") or slide_index)
    slide["slide_type"] = _normalize_slide_type(slide.get("slide_type"))
    slide["shapes"] = [
        _normalize_shape(item, design_system) for item in _as_list(slide.get("shapes"))
    ]
    slide["shapes"] = [shape for shape in slide["shapes"] if shape]
    if not slide["shapes"]:
        slide["shapes"] = [_background_shape(_background_hex(design_system, slide["slide_type"]))]
    _dedupe_shape_ids(slide["shapes"])
    _enforce_master_layout(slide, layout_spec, design_system, slide_content)
    return slide


def _sanitize_colors(value: object, design_system: dict, key: str | None = None) -> object:
    if isinstance(value, dict):
        return {
            item_key: _sanitize_colors(item_value, design_system, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_colors(item, design_system, key) for item in value]
    if isinstance(value, str) and key in _COLOR_KEYS:
        return _normalize_color(value, design_system)
    return value


def _normalize_color(
    value: object, design_system: dict | None = None, fallback: str = "111827"
) -> str:
    if not isinstance(value, str):
        return fallback
    raw = value.strip()
    token = _resolve_design_token(raw, design_system or {})
    if token:
        raw = token
    raw = raw.strip().lstrip("#")
    rgba = _RGBA_RE.match(raw)
    if rgba:
        return "".join(f"{max(0, min(255, int(part))):02x}" for part in rgba.groups())
    if _HEX_RE.match(raw):
        if len(raw) == 3:
            return "".join(ch * 2 for ch in raw).lower()
        if len(raw) in {6, 8}:
            return raw[:6].lower()
    return fallback


def _resolve_design_token(value: str, design_system: dict) -> str | None:
    match = re.match(r"var\((--[A-Za-z0-9_-]+)\)", value)
    if match:
        return _as_dict(design_system.get("css_variables")).get(match.group(1))
    token_name = value.strip().lstrip("#")
    return _as_dict(design_system.get("color_tokens")).get(token_name)


def _normalize_slide_type(value: object) -> str:
    slide_type = str(value or "content").lower().strip()
    slide_type = _SLIDE_TYPE_ALIASES.get(slide_type, slide_type)
    return slide_type if slide_type in _ALLOWED_SLIDE_TYPES else "content"


def _normalize_shape(value: object, design_system: dict) -> dict:
    shape = _as_dict(value)
    if not shape:
        return {}
    shape["id"] = _safe_shape_id(str(shape.get("id") or shape.get("role") or "shape"))
    shape["position"] = _normalize_position(_as_dict(shape.get("position")))
    raw_role = str(shape.get("role") or shape.get("type") or "body").lower().strip()
    role = _ROLE_ALIASES.get(raw_role, raw_role)
    shape["vertical_align"] = shape.get("vertical_align") or "top"
    if shape.get("table"):
        role = "table"
        shape["table"] = _normalize_table(shape.get("table"), design_system)
    if shape.get("chart"):
        role = "chart"
        shape["chart"] = _normalize_chart(shape.get("chart"), design_system)
    if role == "table" and not shape.get("table"):
        role = "body"
    if role == "chart" and not shape.get("chart"):
        role = "body"
    if role not in _ALLOWED_SHAPE_ROLES:
        role = "body" if shape.get("text") else "decorative"
    shape["role"] = role
    if shape.get("fill"):
        shape["fill"] = _normalize_fill(shape.get("fill"), design_system)
    if shape.get("border"):
        shape["border"] = _normalize_border(shape.get("border"), design_system)
    if shape.get("shadow"):
        shape["shadow"] = _normalize_shadow(shape.get("shadow"), design_system)
    _normalize_text_runs(shape, design_system)
    return shape


def _normalize_position(position: dict) -> dict:
    x = _clamp_int(position.get("x"), 0, 940, 60)
    y = _clamp_int(position.get("y"), 0, 520, 128)
    w = _clamp_int(position.get("w") or position.get("width"), 1, 960 - x, 240)
    h = _clamp_int(position.get("h") or position.get("height"), 1, 540 - y, 80)
    return {"x": x, "y": y, "w": w, "h": h}


def _normalize_fill(value: object, design_system: dict) -> dict:
    fill = _as_dict(value)
    fill_type = str(fill.get("type") or "solid").lower()
    if fill_type == "none":
        return {"type": "none"}
    if fill_type == "gradient":
        stops = []
        for index, stop in enumerate(_as_list(fill.get("stops"))[:6]):
            stop_dict = _as_dict(stop)
            stops.append(
                {
                    "position": _clamp_int(
                        stop_dict.get("position"), 0, 100, min(100, index * 100)
                    ),
                    "color": _normalize_color(stop_dict.get("color"), design_system, "f8fafc"),
                }
            )
        if len(stops) < 2:
            base = _normalize_color(fill.get("color"), design_system, "f8fafc")
            stops = [{"position": 0, "color": base}, {"position": 100, "color": base}]
        stops = sorted(stops, key=lambda item: item["position"])
        return {
            "type": "gradient",
            "angle": _clamp_int(fill.get("angle"), 0, 359, 90),
            "stops": stops,
        }
    return {
        "type": "solid",
        "color": _normalize_color(fill.get("color"), design_system, "f8fafc"),
    }


def _normalize_border(value: object, design_system: dict) -> dict:
    border = _as_dict(value)
    style = str(border.get("style") or "solid").lower()
    if style not in {"solid", "dashed", "dotted"}:
        style = "solid"
    return {
        "width": _clamp_int(border.get("width"), 1, 12, 1),
        "color": _normalize_color(border.get("color"), design_system, _border_hex(design_system)),
        "style": style,
    }


def _normalize_shadow(value: object, design_system: dict) -> dict:
    shadow = _as_dict(value)
    return {
        "offset_x": _clamp_int(shadow.get("offset_x"), -40, 40, 0),
        "offset_y": _clamp_int(shadow.get("offset_y"), -40, 40, 4),
        "blur": _clamp_int(shadow.get("blur"), 0, 80, 12),
        "color": _normalize_color(shadow.get("color"), design_system, "000000"),
        "opacity": _clamp_float(shadow.get("opacity"), 0.0, 1.0, 0.15),
    }


def _normalize_table(value: object, design_system: dict) -> dict:
    table = _as_dict(value)
    headers = [str(item)[:80] for item in _as_list(table.get("headers"))[:8]]
    rows = []
    for row in _as_list(table.get("rows"))[:12]:
        rows.append([str(cell)[:120] for cell in _as_list(row)[: max(1, len(headers) or 6)]])
    return {
        "headers": headers,
        "rows": rows,
        "header_fill": _normalize_color(table.get("header_fill"), design_system, "17324d"),
        "header_text_color": _normalize_color(
            table.get("header_text_color"), design_system, "ffffff"
        ),
        "row_fill": _normalize_color(table.get("row_fill"), design_system, "ffffff"),
        "alternate_row_fill": _normalize_color(
            table.get("alternate_row_fill"), design_system, "f5f7fa"
        ),
        "border_color": _normalize_color(table.get("border_color"), design_system, "d8dee8"),
        "font_family": str(table.get("font_family") or "Pretendard"),
        "font_size": _clamp_int(table.get("font_size"), 8, 36, 12),
    }


def _normalize_chart(value: object, design_system: dict) -> dict:
    chart = _as_dict(value)
    chart_type = str(chart.get("chart_type") or chart.get("type") or "bar").lower()
    if chart_type not in _ALLOWED_CHART_TYPES:
        chart_type = "bar"
    points = []
    for item in _as_list(chart.get("data"))[:8]:
        point = _as_dict(item)
        try:
            value_number = float(point.get("value", 0))
        except (TypeError, ValueError):
            value_number = 0.0
        points.append({"label": str(point.get("label") or "")[:48], "value": value_number})
    if not points:
        points = [{"label": "Value", "value": 0.0}]
    return {
        "chart_type": chart_type,
        "title": str(chart.get("title") or "")[:80],
        "series_name": str(chart.get("series_name") or "Series")[:48],
        "data": points,
        "value_axis_title": str(chart.get("value_axis_title") or "")[:48],
        "category_axis_title": str(chart.get("category_axis_title") or "")[:48],
        "color": _normalize_color(chart.get("color"), design_system, "2fb7c8"),
        "show_legend": bool(chart.get("show_legend", False)),
    }


def _normalize_text_runs(shape: dict, design_system: dict) -> None:
    paragraphs = []
    for para in _as_list(shape.get("text")):
        paragraph = _as_dict(para)
        runs = []
        for run_item in _as_list(paragraph.get("runs")):
            run = _as_dict(run_item)
            if not run:
                continue
            run["text"] = str(run.get("text", "")).replace("\n", " ")
            run["color"] = _normalize_color(run.get("color", "111827"), design_system)
            run["font_size"] = _clamp_int(run.get("font_size") or run.get("size"), 8, 120, 16)
            weight = run.get("font_weight") or run.get("weight")
            if weight is None and run.get("bold") is True:
                weight = 700
            run["font_weight"] = _nearest_weight(weight or 400)
            runs.append(run)
        if runs:
            paragraph["runs"] = runs
            paragraph["align"] = _normalize_align(paragraph.get("align"))
            paragraph["line_height"] = _clamp_float(paragraph.get("line_height"), 0.9, 2.0, 1.35)
            paragraph["spacing_before"] = _clamp_int(paragraph.get("spacing_before"), 0, 40, 0)
            paragraph["spacing_after"] = _clamp_int(paragraph.get("spacing_after"), 0, 40, 0)
            paragraphs.append(paragraph)
    if paragraphs:
        shape["text"] = paragraphs


def _normalize_align(value: object) -> str:
    align = str(value or "left").lower()
    return align if align in {"left", "center", "right", "justify"} else "left"


def _nearest_weight(value: object) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError):
        return 400
    weights = (100, 200, 300, 400, 500, 600, 700, 800, 900)
    return min(weights, key=lambda item: abs(item - weight))


def _clamp_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _clamp_float(value: object, minimum: float, maximum: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _safe_shape_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())[:48].strip("_")
    return cleaned or "shape"


def _dedupe_shape_ids(shapes: list[dict]) -> None:
    seen: set[str] = set()
    for index, shape in enumerate(shapes, start=1):
        base = _safe_shape_id(str(shape.get("id") or f"shape_{index}"))
        shape_id = base
        suffix = 2
        while shape_id in seen:
            shape_id = f"{base}_{suffix}"
            suffix += 1
        shape["id"] = shape_id
        seen.add(shape_id)


def _master_layout(layout_spec: dict) -> dict:
    master = _as_dict(layout_spec.get("slide_master"))
    if master:
        return master
    return {
        "regions": {
            "body": {"x": 60, "y": 128, "w": 840, "h": 356},
            "footer": {"x": 60, "y": 500, "w": 840, "h": 26},
        },
        "anchors": {
            "title": {"x": 60, "y": 38, "w": 820, "h": 66},
            "footer_page": {"x": 828, "y": 506, "w": 72, "h": 16},
        },
    }


def _rect(value: object, fallback: dict) -> dict:
    rect = _as_dict(value)
    return {
        "x": _clamp_int(rect.get("x"), 0, 940, fallback["x"]),
        "y": _clamp_int(rect.get("y"), 0, 520, fallback["y"]),
        "w": _clamp_int(rect.get("w") or rect.get("width"), 1, 960, fallback["w"]),
        "h": _clamp_int(rect.get("h") or rect.get("height"), 1, 540, fallback["h"]),
    }


def _enforce_master_layout(
    slide: dict,
    layout_spec: dict,
    design_system: dict,
    slide_content: dict,
) -> None:
    slide_type = slide.get("slide_type", "content")
    shapes = slide["shapes"]
    master = _master_layout(layout_spec)
    regions = _as_dict(master.get("regions"))
    anchors = _as_dict(master.get("anchors"))
    title_anchor = _rect(anchors.get("title"), {"x": 60, "y": 38, "w": 820, "h": 66})
    body_region = _rect(regions.get("body"), {"x": 60, "y": 128, "w": 840, "h": 356})
    footer_region = _rect(regions.get("footer"), {"x": 60, "y": 500, "w": 840, "h": 26})
    footer_page = _rect(anchors.get("footer_page"), {"x": 828, "y": 506, "w": 72, "h": 16})
    bg_hex = _detect_background_hex(shapes) or _background_hex(design_system, slide_type)
    dark_background = _is_dark(bg_hex)
    title_color = "ffffff" if dark_background else _text_hex(design_system)
    divider_color = "cbd5e1" if dark_background else _border_hex(design_system)

    if not _has_background(shapes):
        shapes.insert(0, _background_shape(bg_hex))

    if slide_type in {"cover", "section", "cta"} or slide.get("index") == 1:
        _ensure_text_contrast(shapes, dark_background, title_color)
        return

    title_shape = _find_title_shape(shapes)
    if not title_shape:
        title_shape = _title_shape(
            str(slide_content.get("title") or f"Slide {slide['index']}"),
            title_anchor,
        )
        shapes.append(title_shape)
    title_shape["role"] = "title"
    title_shape["position"] = title_anchor
    title_shape["vertical_align"] = "top"
    _set_text_color(title_shape, title_color)

    _ensure_decorative_line(
        shapes,
        "header_divider",
        {"x": body_region["x"], "y": body_region["y"] - 16, "w": body_region["w"], "h": 1},
        divider_color,
        z_index=2,
    )
    _ensure_decorative_line(
        shapes,
        "footer_divider",
        {"x": footer_region["x"], "y": footer_region["y"], "w": footer_region["w"], "h": 1},
        divider_color,
        z_index=90,
    )
    _ensure_footer_page(
        shapes,
        int(slide.get("index", 1)),
        "ffffff" if dark_background else "64748b",
        footer_page,
    )
    _keep_body_inside_master(shapes, body_region)
    _dedupe_shape_ids(shapes)


def _has_background(shapes: list[dict]) -> bool:
    return any(
        _as_dict(shape.get("position")).get("x") == 0
        and _as_dict(shape.get("position")).get("y") == 0
        and _as_dict(shape.get("position")).get("w", 0) >= 900
        for shape in shapes
    )


def _find_title_shape(shapes: list[dict]) -> dict | None:
    for shape in shapes:
        if shape.get("role") == "title" or shape.get("id") in {"title", "header_title"}:
            return shape
    return None


def _keep_body_inside_master(shapes: list[dict], body_region: dict) -> None:
    header_footer_roles = {"title", "subtitle", "decorative"}
    body_top = body_region["y"]
    body_bottom = body_region["y"] + body_region["h"]
    body_left = body_region["x"]
    body_right = body_region["x"] + body_region["w"]
    for shape in shapes:
        role = shape.get("role")
        if role in header_footer_roles or shape.get("id") in {
            "bg",
            "header_divider",
            "footer_divider",
            "footer_page",
        }:
            continue
        pos = shape["position"]
        if pos["y"] < body_top:
            pos["y"] = body_top
        if pos["y"] >= body_bottom:
            pos["y"] = max(body_top, body_bottom - 24)
        if pos["y"] + pos["h"] > body_bottom:
            pos["h"] = max(24, body_bottom - pos["y"])
        if pos["x"] < body_left:
            pos["x"] = body_left
        if pos["x"] >= body_right:
            pos["x"] = max(body_left, body_right - 24)
        if pos["x"] + pos["w"] > body_right:
            pos["w"] = max(24, body_right - pos["x"])


def _ensure_text_contrast(shapes: list[dict], dark_background: bool, title_color: str) -> None:
    if not dark_background:
        return
    for shape in shapes:
        if shape.get("role") in {"title", "subtitle", "body", "label", "callout", "kpi"}:
            _set_text_color(shape, title_color)


def _set_text_color(shape: dict, color: str) -> None:
    for para in _as_list(shape.get("text")):
        for run in _as_list(_as_dict(para).get("runs")):
            if isinstance(run, dict):
                run["color"] = color


def _ensure_decorative_line(
    shapes: list[dict], shape_id: str, position: dict, color: str, *, z_index: int
) -> None:
    if any(shape.get("id") == shape_id for shape in shapes):
        return
    shapes.append(
        {
            "id": shape_id,
            "role": "decorative",
            "position": position,
            "z_index": z_index,
            "fill": {"type": "solid", "color": color},
        }
    )


def _ensure_footer_page(shapes: list[dict], slide_index: int, color: str, position: dict) -> None:
    if any(shape.get("id") == "footer_page" for shape in shapes):
        return
    shapes.append(
        {
            "id": "footer_page",
            "role": "label",
            "position": position,
            "z_index": 91,
            "vertical_align": "top",
            "text": [
                {
                    "runs": [
                        {
                            "text": f"{slide_index:02d}",
                            "font_size": 10,
                            "font_weight": 500,
                            "font_family": "Pretendard",
                            "color": color,
                        }
                    ],
                    "align": "right",
                    "line_height": 1.0,
                }
            ],
        }
    )


def _title_shape(title: str, position: dict | None = None) -> dict:
    return {
        "id": "title",
        "role": "title",
        "position": position or {"x": 60, "y": 38, "w": 820, "h": 66},
        "z_index": 3,
        "vertical_align": "top",
        "text": [
            {
                "runs": [
                    {
                        "text": title[:120],
                        "font_size": 30,
                        "font_weight": 800,
                        "font_family": "Pretendard",
                        "color": "111827",
                    }
                ],
                "align": "left",
                "line_height": 1.18,
            }
        ],
    }


def _background_shape(color: str) -> dict:
    return {
        "id": "bg",
        "role": "decorative",
        "position": {"x": 0, "y": 0, "w": 960, "h": 540},
        "z_index": 0,
        "fill": {"type": "solid", "color": color},
    }


def _background_hex(design_system: dict, slide_type: str) -> str:
    backgrounds = _as_dict(design_system.get("slide_backgrounds"))
    value = backgrounds.get(slide_type) or backgrounds.get("content")
    color = _first_hex(value)
    if color:
        return color
    color_tokens = _as_dict(design_system.get("color_tokens"))
    return _normalize_color(color_tokens.get("background", "f8fafc"), design_system)


def _text_hex(design_system: dict) -> str:
    color_tokens = _as_dict(design_system.get("color_tokens"))
    return _normalize_color(color_tokens.get("text_primary", "111827"), design_system)


def _border_hex(design_system: dict) -> str:
    color_tokens = _as_dict(design_system.get("color_tokens"))
    return _normalize_color(color_tokens.get("border", "cbd5e1"), design_system, "cbd5e1")


def _detect_background_hex(shapes: list[dict]) -> str | None:
    for shape in shapes:
        pos = _as_dict(shape.get("position"))
        if pos.get("x") == 0 and pos.get("y") == 0 and pos.get("w", 0) >= 900:
            fill = _as_dict(shape.get("fill"))
            if fill.get("type") == "solid":
                return _normalize_color(fill.get("color", ""))
            if fill.get("type") == "gradient":
                stops = _as_list(fill.get("stops"))
                if stops:
                    return _normalize_color(_as_dict(stops[0]).get("color", ""))
    return None


def _first_hex(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"#?([0-9a-fA-F]{6,8})", value)
    return match.group(1)[:6].lower() if match else None


def _is_dark(hex_color: str) -> bool:
    color = _normalize_color(hex_color)
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return luminance < 0.42


async def _generate_single_slide(
    slide_index: int,
    slide_content: dict,
    layout_spec: dict,
    design_system: dict,
    asset_requirements: list[dict],
    research_data: dict | None,
    narrative_plan: dict,
    audience_profile: dict,
    output_language: str,
    system_prompt: str,
    fix_instructions: list[str] | None = None,
    previous_dsl: dict | None = None,
) -> dict:
    """Generate OOXML-DSL JSON for a single slide."""
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    slide_assets = [
        a
        for a in (_as_dict(item) for item in asset_requirements)
        if a.get("slide_index") == slide_index
    ]
    narrative_slide = next(
        (
            s
            for s in (_as_dict(item) for item in _as_list(narrative_plan.get("slides")))
            if s.get("index") == slide_index
        ),
        {},
    )

    context = f"""## Slide #{slide_index}

### 0. Planning Context (must guide this slide)

{output_language_instruction(output_language)}

Presentation title:
{narrative_plan.get("title", "")}

Narrative slide intent:
{json.dumps(narrative_slide, ensure_ascii=False, indent=2)}

Audience profile and tone:
{json.dumps(audience_profile, ensure_ascii=False, indent=2)}

Research evidence relevant to claims/data:
{json.dumps(research_data or {}, ensure_ascii=False, indent=2)[:2500]}

### 1. Content (from Content Writer — use this text EXACTLY)
{json.dumps(slide_content, ensure_ascii=False, indent=2)}

### 2. Layout Specification (from Layout Composer — follow this structure)
{json.dumps(layout_spec, ensure_ascii=False, indent=2)}

### 3. Design System (from Style Director — apply these colors, fonts, effects)

Color Tokens & Variables:
{json.dumps(design_system.get("css_variables", {}), ensure_ascii=False, indent=2)}

Typography Scale:
{json.dumps(design_system.get("typography_scale", []), ensure_ascii=False, indent=2)}

Effect Library:
{json.dumps(design_system.get("effect_library", {}), ensure_ascii=False, indent=2)}

Component Recipes:
{json.dumps(design_system.get("component_recipes", {}), ensure_ascii=False, indent=2)}

Concept System:
{json.dumps(design_system.get("concept_system", {}), ensure_ascii=False, indent=2)}

Element Style Specs:
{json.dumps(design_system.get("element_style_specs", {}), ensure_ascii=False, indent=2)}

Slide Background Rules:
{json.dumps(design_system.get("slide_backgrounds", {}), ensure_ascii=False, indent=2)}

### 4. Visual Assets (from Asset Planner)
{json.dumps(slide_assets, ensure_ascii=False, indent=2) if slide_assets else "None for this slide"}

### Instructions
- Apply the Design System's colors and typography faithfully
- Position elements according to the Layout Spec's grid_type and zones
- Use the Content Writer's text verbatim — do not modify or invent
- Preserve the Narrative Architect's purpose/key_message for this slide
- Calibrate density, terminology, and tone to the Audience profile
- Use Research data only to support existing content/data points; do not invent facts
- Follow the slide master first: fixed header, body, and footer regions
- Put title/subtitle only in the header
- Put tables/charts/KPIs/diagrams/callouts only in the body
- Put only source/page/caption in the footer
- Use native tables/charts, KPI blocks, diagrams, dividers, and lines
- Prevent clipping by resizing boxes, reducing font size, or splitting text shapes
- Output ONLY valid JSON (SlideDSL object), no other text"""

    if fix_instructions and previous_dsl:
        context += f"""

⚠️ PREVIOUS ATTEMPT FAILED VALIDATION. You MUST fix these issues:

Previous DSL JSON (reference only):
{json.dumps(previous_dsl, ensure_ascii=False, indent=2)[:2000]}

Required Fixes:
{chr(10).join(f"- {fix}" for fix in fix_instructions)}

IMPORTANT: Apply ALL fixes above. Output corrected SlideDSL JSON."""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    max_parse_retries = 3
    last_error = None

    for attempt in range(max_parse_retries + 1):
        response = await llm.ainvoke(messages)
        raw_content = response.content

        try:
            slide_dsl = _parse_and_validate(
                raw_content,
                slide_index,
                layout_spec=layout_spec,
                design_system=design_system,
                slide_content=slide_content,
            )
            dsl_dict = slide_dsl.model_dump()
            html = _html_renderer.render_slide(slide_dsl)
            return {
                "index": slide_index,
                "dsl": dsl_dict,
                "html": html,
                "css": "",
                "metadata": {
                    "layout": _as_dict(layout_spec).get("grid_type", "unknown"),
                    "slide_type": slide_dsl.slide_type,
                },
            }
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)
            logger.warning(
                "code_agent.parse_retry", slide=slide_index, attempt=attempt, error=last_error[:200]
            )

            retry_msg = (
                f"Your JSON output was INVALID. Error: {last_error[:300]}\n\n"
                "RULES TO FIX:\n"
                "1. Output ONLY valid JSON — no markdown, no explanation\n"
                '2. All strings must use double quotes (")\n'
                '3. No trailing commas (e.g., NOT {"a":1,} )\n'
                "4. No newlines INSIDE string values\n"
                "5. Keep it compact — max 15 shapes, short text per run\n"
                "6. Ensure ALL brackets are properly closed\n\n"
                "Output the COMPLETE valid SlideDSL JSON now:"
            )
            messages.append(HumanMessage(content=retry_msg))

    # Final fallback: generate a minimal valid slide
    logger.error("code_agent.parse_failed", slide=slide_index, error=last_error[:200])
    return _generate_fallback_slide(slide_index, slide_content)


def _generate_fallback_slide(slide_index: int, slide_content: dict) -> dict:
    """Generate a minimal valid slide as fallback when parsing repeatedly fails."""
    title = slide_content.get("title", f"Slide {slide_index}")
    subtitle = slide_content.get("subtitle", "")

    fallback_dsl = {
        "index": slide_index,
        "slide_type": "content",
        "shapes": [
            {
                "id": "bg",
                "role": "decorative",
                "position": {"x": 0, "y": 0, "w": 960, "h": 540},
                "z_index": 0,
                "fill": {"type": "solid", "color": "1a237e"},
            },
            {
                "id": "title",
                "role": "title",
                "position": {"x": 80, "y": 180, "w": 800, "h": 80},
                "z_index": 1,
                "text": [
                    {
                        "runs": [
                            {
                                "text": title[:60],
                                "font_size": 36,
                                "font_weight": 700,
                                "font_family": "Pretendard",
                                "color": "ffffff",
                            }
                        ],
                        "align": "left",
                        "line_height": 1.3,
                    }
                ],
            },
        ],
    }

    if subtitle:
        fallback_dsl["shapes"].append(
            {
                "id": "subtitle",
                "role": "subtitle",
                "position": {"x": 80, "y": 270, "w": 800, "h": 50},
                "z_index": 1,
                "text": [
                    {
                        "runs": [
                            {
                                "text": subtitle[:80],
                                "font_size": 18,
                                "font_weight": 400,
                                "font_family": "Pretendard",
                                "color": "b0bec5",
                            }
                        ],
                        "align": "left",
                        "line_height": 1.5,
                    }
                ],
            }
        )

    slide_dsl = SlideDSL.model_validate(fallback_dsl)
    html = _html_renderer.render_slide(slide_dsl)

    logger.warning("code_agent.using_fallback", slide=slide_index)
    return {
        "index": slide_index,
        "dsl": slide_dsl.model_dump(),
        "html": html,
        "css": "",
        "metadata": {"layout": "fallback", "slide_type": "content"},
    }


async def code_agent_parallel(state: DocuMindState) -> dict:
    """Generate OOXML-DSL JSON for all slides in parallel batches."""
    logger.info(
        "code_agent.start",
        retry_count=state.get("retry_count", 0),
        qa_iterations=state.get("qa_iterations", 0),
    )

    config = _as_dict(load_agent_config(AGENT_NAME, format_id=FORMAT_ID))
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)
    max_parallel = _as_dict(config.get("parallel")).get("max_concurrent", 4)

    slide_contents = [_as_dict(item) for item in _as_list(state.get("slide_contents"))]
    layout_specs = [_as_dict(item) for item in _as_list(state.get("layout_specs"))]
    design_system = _as_dict(state.get("design_system"))
    asset_requirements = [_as_dict(item) for item in _as_list(state.get("asset_requirements"))]
    research_data = state.get("research_data")
    narrative_plan = _as_dict(state.get("narrative_plan"))
    audience_profile = _as_dict(state.get("audience_profile"))
    output_language = state.get("output_language", "ko_mixed")

    validation_result = _as_dict(state.get("validation_result"))
    qa_feedback = _as_dict(state.get("qa_feedback"))

    fix_instructions = validation_result.get("fix_instructions", [])
    consistency_report = _as_dict(state.get("consistency_report"))
    consistency_fixes = [
        f"Consistency issue: {issue}" for issue in consistency_report.get("issues", [])
    ]
    consistency_fixes += [
        f"Consistency patch: {patch}" for patch in consistency_report.get("patches", [])
    ]
    qa_fix_instructions = qa_feedback.get("fix_instructions", [])
    qa_issues = qa_feedback.get("issues", [])

    all_fix_instructions = fix_instructions + consistency_fixes + qa_fix_instructions
    if qa_issues and not qa_fix_instructions:
        all_fix_instructions += [f"QA Issue: {issue}" for issue in qa_issues]

    previous_slides_dsl = [_as_dict(item) for item in _as_list(state.get("slides_dsl"))]
    previous_slides_html = [_as_dict(item) for item in _as_list(state.get("slides_html"))]
    is_retry = bool(all_fix_instructions) and (
        bool(previous_slides_dsl) or bool(previous_slides_html)
    )

    slides_dsl: list[dict] = []
    slides_html: list[dict] = []

    for batch_start in range(0, len(slide_contents), max_parallel):
        batch = slide_contents[batch_start : batch_start + max_parallel]
        tasks = []

        for slide_content in batch:
            idx = slide_content.get("index", batch_start + len(tasks) + 1)
            layout = next(
                (layout_item for layout_item in layout_specs if layout_item.get("index") == idx),
                layout_specs[min(idx - 1, len(layout_specs) - 1)] if layout_specs else {},
            )

            prev_dsl = None
            slide_fixes = all_fix_instructions
            if is_retry:
                prev_slide_dsl = next(
                    (s for s in previous_slides_dsl if s.get("index") == idx), None
                )
                prev_dsl = prev_slide_dsl if prev_slide_dsl else None
                slide_fixes = [
                    f
                    for f in all_fix_instructions
                    if f"Slide {idx}" in f or f"슬라이드 {idx}" in f or "slide" not in f.lower()
                ]
                if not slide_fixes:
                    slide_fixes = all_fix_instructions

            tasks.append(
                _generate_single_slide(
                    idx,
                    slide_content,
                    layout,
                    design_system,
                    asset_requirements,
                    research_data,
                    narrative_plan,
                    audience_profile,
                    output_language,
                    system_prompt,
                    fix_instructions=slide_fixes if is_retry else None,
                    previous_dsl=prev_dsl,
                )
            )

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in batch_results:
            if isinstance(result, Exception):
                logger.error("code_agent.slide_error", error=str(result))
                continue
            slides_dsl.append(result["dsl"])
            slides_html.append(
                {
                    "index": result["index"],
                    "html": result["html"],
                    "css": "",
                    "metadata": result["metadata"],
                }
            )

    logger.info("code_agent.complete", slides_generated=len(slides_dsl), is_retry=is_retry)
    return {
        "slides_dsl": slides_dsl,
        "slides_html": slides_html,
        "current_phase": "generating",
    }
