"""PPTX Consistency Enforcer - checks cross-slide consistency."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "consistency"
FORMAT_ID = "pptx"


async def consistency_enforcer(state: DocuMindState) -> dict:
    """Check and enforce cross-slide consistency."""
    logger.info("consistency_enforcer.start")

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)

    slides_html = [_as_dict(item) for item in _as_list(state.get("slides_html"))]
    slides_dsl = [_as_dict(item) for item in _as_list(state.get("slides_dsl"))]
    design_system = _as_dict(state.get("design_system"))
    layout_specs = [_as_dict(item) for item in _as_list(state.get("layout_specs"))]

    slides_summary = [
        {"index": slide.get("index"), "html_preview": slide.get("html", "")[:500]}
        for slide in slides_html[:12]
    ]
    dsl_summary = [
        {
            "index": s.get("index"),
            "slide_type": s.get("slide_type"),
            "shapes": [
                {
                    "id": shape.get("id"),
                    "role": shape.get("role"),
                    "position": shape.get("position"),
                    "fill": shape.get("fill"),
                    "font": [
                        run
                        for para in (_as_dict(item) for item in _as_list(shape.get("text")))
                        for run in _as_list(para.get("runs"))
                    ][:3],
                }
                for shape in (_as_dict(item) for item in _as_list(s.get("shapes"))[:14])
            ],
        }
        for s in slides_dsl[:12]
    ]

    context = (
        f"Design system:\n{json.dumps(design_system, ensure_ascii=False, indent=2)}"
        f"\n\nLayout specs:\n{json.dumps(layout_specs[:12], ensure_ascii=False, indent=2)}"
        f"\n\nSlides DSL ({len(slides_dsl)} total):\n{json.dumps(dsl_summary, ensure_ascii=False, indent=2)}"
        f"\n\nHTML preview excerpts ({len(slides_html)} total):\n{json.dumps(slides_summary, ensure_ascii=False, indent=2)}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)

    try:
        consistency_report = parse_llm_json(response.content)
    except json.JSONDecodeError:
        consistency_report = {"is_consistent": True, "issues": [], "patches": []}

    consistency_report = _as_dict(consistency_report) or {
        "is_consistent": True,
        "issues": [],
        "patches": [],
    }

    logger.info(
        "consistency_enforcer.complete",
        is_consistent=consistency_report.get("is_consistent"),
    )
    return {"consistency_report": consistency_report, "current_phase": "generating"}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
