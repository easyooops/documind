"""Agent pipeline state schemas — defines the LangGraph state and agent I/O models."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field


# ═══ LangGraph State TypedDict ═══

class DocuMindState(TypedDict, total=False):
    """Complete state flowing through the v2 LangGraph pipeline.

    Architecture: 4-Phase pipeline
      Phase 0: Init (master context)
      Phase A: Unified Planning (research + blueprint)
      Phase B: HTML Generation (constrained HTML per slide)
      Phase C: Render + VLM QA (capture, convert, verify)
    """

    # ─── Input ───────────────────────────────────────────────────────────────
    user_query: str
    session_id: str
    template_id: str | None
    conversation_history: list[dict]
    document_format: str
    locale: str
    output_language: str
    needs_research: bool
    template_provided: bool
    _template_bytes: bytes
    _template_filename: str
    _template_analysis: dict
    _locked_master_context: dict
    _locked_design_system: dict
    _base_version: dict
    _base_slides_html: list[dict]
    visual_intent: dict
    image_attachment_ids: list[str]
    revision_instruction: str
    revision_scope: str
    slide_revision_instructions: dict[int, str]

    # ─── Phase 0: Master Context ─────────────────────────────────────────────
    master_context: dict

    # ─── Phase A: Unified Planning ───────────────────────────────────────────
    research_data: dict | None
    slide_blueprints: list[dict]
    design_system: dict
    presentation_strategy: dict
    layout_system: dict
    title: str
    changed_slide_indices: list[int]
    visual_asset_plan: dict
    visual_assets: list[dict]
    format_rules: dict
    template_profile: dict
    template_references: list[dict]
    document_intent: dict
    document_spec: dict
    section_blueprints: list[dict]
    _base_document_spec: dict

    # ─── Phase B: HTML Generation ────────────────────────────────────────────
    slides_html: list[dict]
    element_usage: dict

    # ─── Phase C: Render + QA ────────────────────────────────────────────────
    html_screenshots: list[str]
    output_path: str | None
    html_preview_path: str | None
    pptx_screenshots: list[str]
    pptx_render_info: dict
    fidelity_score: float
    fidelity_scores: list[float]
    rule_based_feedback: dict
    rule_based_scores: list[float]
    qa_iterations: int
    qa_feedback: dict
    validation_result: dict

    # ─── Control Flow ────────────────────────────────────────────────────────
    current_phase: str
    errors: list[str]
    retry_count: int


# ═══ Planning Phase Schemas ═══

class SlideBlueprint(BaseModel):
    """Per-slide blueprint combining narrative, content, layout, and style hints."""
    index: int
    slide_type: str = Field(description="cover|toc|content|problem|solution|data|comparison|summary|cta|section")
    title: str
    key_message: str
    purpose: str
    content_elements: list[dict] = Field(default_factory=list)
    data_points: list[dict] = Field(default_factory=list)
    layout_hint: str = "balanced"
    suggested_elements: list[str] = Field(default_factory=list)
    visual_style: str = ""
    source_citations: list[str] = Field(default_factory=list)
    layout_plan: dict = Field(default_factory=dict)


class PlanningOutput(BaseModel):
    """Complete output from the Unified Planner."""
    title: str
    total_slides: int
    narrative_arc: str
    audience_type: str = "professional"
    tone: str = "professional"
    slides: list[SlideBlueprint]
    design_tokens: dict = Field(default_factory=dict)
    presentation_strategy: dict = Field(default_factory=dict)
    layout_system: dict = Field(default_factory=dict)


# ═══ HTML Generation Schemas ═══

class SlideHTML(BaseModel):
    """Output from HTML Generator per slide."""
    index: int
    html: str
    elements_used: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


# ═══ QA Schemas ═══

class QAResult(BaseModel):
    """VLM Quality Assessment result."""
    passed: bool
    fidelity_score: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    fix_instructions: list[str] = Field(default_factory=list)
    slide_scores: dict = Field(default_factory=dict)
