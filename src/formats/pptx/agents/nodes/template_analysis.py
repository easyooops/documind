"""PPTX Template Analyzer - parses uploaded .pptx and extracts design system."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "template_analysis"
FORMAT_ID = "pptx"


async def template_analyzer(state: DocuMindState) -> dict:
    """Analyze the uploaded PPTX template to extract design tokens."""
    logger.info("template_analysis.start")

    template_id = state.get("template_id")
    if not template_id:
        return {"template_profile": None, "current_phase": "designing"}

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Template ID: {template_id}\nPlease generate the analysis result."),
    ]

    response = await llm.ainvoke(messages)

    try:
        template_profile = json.loads(response.content)
    except json.JSONDecodeError:
        template_profile = {"visual_description": response.content}

    logger.info("template_analysis.complete")
    return {"template_profile": template_profile, "current_phase": "designing"}
