"""Research Agent - gathers external data via web search when needed."""

from __future__ import annotations

import json
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.infrastructure.web_search import search_web
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "research"


async def research_agent(state: DocuMindState) -> dict:
    """Execute research to gather external data for the presentation."""
    logger.info("research_agent.start", query=state["user_query"])

    if not state.get("needs_research", False):
        return {"research_data": None, "current_phase": "planning"}

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)
    now = datetime.now()
    current_year_month = now.strftime("%Y-%m")
    current_year = now.strftime("%Y")
    query = state["user_query"]

    search_queries = [
        f"{query} latest data statistics {current_year_month}",
        f"{query} market trend case study {current_year}",
        f"{query} benchmark report {current_year} {current_year_month}",
    ]
    search_results: list[dict[str, str]] = []
    for search_query in search_queries:
        try:
            search_results.extend(await search_web(search_query, max_results=5))
        except Exception as exc:
            logger.warning("research_agent.search_failed", query=search_query, error=str(exc)[:200])

    deduped_results = []
    seen_urls = set()
    for item in search_results:
        url = item.get("url", "")
        if url and url not in seen_urls:
            deduped_results.append(item)
            seen_urls.add(url)
        if len(deduped_results) >= 10:
            break

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"""User request: {state['user_query']}

Current year-month: {current_year_month}
{output_language_instruction(state.get("output_language", "ko_mixed"))}

No-key web search results from multiple providers:
{json.dumps(deduped_results, ensure_ascii=False, indent=2)}

Research key data, statistics, and case studies needed for this presentation.
Prioritize the search results above and current data from {current_year_month}.
Output in JSON format."""),
    ]

    response = await llm.ainvoke(messages)

    try:
        parsed = parse_llm_json(response.content, fallback={})
    except (json.JSONDecodeError, TypeError):
        parsed = {}

    research_data = _normalize_research_data(parsed, response.content)

    research_data["search_results"] = deduped_results
    research_data["current_year_month"] = current_year_month
    research_data["search_queries"] = search_queries

    logger.info(
        "research_agent.complete",
        search_results=len(deduped_results),
        facts=len(research_data["facts"]),
        statistics=len(research_data["statistics"]),
    )
    return {"research_data": research_data, "current_phase": "planning"}


def _normalize_research_data(parsed: object, raw_response: str) -> dict:
    """Keep research_data dict-shaped even when the model returns a list or prose."""
    if isinstance(parsed, dict):
        data = parsed
    elif isinstance(parsed, list):
        data = {"facts": parsed}
    else:
        data = {"raw_research": raw_response}

    normalized = {
        "facts": _as_list(data.get("facts")),
        "statistics": _as_list(data.get("statistics")),
        "case_studies": _as_list(data.get("case_studies")),
        "trends": _as_list(data.get("trends")),
        "sources": _as_list(data.get("sources")),
    }
    if raw_response and not normalized["facts"] and not normalized["statistics"]:
        normalized["raw_research"] = raw_response
    return normalized


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
