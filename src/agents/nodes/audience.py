"""Audience Analyzer - determines tone, complexity, and visual density."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "audience"


async def audience_analyzer(state: DocuMindState) -> dict:
    """Analyze the target audience and calibrate presentation parameters."""
    logger.info("audience_analyzer.start")

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    narrative = state.get("narrative_plan", {})
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"User request: {state['user_query']}\n\nNarrative title: {narrative.get('title', '')}\nSlide count: {narrative.get('total_slides', 0)}"),
    ]

    response = await llm.ainvoke(messages)

    try:
        audience_profile = json.loads(response.content)
    except json.JSONDecodeError:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        audience_profile = json.loads(content.strip())

    logger.info("audience_analyzer.complete", audience_type=audience_profile.get("audience_type"))
    return {"audience_profile": audience_profile, "current_phase": "planning"}
