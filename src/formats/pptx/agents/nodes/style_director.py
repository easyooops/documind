"""PPTX Style Director - creates the complete visual design system."""

from __future__ import annotations

import json
import hashlib

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "style_director"
FORMAT_ID = "pptx"

DESIGN_DIRECTIONS = [
    {
        "name": "obsidian_lime",
        "primary": "#111827",
        "secondary": "#334155",
        "accent": "#A3E635",
        "bg": "#F8FAFC",
        "surface": "#FFFFFF",
        "tint": "#ECFCCB",
    },
    {
        "name": "ink_coral",
        "primary": "#1E1B4B",
        "secondary": "#4338CA",
        "accent": "#F97316",
        "bg": "#FAFAF9",
        "surface": "#FFFFFF",
        "tint": "#FFF7ED",
    },
    {
        "name": "forest_gold",
        "primary": "#12372A",
        "secondary": "#436850",
        "accent": "#D6A84F",
        "bg": "#F7F8F3",
        "surface": "#FFFFFF",
        "tint": "#F8EBC9",
    },
    {
        "name": "plum_cyan",
        "primary": "#3B0764",
        "secondary": "#7E22CE",
        "accent": "#06B6D4",
        "bg": "#F8FAFC",
        "surface": "#FFFFFF",
        "tint": "#ECFEFF",
    },
    {
        "name": "graphite_rose",
        "primary": "#27272A",
        "secondary": "#52525B",
        "accent": "#E11D48",
        "bg": "#F9FAFB",
        "surface": "#FFFFFF",
        "tint": "#FFF1F2",
    },
]


async def style_director(state: DocuMindState) -> dict:
    """Create the visual design system with PPTX-safe CSS tokens."""
    logger.info("style_director.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    audience = _as_dict(state.get("audience_profile"))
    template = _as_dict(state.get("template_profile"))
    layouts = _as_list(state.get("layout_specs"))
    narrative = _as_dict(state.get("narrative_plan"))
    design_direction = _select_design_direction(
        f"{state.get('user_query', '')}|{narrative.get('title', '')}"
    )

    context = (
        f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}\n\n"
        f"Audience profile:\n{json.dumps(audience, ensure_ascii=False, indent=2)}"
        f"\n\nNarrative plan:\n{json.dumps(narrative, ensure_ascii=False, indent=2)[:3000]}"
        f"\n\nLayout specs:\n{json.dumps(layouts, ensure_ascii=False, indent=2)[:3000]}"
    )
    if template:
        context += (
            "\n\nUploaded template profile (use as design basis):\n"
            f"{json.dumps(template, ensure_ascii=False, indent=2)[:6000]}"
        )
        context += (
            "\nTemplate instruction: preserve the uploaded template's palette, typography, "
            "master regions, layout rhythm, background treatment, and accent system. "
            "Adapt new content into similar structures instead of creating an unrelated deck."
        )
    else:
        context += (
            "\nNo template was provided. Create a complete presentation concept, "
            "background system, card/table/diagram style, and slide-type visual rules."
            "\nUse this selected creative direction as the palette/style seed:\n"
            f"{json.dumps(design_direction, ensure_ascii=False, indent=2)}"
        )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        design_system = _as_dict(parse_llm_json(response.content))
        if not design_system:
            raise json.JSONDecodeError("Design system root is not an object", response.content, 0)
    except json.JSONDecodeError as exc:
        logger.warning("style_director.parse_fallback", error=str(exc)[:200])
        design_system = _fallback_design_system(design_direction)

    logger.info("style_director.complete")
    return {"design_system": design_system, "current_phase": "designing"}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _select_design_direction(seed: str) -> dict:
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
    return DESIGN_DIRECTIONS[int(digest[:8], 16) % len(DESIGN_DIRECTIONS)]


def _fallback_design_system(direction: dict | None = None) -> dict:
    """Safe professional design system used when LLM JSON is malformed."""
    direction = direction or DESIGN_DIRECTIONS[0]
    primary = direction["primary"]
    secondary = direction["secondary"]
    accent = direction["accent"]
    bg = direction["bg"]
    surface = direction["surface"]
    tint = direction["tint"]
    return {
        "css_variables": {
            "--primary": primary,
            "--secondary": secondary,
            "--accent": accent,
            "--bg": bg,
            "--surface": surface,
            "--text-primary": "#111827",
            "--text-secondary": "#4b5563",
            "--text-on-primary": "#ffffff",
            "--border": "#d8dee8",
        },
        "color_tokens": {
            "primary": primary,
            "secondary": secondary,
            "accent": accent,
            "background": bg,
            "surface": surface,
            "text_primary": "#111827",
            "text_secondary": "#4b5563",
            "text_on_primary": "#ffffff",
            "border": "#d8dee8",
            "shadow_color": "rgba(17,24,39,0.14)",
        },
        "typography_scale": [
            {
                "role": "h1",
                "font_family": "Pretendard",
                "font_size": "40px",
                "font_weight": "800",
                "line_height": "1.18",
                "letter_spacing": "0",
                "color": "var(--text-primary)",
            },
            {
                "role": "h2",
                "font_family": "Pretendard",
                "font_size": "28px",
                "font_weight": "700",
                "line_height": "1.25",
                "letter_spacing": "0",
                "color": "var(--text-primary)",
            },
            {
                "role": "body",
                "font_family": "Pretendard",
                "font_size": "15px",
                "font_weight": "400",
                "line_height": "1.55",
                "letter_spacing": "0",
                "color": "var(--text-secondary)",
            },
            {
                "role": "metric",
                "font_family": "Pretendard",
                "font_size": "38px",
                "font_weight": "800",
                "line_height": "1.05",
                "letter_spacing": "0",
                "color": "var(--primary)",
            },
        ],
        "effect_library": {
            "shadow_card": "0 6px 20px rgba(17,24,39,0.10)",
            "shadow_subtle": "0 2px 8px rgba(17,24,39,0.06)",
            "gradient_hero": f"linear-gradient(135deg, {primary}, {secondary})",
            "gradient_accent": f"linear-gradient(90deg, {accent}, {secondary})",
            "gradient_surface": f"linear-gradient(180deg, #ffffff, {bg})",
            "border_card": "1px solid #d8dee8",
            "radius_card": "10px",
        },
        "component_recipes": {
            "card": (
                "background-color:#ffffff; border-radius:10px; "
                "border:1px solid #d8dee8; box-shadow:0 6px 20px rgba(17,24,39,0.10);"
            ),
            "callout": f"background-color:{tint}; border-left:4px solid {accent};",
            "table_header": f"background-color:{primary}; color:#ffffff; font-weight:700;",
            "table_row": "background-color:#ffffff; border:1px solid #d8dee8;",
            "arrow": f"height:3px; background-color:{accent};",
        },
        "concept_system": {
            "deck_motif": (
                f"Executive proposal system using {direction['name']} palette with shaped accents"
            ),
            "background_rule": (
                "Cover and section slides use deep gradient; content slides use light canvas"
            ),
            "slide_master_rule": (
                "Non-cover slides use fixed header, body, and footer regions with "
                "consistent dividers and page/source captions"
            ),
            "title_rule": "Titles align to x=60-72, y=36-48, max two lines, bold 700-800",
            "box_rule": "White or tinted cards with 10px radius, subtle border, and soft shadow",
            "table_rule": "Dark header, white rows, subtle borders, bold compact body text",
            "arrow_rule": "2-3px accent connectors with consistent arrowheads and label spacing",
            "chart_rule": "Flat bars/lines, subtle axes, direct labels, one highlighted insight",
        },
        "slide_master": {
            "header": {
                "x": 60,
                "y": 36,
                "w": 840,
                "h": 76,
                "title_x": 60,
                "title_y": 38,
                "title_w": 820,
                "title_h": 66,
            },
            "body": {"x": 60, "y": 128, "w": 840, "h": 356},
            "footer": {"x": 60, "y": 500, "w": 840, "h": 26, "source_x": 60, "page_x": 828},
            "divider_style": "1px solid #d8dee8",
            "page_number_style": "Pretendard 10px 500 #6b7280",
        },
        "element_style_specs": {
            "title": {"font_weight": "800", "placement": "x=60 y=40 w=820 h=76", "max_lines": 2},
            "body": {"font_weight": "400", "line_height": "1.55"},
            "table": {
                "header_fill": primary,
                "row_fill": surface,
                "alternate_row_fill": bg,
                "border": "1px solid #d8dee8",
                "header_weight": "700",
            },
            "kpi": {"number_size": "36-40px", "label_size": "11-13px", "surface": "#ffffff"},
            "callout": {"fill": tint, "border": accent},
            "arrow": {"stroke": "3px", "color": accent, "head_style": "chevron"},
            "chart": {"axis_style": "subtle", "series_style": "flat bars/lines"},
        },
        "slide_backgrounds": {
            "cover": f"linear-gradient(135deg, {primary}, {secondary})",
            "content": bg,
            "problem": bg,
            "solution": bg,
            "data": "#ffffff",
            "closing": f"linear-gradient(135deg, {primary}, {secondary})",
        },
    }
