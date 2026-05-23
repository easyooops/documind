"""Agent pipeline state schemas — defines the LangGraph state and agent I/O models."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, Field


# ═══ LangGraph State TypedDict ═══

class DocuMindState(TypedDict, total=False):
    """Complete state flowing through the LangGraph pipeline."""

    # Input
    user_query: str
    session_id: str
    template_id: str | None
    conversation_history: list[dict]
    document_format: str  # "pptx" | "docx" | "pdf" | "xlsx"
    locale: str  # "ko" | "en"
    output_language: str  # "en" | "ko_mixed"

    # Planning outputs
    research_data: dict | None
    narrative_plan: dict
    slide_contents: list[dict]
    audience_profile: dict

    # Design outputs
    template_profile: dict | None
    layout_specs: list[dict]
    design_system: dict
    asset_requirements: list[dict]

    # Generation outputs
    slides_dsl: list[dict]  # OOXML-DSL JSON (single source of truth)
    slides_html: list[dict]  # Derived from DSL (preview only)
    consistency_report: dict
    validation_result: dict

    # Conversion outputs
    output_path: str | None
    html_preview_path: str | None
    fidelity_scores: list[float]
    qa_iterations: int
    qa_feedback: dict

    # Control flow
    current_phase: str
    errors: list[str]
    retry_count: int
    needs_research: bool
    template_provided: bool


# ═══ Planning Phase Schemas ═══

class SlideNarrative(BaseModel):
    index: int
    slide_type: str = Field(description="cover|toc|problem|solution|data|comparison|summary|cta")
    title: str
    key_message: str
    purpose: str
    content_elements: list[str] = Field(default_factory=list)
    data_needs: list[str] = Field(default_factory=list)
    transition_to_next: str = ""
    visual_metaphor: str | None = None
    emphasis_level: str = "standard"


class NarrativePlan(BaseModel):
    title: str
    total_slides: int
    narrative_arc: str
    slides: list[SlideNarrative]


class ContentBlock(BaseModel):
    type: str = Field(description="paragraph|bullet_list|kpi|quote|callout")
    content: str | list[str]
    emphasis: str = "secondary"


class DataPoint(BaseModel):
    label: str
    value: str | float
    unit: str | None = None
    context: str = ""
    trend: str | None = None


class SlideContent(BaseModel):
    index: int
    title: str
    subtitle: str | None = None
    body_text: list[ContentBlock] = Field(default_factory=list)
    data_points: list[DataPoint] | None = None
    speaker_notes: str | None = None
    source_citations: list[str] | None = None


class AudienceProfile(BaseModel):
    audience_type: str = Field(description="executive|technical|sales|general")
    tone: str = Field(description="formal|professional|casual|inspirational")
    complexity: str = "medium"
    visual_density: str = "balanced"
    attention_span: str = "medium"
    persuasion_style: str = "data-driven"
    language_register: str = "formal"
    design_expectations: str = "sleek corporate"
    key_constraints: list[str] = Field(default_factory=list)


# ═══ Design Phase Schemas ═══

class Zone(BaseModel):
    name: str
    grid_position: str
    purpose: str
    element_types: list[str] = Field(default_factory=list)
    priority: int = 1


class SlideLayout(BaseModel):
    index: int
    grid_type: str = Field(description="hero-left|two-column|card-grid-3|full-bleed|centered")
    zones: list[Zone] = Field(default_factory=list)
    visual_weight: str = "balanced"
    whitespace_ratio: float = 0.3
    alignment: str = "left"


class ColorTokens(BaseModel):
    primary: str
    secondary: str
    accent: str
    background: str
    surface: str
    text_primary: str
    text_secondary: str
    text_on_primary: str
    border: str
    shadow_color: str


class TypeStyle(BaseModel):
    role: str
    font_family: str
    font_size: str
    font_weight: str
    line_height: str
    letter_spacing: str = "normal"
    color: str = ""


class EffectLibrary(BaseModel):
    shadow_subtle: str = ""
    shadow_medium: str = ""
    shadow_elevated: str = ""
    glow_accent: str = ""
    gradient_hero: str = ""
    gradient_card: str = ""
    border_style: str = ""
    overlay_glass: str = ""


class DesignSystem(BaseModel):
    css_variables: dict[str, str] = Field(default_factory=dict)
    color_tokens: ColorTokens
    typography_scale: list[TypeStyle] = Field(default_factory=list)
    effect_library: EffectLibrary = Field(default_factory=EffectLibrary)
    component_recipes: dict[str, str] = Field(default_factory=dict)


class AssetRequirement(BaseModel):
    slide_index: int
    asset_type: str = Field(description="photo|chart|icon|illustration|decorative")
    description: str
    zone: str = ""
    dimensions: tuple[int, int] = (400, 300)
    source_strategy: str = "placeholder"
    style_notes: str = ""
    data_source: dict | None = None


# ═══ Generation Phase Schemas ═══

class SlideHTML(BaseModel):
    index: int
    html: str
    css: str = ""
    metadata: dict = Field(default_factory=dict)


class ValidationResult(BaseModel):
    passed: bool
    level1_structural: dict = Field(default_factory=dict)
    level2_compatibility: dict = Field(default_factory=dict)
    level3_visual: dict = Field(default_factory=dict)
    overall_score: float = 0.0
    issues: list[str] = Field(default_factory=list)
    fix_instructions: list[str] = Field(default_factory=list)


class ConsistencyReport(BaseModel):
    is_consistent: bool = True
    issues: list[str] = Field(default_factory=list)
    patches: list[dict] = Field(default_factory=list)
