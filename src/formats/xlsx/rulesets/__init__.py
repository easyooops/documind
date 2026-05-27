"""Excel workbook design and evaluation rules."""

# ruff: noqa: E501

XLSX_RULESET = {
    "version": "1.0",
    "display_name": "Microsoft Excel workbook (.xlsx)",
    "document_type": "analytical_workbook",
    "fallback_subtitle": "업무 활용을 위한 구조화 데이터 문서",
    "template_search_query": "{topic} Excel dashboard template KPI tracker professional design",
    "design_rules": [
        "Choose sheets and tab order from the requested workbook purpose; do not force a report cover.",
        "Use Korean public-document spreadsheet conventions: restrained navy headings, pale gray-blue header fills, fine gray borders, clear alignment, frozen table headers and filtering.",
        "Do not pour report prose into cells; model the requested data as operational tables.",
    ],
    "content_rules": [
        "Create data and sheet structure that matches the user's artifact, such as a data dictionary, tracker or calculation model.",
        "Generate fictional values only when the user asks for sample data; do not insert stock business-report content.",
        "Use sections as purposeful worksheet tabs, with tables as the default content component.",
    ],
    "native_components": ["purposeful worksheet tabs", "styled table", "frozen pane", "auto-filter", "numeric formats"],
    "quality": {"min_sections": 1, "required_blocks": ["table"], "minimum_score": 0.78},
    "lock_palette_without_template": True,
    "default_design": {
        "template_name": "공공 업무용 표준 데이터 서식",
        "design_rationale": "공공기관 실무 표준에 맞춘 절제된 표 구성과 명확한 정렬 체계입니다.",
        "primary": "#1F4E79",
        "secondary": "#506780",
        "accent": "#7F98B2",
        "background": "#F4F6F8",
        "surface": "#FFFFFF",
        "text_primary": "#1F1F1F",
        "text_secondary": "#555555",
        "font_heading": "Malgun Gothic",
        "font_body": "Malgun Gothic",
        "layout_pattern": "official_table_register",
        "component_treatment": "restrained navy hierarchy, light header shading, thin bordered data tables",
    },
}
