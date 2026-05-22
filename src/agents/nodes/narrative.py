"""Narrative Architect - designs story structure and slide flow."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "narrative"


async def narrative_architect(state: DocuMindState) -> dict:
    """Design the narrative structure and slide-by-slide flow."""
    logger.info("narrative_architect.start")

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    context_parts = [f"User request: {state['user_query']}"]
    if state.get("research_data"):
        context_parts.append(f"\nResearch data:\n{json.dumps(state['research_data'], ensure_ascii=False, indent=2)}")
    if state.get("conversation_history"):
        context_parts.append(f"\nConversation history:\n{json.dumps(state['conversation_history'][-5:], ensure_ascii=False)}")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(context_parts)),
    ]

    response = await llm.ainvoke(messages)
    narrative_plan = _parse_json(response.content)

    logger.info("narrative_architect.complete", total_slides=narrative_plan.get("total_slides"))
    return {"narrative_plan": narrative_plan, "current_phase": "planning"}


def _parse_json(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
