"""PPTX LangGraph pipeline v2 — 4-Phase OOXML Rule-Sets Architecture.

Phase 0: Init (master context + rule-sets loading)
Phase A: Unified Planning (research + OOXML-aware blueprint)
Phase B: Constrained HTML Generation (parallel per-slide, rule-constrained)
Phase C: Render + Design Quality Evaluation (rule checks plus visual LLM Judge)
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.nodes.research import research_agent
from src.formats.pptx.agents.nodes.design_evaluator import design_quality_evaluator
from src.formats.pptx.agents.nodes.html_generator import html_generator_parallel
from src.formats.pptx.agents.nodes.render_convert import render_and_convert
from src.formats.pptx.agents.nodes.unified_planner import unified_planner
from src.formats.pptx.agents.nodes.visual_asset_planner import visual_asset_planner
from src.formats.pptx.agents.nodes.vlm_qa import vlm_quality_gate
from src.schemas.agents import DocuMindState


def _init_master_context(state: DocuMindState) -> dict:
    """Phase 0: Load master context (template or default)."""
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
            "visual_analysis", {}
        )
    return {"master_context": master_context, "current_phase": "init"}


def _route_research(state: DocuMindState) -> str:
    """Skip research if not needed."""
    if state.get("needs_research", False):
        return "research"
    return "plan"


def _route_qa(state: DocuMindState) -> str:
    """Route based on design quality evaluation result."""
    qa_feedback = state.get("qa_feedback", {})
    if isinstance(qa_feedback, dict) and qa_feedback.get("passed", False):
        return "export"

    iterations = state.get("qa_iterations", 0)
    if iterations >= 2:
        return "export"

    return "generate_html"


async def _quality_assessment(state: DocuMindState) -> dict:
    """Run rule-based and visual QA as one user-visible quality step."""
    rule_output = await design_quality_evaluator(state)
    visual_output = await vlm_quality_gate({**state, **rule_output})
    return {
        **rule_output,
        **visual_output,
        "current_phase": "qa",
    }


def _export_node(state: DocuMindState) -> dict:
    """Final export node — marks completion."""
    return {"current_phase": "done"}


def build_pptx_pipeline() -> StateGraph:
    """Construct the v2 PPTX pipeline (4-Phase architecture)."""
    graph = StateGraph(DocuMindState)

    # Phase 0: Init
    graph.add_node("init_context", _init_master_context)

    # Phase A: Planning
    graph.add_node("research", research_agent)
    graph.add_node("plan", unified_planner)
    graph.add_node("visual_asset_plan", visual_asset_planner)

    # Phase B: HTML Generation
    graph.add_node("generate_html", html_generator_parallel)

    # Phase C: Render + Design Quality Evaluation
    graph.add_node("render_convert", render_and_convert)
    graph.add_node("quality_assessment", _quality_assessment)
    graph.add_node("export", _export_node)

    # Entry
    graph.set_entry_point("init_context")

    # Phase 0 → A
    graph.add_conditional_edges("init_context", _route_research, {
        "research": "research",
        "plan": "plan",
    })
    graph.add_edge("research", "plan")

    # Phase A → B
    graph.add_edge("plan", "visual_asset_plan")
    graph.add_edge("visual_asset_plan", "generate_html")

    # Phase B → C
    graph.add_edge("generate_html", "render_convert")
    graph.add_edge("render_convert", "quality_assessment")

    # QA routing (pass → export, fail → retry HTML generation)
    graph.add_conditional_edges("quality_assessment", _route_qa, {
        "export": "export",
        "generate_html": "generate_html",
    })

    graph.add_edge("export", END)

    return graph


def compile_pptx_pipeline():
    """Compile the v2 PPTX pipeline graph for execution."""
    graph = build_pptx_pipeline()
    return graph.compile()
