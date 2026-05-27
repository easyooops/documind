"""DOCX-specific layout, template and evaluation rules."""

# ruff: noqa: E501

DOCX_RULESET = {
    "version": "1.0",
    "display_name": "Microsoft Word (.docx)",
    "document_type": "professional_report",
    "fallback_subtitle": "Formal report | Review edition",
    "template_search_query": "{topic} public sector official Word report template formal table design",
    "lock_palette_without_template": True,
    "design_rules": [
        "When no template is uploaded, use public-sector official report conventions rather than promotional or generic corporate styling.",
        "Use a restrained office palette: one dark heading color, neutral gray borders, and a single muted accent only for small signals.",
        "Use formal cover hierarchy, running header/footer, executive summary, status tables and action sections.",
        "For ko-KR documents, adapt Korean public-institution official form conventions and Korean labels.",
    ],
    "content_rules": [
        "Determine the true document type first: weekly report, official report, proposal, minutes or internal briefing.",
        "For a weekly report, use status summary, key work progress, issues/cooperation needs, and next-week plan.",
        "Start with a short executive summary and compact status indicators.",
        "Use headings, evidence tables, restrained callouts and an action-oriented close.",
        "Keep template research internal; do not add a references section to the Word output unless the user explicitly requests citations.",
    ],
    "native_components": ["formal title block", "metadata table", "status summary", "issue callout", "banded table", "header/footer"],
    "quality": {
        "min_sections": 3,
        "required_blocks": ["kpi_grid", "table", "callout"],
        "minimum_score": 0.78,
    },
    "default_design": {
        "template_name": "Corporate Formal Report",
        "design_rationale": "A restrained business Word form with a formal title block, compact status summary and accountable action table.",
        "primary": "#23384A",
        "secondary": "#596775",
        "accent": "#728294",
        "background": "#F5F6F7",
        "surface": "#FFFFFF",
        "text_primary": "#202C36",
        "text_secondary": "#596775",
        "font_heading": "Malgun Gothic",
        "font_body": "Malgun Gothic",
        "layout_pattern": "formal_title_summary_status_sections",
        "component_treatment": "navy headings, gray divider lines, neutral status tables",
    },
    "locale_presets": {
        "ko-KR": {
            "template_name": "기업·공공 업무보고 표준형",
            "design_rationale": "한국 기업 및 공공기관 보고 양식을 참고한 절제된 제목부, 요약 현황, 추진내용 표 중심의 서식입니다.",
            "primary": "#24384A",
            "secondary": "#5B6874",
            "accent": "#718293",
            "background": "#F5F6F7",
            "surface": "#FFFFFF",
            "text_primary": "#202C36",
            "text_secondary": "#5B6874",
            "font_heading": "Malgun Gothic",
            "font_body": "Malgun Gothic",
            "layout_pattern": "korean_formal_report",
            "component_treatment": "남색 제목, 회색 구분선, 최소 강조색, 표 중심 정보 구조",
        },
        "en-US": {
            "template_name": "Corporate Weekly Status Report",
            "design_rationale": "A subdued enterprise Word template with summary metrics, issue log and action plan.",
            "primary": "#24384A",
            "secondary": "#5B6874",
            "accent": "#718293",
            "background": "#F5F6F7",
            "surface": "#FFFFFF",
            "text_primary": "#202C36",
            "text_secondary": "#5B6874",
            "font_heading": "Aptos",
            "font_body": "Aptos",
            "layout_pattern": "corporate_status_report",
            "component_treatment": "navy headings, quiet rules, neutral tables",
        },
    },
}

DOCX_RULESET["locale_presets"]["ko-KR"].update(
    {
        "template_name": "\ud55c\uad6d \uacf5\uacf5\uae30\uad00 \uc5c5\ubb34\ubcf4\uace0 \ud45c\uc900\ud615",
        "design_rationale": (
            "\ud55c\uad6d \uacf5\uacf5\uae30\uad00 \ubcf4\uace0 \uc11c\uc2dd\uc744 \uae30\uc900\uc73c\ub85c "
            "\uc81c\ubaa9\ubd80, \ubcf4\uace0 \ud604\ud669, \ucd94\uc9c4\ub0b4\uc6a9 \ud45c\ub97c "
            "\uc808\uc81c\ub41c \ud615\uc2dd\uc73c\ub85c \uad6c\uc131\ud569\ub2c8\ub2e4."
        ),
        "component_treatment": (
            "\uc0c9\uc0c1\uc744 \uc808\uc81c\ud55c \uc81c\ubaa9, \ud68c\uc0c9 \uad6c\ubd84\uc120, "
            "\ud589\uc815 \ubcf4\uace0\uc11c\ud615 \ud45c \uc911\uc2ec \uad6c\uc131"
        ),
    }
)
