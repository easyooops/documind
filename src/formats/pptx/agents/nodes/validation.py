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


async def validation_agent(state: DocuMindState) -> dict:
    """Validate generated DSL slides: schema (programmatic) + visual (VLM)."""
    logger.info("validation_agent.start", retry=state.get("retry_count", 0))

    config = load_agent_config(AGENT_NAME, format_id=FORMAT_ID)
    score_threshold = config.get("validation", {}).get("score_threshold", 4.2)

    slides_dsl = state.get("slides_dsl", [])
    slides_html = state.get("slides_html", [])

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

    # Level 2 + 3: VLM visual quality + content accuracy
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    dsl_summary = "\n\n".join([
        f"--- Slide {s.get('index', i+1)} (type: {s.get('slide_type', 'content')}) ---\n{json.dumps(s, ensure_ascii=False, indent=2)[:3000]}"
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

    overall_score = _extract_overall_score(validation_result)
    validation_result["overall_score"] = overall_score

    if overall_score < score_threshold:
        validation_result["passed"] = False

    l3 = validation_result.get("level3_content", {})
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
    """Programmatic Pydantic schema validation for all slides."""
    issues: list[str] = []
    fix_instructions: list[str] = []

    for i, slide_data in enumerate(slides_dsl):
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
