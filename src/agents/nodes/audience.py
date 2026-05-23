"""Audience Analyzer - determines tone, complexity, and visual density."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.language import output_language_instruction
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "audience"


async def audience_analyzer(state: DocuMindState) -> dict:
    """Analyze the target audience and calibrate presentation parameters."""
    logger.info("audience_analyzer.start")

    llm = get_llm_for_agent(AGENT_NAME)
    system_prompt = load_agent_prompt(AGENT_NAME)

    narrative = _as_dict(state.get("narrative_plan"))
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"User request: {state['user_query']}\n\n"
            f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}\n\n"
            f"Narrative title: {narrative.get('title', '')}\n"
            f"Slide count: {narrative.get('total_slides', 0)}"
        )),
    ]

    response = await llm.ainvoke(messages)

    audience_profile = _as_dict(parse_llm_json(response.content)) or {
        "audience_type": "business",
        "tone": "professional",
        "complexity": "executive",
        "persuasion_style": "evidence-led",
    }

    logger.info("audience_analyzer.complete", audience_type=audience_profile.get("audience_type"))
    return {"audience_profile": audience_profile, "current_phase": "planning"}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}
