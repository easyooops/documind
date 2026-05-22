"""Content Writer Agent - writes actual text content for each slide."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "content_writer"


async def content_writer(state: DocuMindState) -> dict:
    """Write detailed, compelling text content for each slide."""
    logger.info("content_writer.start")

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    narrative = state.get("narrative_plan", {})
    research = state.get("research_data")

    context = f"User request: {state['user_query']}\n\nNarrative plan:\n{json.dumps(narrative, ensure_ascii=False, indent=2)}"
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
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
