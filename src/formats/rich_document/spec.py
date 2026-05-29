"""Normalization utilities for native document generation specifications."""

# ruff: noqa: E501

from __future__ import annotations

import ast
import re
from copy import deepcopy
from typing import Any


def as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _legacy_infer_document_intent(
    query: str,
    output_language: str,
    ruleset: dict,
    visual_intent: dict | None = None,
) -> dict:
    """Build a reliable locale- and archetype-aware baseline before any LLM call."""
    text = query.lower()
    korean = output_language != "en"
    weekly = any(
        token in text
        for token in ("\uc8fc\uac04", "\uae08\uc8fc", "\uc8fc\ubcf4", "weekly", "week")
    )
    public = any(
        token in text
        for token in (
            "\uacf5\uacf5",
            "\uacf5\ubb38",
            "\uae30\uad00",
            "\uc815\ubd80",
            "\ud589\uc815",
            "public",
        )
    )
    corporate = any(
        token in text
        for token in (
            "\uae30\uc5c5",
            "\uacbd\uc601",
            "\ubd80\uc11c",
            "\ud300",
            "corporate",
            "company",
        )
    )
    if weekly:
        document_kind = "weekly_status_report"
        family = "한국 기업·공공기관 주간업무보고서" if korean else "Corporate Weekly Status Report"
    elif public:
        document_kind = "official_report"
        family = "공공기관 보고서 서식" if korean else "Public Sector Report"
    else:
        document_kind = ruleset.get("document_type", "professional_report")
        family = "기업 업무보고서" if korean else "Corporate Business Report"
    sector = "public_sector" if public else "corporate" if corporate else "corporate_public"
    if korean:
        queries = [
            f"{family} 양식 워드 DOCX 표지 현황 추진계획",
            f"{family} 서식 샘플 보고서 디자인",
        ]
    else:
        queries = [
            f"{family} Microsoft Word template executive summary action plan",
            f"{family} DOCX template professional design",
        ]
    attachment_role = "content_evidence"
    if visual_intent and any(
        token in text for token in ("디자인 참고", "스타일 참고", "template", "design reference")
    ):
        attachment_role = "template_style_reference"
    return {
        "document_kind": document_kind,
        "template_family": family,
        "institutional_style": sector,
        "locale_market": "ko-KR" if korean else "en-US",
        "output_language": output_language,
        "template_search_queries": queries,
        "attachment_role": attachment_role,
        "content_focus": (
            ["금주 실적", "주요 이슈", "차주 계획", "협조 요청"]
            if korean and weekly
            else ["current status", "key issues", "next actions"]
        ),
    }


def infer_document_intent(
    query: str,
    output_language: str,
    ruleset: dict,
    visual_intent: dict | None = None,
) -> dict:
    """Select a locale-specific public form unless an uploaded form takes precedence later."""
    text = query.lower()
    korean = output_language != "en"
    document_type = ruleset.get("document_type", "official_report")
    if document_type == "analytical_workbook":
        family = "\uc694\uccad \ub9de\ucda4\ud615 Excel \uc6cc\ud06c\ubd81" if korean else "Purpose-built Excel Workbook"
        return {
            "document_kind": "analytical_workbook",
            "template_family": family,
            "institutional_style": "structured_workbook",
            "locale_market": "ko-KR" if korean else "en-US",
            "output_language": output_language,
            "template_search_queries": [
                f"{query[:80]} Excel XLSX \uc11c\uc2dd \uc0d8\ud50c"
                if korean
                else f"{query[:80]} Excel XLSX template"
            ],
            "attachment_role": "content_evidence",
            "content_focus": ["user-requested dataset", "worksheet structure", "usable tables"],
        }
    if document_type == "editorial_knowledge_document":
        return {
            "document_kind": "technical_markdown_document",
            "template_family": "\uae30\uc220 \ubb38\uc11c \ub9c8\ud06c\ub2e4\uc6b4 \ud15c\ud50c\ub9bf" if korean else "Technical Markdown Publication",
            "institutional_style": "documentation",
            "locale_market": "ko-KR" if korean else "en-US",
            "output_language": output_language,
            "template_search_queries": [
                "\uae30\uc220 \ubb38\uc11c Markdown README \uba38\uba54\uc774\ub4dc \ud45c \ud15c\ud50c\ub9bf"
                if korean
                else "technical Markdown documentation Mermaid table template"
            ],
            "attachment_role": "content_evidence",
            "content_focus": ["structure", "tables", "diagrams", "examples"],
        }
    if document_type == "korean_official_report":
        return {
            "document_kind": "korean_official_form",
            "template_family": "\ud55c\uad6d \uacf5\uacf5 \uc11c\uc2dd HWPX" if korean else "Korean Official Form HWPX",
            "institutional_style": "public_sector",
            "locale_market": "ko-KR" if korean else "en-US",
            "output_language": output_language,
            "template_search_queries": [
                "\ud55c\uad6d \uacf5\uacf5 \uc11c\uc2dd HWPX \uc591\uc2dd \uc0d8\ud50c"
                if korean
                else "Korean public form HWPX template"
            ],
            "attachment_role": "content_evidence",
            "content_focus": ["\uc11c\uc2dd \ud56d\ubaa9", "\uc81c\ucd9c \ub0b4\uc6a9", "\uadfc\uac70 \uc790\ub8cc"],
        }
    if document_type == "published_report":
        return {
            "document_kind": "published_report",
            "template_family": "\uc694\uccad \ub9de\ucda4\ud615 PDF \ubb38\uc11c" if korean else "Purpose-built PDF Document",
            "institutional_style": "publication",
            "locale_market": "ko-KR" if korean else "en-US",
            "output_language": output_language,
            "template_search_queries": [
                "\ubc30\ud3ec\uc6a9 PDF \ubcf4\uace0\uc11c \uc5d0\ub514\ud1a0\ub9ac\uc5bc \uc11c\uc2dd"
                if korean
                else "published PDF report editorial template"
            ],
            "attachment_role": "content_evidence",
            "content_focus": ["user-requested subject", "dense page composition", "readable visual structure"],
        }
    weekly = any(
        token in text
        for token in ("\uc8fc\uac04", "\uae08\uc8fc", "\uc8fc\ubcf4", "weekly", "week")
    )
    if weekly:
        document_kind = "weekly_status_report"
        family = (
            "\ud55c\uad6d \uacf5\uacf5\uae30\uad00 \uc8fc\uac04\uc5c5\ubb34\ubcf4\uace0\uc11c"
            if korean
            else "Public Sector Weekly Status Report"
        )
    else:
        document_kind = ruleset.get("document_type", "official_report")
        family = (
            "\ud55c\uad6d \uacf5\uacf5\uae30\uad00 \ubcf4\uace0\uc11c \uc11c\uc2dd"
            if korean
            else "Public Sector Report"
        )
    if korean:
        queries = [
            f"{family} \ud45c\uc900 \uc591\uc2dd \uc6cc\ub4dc DOCX \ubcf4\uace0\uc11c",
            f"{family} \uc11c\uc2dd \uc0d8\ud50c \ud589\uc815\uc5c5\ubb34 \ud45c",
        ]
    else:
        queries = [
            f"{family} official Word template DOCX status table",
            f"{family} government report form Microsoft Word template",
        ]
    attachment_role = "content_evidence"
    if visual_intent and any(
        token in text
        for token in (
            "\ub514\uc790\uc778 \ucc38\uace0",
            "\uc2a4\ud0c0\uc77c \ucc38\uace0",
            "template",
            "design reference",
        )
    ):
        attachment_role = "template_style_reference"
    return {
        "document_kind": document_kind,
        "template_family": family,
        "institutional_style": "public_sector",
        "locale_market": "ko-KR" if korean else "en-US",
        "output_language": output_language,
        "template_search_queries": queries,
        "attachment_role": attachment_role,
        "content_focus": (
            [
                "\uae08\uc8fc \uc2e4\uc801",
                "\uc8fc\uc694 \uc774\uc288",
                "\ucc28\uc8fc \uacc4\ud68d",
                "\ud611\uc870 \uc694\uccad",
            ]
            if korean and weekly
            else ["current status", "key issues", "next actions"]
        ),
    }


def normalize_document_intent(value: object, baseline: dict) -> dict:
    """Merge model interpretation without allowing it to lose language or evidence policy."""
    raw = as_dict(value)
    merged = {**baseline}
    for key in ("document_kind", "template_family", "institutional_style", "locale_market"):
        if raw.get(key):
            merged[key] = str(raw[key])
    queries = [str(item) for item in as_list(raw.get("template_search_queries")) if str(item)]
    if queries:
        merged["template_search_queries"] = queries[:4]
    focus = [str(item) for item in as_list(raw.get("content_focus")) if str(item)]
    if focus:
        merged["content_focus"] = focus[:8]
    # Uploaded images do not silently become a layout template.
    if baseline.get("attachment_role") == "template_style_reference":
        merged["attachment_role"] = "template_style_reference"
    return merged


def normalize_design_system(
    value: object,
    ruleset: dict,
    *,
    document_intent: dict | None = None,
    template_provided: bool = False,
) -> dict:
    """Return complete design tokens even when model output is incomplete."""
    intent = document_intent or {}
    palette = ruleset.get("locale_presets", {}).get(
        str(intent.get("locale_market", "")),
        ruleset["default_design"],
    )
    raw = as_dict(value)
    result = {
        "template_name": str(raw.get("template_name") or palette["template_name"]),
        "design_rationale": str(raw.get("design_rationale") or palette["design_rationale"]),
        "primary": _hex(raw.get("primary"), palette["primary"]),
        "secondary": _hex(raw.get("secondary"), palette["secondary"]),
        "accent": _hex(raw.get("accent"), palette["accent"]),
        "background": _hex(raw.get("background"), palette["background"]),
        "surface": _hex(raw.get("surface"), palette["surface"]),
        "text_primary": _hex(raw.get("text_primary"), palette["text_primary"]),
        "text_secondary": _hex(raw.get("text_secondary"), palette["text_secondary"]),
        "font_heading": str(raw.get("font_heading") or palette["font_heading"]),
        "font_body": str(raw.get("font_body") or palette["font_body"]),
        "layout_pattern": str(raw.get("layout_pattern") or palette["layout_pattern"]),
        "component_treatment": str(
            raw.get("component_treatment") or palette["component_treatment"]
        ),
        "reference_inspiration": as_list(raw.get("reference_inspiration")),
    }
    if ruleset.get("lock_palette_without_template") and not template_provided:
        for token in ("primary", "secondary", "accent", "background", "surface", "text_primary", "text_secondary"):
            result[token] = palette[token]
    return result


def normalize_document_spec(
    value: object,
    query: str,
    ruleset: dict,
    *,
    document_intent: dict | None = None,
) -> dict:
    """Clean LLM output into the renderer contract and add designed fallback content."""
    raw = as_dict(value)
    intent = document_intent or {}
    fallback = _fallback_document(query, ruleset, intent)
    title = _text_value(raw.get("title") or fallback["title"])
    if _instruction_title(title, query):
        title = fallback["title"]
    metadata = _filter_internal_metadata(_metadata(raw.get("metadata")))
    sections = []
    section_values = (
        raw.get("sections")
        or raw.get("worksheets")
        or raw.get("sheets")
        or raw.get("pages")
        or raw.get("content")
    )
    for index, section_value in enumerate(as_list(section_values), 1):
        section = as_dict(section_value)
        blocks = [
            _normalize_block(item)
            for item in as_list(
                section.get("blocks")
                or section.get("tables")
                or section.get("content")
                or (
                    section
                    if section.get("headers") and section.get("rows")
                    else None
                )
            )
        ]
        blocks = [item for item in blocks if item]
        if not blocks:
            blocks = [{"type": "paragraph", "text": _text_value(section.get("summary") or "")}]
        sections.append(
            {
                "index": index,
                "title": _text_value(section.get("title") or section.get("name") or f"Section {index}"),
                "purpose": _text_value(section.get("purpose") or ""),
                "blocks": blocks,
            }
        )

    if not sections:
        sections = fallback["sections"]

    spec = {
        "title": title,
        "subtitle": _text_value(raw.get("subtitle") or fallback["subtitle"]),
        "document_type": _text_value(raw.get("document_type") or fallback["document_type"]),
        "language": str(intent.get("output_language", "ko_mixed")),
        "metadata": metadata or fallback["metadata"],
        "executive_summary": _text_value(
            raw.get("executive_summary")
            or fallback["executive_summary"]
        ),
        "layout_mode": str(raw.get("layout_mode") or "content_first"),
        "sections": sections,
        "sources": (
            _sources(raw.get("sources") or raw.get("references"))
            if _references_requested(query)
            else []
        ),
    }
    return spec


def has_planned_content(value: object) -> bool:
    """Identify whether a planner response supplied content rather than a title shell."""
    raw = as_dict(value)
    summary = _text_value(raw.get("executive_summary") or raw.get("summary"))
    sections = as_list(
        raw.get("sections")
        or raw.get("worksheets")
        or raw.get("sheets")
        or raw.get("pages")
        or raw.get("content")
    )
    return bool(summary.strip() or sections)


def is_content_removal_request(query: str) -> bool:
    """Detect explicit user intent to remove/prune existing document content."""
    lowered = str(query or "").lower()
    return any(
        token in lowered
        for token in (
            "불필요",
            "삭제",
            "제거",
            "빼줘",
            "빼고",
            "지워",
            "remove",
            "delete",
            "omit",
            "exclude",
            "trim",
            "prune",
        )
    )


def is_document_replacement_request(query: str) -> bool:
    """Detect revisions that should replace the previous content, not merge around it."""
    lowered = str(query or "").lower()
    replacement_tokens = (
        "전체", "전체적으로", "전부", "모두", "완전히", "전면",
        "새로 작성", "다시 작성", "교체", "바꿔", "변경해줘",
        "내용으로 변경", "일반 내용", "말고", "대신",
        "rewrite", "replace", "replace all", "entire document", "all content",
        "change to", "instead of",
    )
    return any(token in lowered for token in replacement_tokens)


def revision_guidance(query: str) -> str:
    """Produce deterministic guidance that makes user edits outrank preservation."""
    if not query:
        return ""
    if is_document_replacement_request(query):
        return (
            "Revision priority: USER CHANGE OVERRIDES PRESERVATION. The request is a "
            "replacement/rewrite request. Return the complete revised document specification, "
            "not only small patches. Existing sections may be replaced even when titles differ."
        )
    if is_content_removal_request(query):
        return (
            "Revision priority: remove exactly the content the user asked to remove, then "
            "return the complete remaining document specification."
        )
    return (
        "Revision priority: apply every user-requested edit exactly. Preserve unrelated "
        "content only after the requested changes are reflected."
    )


def merge_revision_spec(
    base_spec: dict,
    revised_spec: dict,
    *,
    allow_new_sections: bool = True,
    prune_missing_sections: bool = False,
    replace_all_sections: bool = False,
) -> dict:
    """Keep a prior document intact while applying explicitly planned revised sections."""
    if not base_spec:
        return revised_spec
    result = deepcopy(base_spec)
    for key in ("title", "subtitle", "document_type", "language", "layout_mode"):
        if not result.get(key) and revised_spec.get(key):
            result[key] = revised_spec[key]
    if revised_spec.get("executive_summary"):
        result["executive_summary"] = revised_spec["executive_summary"]
    result["metadata"] = _merge_metadata(result.get("metadata", []), revised_spec.get("metadata", []))
    revised_sections = deepcopy(revised_spec.get("sections", []))
    if (prune_missing_sections or replace_all_sections) and revised_sections:
        result["sections"] = [
            {**section, "index": index}
            for index, section in enumerate(revised_sections, 1)
        ]
        if revised_spec.get("sources"):
            result["sources"] = revised_spec["sources"]
        return result

    sections = deepcopy(result.get("sections", []))
    for revised in revised_sections:
        revised_key = _match_key(revised.get("title", ""))
        revised_ordinal = _section_ordinal(revised.get("title", ""))
        matched = next(
            (
                index
                for index, existing in enumerate(sections)
                if (
                    revised_ordinal
                    and _section_ordinal(existing.get("title", "")) == revised_ordinal
                )
                or _related_key(_match_key(existing.get("title", "")), revised_key)
            ),
            None,
        )
        if matched is None:
            if allow_new_sections:
                sections.append(deepcopy(revised))
        else:
            sections[matched] = deepcopy(revised)
    result["sections"] = [
        {**section, "index": index}
        for index, section in enumerate(sections, 1)
    ]
    if revised_spec.get("sources"):
        result["sources"] = revised_spec["sources"]
    return result


def has_substantive_content(spec: dict) -> bool:
    """Reject output wrappers that carry labels but no reader-facing substance."""
    summary = str(spec.get("executive_summary", "")).strip()
    block_text = []
    for section in spec.get("sections", []):
        for block in section.get("blocks", []):
            block_text.extend(text.strip() for text in _block_text(block) if text.strip())
    body_length = sum(len(text) for text in block_text)
    has_data_table = any(
        block.get("type") == "table" and block.get("headers") and block.get("rows")
        for section in spec.get("sections", [])
        for block in section.get("blocks", [])
    )
    return bool(block_text) and (has_data_table or len(summary) >= 20 or body_length >= 60)


def iter_text(spec: dict) -> list[str]:
    texts = [spec.get("title", ""), spec.get("subtitle", ""), spec.get("executive_summary", "")]
    for item in spec.get("metadata", []):
        texts.extend([str(item.get("label", "")), str(item.get("value", ""))])
    for section in spec.get("sections", []):
        texts.append(str(section.get("title", "")))
        for block in section.get("blocks", []):
            texts.extend(_block_text(block))
    return [text for text in texts if text]


def _metadata(value: object) -> list[dict[str, str]]:
    result = []
    for item in as_list(value):
        data = as_dict(item)
        label = _text_value(data.get("label") or "").strip()
        content = _text_value(data.get("value") or "").strip()
        if label and content:
            result.append({"label": label, "value": content})
    return result[:8]


def _filter_internal_metadata(items: list[dict[str, str]]) -> list[dict[str, str]]:
    internal_markers = (
        "designed pdf report",
        "pdf document (.pdf)",
        "markdown publication",
        "markdown document (.md)",
        "designed native document",
        "publication-ready visual report",
    )
    return [
        item
        for item in items
        if not any(marker in item["value"].lower() for marker in internal_markers)
    ]


def _normalize_block(value: object) -> dict:
    item = as_dict(value)
    if item.get("headers") and item.get("rows") and not item.get("type"):
        item = {**item, "type": "table"}
    block_type = str(item.get("type") or "paragraph").lower()
    if block_type in {"paragraph", "callout", "quote"}:
        return {
            "type": block_type,
            "title": _text_value(item.get("title") or ""),
            "text": _text_value(item.get("text") or item.get("content") or ""),
        }
    if block_type in {"bullet_list", "timeline", "action_items"}:
        items = []
        for entry in as_list(item.get("items")):
            structured = _structured_item(entry) if block_type == "action_items" else None
            if structured:
                items.append(
                    {
                        str(key): str(field_value)
                        for key, field_value in structured.items()
                        if field_value not in (None, "")
                    }
                )
            elif _text_value(entry).strip():
                items.append(_text_value(entry))
        return {
            "type": block_type,
            "items": items,
        }
    if block_type in {"kpi_grid", "metrics"}:
        values = []
        for entry in as_list(item.get("items")):
            metric = as_dict(entry)
            values.append(
                {
                    "label": _text_value(metric.get("label") or "Metric"),
                    "value": _text_value(metric.get("value") or "-"),
                    "context": _text_value(metric.get("context") or ""),
                }
            )
        return {"type": "kpi_grid", "items": values}
    if block_type in {"table", "matrix", "action_register", "chart_data"}:
        headers = [_text_value(entry) for entry in as_list(item.get("headers"))]
        rows = []
        for row in as_list(item.get("rows")):
            if isinstance(row, dict):
                rows.append([_text_value(row.get(header, "")) for header in headers])
            else:
                rows.append([_text_value(cell) for cell in as_list(row)])
        return {"type": "table", "headers": headers, "rows": rows}
    if block_type in {"mermaid", "diagram"}:
        return {
            "type": "mermaid",
            "code": _text_value(item.get("code") or item.get("text") or item.get("content") or ""),
        }
    if block_type in {"code", "code_block"}:
        return {
            "type": "code_block",
            "language": str(item.get("language") or item.get("lang") or ""),
            "code": _text_value(item.get("code") or item.get("text") or item.get("content") or ""),
        }
    if block_type == "image":
        return {
            "type": "image",
            "alt": _text_value(item.get("alt") or item.get("title") or "image"),
            "src": _text_value(item.get("src") or item.get("url") or ""),
            "caption": _text_value(item.get("caption") or ""),
        }
    return {"type": "paragraph", "text": _text_value(item.get("text") or item.get("content") or "")}


def _structured_item(value: object) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _block_text(block: dict) -> list[str]:
    if block["type"] in {"paragraph", "callout", "quote"}:
        return [str(block.get("title", "")), str(block.get("text", ""))]
    if block["type"] in {"bullet_list", "timeline", "action_items"}:
        return [_plain_item_text(item) for item in block.get("items", [])]
    if block["type"] == "kpi_grid":
        return [
            f"{item.get('label', '')} {item.get('value', '')} {item.get('context', '')}"
            for item in block.get("items", [])
        ]
    if block["type"] == "table":
        return [*block.get("headers", []), *[" ".join(row) for row in block.get("rows", [])]]
    if block["type"] in {"mermaid", "code_block"}:
        return [str(block.get("code", ""))]
    if block["type"] == "image":
        return [str(block.get("alt", "")), str(block.get("caption", ""))]
    return []


def _plain_item_text(item: object) -> str:
    if not isinstance(item, dict):
        return _text_value(item)
    return " ".join(_text_value(value) for value in item.values() if value not in (None, ""))


def _text_value(value: object) -> str:
    if isinstance(value, str) and value.strip().startswith(("{", "[")):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return _text_value(parsed)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "summary", "description", "title"):
            if value.get(key) not in (None, ""):
                return _text_value(value[key])
        return " ".join(_text_value(item) for item in value.values() if item not in (None, ""))
    if isinstance(value, list):
        return " ".join(_text_value(item) for item in value if item not in (None, ""))
    return str(value or "")


def _sources(value: object) -> list[str]:
    output = []
    for item in as_list(value):
        if isinstance(item, str) and item.strip().startswith("{"):
            try:
                parsed = ast.literal_eval(item)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                item = parsed
        if isinstance(item, dict):
            title = _text_value(item.get("title") or item.get("name") or item.get("provider"))
            url = _text_value(item.get("url") or item.get("link"))
            provider = _text_value(item.get("provider"))
            label = title or provider or url
            formatted = f"[{label}]({url})" if label and url else label
        else:
            formatted = _text_value(item)
        if formatted.strip():
            output.append(formatted.strip())
    return output


def _references_requested(query: str) -> bool:
    lowered = query.lower()
    return any(
        token in lowered
        for token in (
            "참고자료",
            "참고 자료",
            "출처",
            "근거자료",
            "근거 자료",
            "인용",
            "reference",
            "references",
            "citation",
            "citations",
            "source list",
        )
    )


def _merge_metadata(base: list, revised: list) -> list[dict[str, str]]:
    output = [dict(item) for item in base if isinstance(item, dict)]
    indexes = {_match_key(item.get("label", "")): index for index, item in enumerate(output)}
    for item in revised:
        key = _match_key(item.get("label", ""))
        if key in indexes:
            output[indexes[key]] = dict(item)
        else:
            output.append(dict(item))
    return output


def _match_key(value: object) -> str:
    return "".join(character for character in _text_value(value).lower() if character.isalnum())


def _related_key(left: str, right: str) -> bool:
    return bool(left and right and (left == right or left in right or right in left))


def _section_ordinal(value: object) -> str:
    """Identify explicitly numbered sections so revisions replace them in place."""
    match = re.match(r"^\s*(\d{1,3})\s*[.)\-:]", _text_value(value))
    return match.group(1) if match else ""


def _fallback_document(query: str, ruleset: dict, intent: dict) -> dict:
    korean = intent.get("output_language", "ko_mixed") != "en"
    document_type = ruleset.get("document_type", "")
    if document_type in {"analytical_workbook", "editorial_knowledge_document", "published_report", "korean_official_report"}:
        return _neutral_document_shell(query, ruleset, intent, korean)
    weekly = intent.get("document_kind") == "weekly_status_report"
    if korean and weekly:
        return _public_korean_weekly_report()
    if korean:
        return _public_korean_report(ruleset)
    return _english_business_report(query, ruleset)


def _neutral_document_shell(query: str, ruleset: dict, intent: dict, korean: bool) -> dict:
    title = _requested_subject(query) or str(intent.get("template_family") or ruleset["display_name"])
    return {
        "title": title,
        "subtitle": str(ruleset.get("fallback_subtitle", "")),
        "document_type": str(intent.get("document_kind") or ruleset.get("document_type", "")),
        "metadata": [],
        "executive_summary": "",
        "sections": [],
    }


def _requested_subject(query: str) -> str:
    first_sentence = str(query).split(".", 1)[0].strip()
    for suffix in (
        "\uc744 \uc791\uc131\ud574\uc918",
        "\ub97c \uc791\uc131\ud574\uc918",
        " \uc791\uc131\ud574\uc918",
        "\uc744 \ub9cc\ub4e4\uc5b4\uc918",
        "\ub97c \ub9cc\ub4e4\uc5b4\uc918",
        " \ub9cc\ub4e4\uc5b4\uc918",
        " please create",
    ):
        if first_sentence.lower().endswith(suffix.lower()):
            return first_sentence[: -len(suffix)].strip()
    return first_sentence


def _korean_weekly_report() -> dict:
    return {
        "title": "주간업무보고서",
        "subtitle": "금주 추진현황 및 차주 실행계획",
        "document_type": "주간 정기보고",
        "metadata": [
            {"label": "보고기간", "value": "금주"},
            {"label": "보고부서", "value": "담당 부서"},
            {"label": "보고구분", "value": "정기보고"},
        ],
        "executive_summary": (
            "금주 주요 업무의 추진현황과 관리 이슈를 점검하고, "
            "차주 우선 추진사항 및 협조 필요 항목을 보고합니다."
        ),
        "sections": [
            {
                "index": 1,
                "title": "금주 핵심 요약",
                "purpose": "의사결정자가 주요 현황을 빠르게 확인합니다.",
                "blocks": [
                    {
                        "type": "kpi_grid",
                        "items": [
                            {"label": "주요업무", "value": "진행", "context": "금주 추진현황"},
                            {"label": "관리이슈", "value": "점검", "context": "협조 필요사항"},
                            {"label": "차주계획", "value": "준비", "context": "우선 실행과제"},
                        ],
                    },
                    {
                        "type": "callout",
                        "title": "보고 요지",
                        "text": "금주 성과와 이슈를 중심으로 검토하고 차주 계획의 실행 책임을 명확히 합니다.",
                    },
                ],
            },
            {
                "index": 2,
                "title": "주요 업무 추진현황",
                "purpose": "업무별 진행상태와 관리사항을 정리합니다.",
                "blocks": [
                    {
                        "type": "table",
                        "headers": ["구분", "금주 추진내용", "상태", "비고"],
                        "rows": [
                            ["핵심업무", "주요 추진내용 정리", "진행", "담당 확인"],
                            ["지원업무", "협조 및 후속조치 관리", "점검", "일정 확인"],
                        ],
                    }
                ],
            },
            {
                "index": 3,
                "title": "이슈 및 협조 필요사항",
                "purpose": "선제적으로 관리할 쟁점과 요청사항을 공유합니다.",
                "blocks": [
                    {
                        "type": "callout",
                        "title": "관리 기준",
                        "text": "일정, 의사결정, 대외 협조가 필요한 항목은 담당자와 완료기한을 명시합니다.",
                    }
                ],
            },
            {
                "index": 4,
                "title": "차주 추진계획",
                "purpose": "다음 보고 주기까지 실행할 업무를 확정합니다.",
                "blocks": [
                    {
                        "type": "action_items",
                        "items": [
                            "핵심업무별 다음 단계와 담당자를 확정합니다.",
                            "관리 이슈의 조치 일정과 협조 요청을 점검합니다.",
                            "차주 보고를 위한 실적 및 근거자료를 정리합니다.",
                        ],
                    }
                ],
            },
        ],
    }


def _korean_business_report(query: str, ruleset: dict) -> dict:
    title = query[:40].rstrip(".?! ") or "업무보고서"
    return {
        "title": title,
        "subtitle": "현황 분석 및 실행계획 보고",
        "document_type": "업무보고",
        "metadata": [
            {"label": "문서유형", "value": ruleset["display_name"]},
            {"label": "검토대상", "value": "내부 검토"},
            {"label": "상태", "value": "작성본"},
        ],
        "executive_summary": "주요 현황, 핵심 검토사항 및 후속 실행계획을 체계적으로 정리한 보고서입니다.",
        "sections": _korean_weekly_report()["sections"][:3],
    }


def _public_korean_weekly_report() -> dict:
    return {
        "title": "\uc8fc\uac04\uc5c5\ubb34\ubcf4\uace0\uc11c",
        "subtitle": "\uae08\uc8fc \ucd94\uc9c4\ud604\ud669 \ubc0f \ucc28\uc8fc \ucd94\uc9c4\uacc4\ud68d",
        "document_type": "\uc8fc\uac04 \uc815\uae30\ubcf4\uace0",
        "metadata": [
            {"label": "\ubcf4\uace0\uae30\uac04", "value": "\uae08\uc8fc"},
            {"label": "\ubcf4\uace0\ubd80\uc11c", "value": "\ud574\ub2f9 \ubd80\uc11c"},
            {"label": "\ubcf4\uace0\uad6c\ubd84", "value": "\uc815\uae30\ubcf4\uace0"},
        ],
        "executive_summary": (
            "\uae08\uc8fc \uc8fc\uc694 \uc5c5\ubb34\uc758 \ucd94\uc9c4\ud604\ud669\uacfc \uad00\ub9ac \uc774\uc288\ub97c "
            "\uc810\uac80\ud558\uace0, \ucc28\uc8fc \ucd94\uc9c4\uacc4\ud68d\uacfc \ud611\uc870 \ud544\uc694\uc0ac\ud56d\uc744 "
            "\ubcf4\uace0\ud569\ub2c8\ub2e4."
        ),
        "sections": [
            {
                "index": 1,
                "title": "\uae08\uc8fc \ud575\uc2ec \uc694\uc57d",
                "purpose": "\uc8fc\uc694 \ucd94\uc9c4\ud604\ud669\uc744 \uc694\uc57d\ud569\ub2c8\ub2e4.",
                "blocks": [
                    {
                        "type": "kpi_grid",
                        "items": [
                            {"label": "\uc8fc\uc694\uc5c5\ubb34", "value": "\uc9c4\ud589", "context": "\uae08\uc8fc \ud604\ud669"},
                            {"label": "\uad00\ub9ac\uc774\uc288", "value": "\uc810\uac80", "context": "\ud611\uc870 \ud544\uc694"},
                            {"label": "\ucc28\uc8fc\uacc4\ud68d", "value": "\uc900\ube44", "context": "\uc608\uc815 \uc5c5\ubb34"},
                        ],
                    }
                ],
            },
            {
                "index": 2,
                "title": "\uc8fc\uc694 \uc5c5\ubb34 \ucd94\uc9c4\ud604\ud669",
                "purpose": "\uc5c5\ubb34\ubcc4 \uc9c4\ud589\uc0c1\ud0dc\ub97c \uc815\ub9ac\ud569\ub2c8\ub2e4.",
                "blocks": [
                    {
                        "type": "table",
                        "headers": ["\uad6c\ubd84", "\ucd94\uc9c4\ub0b4\uc6a9", "\uc0c1\ud0dc", "\ube44\uace0"],
                        "rows": [["\ud575\uc2ec\uc5c5\ubb34", "\uc8fc\uc694 \ucd94\uc9c4\ub0b4\uc6a9 \uc815\ub9ac", "\uc9c4\ud589", "\ud655\uc778 \ud544\uc694"]],
                    }
                ],
            },
            {
                "index": 3,
                "title": "\uc774\uc288 \ubc0f \ud611\uc870 \ud544\uc694\uc0ac\ud56d",
                "purpose": "\uad00\ub9ac \ud544\uc694 \uc0ac\ud56d\uc744 \uacf5\uc720\ud569\ub2c8\ub2e4.",
                "blocks": [
                    {
                        "type": "callout",
                        "title": "\ud611\uc870\uc694\uccad",
                        "text": "\ud544\uc694\ud55c \ud611\uc870\uc0ac\ud56d\uacfc \uc870\uce58 \uae30\ud55c\uc744 \uba85\uc2dc\ud569\ub2c8\ub2e4.",
                    }
                ],
            },
            {
                "index": 4,
                "title": "\ucc28\uc8fc \uc5c5\ubb34 \uacc4\ud68d",
                "purpose": "\ucc28\uc8fc \uc2e4\ud589 \uacc4\ud68d\uc744 \uc815\ub9ac\ud569\ub2c8\ub2e4.",
                "blocks": [
                    {
                        "type": "table",
                        "headers": ["\uc5c5\ubb34\ub0b4\uc6a9", "\ub2f4\ub2f9\uc790", "\uc644\ub8cc\uae30\ud55c", "\uc0c1\ud0dc"],
                        "rows": [["\ucc28\uc8fc \uc8fc\uc694 \uc5c5\ubb34 \ucd94\uc9c4", "\ub2f4\ub2f9\uc790", "\uc608\uc815\uc77c", "\uc608\uc815"]],
                    }
                ],
            },
        ],
    }


def _public_korean_report(ruleset: dict) -> dict:
    weekly = _public_korean_weekly_report()
    return {
        "title": "\uc5c5\ubb34\ubcf4\uace0\uc11c",
        "subtitle": "\ud604\ud669 \ubc0f \ucd94\uc9c4\uacc4\ud68d \ubcf4\uace0",
        "document_type": "\uacf5\uacf5\uae30\uad00 \uc5c5\ubb34\ubcf4\uace0",
        "metadata": [
            {"label": "\ubb38\uc11c\uc720\ud615", "value": ruleset["display_name"]},
            {"label": "\ubcf4\uace0\ubd80\uc11c", "value": "\ud574\ub2f9 \ubd80\uc11c"},
            {"label": "\uc0c1\ud0dc", "value": "\uc791\uc131\ubcf8"},
        ],
        "executive_summary": "\uc8fc\uc694 \ud604\ud669\uacfc \ucd94\uc9c4 \uacc4\ud68d\uc744 \uacf5\uacf5 \ubcf4\uace0\uc11c \uc11c\uc2dd\uc5d0 \ub9de\ucd94\uc5b4 \uc815\ub9ac\ud569\ub2c8\ub2e4.",
        "sections": weekly["sections"][:3],
    }


def _english_business_report(query: str, ruleset: dict) -> dict:
    label = query[:50] or ruleset["display_name"]
    return {
        "title": label,
        "subtitle": ruleset["fallback_subtitle"],
        "document_type": ruleset["document_type"],
        "metadata": [
            {"label": "Document Type", "value": ruleset["display_name"]},
            {"label": "Prepared For", "value": "Stakeholder Review"},
            {"label": "Status", "value": "Designed Draft"},
        ],
        "executive_summary": "A structured summary of the current position, key decisions and responsible next actions.",
        "sections": [
        {
            "index": 1,
            "title": "Executive Snapshot",
            "purpose": "Provide a scannable opening assessment.",
            "blocks": [
                {
                    "type": "kpi_grid",
                    "items": [
                        {"label": "Priority", "value": "High", "context": "Decision focus"},
                        {"label": "Horizon", "value": "This week", "context": "Review cycle"},
                        {"label": "Owner", "value": "Project Team", "context": "Accountability"},
                    ],
                },
                {
                    "type": "callout",
                    "title": "Design brief",
                    "text": f"{label}의 핵심 메시지를 빠르게 검토하고 실행으로 연결하도록 설계된 문서입니다.",
                },
            ],
        },
        {
            "index": 2,
            "title": "Key Findings",
            "purpose": "Organize evidence and decisions.",
            "blocks": [
                {
                    "type": "table",
                    "headers": ["Area", "Observation", "Implication"],
                    "rows": [
                        ["Context", label, "Align scope and audience"],
                        ["Evidence", "Research-backed detail required", "Validate before distribution"],
                        ["Design", "Template-led hierarchy", "Improve readability and trust"],
                    ],
                }
            ],
        },
        {
            "index": 3,
            "title": "Action Plan",
            "purpose": "Close with responsible next actions.",
            "blocks": [
                {
                    "type": "action_items",
                    "items": [
                        "Confirm key facts and review owners.",
                        "Approve priorities and due dates.",
                        "Distribute the finalized, branded document.",
                    ],
                }
            ],
        },
        ],
    }


def _hex(value: Any, fallback: str) -> str:
    candidate = str(value or fallback).strip().lstrip("#")
    if len(candidate) in {3, 6} and all(char in "0123456789abcdefABCDEF" for char in candidate):
        return "#" + candidate.upper()
    return fallback


def _looks_like_instruction_title(title: str, query: str) -> bool:
    cleaned_title = title.strip().rstrip(".!?요")
    cleaned_query = query.strip().rstrip(".!?요")
    if cleaned_title == cleaned_query:
        return True
    instruction_signals = ("작성해", "만들어", "생성해", "please create", "create a", "make a")
    return any(signal in title.lower() for signal in instruction_signals)


def _instruction_title(title: str, query: str) -> bool:
    cleaned_title = title.strip().rstrip(".!? ")
    cleaned_query = query.strip().rstrip(".!? ")
    if cleaned_title == cleaned_query:
        return True
    lowered = cleaned_title.lower()
    if any(
        token in lowered
        for token in (
            "\uc791\uc131\ud574\uc918",
            "\ub9cc\ub4e4\uc5b4\uc918",
            "\uc0dd\uc131\ud574\uc918",
            "please create",
            "create ",
            "make ",
        )
    ):
        return True
    signals = (
        "\uc791\uc131\ud574",
        "\ub9cc\ub4e4\uc5b4",
        "\uc0dd\uc131\ud574",
        "\uc368\uc918",
        "please create",
        "create a",
        "make a",
    )
    return any(signal in title.lower() for signal in signals)
