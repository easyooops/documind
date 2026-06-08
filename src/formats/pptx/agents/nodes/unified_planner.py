"""Phase A: Unified Planner — single LLM call produces complete slide blueprints.

Combines the old narrative, content_writer, audience, layout_composer, and
style_director into ONE agent call that outputs structured Slide Blueprints.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.config import settings
from src.core.logging import get_logger
from src.formats.pptx.master_context import select_design_direction
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "unified_planner"
FORMAT_ID = "pptx"


class _PlannerStructuredOutput(BaseModel):
    """Schema-constrained top-level planner output.

    Keep nested fields flexible because downstream normalization already validates
    and repairs layout IDs, placements, and slide content contracts.
    """

    title: str = ""
    theme_id: str = ""
    presentation_strategy: dict[str, Any] = Field(default_factory=dict)
    layout_system: dict[str, Any] = Field(default_factory=dict)
    header_footer: dict[str, Any] = Field(default_factory=dict)
    slides: list[dict[str, Any]] = Field(default_factory=list)
    design_tokens: dict[str, Any] = Field(default_factory=dict)
    changed_slide_indices: list[int] = Field(default_factory=list)
    removed_slide_indices: list[int] = Field(default_factory=list)
    revision_scope: str = ""


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
    explicit_slide_instructions = _extract_slide_revision_instructions(user_query)

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
    planner_layout_rules = ruleset.get_planner_layout_rules()

    context_parts = [
        output_language_instruction(output_language),
        f"\n## User Request\n{user_query}",
    ]

    if research_data:
        research_summary = _response_excerpt(
            json.dumps(research_data, ensure_ascii=False, indent=2),
            6000,
        )
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
            f"{_compact_parent_slide_plan(base_version.get('slide_plan', []))}"
        )
        if explicit_slide_instructions:
            context_parts.append(
                "\n## Deterministic Slide Targets (OVERRIDE)\n"
                "The user explicitly named these slide numbers. Return changed_slide_indices "
                "matching exactly these slide numbers unless the user also explicitly requests "
                "a global change. Keep each slide's instruction isolated; never apply one "
                "slide's requested visual/content change to another slide.\n"
                f"{json.dumps(explicit_slide_instructions, ensure_ascii=False, indent=2)}"
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

    context_parts.append(f"\n{planner_layout_rules}")
    context_parts.append(f"\n## OOXML Design Constraints\n{ooxml_constraints}")

    explicit_slide_count = _extract_requested_slide_count(user_query)
    target_slide_count = _infer_target_slide_count(
        user_query,
        explicit_slide_count=explicit_slide_count,
        base_version=base_version,
        explicit_slide_instructions=explicit_slide_instructions,
    )
    if explicit_slide_count:
        context_parts.append(
            f"\n## CRITICAL: Slide Count Requirement\n"
            f"User explicitly requested **{explicit_slide_count} slides**. "
            f"You MUST generate exactly {explicit_slide_count} slides in your plan. "
            f"Include 1 cover slide + {explicit_slide_count - 1} content slides unless this is "
            "an existing-document revision."
        )
    elif target_slide_count:
        context_parts.append(
            f"\n## Deterministic Slide Scope\n"
            f"The user did not request a full new deck. Generate exactly {target_slide_count} "
            "slide(s) because the request implies a bounded visual/revision deliverable. "
            "Do not add a cover, agenda, general benefits, roadmap, or conclusion slide unless "
            "the user explicitly asked for a full deck."
        )

    context = "\n".join(context_parts)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    structured_result = await _invoke_planner_with_structured_output(
        llm=llm,
        messages=messages,
        requested_count=target_slide_count,
    )
    if structured_result is not None:
        result = _coerce_planner_result(
            structured_result,
            source="structured",
            requested_count=target_slide_count,
        )
    else:
        response = await llm.ainvoke(messages)
        response_text = _message_text(response.content)
        logger.info(
            "unified_planner.llm_response",
            chars=len(response_text),
            sha=_short_sha(response_text),
            requested_slides=explicit_slide_count,
            target_slides=target_slide_count,
            has_json_fence="```" in response_text,
            brace_delta=response_text.count("{") - response_text.count("}"),
            bracket_delta=response_text.count("[") - response_text.count("]"),
        )

        result = await _parse_or_repair_planner_result(
            llm=llm,
            messages=messages,
            response_text=response_text,
            requested_count=target_slide_count,
            user_query=user_query,
            design_direction=design_direction,
        )

    presentation_strategy = _normalize_presentation_strategy(
        result.get("presentation_strategy"), user_query
    )
    if locked_design_system and locked_design_system.get("master_layout"):
        layout_system = locked_design_system["master_layout"]
    else:
        layout_system = ruleset.resolve_master_layout(result.get("layout_system"))
    slide_blueprints = _normalize_blueprints(result.get("slides", []), ruleset, user_query)

    requested_count = target_slide_count
    if requested_count and len(slide_blueprints) < requested_count:
        logger.warning(
            "unified_planner.slide_count_underproduced",
            requested=requested_count,
            planned=len(slide_blueprints),
            slide_indices=[bp.get("index") for bp in slide_blueprints],
            slide_titles=[str(bp.get("title", ""))[:60] for bp in slide_blueprints],
        )
        repaired_count_result = await _repair_slide_count_result(
            llm=llm,
            base_messages=messages,
            parsed_result=result,
            requested_count=requested_count,
            planned_count=len(slide_blueprints),
            user_query=user_query,
        )
        if repaired_count_result is not None:
            result = repaired_count_result
            slide_blueprints = _normalize_blueprints(result.get("slides", []), ruleset, user_query)
        if len(slide_blueprints) < requested_count:
            missing_repair_result = await _repair_missing_slide_count_result(
                llm=llm,
                base_messages=messages,
                parsed_result=result,
                slide_blueprints=slide_blueprints,
                requested_count=requested_count,
                user_query=user_query,
                ruleset=ruleset,
                research_data=research_data,
            )
            if missing_repair_result is not None:
                result = missing_repair_result
                slide_blueprints = _normalize_blueprints(
                    result.get("slides", []),
                    ruleset,
                    user_query,
                )
        if len(slide_blueprints) < requested_count:
            logger.error(
                "unified_planner.slide_count_last_resort_extend",
                requested=requested_count,
                planned=len(slide_blueprints),
                slide_indices=[bp.get("index") for bp in slide_blueprints],
            )
            slide_blueprints = _extend_blueprints_to_requested_count(
                slide_blueprints,
                requested_count,
                user_query,
                ruleset,
                research_data,
            )
    elif requested_count and not base_version and len(slide_blueprints) > requested_count:
        logger.warning(
            "unified_planner.slide_count_overproduced",
            requested=requested_count,
            planned=len(slide_blueprints),
            slide_indices=[bp.get("index") for bp in slide_blueprints],
            slide_titles=[str(bp.get("title", ""))[:60] for bp in slide_blueprints],
        )
        repaired_count_result = await _repair_slide_count_result(
            llm=llm,
            base_messages=messages,
            parsed_result=result,
            requested_count=requested_count,
            planned_count=len(slide_blueprints),
            user_query=user_query,
        )
        if repaired_count_result is not None:
            result = repaired_count_result
            slide_blueprints = _normalize_blueprints(result.get("slides", []), ruleset, user_query)
        if len(slide_blueprints) > requested_count:
            logger.error(
                "unified_planner.slide_count_last_resort_truncate",
                requested=requested_count,
                planned=len(slide_blueprints),
                slide_indices=[bp.get("index") for bp in slide_blueprints],
            )
            slide_blueprints = slide_blueprints[:requested_count]

    title = result.get("title", user_query[:60])
    generated_design = result.get("design_tokens", {})
    template_design = _build_design_system(design_direction, template_info)
    if locked_design_system:
        design_system = dict(locked_design_system)
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
    design_system.setdefault("master_layout", layout_system)
    design_system.setdefault(
        "designer_principles",
        ruleset.design_strategy.get("philosophy", {}),
    )

    changed_indices = result.get("changed_slide_indices")
    if base_version:
        if explicit_slide_instructions:
            changed_indices = sorted(explicit_slide_instructions)
        elif not isinstance(changed_indices, list):
            changed_indices = []
        else:
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
        slide_blueprints = _normalize_blueprints(merged_plan, ruleset, user_query)

    revision_scope = (
        _normalize_revision_scope(result.get("revision_scope"), user_query)
        if base_version
        else "new_document"
    )

    logger.info("unified_planner.complete", slides=len(slide_blueprints), title=title[:50])
    return {
        "slide_blueprints": slide_blueprints,
        "design_system": design_system,
        "presentation_strategy": presentation_strategy,
        "layout_system": layout_system,
        "title": title,
        "changed_slide_indices": changed_indices,
        "slide_revision_instructions": explicit_slide_instructions,
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


async def _parse_or_repair_planner_result(
    *,
    llm,
    messages: list,
    response_text: str,
    requested_count: int | None,
    user_query: str,
    design_direction: dict,
) -> dict:
    try:
        return _coerce_planner_result(
            parse_llm_json(response_text),
            source="initial",
            requested_count=requested_count,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        _log_planner_parse_failure(
            "unified_planner.parse_failed",
            response_text,
            exc,
            requested_count=requested_count,
        )

    repair_prompt = (
        "The previous response for the PPTX unified planner was not valid JSON. "
        "Repair it into ONLY one valid JSON object matching the required planner schema. "
        "Do not summarize, do not use markdown fences, do not omit fields, and do not add commentary. "
    )
    if requested_count:
        repair_prompt += f"The JSON must contain exactly {requested_count} slides. "
    repair_prompt += (
        "\n\nPrevious invalid response excerpt:\n"
        + _response_excerpt(response_text, 12000)
    )

    try:
        repair_response = await llm.ainvoke([
            *messages[:1],
            HumanMessage(content=repair_prompt),
        ])
        repair_text = _message_text(repair_response.content)
        logger.info(
            "unified_planner.parse_repair_response",
            chars=len(repair_text),
            sha=_short_sha(repair_text),
            requested_slides=requested_count,
            brace_delta=repair_text.count("{") - repair_text.count("}"),
            bracket_delta=repair_text.count("[") - repair_text.count("]"),
        )
        return _coerce_planner_result(
            parse_llm_json(repair_text),
            source="parse_repair",
            requested_count=requested_count,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        _log_planner_parse_failure(
            "unified_planner.parse_repair_failed",
            _message_text(locals().get("repair_text", "")),
            exc,
            requested_count=requested_count,
        )
    except Exception as exc:
        logger.error(
            "unified_planner.parse_repair_call_failed",
            error=str(exc)[:300],
            requested_slides=requested_count,
        )

    logger.error(
        "unified_planner.parse_last_resort_fallback",
        requested_slides=requested_count,
        user_query_excerpt=user_query[:300],
    )
    return _fallback_blueprints(user_query, design_direction)


async def _invoke_planner_with_structured_output(
    *,
    llm,
    messages: list,
    requested_count: int | None,
) -> dict | None:
    structured_llm = _structured_planner_llm(llm)
    if structured_llm is None:
        logger.info(
            "unified_planner.structured_output_unavailable",
            requested_slides=requested_count,
        )
        return None

    try:
        output = await structured_llm.ainvoke(messages)
        result = _structured_output_to_dict(output)
        slides = result.get("slides")
        if not isinstance(slides, list) or not slides:
            raise TypeError("Structured planner output did not include any slides")
        text = json.dumps(result, ensure_ascii=False, sort_keys=True)
        logger.info(
            "unified_planner.structured_response",
            chars=len(text),
            sha=_short_sha(text),
            requested_slides=requested_count,
            slides=len(slides),
        )
        return result
    except Exception as exc:
        logger.warning(
            "unified_planner.structured_response_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
            requested_slides=requested_count,
        )
        return None


def _structured_planner_llm(llm):
    if settings.llm_provider == "bedrock":
        return None

    with_structured_output = getattr(llm, "with_structured_output", None)
    if not callable(with_structured_output):
        return None

    for kwargs in ({"method": "json_schema"}, {}):
        try:
            return with_structured_output(_PlannerStructuredOutput, **kwargs)
        except (NotImplementedError, TypeError, ValueError):
            continue
    return None


def _structured_output_to_dict(value: object) -> dict:
    if isinstance(value, _PlannerStructuredOutput):
        return value.model_dump()
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        if "raw" in value and "parsed" in value:
            return _structured_output_to_dict(value["parsed"])
        return value
    raise TypeError(f"Structured planner output parsed to {type(value).__name__}, expected object")


async def _repair_slide_count_result(
    *,
    llm,
    base_messages: list,
    parsed_result: dict,
    requested_count: int,
    planned_count: int,
    user_query: str,
) -> dict | None:
    repair_prompt = (
        "The previous PPTX planner JSON did not satisfy the requested slide count. "
        f"The deterministic planner scope requires exactly {requested_count} slides, but "
        f"the JSON contained {planned_count} slides. Return ONLY a corrected valid JSON object with exactly "
        f"{requested_count} slides. Preserve the same schema, theme, strategy, and intent. "
        "Do not use markdown fences or commentary.\n\n"
        "Original user request excerpt:\n"
        f"{_response_excerpt(user_query, 6000)}\n\n"
        "Previous parsed JSON:\n"
        f"{_response_excerpt(json.dumps(parsed_result, ensure_ascii=False, indent=2), 18000)}"
    )
    try:
        response = await llm.ainvoke([
            *base_messages[:1],
            HumanMessage(content=repair_prompt),
        ])
        text = _message_text(response.content)
        logger.info(
            "unified_planner.slide_count_repair_response",
            chars=len(text),
            sha=_short_sha(text),
            requested_slides=requested_count,
            previous_planned=planned_count,
        )
        result = _coerce_planner_result(
            parse_llm_json(text),
            source="slide_count_repair",
            requested_count=requested_count,
        )
        repaired_count = len(result.get("slides", []))
        if repaired_count == requested_count:
            logger.info(
                "unified_planner.slide_count_repair_success",
                requested=requested_count,
                planned=repaired_count,
            )
            return result
        logger.warning(
            "unified_planner.slide_count_repair_still_mismatch",
            requested=requested_count,
            planned=repaired_count,
            slide_indices=_raw_slide_indices(result),
        )
        return result
    except (json.JSONDecodeError, TypeError) as exc:
        _log_planner_parse_failure(
            "unified_planner.slide_count_repair_parse_failed",
            _message_text(locals().get("text", "")),
            exc,
            requested_count=requested_count,
        )
    except Exception as exc:
        logger.error(
            "unified_planner.slide_count_repair_call_failed",
            requested=requested_count,
            planned=planned_count,
            error=str(exc)[:300],
        )
    return None


async def _repair_missing_slide_count_result(
    *,
    llm,
    base_messages: list,
    parsed_result: dict,
    slide_blueprints: list[dict],
    requested_count: int,
    user_query: str,
    ruleset,
    research_data: object | None,
) -> dict | None:
    """Ask the model only for missing slides so large decks do not exceed output limits."""
    planned_count = len(slide_blueprints)
    if planned_count >= requested_count:
        return None

    missing_count = requested_count - planned_count
    existing_indices = [
        int(blueprint.get("index", position + 1))
        for position, blueprint in enumerate(slide_blueprints)
        if str(blueprint.get("index", position + 1)).isdigit()
    ]
    next_index = max(existing_indices or [planned_count]) + 1
    expected_indices = list(range(next_index, next_index + missing_count))
    source_context = _build_fallback_source_context(user_query, slide_blueprints, research_data)
    repair_prompt = (
        "The previous PPTX planner JSON still under-produced slides. "
        "Do NOT rewrite the whole deck. Return ONLY one valid JSON object with a `slides` array "
        f"containing exactly the {missing_count} missing slide blueprints. "
        f"The missing slide indices must be exactly {expected_indices}. "
        "Preserve the same narrative, theme, and schema style as the existing plan. "
        "Do not include already-generated slides, markdown fences, or commentary.\n\n"
        "Original user request excerpt:\n"
        f"{_response_excerpt(user_query, 7000)}\n\n"
        "Existing generated slide outline:\n"
        f"{_compact_parent_slide_plan(slide_blueprints, max_chars=9000)}\n\n"
        "Source segments available for the missing slides:\n"
        f"{_response_excerpt(json.dumps(source_context, ensure_ascii=False, indent=2), 7000)}"
    )
    try:
        response = await llm.ainvoke([
            *base_messages[:1],
            HumanMessage(content=repair_prompt),
        ])
        text = _message_text(response.content)
        logger.info(
            "unified_planner.slide_count_missing_repair_response",
            chars=len(text),
            sha=_short_sha(text),
            requested_slides=requested_count,
            planned=planned_count,
            missing=missing_count,
        )
        result = _coerce_planner_result(
            parse_llm_json(text),
            source="slide_count_missing_repair",
            requested_count=missing_count,
        )
        missing_slides = result.get("slides", [])
        if not isinstance(missing_slides, list) or not missing_slides:
            return None
        normalized_missing = _normalize_blueprints(missing_slides, ruleset, user_query)
        normalized_missing = normalized_missing[:missing_count]
        for offset, slide in enumerate(normalized_missing):
            slide["index"] = expected_indices[offset]

        merged = dict(parsed_result)
        merged["slides"] = [*slide_blueprints, *normalized_missing]
        if len(merged["slides"]) >= requested_count:
            logger.info(
                "unified_planner.slide_count_missing_repair_success",
                requested=requested_count,
                planned=len(merged["slides"]),
                added=len(normalized_missing),
            )
        else:
            logger.warning(
                "unified_planner.slide_count_missing_repair_partial",
                requested=requested_count,
                planned=len(merged["slides"]),
                added=len(normalized_missing),
            )
        return merged
    except (json.JSONDecodeError, TypeError) as exc:
        _log_planner_parse_failure(
            "unified_planner.slide_count_missing_repair_parse_failed",
            _message_text(locals().get("text", "")),
            exc,
            requested_count=requested_count,
        )
    except Exception as exc:
        logger.error(
            "unified_planner.slide_count_missing_repair_call_failed",
            requested=requested_count,
            planned=planned_count,
            error=str(exc)[:300],
        )
    return None


def _coerce_planner_result(value: object, *, source: str, requested_count: int | None) -> dict:
    result = {"slides": value} if isinstance(value, list) else value
    if not isinstance(result, dict):
        raise TypeError(f"Planner response parsed to {type(value).__name__}, expected object")
    slides = result.get("slides", [])
    if not isinstance(slides, list):
        raise TypeError("Planner response field `slides` is not a list")
    logger.info(
        "unified_planner.parse_success",
        source=source,
        slides=len(slides),
        requested_slides=requested_count,
        slide_indices=_raw_slide_indices(result),
        title=str(result.get("title", ""))[:80],
    )
    return result


def _log_planner_parse_failure(
    event_name: str,
    response_text: str,
    exc: Exception,
    *,
    requested_count: int | None,
) -> None:
    logger.warning(
        event_name,
        error_type=type(exc).__name__,
        error=str(exc)[:300],
        chars=len(response_text),
        sha=_short_sha(response_text),
        requested_slides=requested_count,
        has_json_fence="```" in response_text,
        first_non_ws=response_text.lstrip()[:1],
        last_non_ws=response_text.rstrip()[-1:] if response_text.strip() else "",
        brace_delta=response_text.count("{") - response_text.count("}"),
        bracket_delta=response_text.count("[") - response_text.count("]"),
        excerpt_head=response_text[:500],
        excerpt_tail=response_text[-500:] if len(response_text) > 500 else "",
    )


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content or "")


def _short_sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:12]


def _response_excerpt(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    half = max(1, limit // 2)
    return value[:half] + "\n...[truncated]...\n" + value[-half:]


def _compact_parent_slide_plan(slide_plan: object, max_chars: int = 18000) -> str:
    """Keep long parent decks visible to revision planning without front-only truncation."""
    slides = [slide for slide in _as_prompt_list(slide_plan) if isinstance(slide, dict)]
    compact = [_compact_slide_plan_item(slide, include_blocks=True) for slide in slides]
    text = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text

    very_compact = [
        _compact_slide_plan_item(slide, include_blocks=False)
        for slide in slides
    ]
    text = json.dumps(very_compact, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return _response_excerpt(text, max_chars)


def _compact_slide_plan_item(slide: dict, *, include_blocks: bool) -> dict:
    item = {
        "index": slide.get("index"),
        "slide_type": slide.get("slide_type"),
        "title": _compact_prompt_text(slide.get("title"), 120),
        "key_message": _compact_prompt_text(slide.get("key_message"), 220),
        "purpose": _compact_prompt_text(slide.get("purpose"), 180),
        "layout_hint": _compact_prompt_text(slide.get("layout_hint"), 100),
    }
    if include_blocks:
        blocks = _compact_content_blocks(slide.get("content_blocks"), max_blocks=3)
        if blocks:
            item["content_blocks"] = blocks
        data_points = _compact_data_points(slide.get("data_points"), max_items=4)
        if data_points:
            item["data_points"] = data_points
    return {key: value for key, value in item.items() if value not in (None, "", [])}


def _compact_content_blocks(value: object, *, max_blocks: int = 4) -> list[dict]:
    if not isinstance(value, list):
        return []
    blocks = []
    for block in value[:max_blocks]:
        if not isinstance(block, dict):
            continue
        compact = {"type": _compact_prompt_text(block.get("type"), 80)}
        items = []
        raw_items = block.get("items")
        if isinstance(raw_items, list):
            for item in raw_items[:4]:
                if isinstance(item, dict):
                    items.append({
                        "title": _compact_prompt_text(item.get("title"), 90),
                        "body": _compact_prompt_text(item.get("body"), 180),
                        "data": _compact_prompt_text(item.get("data"), 80),
                    })
                else:
                    items.append({"body": _compact_prompt_text(item, 180)})
        if items:
            compact["items"] = [
                {key: val for key, val in item.items() if val}
                for item in items
            ]
        blocks.append({key: val for key, val in compact.items() if val})
    return blocks


def _compact_data_points(value: object, *, max_items: int = 4) -> list[dict]:
    if not isinstance(value, list):
        return []
    points = []
    for item in value[:max_items]:
        if isinstance(item, dict):
            points.append({
                "label": _compact_prompt_text(item.get("label"), 80),
                "value": _compact_prompt_text(item.get("value"), 80),
                "context": _compact_prompt_text(item.get("context"), 140),
            })
        else:
            points.append({"label": _compact_prompt_text(item, 140)})
    return [{key: val for key, val in point.items() if val} for point in points]


def _compact_prompt_text(value: object, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _as_prompt_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _raw_slide_indices(result: dict) -> list:
    slides = result.get("slides", [])
    if not isinstance(slides, list):
        return []
    return [
        slide.get("index")
        for slide in slides
        if isinstance(slide, dict)
    ]


def _infer_target_slide_count(
    user_query: str,
    *,
    explicit_slide_count: int | None,
    base_version: dict,
    explicit_slide_instructions: dict[int, str],
) -> int | None:
    if explicit_slide_count:
        return explicit_slide_count
    parent_plan = [
        item for item in base_version.get("slide_plan", []) if isinstance(item, dict)
    ] if isinstance(base_version, dict) else []
    if parent_plan and not _requests_slide_addition(user_query) and not _requests_slide_deletion(user_query):
        return len(parent_plan)
    if explicit_slide_instructions:
        return len(explicit_slide_instructions)

    text = str(user_query or "").lower()
    variant_markers = len(re.findall(r"(?:한\s*장|1\s*장|one\s+slide)", text))
    if variant_markers >= 2:
        return min(variant_markers, 6)

    two_variant_signals = (
        ("diagrams" in text or "다이어그램" in text)
        and ("image model" in text or "이미지 모델" in text or "rendering image" in text)
    )
    if two_variant_signals:
        return 2

    bounded_visual_request = any(
        signal in text
        for signal in (
            "다시 그려", "그려줘", "수정", "변경", "교체", "redraw", "modify",
            "update", "replace",
        )
    ) and any(
        signal in text
        for signal in (
            "이미지", "다이어그램", "architecture", "diagram", "visual", "attached image",
        )
    )
    full_deck_signal = any(
        signal in text
        for signal in (
            "deck", "presentation", "발표", "제안서", "소개서", "브리프", "보고서",
            "전체", "full",
        )
    )
    if bounded_visual_request and not full_deck_signal:
        return 1
    return None


def _normalize_revision_scope(value: object, user_query: str) -> str:
    """Resolve whether an existing slide should be patched or regenerated."""
    allowed = {"minimal_patch", "slide_rewrite", "layout_redesign"}
    if isinstance(value, str) and value in allowed:
        return value
    text = user_query.lower()
    layout_signals = (
        "레이아웃", "디자인", "구성 변경", "배치", "스타일", "색상", "정렬",
        "영역", "구분", "redesign", "layout",
    )
    rewrite_signals = (
        "전체 내용", "내용 전체", "전면", "새로 작성", "다시 작성", "교체",
        "바꿔", "변경해줘", "내용으로 변경", "일반 내용", "말고", "대신",
        "rewrite", "replace", "entire slide", "all content", "change content",
        "not architecture", "instead of architecture",
    )
    if any(signal in text for signal in layout_signals):
        return "layout_redesign"
    if any(signal in text for signal in rewrite_signals):
        return "slide_rewrite"
    return "minimal_patch"


def _extract_slide_revision_instructions(user_query: str) -> dict[int, str]:
    """Parse explicit per-slide revision requests from Korean/English chat text."""
    text = str(user_query or "").strip()
    if not text:
        return {}
    marker_pattern = re.compile(
        r"(?:^|[\s,;:()\[\]{}。])\s*"
        r"(?:슬라이드|슬라|slide|slides|page|pages|p\.)\s*#?\s*"
        r"(?P<targets>\d{1,2}(?![A-Za-z가-힣])"
        r"(?:\s*(?:,|/|&|와|과|및|그리고|and|to|부터|에서|~|-|–|—)\s*"
        r"\d{1,2}(?![A-Za-z가-힣]))*)"
        r"\s*(?:장|페이지|쪽|번|p)?\s*(?:[:：.)\]-]|\s+)?",
        re.IGNORECASE,
    )
    matches = list(marker_pattern.finditer(text))
    if not matches:
        return {}

    instructions: dict[int, str] = {}
    for index, match in enumerate(matches):
        slide_indices = _expand_slide_targets(match.group("targets"))
        if not slide_indices:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        instruction = text[start:end].strip(" \n\r\t,;:：-–—)]}")
        if not instruction:
            instruction = match.group(0).strip()
        for slide_index in slide_indices:
            instructions[slide_index] = instruction[:1200]
    return instructions


def _expand_slide_targets(raw_targets: str) -> list[int]:
    """Expand slide target text like `4, 5` or `4-6` into slide numbers."""
    text = str(raw_targets or "")
    numbers = [int(item) for item in re.findall(r"\d{1,2}", text)]
    if not numbers:
        return []
    range_signal = bool(re.search(r"(?:~|-|–|—|\bto\b|부터|에서)", text, re.IGNORECASE))
    if range_signal and len(numbers) >= 2:
        start, end = numbers[0], numbers[1]
        if start <= end and end - start <= 30:
            return list(range(start, end + 1))
        if end < start and start - end <= 30:
            return list(range(start, end - 1, -1))
    seen = set()
    result = []
    for number in numbers:
        if number in seen:
            continue
        seen.add(number)
        result.append(number)
    return result


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


def _normalize_presentation_strategy(value: object, user_query: str) -> dict:
    """Keep the deck's consulting decision intent explicit downstream."""
    strategy = value if isinstance(value, dict) else {}
    return {
        "request_intent": strategy.get("request_intent", user_query[:160]),
        "presentation_objective": strategy.get(
            "presentation_objective", "Communicate a supported recommendation."
        ),
        "audience": strategy.get("audience", "professional stakeholders"),
        "decision_to_enable": strategy.get(
            "decision_to_enable", "Align on the proposed direction and next action."
        ),
        "narrative_arc": strategy.get(
            "narrative_arc", "context -> evidence -> recommendation -> action"
        ),
        "tone": strategy.get("tone", "clear and decision-oriented"),
    }


def _normalize_blueprints(slides: list, ruleset=None, user_query: str = "") -> list[dict]:
    """Normalize LLM output into clean blueprint dicts."""
    if ruleset is None:
        from src.formats.pptx.rulesets import get_ruleset

        ruleset = get_ruleset()
    blueprints = []
    last_layout_family = ""
    repeated_family_count = 0
    for idx, slide in enumerate(slides, 1):
        if not isinstance(slide, dict):
            continue
        slide_type = slide.get("slide_type", "content")
        raw_layout_plan = slide.get("layout_plan", {})
        layout_plan = raw_layout_plan if isinstance(raw_layout_plan, dict) else {}
        body_layout_id = layout_plan.get("body_layout_id") or slide.get("body_layout_id", "")
        has_valid_body_layout = bool(ruleset.get_body_layout(body_layout_id))
        if slide_type not in {"cover", "section"} and not has_valid_body_layout:
            body_layout_id = _default_body_layout_for_position(slide_type, idx, ruleset)
        if slide_type not in {"cover", "section"}:
            layout_family = _body_layout_family(body_layout_id)
            if layout_family == last_layout_family:
                repeated_family_count += 1
            else:
                repeated_family_count = 1
            if not has_valid_body_layout and repeated_family_count >= 3:
                body_layout_id = _alternate_body_layout_for_position(slide_type, idx, ruleset)
                layout_family = _body_layout_family(body_layout_id)
                repeated_family_count = 1
            last_layout_family = layout_family
        sub_layout_ids = [
            layout_id for layout_id in layout_plan.get("sub_layout_ids", [])
            if ruleset.get_body_layout(layout_id)
        ]
        element_placements = _normalize_element_placements(
            slide,
            slide_type,
            body_layout_id,
            layout_plan.get("element_placements", []),
            user_query,
        )
        suggested_elements = _normalize_suggested_elements(slide, user_query)
        blueprints.append({
            "index": slide.get("index", idx),
            "slide_type": slide_type,
            "title": slide.get("title", f"Slide {idx}"),
            "section_label": slide.get("section_label", ""),
            "subtitle": slide.get("subtitle", ""),
            "key_message": slide.get("key_message", ""),
            "purpose": slide.get("purpose", ""),
            "content_elements": slide.get("content_elements", []),
            "content_blocks": slide.get("content_blocks", []),
            "data_points": slide.get("data_points", []),
            "layout_hint": slide.get("layout_hint", slide.get("layout_pattern", "balanced")),
            "suggested_elements": suggested_elements,
            "visual_style": slide.get("visual_style", ""),
            "visual_density": slide.get("visual_density", "high"),
            "bottom_note": slide.get("bottom_note", ""),
            "source_citations": slide.get("source_citations", []),
            "layout_plan": {
                "master_role": "cover" if slide_type in {"cover", "section"} else "content",
                "body_layout_id": body_layout_id if slide_type not in {"cover", "section"} else "",
                "sub_layout_ids": sub_layout_ids,
                "element_placements": element_placements,
            },
        })
    return blueprints or _minimal_blueprints()


def _normalize_suggested_elements(slide: dict, user_query: str = "") -> list[str]:
    raw = slide.get("suggested_elements", [])
    values = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []
    if _slide_needs_visual_asset_slot(slide, user_query):
        return values
    replacements = {
        "diagram": ["connector", "rounded_rect"],
        "flowchart": ["connector", "rounded_rect"],
        "workflow": ["connector", "rounded_rect"],
        "process_flow": ["connector", "rounded_rect"],
        "architecture_diagram": ["table", "rounded_rect"],
        "image": ["rounded_rect"],
    }
    normalized: list[str] = []
    for value in values:
        key = value.lower()
        if key in replacements:
            normalized.extend(replacements[key])
        else:
            normalized.append(value)
    seen = set()
    deduped = []
    for value in normalized or ["rounded_rect", "textbox"]:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped[:12]


def _default_body_layout_for_position(slide_type: str, index: int, ruleset) -> str:
    if str(slide_type or "content") != "content":
        return ruleset.get_default_body_layout_id(slide_type)
    type_defaults = {
        "data": ["dashboard_chart_sidebar", "dashboard_kpi_top", "dashboard_quad"],
        "comparison": ["compare_vs", "compare_before_after", "compare_matrix"],
        "process": ["process_4col", "process_chevron", "timeline_horizontal"],
        "summary": ["numbered_list_card", "callout_sidebar", "kpi_detail"],
        "solution": ["split_60_40", "hub_spoke", "grid_2x2"],
        "problem": ["grid_3x1", "split_70_30", "fact_sheet"],
        "content": ["split_60_40", "grid_2x2", "dashboard_chart_sidebar", "process_4col", "compare_vs", "hub_spoke"],
    }
    candidates = type_defaults.get(str(slide_type or "content"), type_defaults["content"])
    for offset in range(len(candidates)):
        candidate = candidates[(index + offset - 1) % len(candidates)]
        if ruleset.get_body_layout(candidate):
            return candidate
    return ruleset.get_default_body_layout_id(slide_type)


def _alternate_body_layout_for_position(slide_type: str, index: int, ruleset) -> str:
    candidates = [
        "grid_2x2", "dashboard_chart_sidebar", "process_4col", "compare_vs",
        "split_30_70", "nested_cards", "timeline_horizontal", "hub_spoke",
    ]
    for offset in range(len(candidates)):
        candidate = candidates[(index + offset) % len(candidates)]
        if ruleset.get_body_layout(candidate):
            return candidate
    return _default_body_layout_for_position(slide_type, index, ruleset)


def _body_layout_family(layout_id: str) -> str:
    text = str(layout_id or "")
    if text.startswith("process_") or text.startswith("timeline_"):
        return "process"
    if text.startswith("grid_"):
        return "grid"
    if text.startswith("dashboard_"):
        return "dashboard"
    if text.startswith("compare_"):
        return "comparison"
    if text.startswith("split_"):
        return "asymmetric"
    if text in {"nested_cards", "tree_structure", "pyramid_3", "layers_stacked"}:
        return "hierarchy"
    if text in {"numbered_list_card", "bullet_columns", "callout_sidebar", "table_full", "fact_sheet"}:
        return "content_heavy"
    return "mixed"


def _default_sub_layout_ids(body_layout_id: str, index: int, ruleset) -> list[str]:
    by_family = {
        "asymmetric": ["grid_2x2", "numbered_list_card", "kpi_detail"],
        "dashboard": ["grid_3x1", "callout_sidebar", "bullet_columns"],
        "mixed": ["grid_2x2", "process_3col", "compare_vs"],
    }
    candidates = by_family.get(_body_layout_family(body_layout_id), [])
    if not candidates:
        return []
    candidate = candidates[(index - 1) % len(candidates)]
    return [candidate] if ruleset.get_body_layout(candidate) else []


def _normalize_element_placements(
    slide: dict,
    slide_type: str,
    body_layout_id: str,
    raw_placements: object,
    user_query: str = "",
) -> list[dict]:
    """Return a dense, coordinate-level body plan for downstream slide agents."""
    if slide_type in {"cover", "section"}:
        return []
    raw_items = raw_placements if isinstance(raw_placements, list) else []
    normalized = [
        placement for placement in (
            _normalize_element_placement_item(item) for item in raw_items
        )
        if placement
    ]
    if _placements_are_geometric(normalized):
        return _repair_element_placements(
            slide,
            slide_type,
            body_layout_id,
            normalized,
            user_query,
        )
    return _default_element_placements(slide, slide_type, body_layout_id, user_query)


def _normalize_element_placement_item(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    element = str(item.get("element") or item.get("type") or "shape").strip() or "shape"
    role = str(item.get("role") or "support").strip() or "support"
    zone = str(item.get("zone") or "main").strip() or "main"
    geometry = _placement_geometry(item)
    placement = {
        "id": str(item.get("id") or f"{zone}_{element}_{role}")[:64],
        "element": element,
        "role": role,
        "zone": zone,
    }
    if geometry:
        placement.update(geometry)
    if item.get("asset_id"):
        placement["asset_id"] = str(item.get("asset_id"))
    if item.get("asset_role"):
        placement["asset_role"] = str(item.get("asset_role"))
    if item.get("fit"):
        placement["fit"] = str(item.get("fit"))
    return placement


def _placements_are_geometric(placements: list[dict]) -> bool:
    if len(placements) < 3:
        return False
    geometric = [
        placement for placement in placements
        if all(key in placement for key in ("x", "y", "w", "h"))
    ]
    return len(geometric) >= max(3, len(placements) // 2)


def _repair_element_placements(
    slide: dict,
    slide_type: str,
    body_layout_id: str,
    placements: list[dict],
    user_query: str = "",
) -> list[dict]:
    if _placements_have_problematic_overlap(placements):
        return _default_element_placements(slide, slide_type, body_layout_id, user_query)
    return _apply_layout_inset(placements[:24])


def _placements_have_problematic_overlap(placements: list[dict]) -> bool:
    boxed = [
        placement for placement in placements
        if all(key in placement for key in ("x", "y", "w", "h"))
    ]
    if len(boxed) < 2:
        return False
    for index, first in enumerate(boxed):
        if _is_background_like_placement(first):
            continue
        for second in boxed[index + 1:]:
            if _is_background_like_placement(second):
                continue
            if _planned_overlap_ratio(first, second) > 0.08:
                return True
    return False


def _is_background_like_placement(placement: dict) -> bool:
    role = str(placement.get("role") or "").lower()
    element = str(placement.get("element") or "").lower()
    return role in {"background", "decoration"} or element in {"line", "connector"}


def _planned_overlap_ratio(first: dict, second: dict) -> float:
    ax, ay, aw, ah = first["x"], first["y"], first["w"], first["h"]
    bx, by, bw, bh = second["x"], second["y"], second["w"], second["h"]
    x_overlap = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    y_overlap = max(0, min(ay + ah, by + bh) - max(ay, by))
    if x_overlap <= 0 or y_overlap <= 0:
        return 0.0
    return (x_overlap * y_overlap) / max(1, min(aw * ah, bw * bh))


def _default_element_placements(
    slide: dict,
    slide_type: str,
    body_layout_id: str,
    user_query: str = "",
) -> list[dict]:
    if _slide_needs_visual_asset_slot(slide, user_query):
        return _visual_asset_element_placements(body_layout_id)
    if body_layout_id.startswith("dashboard_chart_sidebar"):
        return _apply_layout_inset([
            _placement("main_chart", "chart", "proof_object", "main", 40, 92, 560, 292),
            _placement("metric_1", "card", "support", "rail", 620, 92, 300, 78),
            _placement("metric_2", "card", "support", "rail", 620, 184, 300, 78),
            _placement("metric_3", "card", "support", "rail", 620, 276, 300, 78),
            _placement("bottom_implication", "callout", "synthesis", "callout", 40, 410, 880, 86),
        ])
    if body_layout_id.startswith("process_"):
        return _apply_layout_inset([
            _placement("step_1", "card", "process_step", "main", 40, 112, 184, 296),
            _placement("step_2", "card", "process_step", "main", 268, 112, 184, 296),
            _placement("step_3", "card", "process_step", "main", 496, 112, 184, 296),
            _placement("step_4", "card", "process_step", "main", 724, 112, 184, 296),
            _placement("process_summary", "callout", "synthesis", "callout", 40, 428, 880, 74),
        ])
    if body_layout_id.startswith("grid_"):
        return _apply_layout_inset([
            _placement("card_1", "card", "support", "main", 40, 92, 420, 180),
            _placement("card_2", "card", "support", "main", 500, 92, 420, 180),
            _placement("card_3", "card", "support", "main", 40, 300, 420, 180),
            _placement("card_4", "card", "support", "main", 500, 300, 420, 180),
        ])
    if body_layout_id.startswith("compare_"):
        return _apply_layout_inset([
            _placement("left_panel", "card", "comparison", "main", 40, 98, 410, 346),
            _placement("right_panel", "card", "comparison", "main", 510, 98, 410, 346),
            _placement("bottom_decision", "callout", "synthesis", "callout", 40, 462, 880, 42),
        ])
    return _apply_layout_inset([
        _placement("main_proof", "card", "proof_object", "main", 40, 92, 540, 318),
        _placement("support_1", "card", "support", "rail", 612, 92, 308, 92),
        _placement("support_2", "card", "support", "rail", 612, 208, 308, 92),
        _placement("support_3", "card", "support", "rail", 612, 324, 308, 86),
        _placement("bottom_takeaway", "callout", "synthesis", "callout", 40, 430, 880, 72),
    ])


def _visual_asset_element_placements(body_layout_id: str) -> list[dict]:
    family = _body_layout_family(body_layout_id)
    if family == "comparison":
        return _apply_layout_inset([
            _placement("visual_asset_left", "image", "proof_object", "main", 40, 104, 412, 292, asset_role="visual_asset", fit="contain"),
            _placement("comparison_right", "card", "comparison", "main", 500, 104, 420, 136),
            _placement("risk_right", "card", "support", "rail", 500, 260, 420, 92),
            _placement("decision_callout", "callout", "synthesis", "callout", 40, 424, 880, 72),
        ])
    if family == "process":
        return _apply_layout_inset([
            _placement("visual_asset_flow", "image", "proof_object", "main", 72, 98, 816, 230, asset_role="visual_asset", fit="contain"),
            _placement("step_1", "card", "process_step", "main", 40, 358, 198, 80),
            _placement("step_2", "card", "process_step", "main", 262, 358, 198, 80),
            _placement("step_3", "card", "process_step", "main", 500, 358, 198, 80),
            _placement("step_4", "card", "process_step", "main", 722, 358, 198, 80),
            _placement("process_takeaway", "callout", "synthesis", "callout", 40, 456, 880, 46),
        ])
    if family == "grid":
        return _apply_layout_inset([
            _placement("visual_asset_main", "image", "proof_object", "main", 48, 96, 540, 250, asset_role="visual_asset", fit="contain"),
            _placement("evidence_card_1", "card", "support", "rail", 620, 96, 300, 108),
            _placement("evidence_card_2", "card", "support", "rail", 620, 232, 300, 108),
            _placement("detail_card_1", "card", "annotation", "main", 48, 372, 260, 90),
            _placement("detail_card_2", "card", "annotation", "main", 336, 372, 260, 90),
            _placement("detail_card_3", "card", "annotation", "main", 624, 372, 296, 90),
        ])
    if family == "dashboard":
        return _apply_layout_inset([
            _placement("kpi_1", "card", "support", "main", 40, 92, 196, 72),
            _placement("kpi_2", "card", "support", "main", 252, 92, 196, 72),
            _placement("kpi_3", "card", "support", "main", 464, 92, 196, 72),
            _placement("visual_asset_dashboard", "image", "proof_object", "main", 40, 190, 620, 240, asset_role="visual_asset", fit="contain"),
            _placement("insight_rail", "card", "synthesis", "rail", 692, 92, 228, 338),
            _placement("bottom_implication", "callout", "synthesis", "callout", 40, 454, 880, 48),
        ])
    if family == "hierarchy":
        return _apply_layout_inset([
            _placement("top_claim", "callout", "synthesis", "main", 84, 92, 792, 58),
            _placement("visual_asset_center", "image", "proof_object", "main", 150, 176, 660, 250, asset_role="visual_asset", fit="contain"),
            _placement("left_annotation", "card", "annotation", "rail", 40, 210, 92, 170),
            _placement("right_annotation", "card", "annotation", "rail", 828, 210, 92, 170),
            _placement("bottom_takeaway", "callout", "synthesis", "callout", 84, 452, 792, 50),
        ])
    return _apply_layout_inset([
        _placement("visual_asset_main", "image", "proof_object", "main", 48, 92, 604, 318, asset_role="visual_asset", fit="contain"),
        _placement("insight_rail_1", "card", "support", "rail", 676, 92, 244, 92),
        _placement("insight_rail_2", "card", "support", "rail", 676, 208, 244, 92),
        _placement("insight_rail_3", "card", "support", "rail", 676, 324, 244, 86),
        _placement("bottom_takeaway", "callout", "synthesis", "callout", 48, 430, 872, 72),
    ])


def _apply_layout_inset(placements: list[dict]) -> list[dict]:
    """Give each planned slot a small internal margin without changing layout logic."""
    inset = 4
    result = []
    for placement in placements:
        copied = dict(placement)
        if not all(key in copied for key in ("x", "y", "w", "h")):
            result.append(copied)
            continue
        if _is_background_like_placement(copied):
            result.append(copied)
            continue
        element = str(copied.get("element") or "").lower()
        local_inset = 3 if element in {"connector", "line", "arrow", "right_arrow", "left_arrow"} else inset
        if copied["w"] <= local_inset * 2 + 32 or copied["h"] <= local_inset * 2 + 24:
            result.append(copied)
            continue
        copied["x"] = int(copied["x"] + local_inset)
        copied["y"] = int(copied["y"] + local_inset)
        copied["w"] = int(copied["w"] - local_inset * 2)
        copied["h"] = int(copied["h"] - local_inset * 2)
        result.append(copied)
    return result


def _placement(
    placement_id: str,
    element: str,
    role: str,
    zone: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    asset_role: str = "",
    fit: str = "",
) -> dict:
    placement = {
        "id": placement_id,
        "element": element,
        "role": role,
        "zone": zone,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
    }
    if asset_role:
        placement["asset_role"] = asset_role
    if fit:
        placement["fit"] = fit
    return placement


def _placement_geometry(item: dict) -> dict | None:
    x = _optional_px(item.get("x", item.get("left")))
    y = _optional_px(item.get("y", item.get("top")))
    w = _optional_px(item.get("w", item.get("width")))
    h = _optional_px(item.get("h", item.get("height")))
    if None in {x, y, w, h}:
        return None
    x = max(40, min(int(round(x)), 900))
    y = max(82, min(int(round(y)), 500))
    w = max(80, min(int(round(w)), 920 - x))
    h = max(36, min(int(round(h)), 510 - y))
    return {"x": x, "y": y, "w": w, "h": h}


def _optional_px(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("px", "").strip())
    except (TypeError, ValueError):
        return None


def _slide_needs_visual_asset_slot(slide: dict, user_query: str = "") -> bool:
    text = " ".join([
        str(user_query or ""),
        str(slide.get("title") or ""),
        str(slide.get("key_message") or ""),
        str(slide.get("purpose") or ""),
        json.dumps(slide.get("suggested_elements", []), ensure_ascii=False),
        json.dumps(slide.get("content_blocks", []), ensure_ascii=False)[:2000],
    ]).lower()
    method_signals = (
        "diagrams", "image model", "rendered image", "external image",
        "다이어그램스", "이미지 모델", "이미지모델", "렌더링 이미지",
    )
    if any(signal in text for signal in method_signals):
        return True
    if _slide_has_required_diagram_context(slide):
        return True
    action_signals = (
        "draw", "redraw", "render", "visualize", "generate image",
        "그려", "다시 그려", "그려줘", "렌더", "시각화",
    )
    visual_objects = (
        "diagram", "flowchart", "architecture", "topology", "image",
        "다이어그램", "아키텍처", "구성도", "토폴로지", "이미지",
    )
    return any(signal in text for signal in action_signals) and any(
        signal in text for signal in visual_objects
    )


def _slide_has_required_diagram_context(slide: dict) -> bool:
    suggested = {
        str(item).strip().lower()
        for item in slide.get("suggested_elements", [])
        if str(item).strip()
    }
    if not (suggested & {"diagram", "flowchart", "architecture_diagram"}):
        return False
    text = " ".join([
        str(slide.get("title") or ""),
        str(slide.get("key_message") or ""),
        str(slide.get("purpose") or ""),
        json.dumps(slide.get("content_blocks", []), ensure_ascii=False)[:2000],
    ]).lower()
    explicit_diagram_terms = (
        "diagram", "flowchart", "architecture diagram", "pipeline diagram",
        "topology diagram", "다이어그램", "흐름도", "구성도", "아키텍처 다이어그램",
    )
    requirement_terms = (
        "required", "needed", "must show", "must explain", "필요", "필수", "보여줘야",
    )
    return any(term in text for term in explicit_diagram_terms) or (
        any(term in text for term in requirement_terms)
        and any(term in text for term in ("architecture", "pipeline", "topology", "아키텍처", "파이프라인", "토폴로지"))
    )


def _extend_blueprints_to_requested_count(
    blueprints: list[dict],
    requested_count: int,
    user_query: str,
    ruleset,
    research_data: object | None = None,
) -> list[dict]:
    """Deterministically add missing slides from source material when the planner under-produces."""
    if len(blueprints) >= requested_count:
        return blueprints
    existing = [dict(blueprint) for blueprint in blueprints]
    source_context = _build_fallback_source_context(user_query, existing, research_data)
    existing_indices = [
        int(blueprint.get("index", position + 1))
        for position, blueprint in enumerate(existing)
        if str(blueprint.get("index", position + 1)).isdigit()
    ]
    next_index = max(existing_indices or [0]) + 1
    while len(existing) < requested_count:
        missing_number = len(existing) + 1
        slide_type = "summary" if missing_number == requested_count else "content"
        raw_slide = _missing_slide_blueprint(
            next_index,
            slide_type,
            missing_number,
            requested_count,
            user_query,
            existing,
            source_context,
        )
        normalized = _normalize_blueprints([raw_slide], ruleset)
        existing.extend(normalized)
        next_index += 1
    return existing[:requested_count]


def _missing_slide_blueprint(
    index: int,
    slide_type: str,
    missing_number: int,
    requested_count: int,
    user_query: str,
    existing_blueprints: list[dict] | None = None,
    source_context: dict | None = None,
) -> dict:
    source_context = source_context or _build_fallback_source_context(
        user_query,
        existing_blueprints or [],
        None,
    )
    segments = source_context.get("segments") or []
    deck_topic = source_context.get("deck_topic") or _deck_topic_from_existing(
        existing_blueprints or []
    )
    if slide_type == "summary":
        title = _compact_prompt_text(f"{deck_topic} 핵심 요약", 70)
        summary_segments = segments[-4:] or _segments_from_existing(existing_blueprints or [])[-4:]
        items = []
        for offset, segment in enumerate(summary_segments[:4], 1):
            point = _segment_point_text(segment, offset)
            items.append({
                "title": _compact_prompt_text(segment.get("title") or f"Point {offset}", 42),
                "body": _compact_prompt_text(point, 180),
                "icon": ["target", "chart", "shield", "rocket"][(offset - 1) % 4],
            })
        if not items:
            items = [{
                "title": _compact_prompt_text(deck_topic, 42),
                "body": _compact_prompt_text(user_query, 180),
                "icon": "target",
            }]
        key_message = _compact_prompt_text(
            " / ".join(item["title"] for item in items[:3]),
            160,
        )
        purpose = "Summarize source-backed decision rationale and next steps."
        body_layout_id = "numbered_list_card"
        suggested = ["rounded_rect", "icon", "textbox", "connector"]
        blocks = [{"type": "source_backed_summary", "items": items}]
    else:
        segment = _segment_for_missing_slide(segments, missing_number)
        title = _fallback_slide_title(segment, deck_topic, missing_number)
        points = _segment_points(segment)
        items = []
        for offset, point in enumerate(points[:5], 1):
            items.append({
                "title": _point_title(point, offset),
                "body": _compact_prompt_text(point, 190),
                "icon": ["document", "layers", "chart", "shield", "gear"][(offset - 1) % 5],
            })
        if not items:
            items = [{
                "title": _compact_prompt_text(title, 42),
                "body": _compact_prompt_text(user_query, 190),
                "icon": "document",
            }]
        key_message = _compact_prompt_text(_segment_point_text(segment, 1), 180)
        purpose = f"Cover source-backed material for {title} without crowding adjacent slides."
        body_layout_id = _fallback_body_layout_for_missing_slide(missing_number)
        suggested = ["rounded_rect", "table", "icon", "textbox"]
        blocks = [{"type": "source_backed_supporting_points", "items": items}]
    point_text = " ".join(
        str(item.get("body", ""))
        for block in blocks
        for item in block.get("items", [])
        if isinstance(item, dict)
    )
    return {
        "index": index,
        "slide_type": slide_type,
        "section_label": "Source-backed",
        "title": title,
        "key_message": key_message,
        "purpose": purpose,
        "content_blocks": blocks,
        "data_points": _source_data_points(point_text),
        "layout_plan": {"body_layout_id": body_layout_id},
        "layout_hint": "source-backed auto-extension to satisfy explicit slide count",
        "suggested_elements": suggested,
        "visual_density": "high",
        "source_citations": [],
    }


def _build_fallback_source_context(
    user_query: str,
    existing_blueprints: list[dict],
    research_data: object | None,
) -> dict:
    segments = _source_segments_from_text(user_query)
    segments.extend(_source_segments_from_research(research_data))
    if not segments:
        segments = _segments_from_existing(existing_blueprints)
    return {
        "segments": segments,
        "deck_topic": _deck_topic_from_sources(user_query, existing_blueprints, segments),
    }


def _source_segments_from_text(text: str) -> list[dict]:
    lines = [line.strip() for line in str(text or "").splitlines()]
    segments: list[dict] = []
    current: dict | None = None
    for line in lines:
        if not line:
            continue
        heading = _source_heading(line)
        if heading:
            if current:
                segments.append(current)
            current = {"title": heading, "points": []}
            continue
        point = _clean_source_point(line)
        if not point:
            continue
        if current is None:
            current = {"title": _compact_prompt_text(point, 70), "points": []}
        current.setdefault("points", []).append(point)
    if current:
        segments.append(current)

    if segments:
        return _dedupe_segments(segments)[:40]
    sentence_points = [
        _clean_source_point(item)
        for item in re.split(r"(?<=[.!?。])\s+|\n+", str(text or ""))
        if _clean_source_point(item)
    ]
    if not sentence_points:
        return []
    return [{"title": _compact_prompt_text(sentence_points[0], 70), "points": sentence_points[:8]}]


def _source_heading(line: str) -> str:
    text = str(line or "").strip()
    markdown = re.match(r"^#{1,6}\s+(.+)$", text)
    if markdown:
        return _clean_source_point(markdown.group(1))[:90]
    bracketed = re.match(r"^【(.{2,90})】", text)
    if bracketed:
        return _clean_source_point(bracketed.group(1))[:90]
    numbered = re.match(r"^(?:\d{1,2}[.)]|[A-Z][.)])\s+(.{4,90})$", text)
    if numbered:
        return _clean_source_point(numbered.group(1))[:90]
    return ""


def _clean_source_point(text: object) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^\s*(?:[-*+•]\s+|\d{1,2}[.)]\s+)", "", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip(" -–—:")
    return value


def _source_segments_from_research(research_data: object | None) -> list[dict]:
    if not isinstance(research_data, dict):
        return []
    segments = []
    for key in ("facts", "statistics", "case_studies", "trends"):
        items = research_data.get(key)
        if not isinstance(items, list) or not items:
            continue
        points = [_compact_prompt_text(_research_item_text(item), 220) for item in items[:6]]
        points = [point for point in points if point]
        if points:
            segments.append({"title": key.replace("_", " ").title(), "points": points})
    return segments


def _research_item_text(item: object) -> str:
    if isinstance(item, dict):
        parts = [
            str(item.get(key) or "").strip()
            for key in ("title", "metric", "value", "summary", "snippet", "source")
            if str(item.get(key) or "").strip()
        ]
        return " | ".join(parts) if parts else json.dumps(item, ensure_ascii=False)
    return str(item or "")


def _segments_from_existing(existing_blueprints: list[dict]) -> list[dict]:
    segments = []
    for blueprint in existing_blueprints:
        title = _compact_prompt_text(blueprint.get("title"), 70)
        points = [
            _compact_prompt_text(blueprint.get("key_message"), 180),
            _compact_prompt_text(blueprint.get("purpose"), 180),
        ]
        points = [point for point in points if point]
        if title or points:
            segments.append({"title": title or "Existing slide", "points": points})
    return segments


def _dedupe_segments(segments: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for segment in segments:
        title = _compact_prompt_text(segment.get("title"), 90)
        points = [
            _compact_prompt_text(point, 220)
            for point in segment.get("points", [])
            if _compact_prompt_text(point, 220)
        ]
        key = (title.lower(), tuple(points[:2]))
        if not title and not points:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append({"title": title, "points": points})
    return result


def _deck_topic_from_sources(
    user_query: str,
    existing_blueprints: list[dict],
    segments: list[dict],
) -> str:
    for blueprint in existing_blueprints:
        title = _compact_prompt_text(blueprint.get("title"), 60)
        if title and str(blueprint.get("slide_type")) == "cover":
            return title
    if segments:
        return _compact_prompt_text(segments[0].get("title"), 60)
    first_line = next(
        (line.strip() for line in str(user_query or "").splitlines() if line.strip()),
        "",
    )
    return _compact_prompt_text(first_line or "Presentation", 60)


def _deck_topic_from_existing(existing_blueprints: list[dict]) -> str:
    for blueprint in existing_blueprints:
        title = _compact_prompt_text(blueprint.get("title"), 60)
        if title:
            return title
    return "Presentation"


def _segment_for_missing_slide(segments: list[dict], missing_number: int) -> dict:
    if not segments:
        return {"title": "Source Material", "points": []}
    index = max(0, min(missing_number - 1, len(segments) - 1))
    return segments[index]


def _segment_points(segment: dict) -> list[str]:
    points = [
        _compact_prompt_text(point, 220)
        for point in segment.get("points", [])
        if _compact_prompt_text(point, 220)
    ]
    if points:
        return points
    title = _compact_prompt_text(segment.get("title"), 220)
    return [title] if title else []


def _segment_point_text(segment: dict, index: int) -> str:
    points = _segment_points(segment)
    if points:
        return points[min(max(index - 1, 0), len(points) - 1)]
    return _compact_prompt_text(segment.get("title"), 180)


def _fallback_slide_title(segment: dict, deck_topic: str, missing_number: int) -> str:
    title = _compact_prompt_text(segment.get("title"), 70)
    if title and title.lower() not in {"source material", "presentation"}:
        return title
    return _compact_prompt_text(f"{deck_topic} - source point {missing_number}", 70)


def _point_title(point: str, index: int) -> str:
    text = _compact_prompt_text(point, 80)
    for delimiter in (":", " - ", " — ", " – "):
        if delimiter in text:
            head = text.split(delimiter, 1)[0].strip()
            if 3 <= len(head) <= 42:
                return head
    words = text.split()
    title = " ".join(words[:5]) if words else f"Point {index}"
    return _compact_prompt_text(title, 42)


def _source_data_points(text: str) -> list[dict]:
    metrics = []
    pattern = re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:%|개월|년|장|개|종|억원|시간|배|명|건|원|달러|x|X|months?|years?)\b"
    )
    for match in pattern.finditer(str(text or "")):
        value = match.group(0).strip()
        if value in metrics:
            continue
        metrics.append(value)
        if len(metrics) >= 4:
            break
    return [
        {
            "label": f"Source metric {index}",
            "value": value,
            "context": "Extracted from user/research material",
        }
        for index, value in enumerate(metrics, 1)
    ]


def _fallback_body_layout_for_missing_slide(missing_number: int) -> str:
    layouts = ["split_60_40", "grid_2x2", "fact_sheet", "bullet_columns"]
    return layouts[(missing_number - 1) % len(layouts)]


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
  "presentation_strategy": {
    "request_intent": "What the user is trying to achieve",
    "presentation_objective": "What this deck must accomplish",
    "audience": "Who must decide or act",
    "decision_to_enable": "Concrete decision or next action",
    "narrative_arc": "context -> evidence -> recommendation -> action",
    "tone": "clear and decision-oriented"
  },
  "layout_system": {
    "cover_layout_id": "approved cover layout ID",
    "header_zone_id": "approved fixed header zone ID",
    "footer_zone_id": "approved fixed footer zone ID"
  },
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
      "layout_plan": {
        "body_layout_id": "approved body layout ID",
        "sub_layout_ids": [],
        "element_placements": [{"element": "chart", "role": "proof_object"}]
      },
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
7. Select one fixed header/footer pair for every non-cover slide
8. Plan only elements convertible through the deterministic OOXML mapper
9. Output ONLY valid JSON, no markdown fences or explanations"""


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
