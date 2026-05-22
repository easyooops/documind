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

logger = get_logger(__name__)

AGENT_NAME = "code_generator"
FORMAT_ID = "pptx"

_html_renderer = DSLtoHTMLRenderer()

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


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
            s = s[:last_complete + 1]
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


def _parse_and_validate(raw: str, slide_index: int) -> SlideDSL:
    """Parse raw LLM output → SlideDSL. Tries repair on failure."""
    json_str = _extract_json(raw)

    # First attempt: parse as-is
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Second attempt: repair and retry
        repaired = _repair_json(json_str)
        data = json.loads(repaired)

    if "index" not in data:
        data["index"] = slide_index
    return SlideDSL.model_validate(data)


async def _generate_single_slide(
    slide_index: int,
    slide_content: dict,
    layout_spec: dict,
    design_system: dict,
    asset_requirements: list[dict],
    system_prompt: str,
    fix_instructions: list[str] | None = None,
    previous_dsl: dict | None = None,
) -> dict:
    """Generate OOXML-DSL JSON for a single slide."""
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    slide_assets = [a for a in asset_requirements if a.get("slide_index") == slide_index]

    context = f"""## Slide #{slide_index}

### 1. Content (from Content Writer — use this text EXACTLY)
{json.dumps(slide_content, ensure_ascii=False, indent=2)}

### 2. Layout Specification (from Layout Composer — follow this structure)
{json.dumps(layout_spec, ensure_ascii=False, indent=2)}

### 3. Design System (from Style Director — apply these colors, fonts, effects)

Color Tokens & Variables:
{json.dumps(design_system.get('css_variables', {}), ensure_ascii=False, indent=2)}

Typography Scale:
{json.dumps(design_system.get('typography_scale', []), ensure_ascii=False, indent=2)}

Effect Library:
{json.dumps(design_system.get('effect_library', {}), ensure_ascii=False, indent=2)}

Component Recipes:
{json.dumps(design_system.get('component_recipes', {}), ensure_ascii=False, indent=2)}

### 4. Visual Assets (from Asset Planner)
{json.dumps(slide_assets, ensure_ascii=False, indent=2) if slide_assets else "None for this slide"}

### Instructions
- Apply the Design System's colors and typography faithfully
- Position elements according to the Layout Spec's grid_type and zones
- Use the Content Writer's text verbatim — do not modify or invent
- Output ONLY valid JSON (SlideDSL object), no other text"""

    if fix_instructions and previous_dsl:
        context += f"""

⚠️ PREVIOUS ATTEMPT FAILED VALIDATION. You MUST fix these issues:

Previous DSL JSON (reference only):
{json.dumps(previous_dsl, ensure_ascii=False, indent=2)[:2000]}

Required Fixes:
{chr(10).join(f'- {fix}' for fix in fix_instructions)}

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
            slide_dsl = _parse_and_validate(raw_content, slide_index)
            dsl_dict = slide_dsl.model_dump()
            html = _html_renderer.render_slide(slide_dsl)
            return {
                "index": slide_index,
                "dsl": dsl_dict,
                "html": html,
                "css": "",
                "metadata": {"layout": layout_spec.get("grid_type", "unknown"), "slide_type": slide_dsl.slide_type},
            }
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)
            logger.warning("code_agent.parse_retry", slide=slide_index, attempt=attempt, error=last_error[:200])

            retry_msg = (
                f"Your JSON output was INVALID. Error: {last_error[:300]}\n\n"
                "RULES TO FIX:\n"
                "1. Output ONLY valid JSON — no markdown, no explanation\n"
                "2. All strings must use double quotes (\")\n"
                "3. No trailing commas (e.g., NOT {\"a\":1,} )\n"
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
                "text": [{"runs": [{"text": title[:60], "font_size": 36, "font_weight": 700, "font_family": "Pretendard", "color": "ffffff"}], "align": "left", "line_height": 1.3}],
            },
        ],
    }

    if subtitle:
        fallback_dsl["shapes"].append({
            "id": "subtitle",
            "role": "subtitle",
            "position": {"x": 80, "y": 270, "w": 800, "h": 50},
            "z_index": 1,
            "text": [{"runs": [{"text": subtitle[:80], "font_size": 18, "font_weight": 400, "font_family": "Pretendard", "color": "b0bec5"}], "align": "left", "line_height": 1.5}],
        })

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
    logger.info("code_agent.start", retry_count=state.get("retry_count", 0), qa_iterations=state.get("qa_iterations", 0))

    config = load_agent_config(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)
    max_parallel = config.get("parallel", {}).get("max_concurrent", 4)

    slide_contents = state.get("slide_contents", [])
    layout_specs = state.get("layout_specs", [])
    design_system = state.get("design_system", {})
    asset_requirements = state.get("asset_requirements", [])

    validation_result = state.get("validation_result", {})
    qa_feedback = state.get("qa_feedback", {})

    fix_instructions = validation_result.get("fix_instructions", [])
    qa_fix_instructions = qa_feedback.get("fix_instructions", [])
    qa_issues = qa_feedback.get("issues", [])

    all_fix_instructions = fix_instructions + qa_fix_instructions
    if qa_issues and not qa_fix_instructions:
        all_fix_instructions += [f"QA Issue: {issue}" for issue in qa_issues]

    previous_slides_dsl = state.get("slides_dsl", [])
    previous_slides_html = state.get("slides_html", [])
    is_retry = bool(all_fix_instructions) and (bool(previous_slides_dsl) or bool(previous_slides_html))

    slides_dsl: list[dict] = []
    slides_html: list[dict] = []

    for batch_start in range(0, len(slide_contents), max_parallel):
        batch = slide_contents[batch_start:batch_start + max_parallel]
        tasks = []

        for slide_content in batch:
            idx = slide_content.get("index", batch_start + len(tasks) + 1)
            layout = next(
                (l for l in layout_specs if l.get("index") == idx),
                layout_specs[min(idx - 1, len(layout_specs) - 1)] if layout_specs else {},
            )

            prev_dsl = None
            slide_fixes = all_fix_instructions
            if is_retry:
                prev_slide_dsl = next((s for s in previous_slides_dsl if s.get("index") == idx), None)
                prev_dsl = prev_slide_dsl if prev_slide_dsl else None
                slide_fixes = [
                    f for f in all_fix_instructions
                    if f"Slide {idx}" in f or f"슬라이드 {idx}" in f
                    or "slide" not in f.lower()
                ]
                if not slide_fixes:
                    slide_fixes = all_fix_instructions

            tasks.append(_generate_single_slide(
                idx, slide_content, layout, design_system, asset_requirements, system_prompt,
                fix_instructions=slide_fixes if is_retry else None,
                previous_dsl=prev_dsl,
            ))

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in batch_results:
            if isinstance(result, Exception):
                logger.error("code_agent.slide_error", error=str(result))
                continue
            slides_dsl.append(result["dsl"])
            slides_html.append({"index": result["index"], "html": result["html"], "css": "", "metadata": result["metadata"]})

    logger.info("code_agent.complete", slides_generated=len(slides_dsl), is_retry=is_retry)
    return {
        "slides_dsl": slides_dsl,
        "slides_html": slides_html,
        "current_phase": "generating",
    }
