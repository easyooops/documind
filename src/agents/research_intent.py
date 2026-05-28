"""Intent-based gating for external web research."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent
from src.core.logging import get_logger
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)


@dataclass(frozen=True)
class ResearchIntent:
    """Result of query intent analysis for research execution."""

    needs_research: bool
    intent_label: str
    reason: str


_RESEARCH_POSITIVE_KEYWORDS = (
    "검색",
    "외부 자료",
    "외부자료",
    "레퍼런스",
    "참고 자료",
    "참고자료",
    "최신",
    "통계",
    "수치",
    "근거",
    "출처",
    "리서치",
    "조사",
    "시장",
    "트렌드",
    "동향",
    "뉴스",
    "사례",
    "비교",
    "벤치마크",
    "백서",
    "보고서",
    "latest",
    "recent",
    "statistics",
    "data",
    "source",
    "search",
    "external source",
    "reference",
    "evidence",
    "research",
    "survey",
    "market",
    "trend",
    "news",
    "case study",
    "benchmark",
    "white paper",
    "industry report",
)

_RESEARCH_NEGATIVE_KEYWORDS = (
    "초안",
    "요약",
    "교정",
    "윤문",
    "번역",
    "말투",
    "문장 다듬",
    "템플릿에 맞춰",
    "형식만",
    "format only",
    "rewrite",
    "paraphrase",
    "summarize",
    "proofread",
    "translate",
    "tone",
)


async def analyze_research_intent(query: str) -> ResearchIntent:
    """Decide if external search is needed for the user's query via LLM."""
    text = (query or "").strip().lower()
    if not text:
        return ResearchIntent(False, "empty_query", "empty query")

    prompt = (
        "You are an intent classifier for document-generation workflows.\n"
        "Decide whether external web search is required before writing.\n\n"
        "Return JSON only with keys:\n"
        '- needs_research: boolean\n'
        '- intent_label: short snake_case string\n'
        '- reason: one short sentence\n\n'
        "Decision policy:\n"
        "- needs_research=true when user asks for latest facts/statistics/sources, market data, "
        "benchmarks, comparisons, references, or explicitly requests web/external search.\n"
        "- needs_research=false for pure writing tasks like rewrite, summarize, translate, "
        "proofread, tone/style edits, or formatting without evidence needs.\n\n"
        f"User query: {query}"
    )
    try:
        llm = get_llm_for_agent("research")
        response = await llm.ainvoke(
            [
                SystemMessage(content="Classify intent and output strict JSON only."),
                HumanMessage(content=prompt),
            ]
        )
        parsed = parse_llm_json(getattr(response, "content", "") or "", fallback={})
        if isinstance(parsed, dict):
            needs_research = bool(parsed.get("needs_research", False))
            intent_label = str(parsed.get("intent_label", "")).strip() or (
                "evidence_required" if needs_research else "writing_only"
            )
            reason = str(parsed.get("reason", "")).strip() or (
                "llm intent classification"
            )
            return ResearchIntent(needs_research, intent_label, reason)
    except Exception as exc:
        logger.warning("research_intent.llm_failed", error=str(exc)[:160])

    return _fallback_keyword_intent(text)


def _fallback_keyword_intent(text: str) -> ResearchIntent:
    """Fallback classifier when LLM intent inference is unavailable."""

    has_positive = any(keyword in text for keyword in _RESEARCH_POSITIVE_KEYWORDS)
    has_negative = any(keyword in text for keyword in _RESEARCH_NEGATIVE_KEYWORDS)

    if has_positive and not has_negative:
        return ResearchIntent(True, "evidence_required", "query asks for evidence or recent facts")
    if has_negative and not has_positive:
        return ResearchIntent(False, "writing_only", "query is writing/transformation oriented")

    question_like = "?" in text or text.startswith(("왜", "어떻게", "what", "how", "why"))
    if question_like and ("최신" in text or "latest" in text):
        return ResearchIntent(True, "question_with_freshness", "question asks for fresh information")

    # Default to skipping search unless explicit evidence intent exists.
    return ResearchIntent(False, "internal_generation", "no explicit external research intent")
