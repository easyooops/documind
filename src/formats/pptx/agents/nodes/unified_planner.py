"""Phase A: Unified Planner — single LLM call produces complete slide blueprints.

Combines the old narrative, content_writer, audience, layout_composer, and
style_director into ONE agent call that outputs structured Slide Blueprints.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.formats.pptx.master_context import select_design_direction
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "unified_planner"
FORMAT_ID = "pptx"


async def unified_planner(state: DocuMindState) -> dict:
    """Generate complete slide blueprints in a single LLM call.

    Input: user_query, research_data, master_context
    Output: slide_blueprints, design_system, title
    """
    logger.info("unified_planner.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    if not system_prompt:
        system_prompt = _default_system_prompt()

    master_context = state.get("master_context", {})
    research_data = state.get("research_data")
    user_query = state.get("user_query", "")
    output_language = state.get("output_language", "ko_mixed")
    base_version = state.get("_base_version", {})
    locked_design_system = state.get("_locked_design_system", {})
    visual_intent = state.get("visual_intent", {})

    design_direction = master_context.get("design_direction") or select_design_direction(user_query)
    template_info = master_context.get("template")
    template_visual = (
        template_info.get("visual_analysis", {}).get("profile", {})
        if isinstance(template_info, dict)
        else {}
    )

    from src.formats.pptx.rulesets import get_ruleset
    ruleset = get_ruleset()
    ooxml_constraints = ruleset.get_generator_prompt_rules()

    context_parts = [
        output_language_instruction(output_language),
        f"\n## User Request\n{user_query}",
    ]

    if research_data:
        research_summary = json.dumps(research_data, ensure_ascii=False, indent=2)[:3000]
        context_parts.append(f"\n## Research Data\n{research_summary}")

    if visual_intent:
        context_parts.append(
            "\n## Attached Image Visual Intent\n"
            "Use this evidence to understand only the requested change. Do not use it as "
            "permission to replace unrelated slide content or layout.\n"
            f"{json.dumps(visual_intent, ensure_ascii=False, indent=2)[:4000]}"
        )

    if template_info:
        context_parts.append(
            f"\n## Template OOXML Profile (use as structural evidence)\n"
            f"{json.dumps(template_info, ensure_ascii=False, indent=2)[:4000]}"
        )
        if template_visual:
            context_parts.append(
                "\n## Template Rendered-Slide Visual Profile (PRIMARY design basis)\n"
                "The template was rendered and inspected visually. Match this cover/body concept, "
                "composition, palette relationships, typography hierarchy, chrome, spacing, and "
                "component treatment. Do not fall back to the default layout when it conflicts "
                "with this profile.\n"
                f"{json.dumps(template_visual, ensure_ascii=False, indent=2)[:6000]}"
            )
    else:
        context_parts.append(
            f"\n## Design Direction\n"
            f"{json.dumps(design_direction, ensure_ascii=False, indent=2)}"
        )

    if base_version:
        context_parts.append(
            "\n## Revision Contract (MANDATORY)\n"
            "This is a revision of an existing document. Preserve its cover/body visual style, "
            "header, footer, slide order, and all content that the request does not explicitly "
            "change. "
            "Return `changed_slide_indices` containing ONLY slides that must be regenerated. "
            "Return `removed_slide_indices` only when the user explicitly requests deletion. "
            "Also return `revision_scope` as one of `minimal_patch`, `slide_rewrite`, or "
            "`layout_redesign`. Use `minimal_patch` for small factual/wording corrections; "
            "`slide_rewrite` when the user asks to replace, substantially update, or rewrite a "
            "slide's contents; and `layout_redesign` when composition or visual elements must "
            "change. A changed slide may be fully rewritten when the selected scope requires it. "
            "Keep unchanged slide blueprints identical and preserve the global header/footer/style "
            "unless the product explicitly permits a separate global style edit. Do not add, "
            "delete, reorder, or redesign slides unless that action is explicitly requested. "
            "If the request is ambiguous, prefer no structural change and a minimal patch.\n"
            f"Selected parent version: v{base_version.get('version_number')}\n"
            f"Parent title and slide plan:\n"
            f"{json.dumps(base_version.get('slide_plan', []), ensure_ascii=False, indent=2)[:7000]}"
        )
        context_parts.append(
            "\n## Locked Design Tokens (MUST NOT CHANGE)\n"
            f"{json.dumps(locked_design_system, ensure_ascii=False, indent=2)[:4000]}"
        )

    context_parts.append(
        "\n## Available PPTX Elements (use diversely)\n"
        "shapes: rect, rounded_rect, oval, triangle, diamond, chevron, right_arrow, cloud, star_5\n"
        "data_viz: table, chart_bar, chart_line, chart_pie, smartart\n"
        "decorative: line, connector, gradient_fill, group\n"
        "text: textbox, placeholder\n"
    )

    context_parts.append(f"\n## OOXML Design Constraints\n{ooxml_constraints}")

    requested_slide_count = _extract_requested_slide_count(user_query)
    if requested_slide_count:
        context_parts.append(
            f"\n## CRITICAL: Slide Count Requirement\n"
            f"User explicitly requested **{requested_slide_count} slides**. "
            f"You MUST generate exactly {requested_slide_count} slides in your plan. "
            f"Include 1 cover slide + {requested_slide_count - 1} content slides."
        )

    context = "\n".join(context_parts)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        result = parse_llm_json(response.content)
        if not isinstance(result, dict):
            result = {"slides": result if isinstance(result, list) else []}
    except (json.JSONDecodeError, TypeError):
        logger.warning("unified_planner.parse_fallback")
        result = _fallback_blueprints(user_query, design_direction)

    slide_blueprints = _normalize_blueprints(result.get("slides", []))

    requested_count = _extract_requested_slide_count(user_query)
    if requested_count and len(slide_blueprints) < requested_count:
        logger.warning(
            "unified_planner.slide_count_mismatch",
            requested=requested_count,
            planned=len(slide_blueprints),
        )

    title = result.get("title", user_query[:60])
    generated_design = result.get("design_tokens", {})
    template_design = _build_design_system(design_direction, template_info)
    if locked_design_system:
        design_system = locked_design_system
    elif template_info:
        design_system = {**generated_design, **template_design}
    else:
        design_system = generated_design or template_design

    theme_id = result.get("theme_id", "")
    if theme_id and not template_info and not locked_design_system:
        theme_colors = _load_theme_colors(theme_id)
        if theme_colors:
            design_system.update(theme_colors)

    header_footer = result.get("header_footer", {})
    if header_footer and not locked_design_system:
        design_system["header_footer"] = header_footer

    changed_indices = result.get("changed_slide_indices")
    if base_version:
        if not isinstance(changed_indices, list):
            changed_indices = []
        changed_indices = [
            int(index) for index in changed_indices
            if isinstance(index, int) or (isinstance(index, str) and index.isdigit())
        ]
    else:
        changed_indices = [bp.get("index") for bp in slide_blueprints]

    if base_version:
        changed_set = set(changed_indices)
        generated_by_index = {bp.get("index"): bp for bp in slide_blueprints}
        parent_plan = [
            bp for bp in base_version.get("slide_plan", []) if isinstance(bp, dict)
        ]
        parent_indices = {bp.get("index") for bp in parent_plan}
        allow_additions = _requests_slide_addition(user_query)
        allow_deletions = _requests_slide_deletion(user_query)
        removed_indices = (
            set(_normalize_indices(result.get("removed_slide_indices")))
            if allow_deletions
            else set()
        )
        if not allow_additions:
            changed_set &= parent_indices
            changed_indices = [index for index in changed_indices if index in parent_indices]
        merged_plan = [
            generated_by_index.get(bp.get("index"), bp)
            if bp.get("index") in changed_set else bp
            for bp in parent_plan
            if bp.get("index") not in removed_indices
        ]
        if allow_additions:
            merged_plan.extend(
                bp for bp in slide_blueprints
                if bp.get("index") not in parent_indices and bp.get("index") in changed_set
            )
        slide_blueprints = _normalize_blueprints(merged_plan)

    revision_scope = (
        _normalize_revision_scope(result.get("revision_scope"), user_query)
        if base_version
        else "new_document"
    )

    logger.info("unified_planner.complete", slides=len(slide_blueprints), title=title[:50])
    return {
        "slide_blueprints": slide_blueprints,
        "design_system": design_system,
        "title": title,
        "changed_slide_indices": changed_indices,
        "revision_instruction": (
            user_query
            + (
                "\nVisual reference interpretation: "
                + json.dumps(visual_intent, ensure_ascii=False)
                if visual_intent else ""
            )
        ),
        "revision_scope": revision_scope,
        "current_phase": "planning",
    }


def _normalize_revision_scope(value: object, user_query: str) -> str:
    """Resolve whether an existing slide should be patched or regenerated."""
    allowed = {"minimal_patch", "slide_rewrite", "layout_redesign"}
    if isinstance(value, str) and value in allowed:
        return value
    text = user_query.lower()
    layout_signals = (
        "레이아웃", "디자인", "구성 변경", "배치", "스타일", "redesign", "layout",
    )
    rewrite_signals = (
        "전체 내용", "내용 전체", "전면", "새로 작성", "다시 작성", "교체",
        "rewrite", "replace", "entire slide", "all content",
    )
    if any(signal in text for signal in layout_signals):
        return "layout_redesign"
    if any(signal in text for signal in rewrite_signals):
        return "slide_rewrite"
    return "minimal_patch"


def _normalize_indices(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        int(index)
        for index in value
        if isinstance(index, int) or (isinstance(index, str) and index.isdigit())
    ]


def _requests_slide_addition(user_query: str) -> bool:
    text = user_query.lower()
    return any(
        signal in text
        for signal in (
            "슬라이드 추가", "페이지 추가", "장 추가", "한 장 더", "새 슬라이드",
            "add slide", "add a slide", "new slide", "append slide",
        )
    )


def _requests_slide_deletion(user_query: str) -> bool:
    text = user_query.lower()
    return any(
        signal in text
        for signal in (
            "슬라이드 삭제", "페이지 삭제", "장 삭제", "슬라이드 제거", "페이지 제거",
            "remove slide", "delete slide", "drop slide",
        )
    )


def _normalize_blueprints(slides: list) -> list[dict]:
    """Normalize LLM output into clean blueprint dicts."""
    blueprints = []
    for idx, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            continue
        blueprints.append({
            "index": slide.get("index", idx),
            "slide_type": slide.get("slide_type", "content"),
            "title": slide.get("title", f"Slide {idx}"),
            "section_label": slide.get("section_label", ""),
            "subtitle": slide.get("subtitle", ""),
            "key_message": slide.get("key_message", ""),
            "purpose": slide.get("purpose", ""),
            "content_elements": slide.get("content_elements", []),
            "content_blocks": slide.get("content_blocks", []),
            "data_points": slide.get("data_points", []),
            "layout_hint": slide.get("layout_hint", slide.get("layout_pattern", "balanced")),
            "suggested_elements": slide.get("suggested_elements", []),
            "visual_style": slide.get("visual_style", ""),
            "visual_density": slide.get("visual_density", "high"),
            "bottom_note": slide.get("bottom_note", ""),
            "source_citations": slide.get("source_citations", []),
        })
    return blueprints or _minimal_blueprints()


def _load_theme_colors(theme_id: str) -> dict | None:
    """Load color tokens from predefined theme palette JSON."""
    import json
    from pathlib import Path

    themes_path = Path(__file__).parent.parent.parent / "rulesets" / "presets" / "color_themes.json"
    if not themes_path.exists():
        return None

    try:
        data = json.loads(themes_path.read_text(encoding="utf-8"))
        theme = data.get("themes", {}).get(theme_id)
        if not theme:
            return None

        tokens = theme.get("tokens", {})
        result = {**tokens}
        result["card_fills"] = theme.get("card_fills", [])
        result["chart_colors"] = theme.get("chart_colors", [])
        result["cover_background"] = theme.get("cover_background", "")
        result["body_background"] = theme.get("body_background", "")
        result["accent_variants"] = theme.get("accent_variants", [])
        result["theme_id"] = theme_id
        return result
    except (json.JSONDecodeError, OSError):
        return None


def _minimal_blueprints() -> list[dict]:
    return [
        {
            "index": 1,
            "slide_type": "cover",
            "title": "Presentation",
            "key_message": "",
            "purpose": "cover",
            "content_elements": [],
            "data_points": [],
            "layout_hint": "center_dominant",
            "suggested_elements": ["gradient_bg", "large_title"],
            "visual_style": "",
            "source_citations": [],
        }
    ]


def _build_design_system(direction: dict | None, template_info: dict | None) -> dict:
    """Build a design token system from either template or direction."""
    if template_info and template_info.get("theme"):
        theme = template_info["theme"]
        return {
            "primary": theme.get("colors", {}).get("primary", "#1E293B"),
            "secondary": theme.get("colors", {}).get("secondary", "#475569"),
            "accent": theme.get("colors", {}).get("accent", "#2FB7C8"),
            "background": theme.get("colors", {}).get("background", "#F8FAFC"),
            "surface": "#FFFFFF",
            "text_primary": theme.get("colors", {}).get("text", "#111827"),
            "text_secondary": "#4B5563",
            "font_heading": theme.get("fonts", {}).get("major", "Pretendard"),
            "font_body": theme.get("fonts", {}).get("minor", "Pretendard"),
        }

    d = direction or {"primary": "#1E293B", "secondary": "#475569", "accent": "#10B981",
                      "background": "#F8FAFC", "surface": "#FFFFFF", "tint": "#ECFDF5"}
    return {
        "primary": d.get("primary", "#1E293B"),
        "secondary": d.get("secondary", "#475569"),
        "accent": d.get("accent", "#10B981"),
        "background": d.get("background", "#F8FAFC"),
        "surface": d.get("surface", "#FFFFFF"),
        "tint": d.get("tint", "#F0F9FF"),
        "text_primary": "#111827",
        "text_secondary": "#4B5563",
        "font_heading": "Pretendard",
        "font_body": "Pretendard",
    }


def _fallback_blueprints(query: str, direction: dict) -> dict:
    return {
        "title": query[:60],
        "slides": [
            {"index": 1, "slide_type": "cover", "title": query[:60],
             "key_message": query, "purpose": "introduce topic",
             "layout_hint": "center_dominant", "suggested_elements": ["gradient_bg", "large_title"]},
            {"index": 2, "slide_type": "content", "title": "Overview",
             "key_message": "Key points overview", "purpose": "outline main ideas",
             "layout_hint": "balanced", "suggested_elements": ["rounded_rect", "textbox"]},
            {"index": 3, "slide_type": "data", "title": "Analysis",
             "key_message": "Data-driven insights", "purpose": "present evidence",
             "layout_hint": "balanced", "suggested_elements": ["table", "chart_bar"]},
            {"index": 4, "slide_type": "summary", "title": "Conclusion",
             "key_message": "Key takeaways", "purpose": "summarize and recommend",
             "layout_hint": "balanced", "suggested_elements": ["rounded_rect", "connector"]},
        ],
        "design_tokens": _build_design_system(direction, None),
    }


def _default_system_prompt() -> str:
    return """You are a presentation planning expert. Given a user request, produce a complete slide deck plan.

Output ONLY valid JSON with this structure:
{
  "title": "Presentation Title",
  "slides": [
    {
      "index": 1,
      "slide_type": "cover|toc|content|problem|solution|data|comparison|summary|cta|section",
      "title": "Slide Title",
      "key_message": "The one thing the audience should remember",
      "purpose": "Why this slide exists in the narrative",
      "content_elements": [
        {"type": "paragraph|bullet_list|kpi|quote|callout", "content": "..."}
      ],
      "data_points": [
        {"label": "Metric", "value": "42%", "context": "Year over year growth"}
      ],
      "layout_hint": "center_dominant|balanced|left_heavy|right_heavy|grid_3|two_column",
      "suggested_elements": ["rounded_rect", "table", "chart_bar", "connector", ...],
      "visual_style": "Brief description of visual treatment",
      "source_citations": ["Source 1", "Source 2"]
    }
  ],
  "design_tokens": {
    "primary": "#hex", "secondary": "#hex", "accent": "#hex",
    "background": "#hex", "surface": "#hex", "text_primary": "#hex"
  }
}

Rules:
1. Create 4-12 slides depending on topic complexity
2. Every deck must have: cover, at least 2 content slides, and a summary/cta
3. Use diverse slide_types — never repeat the same type 3 times in a row
4. suggested_elements should use DIVERSE PPTX objects (tables, charts, shapes, connectors)
5. Content must be substantive — no placeholder text
6. data_points should contain real or realistic data
7. Output ONLY valid JSON, no markdown fences or explanations"""


def _extract_requested_slide_count(user_query: str) -> int | None:
    """Extract explicitly requested slide count from user query."""
    import re

    patterns = [
        r"(\d+)\s*장",
        r"(\d+)\s*slides?",
        r"(\d+)\s*페이지",
        r"(\d+)\s*개.*슬라이드",
        r"슬라이드.*?(\d+)\s*장",
        r"슬라이드.*?(\d+)\s*개",
    ]
    for pattern in patterns:
        match = re.search(pattern, user_query, re.IGNORECASE)
        if match:
            count = int(match.group(1))
            if 2 <= count <= 30:
                return count
    return None
