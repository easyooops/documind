"""Korean HWPX business-document design and quality rules."""

# ruff: noqa: E501

HWPX_RULESET = {
    "version": "1.1",
    "display_name": "한글 HWPX 문서 (.hwpx)",
    "document_type": "korean_official_report",
    "fallback_subtitle": "검토와 결재를 위한 공식 문서",
    "template_search_query": "{topic} 한국 공공기관 HWPX 공식 서식 행정 업무 문서 양식",
    "design_rules": [
        "한국 공공 문서 관행에 맞춰 제목, 작성 정보, 결재·검토 정보 및 본문 항목을 명료하게 구성합니다.",
        "과한 장식이나 색상을 피하고 흑색 본문, 남색 계열 제목, 얇은 표 선 중심의 서식을 사용합니다.",
        "레거시 바이너리 HWP가 아닌 열 수 있는 표준 HWPX 패키지를 생성합니다.",
    ],
    "content_rules": [
        "사용자가 요청한 문서 유형을 먼저 판단하고 해당 공공·행정 문서 구성에 맞는 항목만 작성합니다.",
        "주간보고, 장애보고, 규격서 등 특정 유형은 사용자가 요구하거나 템플릿이 명시할 때만 적용합니다.",
        "업로드된 HWPX 템플릿이 있으면 원본 서식과 필수 구조를 유지하고 빈 입력 영역만 채웁니다.",
    ],
    "native_components": ["HWPX native style mapping", "공식 제목부", "작성 정보 표", "본문 표", "검토 항목"],
    "quality": {"min_sections": 2, "required_blocks": ["table", "callout"], "minimum_score": 0.78},
    "lock_palette_without_template": True,
    "default_design": {
        "template_name": "한국 공공기관 표준 문서 서식",
        "design_rationale": "행정 문서에 적합한 절제된 제목 체계와 표 중심 구성입니다.",
        "primary": "#1F4E79",
        "secondary": "#506780",
        "accent": "#7F98B2",
        "background": "#F4F6F8",
        "surface": "#FFFFFF",
        "text_primary": "#1F1F1F",
        "text_secondary": "#555555",
        "font_heading": "맑은 고딕",
        "font_body": "맑은 고딕",
        "layout_pattern": "official_form_title_metadata_body",
        "component_treatment": "남색 제목, 연한 회색 표 머리글, 얇은 경계선",
    },
}
