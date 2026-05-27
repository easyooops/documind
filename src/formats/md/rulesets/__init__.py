"""Markdown publishing-template and evaluation rules."""

# ruff: noqa: E501

MARKDOWN_RULESET = {
    "version": "1.0",
    "display_name": "Markdown document (.md)",
    "document_type": "editorial_knowledge_document",
    "fallback_subtitle": "구조화된 기술 문서",
    "template_search_query": "{topic} markdown report README dashboard template design executive summary",
    "design_rules": [
        "Use a publication-style cover, navigation, callouts and aligned information tables.",
        "Markdown must read well in GitHub, Notion and documentation renderers.",
        "Present metrics and actions in visual table components rather than paragraph-only prose.",
    ],
    "content_rules": [
        "Include front matter and a table of contents.",
        "Provide substantive reader-facing sections and use tables where they improve understanding.",
        "When a flow, architecture, relationship or process is requested, express it as a Mermaid block.",
        "Preserve code examples as fenced code blocks and images as Markdown image elements.",
        "Use source links or references where evidence is present.",
    ],
    "native_components": ["YAML front matter", "cover hierarchy", "blockquote callout", "dashboard table", "checklist", "Mermaid diagram", "fenced code block", "image"],
    "quality": {
        "min_sections": 2,
        "required_blocks": ["table"],
        "minimum_score": 0.78,
    },
    "default_design": {
        "template_name": "Editorial Knowledge Brief",
        "design_rationale": "A documentation-native brief combining a clean masthead, dashboard and action register.",
        "primary": "#152A38",
        "secondary": "#536777",
        "accent": "#0E7490",
        "background": "#F8FAFC",
        "surface": "#FFFFFF",
        "text_primary": "#18232C",
        "text_secondary": "#52606D",
        "font_heading": "System UI",
        "font_body": "System UI",
        "layout_pattern": "masthead_toc_dashboard_sections",
        "component_treatment": "strong headings, callout quotes, compact tables",
    },
}
