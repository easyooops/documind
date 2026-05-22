"""PPTX Style Director - creates the complete visual design system."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "style_director"
FORMAT_ID = "pptx"


async def style_director(state: DocuMindState) -> dict:
    """Create the visual design system with PPTX-safe CSS tokens."""
    logger.info("style_director.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    audience = state.get("audience_profile", {})
    template = state.get("template_profile")
    layouts = state.get("layout_specs", [])

    context = f"Audience profile:\n{json.dumps(audience, ensure_ascii=False, indent=2)}\n\nLayout summary: {len(layouts)} slides"
    if template:
        context += f"\nTemplate colors: {template.get('color_palette', {})}"
        context += f"\nKeywords: {template.get('design_keywords', [])}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        design_system = json.loads(response.content)
    except json.JSONDecodeError:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        design_system = json.loads(content.strip())

    logger.info("style_director.complete")
    return {"design_system": design_system, "current_phase": "designing"}
