"""Reusable LangGraph flow for richly designed native document formats."""

# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from src.agents.loader import get_llm_for_agent
from src.agents.nodes.research import research_agent
from src.agents.research_intent import analyze_research_intent
from src.core.config import settings
from src.core.logging import get_logger
from src.infrastructure.web_search import search_web
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json
from src.utils.language import output_language_instruction

from .quality import evaluate_document
from .spec import (
    as_list,
    has_planned_content,
    infer_document_intent,
    is_content_removal_request,
    merge_revision_spec,
    normalize_design_system,
    normalize_document_intent,
    normalize_document_spec,
)

logger = get_logger(__name__)
POPULATED_TEMPLATE_FORMATS = {"docx", "hwp", "xlsx", "md", "pdf"}


def build_native_document_pipeline(format_id: str, renderer_class: type, ruleset: dict) -> StateGraph:
    """Build a template-led document flow with native render and quality feedback."""
    graph = StateGraph(DocuMindState)

    async def init_document_context(state: DocuMindState) -> dict:
        return {
            "format_rules": ruleset,
            "master_context": {
                "format": format_id,
                "architecture": "template-led-native-document",
                "template_provided": bool(state.get("_template_bytes")),
                "ruleset_version": ruleset["version"],
            },
            "current_phase": "planning",
        }

    async def interpret_request(state: DocuMindState) -> dict:
        """Determine the real document archetype before selecting a template."""
        uploaded_template = state.get("_template_analysis", {})
        baseline = infer_document_intent(
            state.get("user_query", ""),
            state.get("output_language", "ko_mixed"),
            ruleset,
            state.get("visual_intent", {}),
        )
        if format_id in POPULATED_TEMPLATE_FORMATS and state.get("_template_bytes"):
            return {
                "document_intent": {
                    **baseline,
                    "template_family": state.get("_template_filename", "Uploaded native template"),
                    "institutional_style": "uploaded_template",
                    "template_search_queries": [],
                    "template_mode": "populate_existing_form",
                },
                "current_phase": "planning",
            }
        prompt = (
            "You are a document-intent analyst. Decide which real-world document template "
            "best fits the user's request before writing content. Return JSON only with keys "
            "document_kind, template_family, institutional_style, locale_market, "
            "template_search_queries, and content_focus.\n\n"
            "Important rules:\n"
            "- Choose the user's intended artifact, not a literal title copied from the request.\n"
            "- For XLSX and PDF, infer the exact artifact from the request; do not assume a status or weekly report.\n"
            "- Select public/corporate/editorial references only when appropriate for the inferred artifact and language.\n"
            "- If an uploaded native template exists, its document type and fields are "
            "authoritative; select content to populate that supplied form instead of choosing "
            "another external form.\n"
            "- Attached images are content evidence only unless the user explicitly asks to copy "
            "their design or use them as a template.\n"
            f"Target format: {ruleset['display_name']}\n"
            f"User request: {state.get('user_query', '')}\n"
            f"Uploaded native template: {json.dumps(uploaded_template, ensure_ascii=False)[:3500]}\n"
            f"Attachment evidence: {json.dumps(state.get('visual_intent', {}), ensure_ascii=False)[:2500]}\n"
            f"Heuristic baseline: {json.dumps(baseline, ensure_ascii=False)}\n"
            f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}"
        )
        try:
            llm = get_llm_for_agent("document_intent", format_id=format_id)
            response = await llm.ainvoke(
                [
                    SystemMessage(content="Infer document intent and template market as valid JSON."),
                    HumanMessage(content=prompt),
                ]
            )
            raw = parse_llm_json(response.content, fallback={})
        except Exception as exc:
            logger.warning("native_document_intent.fallback", format=format_id, error=str(exc)[:120])
            raw = {}
        intent = normalize_document_intent(raw, baseline)
        return {"document_intent": intent, "current_phase": "planning"}

    async def template_design(state: DocuMindState) -> dict:
        locked = state.get("_locked_design_system")
        if locked:
            return {"design_system": locked, "current_phase": "designing"}

        references = []
        template_analysis = state.get("_template_analysis", {})
        document_intent = state.get("document_intent", {})
        if format_id in POPULATED_TEMPLATE_FORMATS and state.get("_template_bytes"):
            design = normalize_design_system(
                {},
                ruleset,
                document_intent=document_intent,
                template_provided=True,
            )
            design.update(
                {
                    "template_name": state.get("_template_filename", "Uploaded native template"),
                    "design_rationale": (
                        "The uploaded native template is preserved as the authoritative layout; "
                        "generated content is populated into its existing fields."
                    ),
                    "template_source": "uploaded",
                    "native_template_mode": "populate_existing_form",
                }
            )
            return {
                "design_system": design,
                "template_profile": design,
                "template_references": [],
                "current_phase": "designing",
            }
        if not state.get("_template_bytes"):
            search_intent = await analyze_research_intent(state.get("user_query", ""))
            if search_intent.needs_research:
                search_queries = document_intent.get("template_search_queries", [])
                if not search_queries:
                    search_queries = [
                        ruleset["template_search_query"].format(topic=state.get("user_query", "")[:80])
                    ]
                seen_urls: set[str] = set()
                for query in search_queries[:3]:
                    try:
                        for result in await search_web(str(query), max_results=3):
                            url = str(result.get("url", ""))
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                references.append(result)
                    except Exception as exc:
                        logger.warning(
                            "native_template_reference.failed",
                            format=format_id,
                            query=str(query)[:80],
                            error=str(exc)[:120],
                        )
                    if len(references) >= 6:
                        break
            else:
                logger.info(
                    "native_template_reference.skipped",
                    format=format_id,
                    reason=search_intent.reason,
                    intent=search_intent.intent_label,
                )

        prompt = (
            "You are a document art director. Design a production-ready native document template. "
            "Never propose a plain text-only document. Use the supplied visual language and native "
            "components. Return JSON only with keys template_name, design_rationale, primary, "
            "secondary, accent, background, surface, text_primary, text_secondary, font_heading, "
            "font_body, layout_pattern, component_treatment, reference_inspiration.\n\n"
            f"Target format: {ruleset['display_name']}\n"
            f"Design rules: {json.dumps(ruleset['design_rules'], ensure_ascii=False)}\n"
            f"Native components: {json.dumps(ruleset['native_components'], ensure_ascii=False)}\n"
            f"Document intent and template market: {json.dumps(document_intent, ensure_ascii=False)}\n"
            f"Uploaded template analysis: {json.dumps(template_analysis, ensure_ascii=False)[:2500]}\n"
            f"External template references: {json.dumps(references, ensure_ascii=False)[:3500]}\n"
            "When references are available, infer their structural conventions and produce an "
            "original template design suitable for that market. For Word public-sector forms, "
            "use restrained colors and formal information hierarchy rather than decorative accents.\n"
            f"User request: {state.get('user_query', '')}\n"
            "If a DOCX template was uploaded, preserve its native layout, styles, tables, headers "
            "and footers exactly; do not redesign it or replace it with a generated layout.\n"
            f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}"
        )
        try:
            llm = get_llm_for_agent("template_designer", format_id=format_id)
            response = await llm.ainvoke(
                [SystemMessage(content="You produce native document design systems as valid JSON."),
                 HumanMessage(content=prompt)]
            )
            raw = parse_llm_json(response.content, fallback={})
        except Exception as exc:
            logger.warning("native_template_design.fallback", format=format_id, error=str(exc)[:120])
            raw = {}
        design = normalize_design_system(
            raw,
            ruleset,
            document_intent=document_intent,
            template_provided=bool(state.get("_template_bytes")),
        )
        design["reference_inspiration"] = references or design["reference_inspiration"]
        design["template_source"] = "uploaded" if state.get("_template_bytes") else "researched_and_designed"
        return {
            "design_system": design,
            "template_profile": design,
            "template_references": references,
            "current_phase": "designing",
        }

    async def document_plan(state: DocuMindState) -> dict:
        base_spec = state.get("_base_document_spec", {})
        user_query = str(state.get("user_query", ""))
        delete_requested = is_content_removal_request(user_query)
        revision = ""
        if base_spec:
            revision = (
                "\nThis is a revision of an existing document. Preserve its title, layout, unchanged "
                "sections and populated content unless the user explicitly requests replacement or removal. "
                "If the user asks to remove/delete unnecessary content, return the complete list of "
                "remaining sections after deletion (do not include removed sections). "
                "Otherwise, return only changed/new sections and changed metadata or summary; omitted "
                "existing sections will remain in the document. Never convert it to a generic report. "
                "Existing document specification:\n"
                + json.dumps(base_spec, ensure_ascii=False)[:6000]
            )
        if state.get("qa_feedback", {}).get("issues"):
            revision += (
                "\nThe prior render failed quality checks. Correct these issues in this plan:\n"
                + json.dumps(state["qa_feedback"]["issues"], ensure_ascii=False)[:2000]
            )
        prompt = (
            "You are a senior content architect. Produce a highly designed, substantive native "
            "document specification as JSON. It must use visual information structures rather than "
            "a plain series of paragraphs.\n\n"
            "JSON schema: {title, subtitle, document_type, metadata:[{label,value}], "
            "layout_mode, executive_summary, sections:[{title,purpose,blocks:[{type, ...}]}], sources:[]}\n"
            "Allowed block types: paragraph, callout, quote, bullet_list, timeline, action_items, "
            "kpi_grid ({items:[{label,value,context}]}), table ({headers:[],rows:[[]]}), "
            "mermaid ({code}), code_block ({language,code}), image ({alt,src,caption}).\n"
            f"Format: {ruleset['display_name']}\n"
            f"Format content rules: {json.dumps(ruleset['content_rules'], ensure_ascii=False)}\n"
            f"Required components: {json.dumps(ruleset['quality']['required_blocks'], ensure_ascii=False)}\n"
            f"Template design: {json.dumps(state.get('design_system', {}), ensure_ascii=False)}\n"
            f"Uploaded native template fields: {json.dumps(state.get('_template_analysis', {}), ensure_ascii=False)[:4000]}\n"
            f"Document intent: {json.dumps(state.get('document_intent', {}), ensure_ascii=False)}\n"
            f"User request: {user_query}\n"
            "Use the inferred artifact name as the title; do not copy an instruction sentence as "
            "the document title. Use labels and headings native to the target language. "
            "Do not invent a weekly/status report structure unless the user requests that artifact. "
            "For XLSX, return worksheet-oriented tables and fictional row data only when requested; "
            "avoid cover/report prose. For PDF, provide sufficiently rich page content and blocks "
            "to produce a filled publication layout and honor any requested page count or length; "
            "for long reports, organize complete content into compact sections and tables so the JSON remains valid. "
            "When revising a PDF because the Overview or executive summary area is blank or insufficient, "
            "return an updated executive_summary only unless the request names another section to change; "
            "do not rewrite or expand unaffected sections. "
            "For Markdown, include substantive article sections and use Mermaid diagrams or fenced "
            "code when the requested technical content benefits from them; never emit an empty contents list. "
            "Treat attachment analysis as factual/content input unless its role explicitly says "
            "template_style_reference.\n"
            "When an uploaded DOCX, HWPX, XLSX, Markdown or fillable PDF template is provided, compose values "
            "for its existing fields and sections only. Do not add a reference/sources section unless the user explicitly "
            "requests citations as document content.\n"
            f"Attachment evidence: {json.dumps(state.get('visual_intent', {}), ensure_ascii=False)[:3500]}\n"
            f"Research evidence: {json.dumps(state.get('research_data', {}), ensure_ascii=False)[:5000]}"
            f"{revision}\n{output_language_instruction(state.get('output_language', 'ko_mixed'))}"
        )
        try:
            llm = get_llm_for_agent("document_planner", format_id=format_id)
            response = await llm.ainvoke(
                [SystemMessage(content="Return only valid JSON for a designed native document."),
                 HumanMessage(content=prompt)]
            )
            raw = parse_llm_json(response.content, fallback={})
        except Exception as exc:
            logger.warning("native_document_plan.fallback", format=format_id, error=str(exc)[:120])
            raw = {}
        existing_spec = (
            normalize_document_spec(
                base_spec,
                state.get("user_query", ""),
                ruleset,
                document_intent=state.get("document_intent", {}),
            )
            if base_spec
            else {}
        )
        if not has_planned_content(raw):
            recovery_prompt = (
                "Your previous document plan was empty. Produce reader-facing content now as JSON only. "
                "Never return empty executive_summary or empty sections. Do not describe the template, "
                "renderer, file format, or design system as document content. "
                f"Create at least {ruleset['quality']['min_sections']} substantive sections using the "
                f"requested native components {json.dumps(ruleset['quality']['required_blocks'], ensure_ascii=False)}. "
                f"Format: {format_id}. User request: {state.get('user_query', '')}. "
                f"Document intent: {json.dumps(state.get('document_intent', {}), ensure_ascii=False)}. "
                f"Research evidence: {json.dumps(state.get('research_data', {}), ensure_ascii=False)[:4500]}. "
                f"Existing document to revise without losing content: {json.dumps(existing_spec, ensure_ascii=False)[:5000]}. "
                f"{output_language_instruction(state.get('output_language', 'ko_mixed'))}"
            )
            try:
                response = await llm.ainvoke(
                    [
                        SystemMessage(content="Repair an empty native document plan. Return substantive valid JSON only."),
                        HumanMessage(content=recovery_prompt),
                    ]
                )
                repaired = parse_llm_json(response.content, fallback={})
                if has_planned_content(repaired):
                    raw = repaired
            except Exception as exc:
                logger.warning("native_document_plan.repair_failed", format=format_id, error=str(exc)[:120])
        if has_planned_content(raw):
            spec = normalize_document_spec(
                raw,
                state.get("user_query", ""),
                ruleset,
                document_intent=state.get("document_intent", {}),
            )
            if existing_spec:
                if not as_list(
                    raw.get("sections")
                    or raw.get("worksheets")
                    or raw.get("sheets")
                    or raw.get("pages")
                    or raw.get("content")
                ):
                    spec["sections"] = []
                if not as_list(raw.get("metadata")):
                    spec["metadata"] = []
                if not raw.get("executive_summary"):
                    spec["executive_summary"] = ""
                query = str(state.get("user_query", "")).lower()
                summary_only_pdf_revision = (
                    format_id == "pdf"
                    and any(marker in query for marker in ("overview", "요약", "summary"))
                    and not any(marker in query for marker in ("섹션 추가", "장 추가", "add section", "new section"))
                )
                if summary_only_pdf_revision:
                    spec["sections"] = []
                    spec["metadata"] = []
                spec = merge_revision_spec(
                    existing_spec,
                    spec,
                    allow_new_sections=not summary_only_pdf_revision,
                    prune_missing_sections=delete_requested and not summary_only_pdf_revision,
                )
        else:
            spec = existing_spec or normalize_document_spec(
                raw,
                state.get("user_query", ""),
                ruleset,
                document_intent=state.get("document_intent", {}),
            )
        return {
            "title": spec["title"],
            "document_spec": spec,
            "section_blueprints": spec["sections"],
            "current_phase": "generating",
        }

    async def native_render(state: DocuMindState) -> dict:
        renderer = renderer_class()
        output_dir = Path(settings.storage_local_path)
        output_path = await renderer.render(
            state.get("document_spec", {}),
            output_dir,
            design_system=state.get("design_system", {}),
            template_bytes=state.get("_template_bytes"),
        )
        return {"output_path": str(output_path), "current_phase": "converting"}

    async def quality_evaluate(state: DocuMindState) -> dict:
        result = evaluate_document(
            state.get("document_spec", {}),
            state.get("design_system", {}),
            ruleset,
            state.get("output_path"),
        )
        scores = list(state.get("fidelity_scores", [])) + [result["score"]]
        return {
            "validation_result": result,
            "qa_feedback": result,
            "fidelity_score": result["score"],
            "fidelity_scores": scores,
            "qa_iterations": state.get("qa_iterations", 0) + 1,
            "current_phase": "qa",
        }

    def route_research(state: DocuMindState) -> str:
        return "research" if state.get("needs_research", False) else "plan"

    def route_quality(state: DocuMindState) -> str:
        feedback = state.get("qa_feedback", {})
        if feedback.get("passed"):
            return "export"
        if state.get("qa_iterations", 0) >= 2:
            return "fail"
        return "plan"

    def export_document(state: DocuMindState) -> dict:
        return {"current_phase": "done"}

    async def reject_document(state: DocuMindState) -> dict:
        issues = state.get("qa_feedback", {}).get("issues", [])
        detail = "; ".join(str(issue) for issue in issues) or "unknown quality failure"
        raise ValueError(f"Native document quality validation failed: {detail}")

    graph.add_node("init_document_context", init_document_context)
    graph.add_node("interpret_request", interpret_request)
    graph.add_node("template_design", template_design)
    graph.add_node("research", research_agent)
    graph.add_node("document_plan", document_plan)
    graph.add_node("native_render", native_render)
    graph.add_node("quality_evaluate", quality_evaluate)
    graph.add_node("export_document", export_document)
    graph.add_node("reject_document", reject_document)
    graph.set_entry_point("init_document_context")
    graph.add_edge("init_document_context", "interpret_request")
    graph.add_edge("interpret_request", "template_design")
    graph.add_conditional_edges("template_design", route_research, {"research": "research", "plan": "document_plan"})
    graph.add_edge("research", "document_plan")
    graph.add_edge("document_plan", "native_render")
    graph.add_edge("native_render", "quality_evaluate")
    graph.add_conditional_edges(
        "quality_evaluate",
        route_quality,
        {"plan": "document_plan", "export": "export_document", "fail": "reject_document"},
    )
    graph.add_edge("export_document", END)
    return graph


def compile_native_document_pipeline(format_id: str, renderer_class: type, ruleset: dict):
    return build_native_document_pipeline(format_id, renderer_class, ruleset).compile()
