"""PPTX Layout Composer - determines spatial arrangement for each slide."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "layout_composer"
FORMAT_ID = "pptx"


async def layout_composer(state: DocuMindState) -> dict:
    """Design spatial layout specifications for each slide."""
    logger.info("layout_composer.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    narrative = state.get("narrative_plan", {})
    audience = state.get("audience_profile", {})
    template = state.get("template_profile")

    context = f"Narrative plan:\n{json.dumps(narrative, ensure_ascii=False, indent=2)}\n\nAudience profile:\n{json.dumps(audience, ensure_ascii=False, indent=2)}"
    if template:
        context += f"\n\nTemplate profile:\n{json.dumps(template, ensure_ascii=False, indent=2)}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        layout_specs = json.loads(response.content)
    except json.JSONDecodeError:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        layout_specs = json.loads(content.strip())

    logger.info("layout_composer.complete", layouts_count=len(layout_specs))
    return {"layout_specs": layout_specs, "current_phase": "designing"}
