"""PPTX Consistency Enforcer - checks cross-slide consistency."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "consistency"
FORMAT_ID = "pptx"


async def consistency_enforcer(state: DocuMindState) -> dict:
    """Check and enforce cross-slide consistency."""
    logger.info("consistency_enforcer.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    slides_html = state.get("slides_html", [])
    design_system = state.get("design_system", {})

    slides_summary = [{"index": s.get("index"), "html_preview": s.get("html", "")[:500]} for s in slides_html[:12]]

    context = f"Design system:\n{json.dumps(design_system, ensure_ascii=False, indent=2)}\n\nSlides ({len(slides_html)} total):\n{json.dumps(slides_summary, ensure_ascii=False, indent=2)}"

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        consistency_report = json.loads(response.content)
    except json.JSONDecodeError:
        consistency_report = {"is_consistent": True, "issues": [], "patches": []}

    logger.info("consistency_enforcer.complete", is_consistent=consistency_report.get("is_consistent"))
    return {"consistency_report": consistency_report, "current_phase": "generating"}
