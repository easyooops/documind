"""PPTX LangGraph pipeline — orchestrates shared planning + PPTX-specific agents."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.nodes.audience import audience_analyzer
from src.agents.nodes.content_writer import content_writer
from src.agents.nodes.narrative import narrative_architect
from src.agents.nodes.research import research_agent
from src.formats.pptx.agents.nodes.asset_planner import visual_asset_planner
from src.formats.pptx.agents.nodes.code_generator import code_agent_parallel
from src.formats.pptx.agents.nodes.consistency import consistency_enforcer
from src.formats.pptx.agents.nodes.conversion import conversion_node
from src.formats.pptx.agents.nodes.layout_composer import layout_composer
from src.formats.pptx.agents.nodes.qa_critic import qa_critic
from src.formats.pptx.agents.nodes.style_director import style_director
from src.formats.pptx.agents.nodes.template_analysis import template_analyzer
from src.formats.pptx.agents.nodes.validation import validation_agent
from src.schemas.agents import DocuMindState


def _route_research(state: DocuMindState) -> str:
    """Skip research if not needed."""
    if state.get("needs_research", False):
        return "research"
    return "narrative"


def _route_to_design(state: DocuMindState) -> str:
    """Route to template analysis if a template is provided."""
    if state.get("template_provided", False):
        return "template_analysis"
    return "layout_compose"


def _route_validation(state: DocuMindState) -> str:
    """Route based on validation result — pass to conversion or retry code generation.

    Safety: total retries (validation + QA) are capped to prevent infinite loops.
    """
    result = _as_dict(state.get("validation_result"))
    if result.get("passed", False):
        return "convert"

    if result.get("_fallback") or result.get("_parse_fallback"):
        return "convert"

    retry = state.get("retry_count", 0)
    qa_iterations = state.get("qa_iterations", 0)
    total_retries = retry + qa_iterations
    if total_retries >= 2:
        return "convert"
    return "code_generate"


def _route_qa(state: DocuMindState) -> str:
    """Route based on QA fidelity — pass to export or retry from code generation.

    With DSL architecture, PPTX is built directly from DSL so fidelity is
    inherently high. Only retry if score is very low.
    """
    scores = _as_list(state.get("fidelity_scores"))
    iterations = state.get("qa_iterations", 0)

    if scores and scores[-1] >= 0.88:
        return "export"
    if iterations >= 1:
        return "export"
    return "code_generate"


def _export_node(state: DocuMindState) -> dict:
    """Final export node — marks completion."""
    return {"current_phase": "done"}


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def build_pptx_pipeline() -> StateGraph:
    """Construct the PPTX-specific LangGraph pipeline."""
    graph = StateGraph(DocuMindState)

    # Shared planning phase
    graph.add_node("research", research_agent)
    graph.add_node("narrative", narrative_architect)
    graph.add_node("content_writer", content_writer)
    graph.add_node("audience", audience_analyzer)

    # PPTX design phase
    graph.add_node("template_analysis", template_analyzer)
    graph.add_node("layout_compose", layout_composer)
    graph.add_node("style_direct", style_director)
    graph.add_node("asset_plan", visual_asset_planner)

    # PPTX generation phase
    graph.add_node("code_generate", code_agent_parallel)
    graph.add_node("consistency_check", consistency_enforcer)
    graph.add_node("validate", validation_agent)

    # PPTX conversion & QA phase
    graph.add_node("convert", conversion_node)
    graph.add_node("qa_critic", qa_critic)
    graph.add_node("export", _export_node)

    # Entry: always start at research (self-skips if not needed)
    graph.set_entry_point("research")

    # Planning edges
    graph.add_edge("research", "narrative")
    graph.add_edge("narrative", "content_writer")
    graph.add_edge("content_writer", "audience")

    # Design routing
    graph.add_conditional_edges("audience", _route_to_design, {
        "template_analysis": "template_analysis",
        "layout_compose": "layout_compose",
    })
    graph.add_edge("template_analysis", "layout_compose")
    graph.add_edge("layout_compose", "style_direct")
    graph.add_edge("style_direct", "asset_plan")

    # Generation edges
    graph.add_edge("asset_plan", "code_generate")
    graph.add_edge("code_generate", "consistency_check")
    graph.add_edge("consistency_check", "validate")

    # Validation routing (pass -> convert, fail -> retry code_generate)
    graph.add_conditional_edges("validate", _route_validation, {
        "convert": "convert",
        "code_generate": "code_generate",
    })

    # QA routing — if fidelity low, go back to code_generate (not convert)
    graph.add_edge("convert", "qa_critic")
    graph.add_conditional_edges("qa_critic", _route_qa, {
        "export": "export",
        "code_generate": "code_generate",
    })

    graph.add_edge("export", END)

    return graph


def compile_pptx_pipeline():
    """Compile the PPTX pipeline graph for execution."""
    graph = build_pptx_pipeline()
    return graph.compile()
