"""Narrative Architect - designs story structure and slide flow."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.language import output_language_instruction
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "narrative"


async def narrative_architect(state: DocuMindState) -> dict:
    """Design the narrative structure and slide-by-slide flow."""
    logger.info("narrative_architect.start")

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    context_parts = [f"User request: {state['user_query']}"]
    context_parts.append(output_language_instruction(state.get("output_language", "ko_mixed")))
    if state.get("research_data"):
        context_parts.append(f"\nResearch data:\n{json.dumps(state['research_data'], ensure_ascii=False, indent=2)}")
    if state.get("conversation_history"):
        context_parts.append(f"\nConversation history:\n{json.dumps(state['conversation_history'][-5:], ensure_ascii=False)}")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(context_parts)),
    ]

    response = await llm.ainvoke(messages)
    narrative_plan = _normalize_narrative_plan(_parse_json(response.content), state["user_query"])

    logger.info("narrative_architect.complete", total_slides=narrative_plan.get("total_slides"))
    return {"narrative_plan": narrative_plan, "current_phase": "planning"}


def _parse_json(content: str) -> dict:
    return parse_llm_json(content)


def _normalize_narrative_plan(value: object, title: str) -> dict:
    if isinstance(value, dict):
        slides = value.get("slides", [])
        value["slides"] = [_as_dict(item) for item in _as_list(slides)]
        value["total_slides"] = value.get("total_slides") or len(value["slides"])
        value["title"] = value.get("title") or title[:80]
        return value
    if isinstance(value, list):
        slides = [_as_dict(item) for item in value]
        return {"title": title[:80], "total_slides": len(slides), "slides": slides}
    return {"title": title[:80], "total_slides": 0, "slides": []}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
