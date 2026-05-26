"""Phase C: Design Quality Evaluator — rule-based slide quality assessment.

Replaces VLM QA with deterministic, rule-based evaluation against OOXML Rule-Sets.
No image comparison needed — validates HTML structure directly against design rules.
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.formats.pptx.rulesets import get_ruleset
from src.formats.pptx.rulesets.validator import DesignQualityEvaluator, EvaluationResult
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)


async def design_quality_evaluator(state: DocuMindState) -> dict:
    """Evaluate generated slides against OOXML rule-sets.

    Input: slides_html, design_system, qa_iterations
    Output: qa_feedback, fidelity_scores, qa_iterations
    """
    iteration = state.get("qa_iterations", 0)
    logger.info("design_evaluator.start", iteration=iteration)

    slides_html = state.get("slides_html", [])
    design_system = state.get("design_system", {})

    if not slides_html:
        logger.warning("design_evaluator.no_slides")
        return {
            "qa_feedback": {"passed": True, "fix_instructions": []},
            "fidelity_scores": [0.0],
            "qa_iterations": iteration + 1,
            "current_phase": "qa",
        }

    ruleset = get_ruleset()
    evaluator = DesignQualityEvaluator(ruleset)
    result: EvaluationResult = evaluator.evaluate(slides_html, design_system)

    fidelity_scores = state.get("fidelity_scores", [])
    fidelity_scores.append(result.score)

    logger.info(
        "design_evaluator.complete",
        score=round(result.score, 3),
        passed=result.passed,
        issues_count=sum(len(s.get("issues", [])) for s in result.per_slide),
        iteration=iteration + 1,
        category_scores=result.category_scores,
    )

    return {
        "qa_feedback": {
            "passed": result.passed,
            "score": result.score,
            "fix_instructions": result.fix_instructions,
            "category_scores": result.category_scores,
            "per_slide_scores": [
                {"index": s["index"], "score": s["score"]}
                for s in result.per_slide
            ],
        },
        "fidelity_scores": fidelity_scores,
        "qa_iterations": iteration + 1,
        "current_phase": "qa",
    }
