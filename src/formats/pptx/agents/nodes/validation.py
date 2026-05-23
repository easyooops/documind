"""PPTX Validation Agent — DSL schema-based validation + VLM visual quality check."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.agents.loader import get_llm_for_agent, load_agent_config, load_agent_prompt
from src.core.logging import get_logger
from src.formats.pptx.dsl.schema import SlideDSL
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "validation"
FORMAT_ID = "pptx"


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


async def validation_agent(state: DocuMindState) -> dict:
    """Validate generated DSL slides: schema (programmatic) + visual (VLM)."""
    logger.info("validation_agent.start", retry=state.get("retry_count", 0))

    config = _as_dict(load_agent_config(AGENT_NAME, format_id=FORMAT_ID))
    score_threshold = _as_dict(config.get("validation")).get("score_threshold", 4.2)

    slides_dsl = [_as_dict(item) for item in _as_list(state.get("slides_dsl"))]
    slides_html = [_as_dict(item) for item in _as_list(state.get("slides_html"))]
    consistency_report = _as_dict(state.get("consistency_report"))

    if not slides_dsl:
        logger.warning("validation_agent.no_dsl")
        return {
            "validation_result": {
                "passed": False,
                "overall_score": 0,
                "issues": ["No DSL slides generated"],
                "fix_instructions": ["Generate all slides as valid OOXML-DSL JSON"],
            },
            "retry_count": state.get("retry_count", 0) + 1,
            "current_phase": "validating",
        }

    # Level 1: Programmatic schema validation
    schema_result = _validate_schema(slides_dsl)
    if not schema_result["passed"]:
        logger.info("validation_agent.schema_failed", issues=schema_result["issues"][:3])
        return {
            "validation_result": {
                "passed": False,
                "overall_score": 0,
                "level1_schema": schema_result,
                "issues": schema_result["issues"],
                "fix_instructions": schema_result["fix_instructions"],
            },
            "retry_count": state.get("retry_count", 0) + 1,
            "current_phase": "validating",
        }

    quality_result = _validate_quality(slides_dsl)

    # Level 2 + 3: VLM visual quality + content accuracy
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    dsl_summary = "\n\n".join([
        f"--- Slide {s.get('index', i+1)} "
        f"(type: {s.get('slide_type', 'content')}) ---\n"
        f"{json.dumps(s, ensure_ascii=False, indent=2)[:3000]}"
        for i, s in enumerate(slides_dsl)
    ])

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Validate these {len(slides_dsl)} OOXML-DSL slides:\n\n{dsl_summary}"),
    ]

    try:
        response = await llm.ainvoke(messages)
        validation_result = _parse_validation_response(response.content, score_threshold)
    except Exception as e:
        logger.error("validation_agent.llm_error", error=str(e))
        validation_result = {
            "passed": True,
            "overall_score": score_threshold,
            "issues": [],
            "fix_instructions": [],
            "_fallback": True,
        }

    validation_result["level1_schema"] = schema_result
    validation_result["deterministic_quality"] = quality_result

    overall_score = _extract_overall_score(validation_result)
    if not quality_result["passed"]:
        validation_result.setdefault("issues", []).extend(quality_result["issues"])
        validation_result.setdefault("fix_instructions", []).extend(
            quality_result["fix_instructions"]
        )
        validation_result["passed"] = False
        overall_score = min(overall_score, quality_result["score"])
    validation_result["overall_score"] = overall_score

    if consistency_report and not consistency_report.get("is_consistent", True):
        consistency_issues = consistency_report.get("issues", [])
        consistency_patches = consistency_report.get("patches", [])
        validation_result["passed"] = False
        validation_result.setdefault("issues", []).extend(consistency_issues)
        validation_result.setdefault("fix_instructions", []).extend(
            [f"Resolve cross-slide consistency issue: {issue}" for issue in consistency_issues]
        )
        validation_result.setdefault("fix_instructions", []).extend(
            [f"Apply consistency patch: {patch}" for patch in consistency_patches]
        )
        validation_result["consistency_report"] = consistency_report

    if overall_score < score_threshold:
        validation_result["passed"] = False

    l3 = validation_result.get("level3_content", {})
    l3 = _as_dict(l3)
    if l3 and not l3.get("passed", True):
        validation_result["passed"] = False

    retry_count = state.get("retry_count", 0)
    if not validation_result.get("passed", True):
        retry_count += 1

    logger.info(
        "validation_agent.complete",
        passed=validation_result.get("passed"),
        score=overall_score,
        issues_count=len(validation_result.get("issues", [])),
        retry=retry_count,
    )
    return {"validation_result": validation_result, "retry_count": retry_count, "current_phase": "validating"}


def _validate_schema(slides_dsl: list[dict]) -> dict:
    """Programmatic schema and deterministic quality validation for all slides."""
    issues: list[str] = []
    fix_instructions: list[str] = []

    for i, slide_data in enumerate(_as_dict(item) for item in slides_dsl):
        try:
            slide = SlideDSL.model_validate(slide_data)

            for shape in slide.shapes:
                if shape.position.x + shape.position.w > 960:
                    issues.append(f"Slide {slide.index}: Shape '{shape.id}' exceeds viewport width (x={shape.position.x}, w={shape.position.w}, total={shape.position.x + shape.position.w} > 960)")
                    fix_instructions.append(f"Slide {slide.index}: Reduce shape '{shape.id}' position.x or position.w so x+w <= 960")
                if shape.position.y + shape.position.h > 540:
                    issues.append(f"Slide {slide.index}: Shape '{shape.id}' exceeds viewport height (y={shape.position.y}, h={shape.position.h}, total={shape.position.y + shape.position.h} > 540)")
                    fix_instructions.append(f"Slide {slide.index}: Reduce shape '{shape.id}' position.y or position.h so y+h <= 540")

            shape_ids = [s.id for s in slide.shapes]
            if len(shape_ids) != len(set(shape_ids)):
                dupes = [sid for sid in shape_ids if shape_ids.count(sid) > 1]
                issues.append(f"Slide {slide.index}: Duplicate shape IDs: {set(dupes)}")
                fix_instructions.append(f"Slide {slide.index}: Make all shape IDs unique")

        except ValidationError as e:
            for error in e.errors():
                loc = " → ".join(str(x) for x in error["loc"])
                issues.append(f"Slide {i+1}: Schema error at {loc}: {error['msg']}")
                fix_instructions.append(f"Slide {i+1}: Fix {loc} — {error['msg']}")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "fix_instructions": fix_instructions,
    }


def _validate_quality(slides_dsl: list[dict]) -> dict:
    issues: list[str] = []
    fix_instructions: list[str] = []

    chart_count = 0
    for slide_data in slides_dsl:
        try:
            slide = SlideDSL.model_validate(slide_data)
        except ValidationError:
            continue
        chart_count += sum(1 for shape in slide.shapes if shape.chart)
        quality = _validate_slide_quality(slide)
        issues.extend(quality["issues"])
        fix_instructions.extend(quality["fix_instructions"])

    max_charts = max(1, len(slides_dsl) // 4)
    if chart_count > max_charts:
        issues.append(
            f"Deck uses too many charts ({chart_count}); maximum recommended is {max_charts}"
        )
        fix_instructions.append(
            "Reduce chart usage. Keep only charts with real numeric trend/comparison data; convert others to tables, KPI cards, or diagrams"
        )

    penalty = min(len(issues) * 0.25, 2.0)
    score = max(2.2, 4.2 - penalty) if issues else 4.2
    return {
        "passed": len(issues) == 0,
        "score": score,
        "issues": issues,
        "fix_instructions": fix_instructions,
    }


def _validate_slide_quality(slide: SlideDSL) -> dict:
    """Deterministic checks for clipping, overlap, fonts, and proposal-grade richness."""
    issues: list[str] = []
    fix_instructions: list[str] = []

    text_shapes = [shape for shape in slide.shapes if shape.text]
    content_type = slide.slide_type not in {"cover", "section"}
    visual_shapes = [
        shape
        for shape in slide.shapes
        if not shape.text
        and shape.role in {
            "chart",
            "kpi",
            "image",
            "decorative",
            "table",
            "diagram",
            "line",
            "arrow",
            "callout",
        }
        and shape.id.lower() not in {"bg", "background", "slide_bg", "slide-background"}
        and (shape.position.w >= 20 or shape.position.h >= 20)
    ]
    filled_text_visuals = [
        shape
        for shape in text_shapes
        if shape.fill is not None
        and shape.role in {"chart", "kpi", "badge", "label", "body", "table", "callout"}
    ]

    if content_type and len(visual_shapes) + len(filled_text_visuals) < 2:
        issues.append(
            f"Slide {slide.index}: Proposal-grade visual structure is too sparse; add charts, KPI cards, table-like grids, diagrams, dividers, or callout boxes"
        )
        fix_instructions.append(
            f"Slide {slide.index}: Add at least two non-paragraph visual structures such as KPI cards, chart-like bars/lines, table rows, process boxes, dividers, or highlighted callouts"
        )

    table_like_words = ("|", "\t", "구분", "항목", "현황", "시사점", "category", "status")
    chart_like_words = ("chart", "graph", "trend", "차트", "그래프", "추이")
    for shape in text_shapes:
        combined_text = " ".join(
            run.text
            for para in shape.text or []
            for run in para.runs
        )
        if shape.role != "table" and any(word in combined_text.lower() for word in table_like_words):
            issues.append(
                f"Slide {slide.index}: Shape '{shape.id}' appears to fake a table inside a text box"
            )
            fix_instructions.append(
                f"Slide {slide.index}: Replace shape '{shape.id}' with role='table' and a native table field"
            )
        if shape.role != "chart" and any(word in combined_text.lower() for word in chart_like_words):
            issues.append(
                f"Slide {slide.index}: Shape '{shape.id}' appears to describe a chart instead of using chart data"
            )

        if shape.vertical_align != "top" and shape.role not in {"kpi", "badge"}:
            issues.append(
                f"Slide {slide.index}: Shape '{shape.id}' uses vertical_align='{shape.vertical_align}' but should be top-aligned"
            )
            fix_instructions.append(
                f"Slide {slide.index}: Set shape '{shape.id}' vertical_align to 'top' unless it is a KPI/badge"
            )

        if 488 < shape.position.y < 500:
            issues.append(
                f"Slide {slide.index}: Shape '{shape.id}' sits in the unsafe gap between body and footer"
            )
            fix_instructions.append(
                f"Slide {slide.index}: Move shape '{shape.id}' into body zone (y<=488) or footer zone (y>=500)"
            )
        if shape.position.y >= 500 and shape.role not in {"label", "decorative"}:
            issues.append(
                f"Slide {slide.index}: Shape '{shape.id}' places body content in footer zone"
            )
            fix_instructions.append(
                f"Slide {slide.index}: Move body content '{shape.id}' above y=488 and reserve footer for source/page labels"
            )
            fix_instructions.append(
                f"Slide {slide.index}: Create role='chart' with chart.data and use separate label text shapes for annotations"
            )

        fit = _estimate_text_fit(shape)
        if not fit["fits"]:
            issues.append(
                f"Slide {slide.index}: Shape '{shape.id}' likely clips text; estimated text height {fit['estimated_height']:.0f}px exceeds usable height {fit['usable_height']:.0f}px"
            )
            fix_instructions.append(
                f"Slide {slide.index}: Increase shape '{shape.id}' height/width, reduce font_size within hierarchy, or split the exact text into multiple shapes"
            )

        for para in shape.text or []:
            for run in para.runs:
                if not _is_premium_font(run.font_family):
                    issues.append(
                        f"Slide {slide.index}: Shape '{shape.id}' uses non-preferred font '{run.font_family}'"
                    )
                    fix_instructions.append(
                        f"Slide {slide.index}: Change shape '{shape.id}' font_family to Pretendard, Noto Sans KR, Inter, Aptos, or Segoe UI"
                    )
                if shape.role == "title" and run.font_size > 44 and len(run.text) > 28:
                    issues.append(
                        f"Slide {slide.index}: Shape '{shape.id}' title is long at font_size={run.font_size}, high clipping risk"
                    )
                    fix_instructions.append(
                        f"Slide {slide.index}: For shape '{shape.id}', use 32-40px title size with larger height or split title into two lines/shapes"
                    )

                contrast_issue = _check_text_contrast(shape, run.color)
                if contrast_issue:
                    issues.append(f"Slide {slide.index}: Shape '{shape.id}' {contrast_issue}")
                    fix_instructions.append(
                        f"Slide {slide.index}: Adjust shape '{shape.id}' text or fill color to meet at least 4.5:1 contrast"
                    )

    for i, first in enumerate(text_shapes):
        for second in text_shapes[i + 1:]:
            if _overlap_area(first, second) > 200:
                issues.append(
                    f"Slide {slide.index}: Text shapes '{first.id}' and '{second.id}' overlap"
                )
                fix_instructions.append(
                    f"Slide {slide.index}: Move or resize '{first.id}'/'{second.id}' to keep text boxes separated"
                )

            gap = _minimum_gap(first, second)
            if gap is not None and 0 < gap < 8:
                issues.append(
                    f"Slide {slide.index}: Text shapes '{first.id}' and '{second.id}' have only {gap}px spacing"
                )
                fix_instructions.append(
                    f"Slide {slide.index}: Increase spacing between '{first.id}' and '{second.id}' to at least 8px; major blocks need 24px"
                )

    for shape in slide.shapes:
        if shape.role == "table" and not shape.table:
            issues.append(f"Slide {slide.index}: Shape '{shape.id}' has role='table' but no native table data")
            fix_instructions.append(f"Slide {slide.index}: Add a `table` field to shape '{shape.id}'")
        if shape.role == "chart" and not shape.chart:
            issues.append(f"Slide {slide.index}: Shape '{shape.id}' has role='chart' but no native chart data")
            fix_instructions.append(f"Slide {slide.index}: Add a `chart` field to shape '{shape.id}' or change role")

    return {"issues": issues, "fix_instructions": fix_instructions}


def _estimate_text_fit(shape) -> dict:
    usable_w = max(shape.position.w - 16, 1)
    usable_h = max(shape.position.h - 8, 1)
    estimated_h = 0.0

    for para in shape.text or []:
        text_width = 0.0
        max_font = 8
        for run in para.runs:
            max_font = max(max_font, run.font_size)
            text_width += _estimate_text_width(run.text, run.font_size)
        lines = max(1, int((text_width + usable_w - 1) // usable_w))
        estimated_h += para.spacing_before + (max_font * para.line_height * lines)

    return {
        "fits": estimated_h <= usable_h * 1.05,
        "estimated_height": estimated_h,
        "usable_height": usable_h,
    }


def _estimate_text_width(text: str, font_size: int) -> float:
    width = 0.0
    for ch in text:
        if ch.isspace():
            width += font_size * 0.35
        elif ord(ch) >= 0x2E80:
            width += font_size * 0.92
        elif ch.isupper():
            width += font_size * 0.62
        else:
            width += font_size * 0.52
    return width


def _is_premium_font(font_family: str) -> bool:
    allowed = ("pretendard", "noto sans kr", "inter", "aptos", "segoe ui")
    normalized = font_family.lower()
    return any(font in normalized for font in allowed)


def _check_text_contrast(shape, text_color: str) -> str | None:
    fill = shape.fill
    if not fill or getattr(fill, "type", None) != "solid":
        return None
    ratio = _contrast_ratio(text_color, fill.color)
    if ratio < 4.5:
        return f"has low text/fill contrast ({ratio:.1f}:1)"
    return None


def _contrast_ratio(fg: str, bg: str) -> float:
    def channel(value: int) -> float:
        normalized = value / 255
        if normalized <= 0.03928:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    def luminance(hex_color: str) -> float:
        color = hex_color.lstrip("#")
        r = channel(int(color[0:2], 16))
        g = channel(int(color[2:4], 16))
        b = channel(int(color[4:6], 16))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    l1 = luminance(fg)
    l2 = luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _overlap_area(first, second) -> int:
    left = max(first.position.x, second.position.x)
    right = min(first.position.x + first.position.w, second.position.x + second.position.w)
    top = max(first.position.y, second.position.y)
    bottom = min(first.position.y + first.position.h, second.position.y + second.position.h)
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def _minimum_gap(first, second) -> int | None:
    first_left = first.position.x
    first_right = first.position.x + first.position.w
    first_top = first.position.y
    first_bottom = first.position.y + first.position.h
    second_left = second.position.x
    second_right = second.position.x + second.position.w
    second_top = second.position.y
    second_bottom = second.position.y + second.position.h

    horizontal_overlap = first_left < second_right and second_left < first_right
    vertical_overlap = first_top < second_bottom and second_top < first_bottom
    if horizontal_overlap and vertical_overlap:
        return 0
    if horizontal_overlap:
        return min(abs(first_bottom - second_top), abs(second_bottom - first_top))
    if vertical_overlap:
        return min(abs(first_right - second_left), abs(second_right - first_left))
    return None


def _extract_overall_score(result: dict) -> float:
    """Extract overall score from validation result."""
    if "overall_score" in result:
        try:
            return float(result["overall_score"])
        except (TypeError, ValueError):
            pass

    l2 = result.get("level2_visual", {})
    if isinstance(l2, dict) and "score" in l2:
        try:
            return float(l2["score"])
        except (TypeError, ValueError):
            pass

    breakdown = l2.get("breakdown", {}) if isinstance(l2, dict) else {}
    if breakdown:
        scores = []
        weights = {
            "layout_composition": 0.25,
            "typography_readability": 0.25,
            "color_visual": 0.20,
            "information_design": 0.15,
            "professional_polish": 0.15,
        }
        total_weight = 0
        for key, weight in weights.items():
            cat = breakdown.get(key, {})
            if isinstance(cat, dict) and "score" in cat:
                scores.append(float(cat["score"]) * weight)
                total_weight += weight
            elif isinstance(cat, (int, float)):
                scores.append(float(cat) * weight)
                total_weight += weight
        if scores and total_weight > 0:
            return sum(scores) / total_weight

    return 5.0


def _parse_validation_response(content: str, fallback_score: float) -> dict:
    """Parse LLM response with multiple fallback strategies."""
    import re

    content = content.strip()

    if content.startswith("{"):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

    if "```json" in content:
        try:
            json_block = content.split("```json")[1].split("```")[0]
            return json.loads(json_block.strip())
        except (json.JSONDecodeError, IndexError):
            pass

    if "```" in content:
        try:
            json_block = content.split("```")[1].split("```")[0]
            return json.loads(json_block.strip())
        except (json.JSONDecodeError, IndexError):
            pass

    json_match = re.search(r'\{[\s\S]*"passed"[\s\S]*\}', content)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("validation_agent.parse_fallback", content_len=len(content))
    return {
        "passed": True,
        "overall_score": fallback_score,
        "issues": [],
        "fix_instructions": [],
        "_parse_fallback": True,
    }
