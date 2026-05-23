"""PPTX QA Critic — validates PPTX output against OOXML-DSL source of truth."""

from __future__ import annotations

import json

from src.agents.loader import get_llm_for_agent, load_agent_config
from src.core.logging import get_logger
from src.formats.pptx.qa import PPTXQualityAssurance
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "qa_critic"
FORMAT_ID = "pptx"


async def qa_critic(state: DocuMindState) -> dict:
    """Validate generated PPTX against DSL source of truth."""
    logger.info("qa_critic.start", iteration=state.get("qa_iterations", 0))

    config = _as_dict(load_agent_config(AGENT_NAME, format_id=FORMAT_ID))
    qa_config = _as_dict(config.get("qa"))

    output_path = state.get("output_path")
    slides_dsl = [_as_dict(item) for item in _as_list(state.get("slides_dsl"))]

    if not output_path:
        return {
            "fidelity_scores": [0.0],
            "qa_iterations": state.get("qa_iterations", 0) + 1,
            "qa_feedback": {"issues": ["No output file generated"], "fix_instructions": ["Regenerate all slides"]},
            "current_phase": "qa",
        }

    qa = PPTXQualityAssurance(
        max_iterations=qa_config.get("max_iterations", 4),
        fidelity_threshold=qa_config.get("fidelity_threshold", 0.98),
    )

    slides_html = [_as_dict(item) for item in _as_list(state.get("slides_html"))]
    fidelity_score = await qa.evaluate(output_path, slides_html)
    iterations = state.get("qa_iterations", 0) + 1

    ooxml_result = qa._check_ooxml_compliance(output_path, slides_html)

    qa_feedback = {}
    if fidelity_score < qa_config.get("fidelity_threshold", 0.98):
        qa_feedback = await _generate_detailed_feedback(
            slides_dsl, fidelity_score, ooxml_result
        )

    logger.info(
        "qa_critic.complete",
        fidelity=fidelity_score,
        ooxml_score=ooxml_result.get("score", 0),
        iteration=iterations,
        issues_count=len(qa_feedback.get("issues", [])),
    )
    return {
        "fidelity_scores": _as_list(state.get("fidelity_scores")) + [fidelity_score],
        "qa_iterations": iterations,
        "qa_feedback": qa_feedback,
        "current_phase": "qa",
    }


async def _generate_detailed_feedback(
    slides_dsl: list[dict], score: float, ooxml_result: dict
) -> dict:
    """Use LLM to generate actionable DSL-level feedback."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)

        safe_slides = [_as_dict(item) for item in _as_list(slides_dsl)[:5]]
        dsl_summary = "\n\n".join(
            f"Slide {s.get('index', i + 1)} ({s.get('slide_type', 'content')}):\n"
            f"{json.dumps(s, ensure_ascii=False)[:1000]}"
            for i, s in enumerate(safe_slides)
        )

        ooxml_issues_text = ""
        ooxml_result = _as_dict(ooxml_result)
        if ooxml_result.get("issues"):
            ooxml_issues_text = "\n\nOOXML Compliance Issues (programmatic check):\n"
            ooxml_issues_text += "\n".join(f"- {issue}" for issue in ooxml_result["issues"][:10])

        prompt = f"""PPTX quality scored {score:.2f} (threshold: 0.98).
OOXML compliance score: {ooxml_result.get('score', 0):.2f}
{ooxml_issues_text}

Analyze the OOXML-DSL slides below and identify specific issues affecting PPTX quality.

Focus on:
1. Shapes that exceed viewport bounds (x+w > 960 or y+h > 540)
2. Text content too long for shape dimensions (Korean: ~20-24px width per char)
3. Font sizes outside reliable PPTX range (8-100px)
4. Gradient stops that are too close together or use very similar colors
5. Shadow parameters that may produce poor OOXML results
6. Shapes with opacity < 0.1 that are effectively invisible
7. Long titles likely to wrap and clip vertically
8. Missing proposal-grade structures: tables, chart-like visuals, diagrams, KPI cards, dividers, callouts
9. Non-premium or inconsistent fonts for Korean business proposal output
10. Low-quality box treatments: flat gray fills, weak contrast, inconsistent card/callout colors

DSL Slides:
{dsl_summary}

Output ONLY valid JSON:
{{
  "issues": ["Slide 1: Shape 'title' text '메가존클라우드' at font_size=42 in w=400 container may truncate"],
  "fix_instructions": ["Slide 1: Increase shape 'title' position.w from 400 to 700 or reduce font_size to 32"],
  "severity": "major"
}}"""

        messages = [
            SystemMessage(content=(
                "You are a strict PPTX/OOXML quality critic working with OOXML-DSL. "
                "The DSL defines shapes with explicit positions and properties that map directly to DrawingML. "
                "Your feedback must reference specific shape IDs, slide indexes, and DSL field values. "
                "Generic feedback is FORBIDDEN — be precise."
            )),
            HumanMessage(content=prompt),
        ]

        response = await llm.ainvoke(messages)

        import re
        json_match = re.search(r'\{[\s\S]*"issues"[\s\S]*\}', response.content)
        if json_match:
            result = json.loads(json_match.group())
            if ooxml_result.get("issues"):
                result.setdefault("ooxml_issues", ooxml_result["issues"][:5])
            return result

        return {
            "issues": [response.content[:500]],
            "fix_instructions": ["Review and fix the issues described above"],
        }
    except Exception as e:
        logger.warning("qa_critic.feedback_error", error=str(e))
        feedback = {
            "issues": [f"QA evaluation scored {score:.2f} — below threshold"],
            "fix_instructions": [
                "Ensure all shapes stay within viewport bounds (x+w<=960, y+h<=540)",
                "Ensure text content fits within shape dimensions at the specified font_size",
                "Use border_radius values less than half the minimum dimension",
            ],
        }
        if ooxml_result.get("issues"):
            feedback["ooxml_issues"] = ooxml_result["issues"][:5]
        return feedback


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
