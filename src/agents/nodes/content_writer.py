"""Content Writer Agent - writes actual text content for each slide."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.language import output_language_instruction
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "content_writer"


async def content_writer(state: DocuMindState) -> dict:
    """Write detailed, compelling text content for each slide."""
    logger.info("content_writer.start")

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    narrative = _as_dict(state.get("narrative_plan"))
    research = state.get("research_data")

    context = (
        f"User request: {state['user_query']}\n\n"
        f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}\n\n"
        f"Narrative plan:\n{json.dumps(narrative, ensure_ascii=False, indent=2)}"
    )
    if research:
        context += f"\n\nResearch data:\n{json.dumps(research, ensure_ascii=False, indent=2)}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)
    slide_contents = _parse_json_array(response.content)

    logger.info("content_writer.complete", slides_written=len(slide_contents))
    return {"slide_contents": slide_contents, "current_phase": "planning"}


def _parse_json_array(content: str) -> list:
    parsed = parse_llm_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("slides") or parsed.get("slide_contents") or [parsed]
    return [_as_dict(item) for item in _as_list(parsed)]


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
