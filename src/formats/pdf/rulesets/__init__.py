"""PDF publication and visual-quality rules."""

# ruff: noqa: E501

PDF_RULESET = {
    "version": "1.0",
    "display_name": "PDF document (.pdf)",
    "document_type": "published_report",
    "fallback_subtitle": "주제 중심 분석 문서",
    "template_search_query": "{topic} annual report PDF editorial template dashboard layout design",
    "design_rules": [
        "Treat the output as a final publication with deliberate page composition.",
        "Use a high-impact cover, repeated section chrome and generous whitespace.",
        "Render metrics, tables and insight panels as visual page components.",
    ],
    "content_rules": [
        "Derive every section from the requested artifact and topic; never default to a weekly status report.",
        "Honor requested page count or document length by planning enough substantive sections, tables and explanatory blocks to fill that extent.",
        "Compose pages densely with meaningful text, tables, diagrams or callouts rather than large unused areas.",
        "Keep a references section only for research-backed deliverables that require attribution.",
    ],
    "native_components": ["cover page", "page folio", "KPI tile", "insight panel", "banded data table"],
    "quality": {"min_sections": 2, "required_blocks": [], "minimum_score": 0.78},
    "default_design": {
        "template_name": "Editorial Impact Report",
        "design_rationale": "A publication layout with a dark cover, aqua signal color and modular information cards.",
        "primary": "#112738",
        "secondary": "#304A5D",
        "accent": "#12A6A6",
        "background": "#F3F6F7",
        "surface": "#FFFFFF",
        "text_primary": "#17242D",
        "text_secondary": "#5B6974",
        "font_heading": "Malgun Gothic",
        "font_body": "Malgun Gothic",
        "layout_pattern": "cover_editorial_sections",
        "component_treatment": "dark masthead, aqua rules, elevated information panels",
    },
}
