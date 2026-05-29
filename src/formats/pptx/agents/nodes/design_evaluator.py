"""Deterministic slide checks that complement the visual LLM Judge."""

from __future__ import annotations

from src.core.logging import get_logger
from src.formats.pptx.rulesets import get_ruleset
from src.formats.pptx.rulesets.validator import DesignQualityEvaluator, EvaluationResult
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)


async def design_quality_evaluator(state: DocuMindState) -> dict:
    """Evaluate HTML structure and expose its result to the visual QA node."""
    iteration = state.get("qa_iterations", 0)
    logger.info("design_evaluator.start", iteration=iteration)

    slides_html = state.get("slides_html", [])
    design_system = state.get("design_system", {})
    if not slides_html:
        return {
            "rule_based_feedback": {"passed": True, "fix_instructions": []},
            "rule_based_scores": state.get("rule_based_scores", []) + [0.0],
            "current_phase": "qa",
        }

    evaluator = DesignQualityEvaluator(get_ruleset())
    result: EvaluationResult = evaluator.evaluate(slides_html, design_system)
    feedback = {
        "passed": result.passed,
        "score": result.score,
        "fix_instructions": result.fix_instructions,
        "issues": _issue_summaries(result.per_slide),
        "per_slide_issues": _per_slide_issues(result.per_slide),
        "issues_count": sum(len(slide.get("issues", [])) for slide in result.per_slide),
        "category_scores": result.category_scores,
        "per_slide_scores": [
            {"index": slide["index"], "score": slide["score"]}
            for slide in result.per_slide
        ],
    }

    logger.info(
        "design_evaluator.complete",
        score=round(result.score, 3),
        passed=result.passed,
        issues_count=sum(len(slide.get("issues", [])) for slide in result.per_slide),
        iteration=iteration,
        category_scores=result.category_scores,
    )
    return {
        "rule_based_feedback": feedback,
        "rule_based_scores": state.get("rule_based_scores", []) + [result.score],
        "qa_feedback": feedback,
        "current_phase": "qa",
    }


def _issue_summaries(per_slide: list[dict]) -> list[str]:
    summaries = []
    for slide in per_slide:
        idx = slide.get("index")
        for issue in slide.get("issues", []):
            message = str(issue.get("message") or "").strip()
            if not message:
                continue
            severity = str(issue.get("severity") or "issue")
            category = str(issue.get("category") or "quality")
            summaries.append(f"Slide {idx}: [{severity}/{category}] {message}")
    return summaries


def _per_slide_issues(per_slide: list[dict]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for slide in per_slide:
        try:
            idx = int(slide.get("index"))
        except (TypeError, ValueError):
            continue
        grouped[idx] = [
            f"[{issue.get('severity', 'issue')}/{issue.get('category', 'quality')}] {issue.get('message', '')}"
            for issue in slide.get("issues", [])
            if issue.get("message")
        ]
    return grouped
