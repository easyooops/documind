"""Research Agent - gathers external data via web search when needed."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "research"


async def research_agent(state: DocuMindState) -> dict:
    """Execute research to gather external data for the presentation."""
    logger.info("research_agent.start", query=state["user_query"])

    if not state.get("needs_research", False):
        return {"research_data": None, "current_phase": "planning"}

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"""User request: {state['user_query']}

Research key data, statistics, and case studies needed for this presentation.
Output in JSON format."""),
    ]

    response = await llm.ainvoke(messages)

    try:
        research_data = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        research_data = {"raw_research": response.content, "facts": [], "statistics": []}

    logger.info("research_agent.complete")
    return {"research_data": research_data, "current_phase": "planning"}
