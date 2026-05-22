"""PPTX Visual Asset Planner - determines image/chart/icon requirements per slide."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "asset_planner"
FORMAT_ID = "pptx"


async def visual_asset_planner(state: DocuMindState) -> dict:
    """Plan visual assets (images, charts, icons) for each slide."""
    logger.info("asset_planner.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    narrative = state.get("narrative_plan", {})
    design = state.get("design_system", {})

    context = f"Narrative plan:\n{json.dumps(narrative, ensure_ascii=False, indent=2)}\n\nDesign colors: {json.dumps(design.get('color_tokens', {}), ensure_ascii=False)}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        asset_requirements = json.loads(response.content)
    except json.JSONDecodeError:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        try:
            asset_requirements = json.loads(content.strip())
        except json.JSONDecodeError:
            logger.warning("asset_planner.json_truncated", fallback="empty_list")
            asset_requirements = []

    logger.info("asset_planner.complete", assets_count=len(asset_requirements))
    return {"asset_requirements": asset_requirements, "current_phase": "designing"}
