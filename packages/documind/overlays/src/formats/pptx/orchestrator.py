"""SDK PPTX LangGraph pipeline.

This graph keeps PPTX generation but excludes browser capture, visual QA, QA
retry loops, and diagrams/Graphviz rendering. Image-model assets are still
allowed through the SDK visual asset planner.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.nodes.research import research_agent
from src.formats.pptx.agents.nodes.html_generator import html_generator_parallel
from src.formats.pptx.agents.nodes.render_convert import render_and_convert
from src.formats.pptx.agents.nodes.unified_planner import unified_planner
from src.formats.pptx.agents.nodes.visual_asset_planner import visual_asset_planner
from src.schemas.agents import DocuMindState


def _init_master_context(state: DocuMindState) -> dict:
    from src.formats.pptx.master_context import build_master_context

    locked_context = state.get("_locked_master_context")
    if locked_context:
        return {"master_context": locked_context, "current_phase": "init"}

    template_bytes = state.get("_template_bytes")
    template_filename = state.get("_template_filename", "template.pptx")
    seed = state.get("user_query", "") + "|" + state.get("session_id", "")
    master_context = build_master_context(
        template_bytes=template_bytes,
        template_filename=template_filename,
        seed=seed,
    )
    stored_template_analysis = state.get("_template_analysis", {})
    if stored_template_analysis and master_context.get("template"):
        master_context["template"]["visual_analysis"] = stored_template_analysis.get(
            "visual_analysis",
            {},
        )
    return {"master_context": master_context, "current_phase": "init"}


def _route_research(state: DocuMindState) -> str:
    return "research" if state.get("needs_research", False) else "plan"


def _export_node(state: DocuMindState) -> dict:
    return {
        "current_phase": "done",
        "validation_result": {"passed": True, "score": None, "sdk_qa_disabled": True},
        "qa_feedback": {"passed": True, "sdk_qa_disabled": True},
        "qa_iterations": 0,
    }


def build_pptx_pipeline() -> StateGraph:
    graph = StateGraph(DocuMindState)
    graph.add_node("init_context", _init_master_context)
    graph.add_node("research", research_agent)
    graph.add_node("plan", unified_planner)
    graph.add_node("visual_asset_plan", visual_asset_planner)
    graph.add_node("generate_html", html_generator_parallel)
    graph.add_node("render_convert", render_and_convert)
    graph.add_node("export", _export_node)

    graph.set_entry_point("init_context")
    graph.add_conditional_edges(
        "init_context",
        _route_research,
        {"research": "research", "plan": "plan"},
    )
    graph.add_edge("research", "plan")
    graph.add_edge("plan", "visual_asset_plan")
    graph.add_edge("visual_asset_plan", "generate_html")
    graph.add_edge("generate_html", "render_convert")
    graph.add_edge("render_convert", "export")
    graph.add_edge("export", END)
    return graph


def compile_pptx_pipeline():
    return build_pptx_pipeline().compile()
