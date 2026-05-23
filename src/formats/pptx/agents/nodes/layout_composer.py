"""PPTX Layout Composer - determines spatial arrangement for each slide."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "layout_composer"
FORMAT_ID = "pptx"

SLIDE_MASTER = {
    "canvas": {"width": 960, "height": 540},
    "safe_margin": {"left": 60, "right": 60, "top": 36, "bottom": 14},
    "regions": {
        "header": {"x": 60, "y": 36, "w": 840, "h": 76},
        "body": {"x": 60, "y": 128, "w": 840, "h": 356},
        "footer": {"x": 60, "y": 500, "w": 840, "h": 26},
    },
    "anchors": {
        "title": {"x": 60, "y": 38, "w": 820, "h": 66, "max_lines": 2},
        "subtitle": {"x": 60, "y": 92, "w": 760, "h": 24, "max_lines": 1},
        "footer_source": {"x": 60, "y": 506, "w": 650, "h": 16},
        "footer_page": {"x": 828, "y": 506, "w": 72, "h": 16},
    },
    "grid": {"columns": 12, "rows": 9, "body_columns": 12, "gutter": 20},
    "rules": [
        "All non-cover slides use the same header/body/footer coordinates.",
        "Header contains only title, subtitle, section label, and optional divider.",
        "Body contains all argument, table, chart, KPI, diagram, and callout content.",
        "Footer contains only source, confidentiality note, page number, and footer divider.",
        "Body layout is secondary and never replaces master regions.",
    ],
}


async def layout_composer(state: DocuMindState) -> dict:
    """Design spatial layout specifications for each slide."""
    logger.info("layout_composer.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    narrative = _as_dict(state.get("narrative_plan"))
    audience = _as_dict(state.get("audience_profile"))
    template = _as_dict(state.get("template_profile"))

    context = (
        f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}\n\n"
        f"Narrative plan:\n{json.dumps(narrative, ensure_ascii=False, indent=2)}\n\n"
        f"Audience profile:\n{json.dumps(audience, ensure_ascii=False, indent=2)}"
    )
    if template:
        context += f"\n\nTemplate profile:\n{json.dumps(template, ensure_ascii=False, indent=2)}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    layout_specs = None
    last_error = None
    for attempt in range(3):
        response = await llm.ainvoke(messages)
        try:
            layout_specs = _normalize_layout_specs(parse_llm_json(response.content))
            break
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
            logger.debug("layout_composer.parse_retry", attempt=attempt, error=str(exc)[:200])
            messages.append(
                HumanMessage(
                    content=(
                        "Your previous response was invalid JSON. "
                        "Return the COMPLETE JSON array again. "
                        "No markdown fences, comments, trailing commas, or truncated objects. "
                        "Use compact JSON and keep each string short. "
                        "Root must be an array with index, grid_type, zones, "
                        "visual_weight, whitespace_ratio, and alignment."
                    )
                )
            )

    if layout_specs is None:
        logger.warning("layout_composer.parse_fallback", error=str(last_error)[:200])
        layout_specs = _fallback_layouts(narrative)

    layout_specs = _apply_slide_master(layout_specs)

    logger.info("layout_composer.complete", layouts_count=len(layout_specs))
    return {"layout_specs": layout_specs, "current_phase": "designing"}


def _fallback_layouts(narrative: dict) -> list[dict]:
    slides = _as_list(narrative.get("slides")) or [{"index": 1}]
    layouts = []
    for slide_item in slides:
        slide = _as_dict(slide_item)
        idx = slide.get("index", len(layouts) + 1)
        slide_type = slide.get("slide_type", "content")
        if idx == 1 or slide_type == "cover":
            layouts.append(
                {
                    "index": idx,
                    "grid_type": "hero-gradient",
                    "zones": [
                        {
                            "name": "background",
                            "x": 0,
                            "y": 0,
                            "width": 960,
                            "height": 540,
                            "purpose": "hero background",
                            "element_types": ["decorative"],
                            "priority": 0,
                        },
                        {
                            "name": "title-area",
                            "x": 72,
                            "y": 200,
                            "width": 760,
                            "height": 120,
                            "purpose": "cover title",
                            "element_types": ["heading"],
                            "priority": 1,
                        },
                        {
                            "name": "subtitle-area",
                            "x": 72,
                            "y": 330,
                            "width": 680,
                            "height": 56,
                            "purpose": "cover subtitle",
                            "element_types": ["body"],
                            "priority": 2,
                        },
                    ],
                    "visual_weight": "left-anchored",
                    "whitespace_ratio": 0.45,
                    "alignment": "left",
                }
            )
        else:
            layouts.append(
                {
                    "index": idx,
                    "grid_type": "top-title-grid",
                    "zones": [
                        {
                            "name": "background",
                            "x": 0,
                            "y": 0,
                            "width": 960,
                            "height": 540,
                            "purpose": "light background",
                            "element_types": ["decorative"],
                            "priority": 0,
                        },
                        {
                            "name": "title-area",
                            "x": 60,
                            "y": 36,
                            "width": 820,
                            "height": 76,
                            "purpose": "slide title",
                            "element_types": ["heading"],
                            "priority": 1,
                        },
                        {
                            "name": "main-visual",
                            "x": 60,
                            "y": 132,
                            "width": 540,
                            "height": 340,
                            "purpose": "table/chart/diagram",
                            "element_types": ["table", "chart", "diagram"],
                            "priority": 2,
                        },
                        {
                            "name": "insight-callout",
                            "x": 632,
                            "y": 132,
                            "width": 268,
                            "height": 340,
                            "purpose": "key implication",
                            "element_types": ["callout", "kpi"],
                            "priority": 3,
                        },
                    ],
                    "visual_weight": "balanced",
                    "whitespace_ratio": 0.28,
                    "alignment": "left",
                }
            )
    return layouts


def _normalize_layout_specs(value: object) -> list[dict]:
    if isinstance(value, dict):
        value = value.get("layout_specs") or value.get("layouts") or value.get("slides")
    if not isinstance(value, list):
        raise TypeError("layout response must be a JSON array")

    normalized = []
    for index, item in enumerate(value, start=1):
        layout = _as_dict(item)
        if not layout:
            continue
        zones = [_as_dict(zone) for zone in _as_list(layout.get("zones"))]
        zones = [zone for zone in zones if zone]
        layout["index"] = layout.get("index", index)
        layout["grid_type"] = layout.get("grid_type", "custom")
        layout["zones"] = zones
        layout["visual_weight"] = layout.get("visual_weight", "balanced")
        layout["whitespace_ratio"] = layout.get("whitespace_ratio", 0.28)
        layout["alignment"] = layout.get("alignment", "left")
        layout["body_layout"] = _as_dict(layout.get("body_layout")) or _infer_body_layout(layout)
        normalized.append(layout)

    if not normalized:
        raise ValueError("layout response did not contain usable layout objects")
    return normalized


def _apply_slide_master(layouts: list[dict]) -> list[dict]:
    return [_with_master_regions(layout) for layout in layouts]


def _with_master_regions(layout: dict) -> dict:
    layout = dict(layout)
    slide_type = str(layout.get("slide_type") or "").lower()
    is_cover_like = slide_type in {"cover", "section", "cta"} or layout.get("index") == 1

    layout["slide_master"] = SLIDE_MASTER
    layout["master_usage"] = (
        "cover_or_section_full_bleed" if is_cover_like else "fixed_header_body_footer"
    )
    layout["body_layout"] = _as_dict(layout.get("body_layout")) or _infer_body_layout(layout)

    if is_cover_like:
        return layout

    zones = [_as_dict(zone) for zone in _as_list(layout.get("zones"))]
    zones = [zone for zone in zones if zone]
    existing_names = {str(zone.get("name", "")) for zone in zones}
    required_zones = [
        {
            "name": "background",
            "x": 0,
            "y": 0,
            "width": 960,
            "height": 540,
            "purpose": "deck background from slide master",
            "element_types": ["decorative"],
            "priority": 0,
        },
        {
            "name": "header-title",
            "x": 60,
            "y": 38,
            "width": 820,
            "height": 66,
            "purpose": "fixed slide title area, max two lines",
            "element_types": ["heading"],
            "priority": 1,
        },
        {
            "name": "header-divider",
            "x": 60,
            "y": 112,
            "width": 840,
            "height": 2,
            "purpose": "fixed header/body separator",
            "element_types": ["decorative", "line"],
            "priority": 2,
        },
        {
            "name": "body-canvas",
            "x": 60,
            "y": 128,
            "width": 840,
            "height": 356,
            "purpose": "all primary slide content and second-level layout",
            "element_types": ["body", "table", "chart", "diagram", "card", "kpi", "callout"],
            "priority": 3,
        },
        {
            "name": "footer-divider",
            "x": 60,
            "y": 500,
            "width": 840,
            "height": 1,
            "purpose": "fixed footer separator",
            "element_types": ["decorative", "line"],
            "priority": 90,
        },
        {
            "name": "footer-source",
            "x": 60,
            "y": 506,
            "width": 650,
            "height": 16,
            "purpose": "source/caption/confidentiality only",
            "element_types": ["caption", "label"],
            "priority": 91,
        },
        {
            "name": "footer-page",
            "x": 828,
            "y": 506,
            "width": 72,
            "height": 16,
            "purpose": "page number only",
            "element_types": ["caption", "label"],
            "priority": 92,
        },
    ]
    zones = [zone for zone in zones if str(zone.get("name")) not in {"title-area", "subtitle-area"}]
    zones.extend(zone for zone in required_zones if zone["name"] not in existing_names)
    layout["zones"] = sorted(zones, key=lambda zone: int(zone.get("priority", 50)))
    return layout


def _infer_body_layout(layout: dict) -> dict:
    grid_type = str(layout.get("grid_type", "top-title-grid"))
    base = {
        "region": "body",
        "x": 60,
        "y": 128,
        "width": 840,
        "height": 356,
        "min_gap": 20,
    }
    if grid_type == "two-column":
        base["columns"] = [
            {"name": "body-left", "x": 60, "y": 128, "width": 410, "height": 356},
            {"name": "body-right", "x": 490, "y": 128, "width": 410, "height": 356},
        ]
    elif grid_type in {"card-grid-3", "top-title-grid"}:
        base["columns"] = [
            {"name": "card-1", "x": 60, "y": 136, "width": 260, "height": 320},
            {"name": "card-2", "x": 350, "y": 136, "width": 260, "height": 320},
            {"name": "card-3", "x": 640, "y": 136, "width": 260, "height": 320},
        ]
    elif grid_type == "table-detail":
        base["columns"] = [
            {"name": "table-main", "x": 60, "y": 132, "width": 600, "height": 330},
            {"name": "implication", "x": 684, "y": 132, "width": 216, "height": 330},
        ]
    elif grid_type == "data-chart":
        base["columns"] = [
            {"name": "chart-main", "x": 60, "y": 136, "width": 570, "height": 310},
            {"name": "insight", "x": 656, "y": 136, "width": 244, "height": 310},
        ]
    elif grid_type == "process-diagram":
        base["columns"] = [
            {"name": "process-lane", "x": 60, "y": 168, "width": 840, "height": 210},
            {"name": "supporting-evidence", "x": 60, "y": 402, "width": 840, "height": 72},
        ]
    else:
        base["columns"] = [{"name": "body-main", "x": 60, "y": 128, "width": 840, "height": 356}]
    return base


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
