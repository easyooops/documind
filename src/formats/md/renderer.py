"""Rich Markdown publication renderer."""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from src.formats.base import DocumentRenderer


class MarkdownRenderer(DocumentRenderer):
    @property
    def format_name(self) -> str:
        return "md"

    @property
    def mime_type(self) -> str:
        return "text/markdown"

    @property
    def file_extension(self) -> str:
        return ".md"

    async def render(
        self,
        document_spec: dict,
        output_dir: Path,
        *,
        design_system: dict | None = None,
        template_bytes: bytes | None = None,
    ) -> Path:
        design = design_system or {}
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"document_{uuid.uuid4().hex[:8]}.md"
        sections = document_spec.get("sections", [])
        korean = document_spec.get("language", "ko_mixed") != "en"
        summary_heading = "핵심 요약" if korean else "Executive Summary"
        summary_label = "요약" if korean else "At a glance"
        contents_heading = "목차" if korean else "Contents"
        references_heading = "참고자료" if korean else "References"
        lines = [
            "---",
            f'title: "{_text(document_spec.get("title", "Document"))}"',
            f'template: "{_text(design.get("template_name", "Editorial Knowledge Brief"))}"',
            f'generated: "{date.today().isoformat()}"',
            f'accent: "{_text(design.get("accent", "#0E7490"))}"',
            "status: designed-draft",
            "---",
            "",
            f"# {_text(document_spec.get('title', 'Document'))}",
            "",
            f"### {_text(document_spec.get('subtitle', 'Designed brief'))}",
            "",
            _metadata_table(document_spec.get("metadata", [])),
            "",
            f"## {summary_heading}",
            "",
            f"> **{summary_label}**  ",
            f"> {_text(document_spec.get('executive_summary', ''))}",
            "",
            f"## {contents_heading}",
            "",
        ]
        for index, section in enumerate(sections, 1):
            lines.append(f"{index}. [{_text(section.get('title', 'Section'))}](#{_anchor(section.get('title', 'section'))})")
        lines.append("")
        for section in sections:
            lines.extend([f"## {_text(section.get('title', 'Section'))}", ""])
            purpose = _text(section.get("purpose", ""))
            if purpose:
                lines.extend([f"*{purpose}*", ""])
            for block in section.get("blocks", []):
                lines.extend(_render_block(block))
        sources = document_spec.get("sources", [])
        if sources:
            lines.extend([f"## {references_heading}", ""])
            lines.extend(f"- {_text(source)}" for source in sources)
            lines.append("")
        lines.extend(["---", ""])
        generated = "\n".join(lines)
        if template_bytes:
            generated = _populate_template(template_bytes, generated, document_spec)
        path.write_text(generated, encoding="utf-8")
        return path


def _render_block(block: dict) -> list[str]:
    kind = block.get("type")
    if kind == "paragraph":
        return [_text(block.get("text", "")), ""]
    if kind in {"callout", "quote"}:
        title = _text(block.get("title", "Insight"))
        return [f"> **{title or 'Insight'}**  ", f"> {_text(block.get('text', ''))}", ""]
    if kind in {"bullet_list", "timeline"}:
        return [*[f"- {_item_text(item)}" for item in block.get("items", [])], ""]
    if kind == "action_items":
        return [*[f"- [ ] {_item_text(item)}" for item in block.get("items", [])], ""]
    if kind == "kpi_grid":
        headers = ["Indicator", "Value", "Context"]
        rows = [
            [_text(item.get("label", "")), f"**{_text(item.get('value', '-'))}**", _text(item.get("context", ""))]
            for item in block.get("items", [])
        ]
        return [_table(headers, rows), ""]
    if kind == "table":
        return [_table(block.get("headers", []), block.get("rows", [])), ""]
    if kind == "mermaid":
        return ["```mermaid", str(block.get("code", "")).rstrip(), "```", ""]
    if kind == "code_block":
        return [f"```{str(block.get('language', '')).strip()}", str(block.get("code", "")).rstrip(), "```", ""]
    if kind == "image" and block.get("src"):
        output = [f"![{_text(block.get('alt', 'image'))}]({str(block['src']).strip()})"]
        if block.get("caption"):
            output.extend(["", f"*{_text(block['caption'])}*"])
        return [*output, ""]
    return []


def _populate_template(template_bytes: bytes, generated: str, spec: dict) -> str:
    template = template_bytes.decode("utf-8", errors="ignore")
    replacements = {
        "{{title}}": str(spec.get("title", "")),
        "{{subtitle}}": str(spec.get("subtitle", "")),
        "{{executive_summary}}": str(spec.get("executive_summary", "")),
        "{{content}}": generated,
    }
    replaced = template
    replaced_any = False
    for marker, value in replacements.items():
        if marker in replaced:
            replaced = replaced.replace(marker, value)
            replaced_any = True
    if "{{content}}" in template:
        return replaced
    if replaced_any:
        return replaced.rstrip() + "\n"
    return template.rstrip() + "\n\n" + generated + "\n"


def _metadata_table(items: list) -> str:
    return _table(
        ["Document Detail", "Value"],
        [[_text(item.get("label", "")), _text(item.get("value", ""))] for item in items],
    )


def _table(headers: list, rows: list) -> str:
    if not headers:
        return ""
    safe_headers = [_text(item) for item in headers]
    output = ["| " + " | ".join(safe_headers) + " |", "| " + " | ".join("---" for _ in safe_headers) + " |"]
    for row in rows:
        values = [_text(value) for value in row]
        values += [""] * (len(safe_headers) - len(values))
        output.append("| " + " | ".join(values[: len(safe_headers)]) + " |")
    return "\n".join(output)


def _text(value: object) -> str:
    return str(value or "").replace("|", r"\|").replace("\n", " ").strip()


def _item_text(value: object) -> str:
    if not isinstance(value, dict):
        return _text(value)
    action = value.get("action") or value.get("task") or value.get("title") or ""
    details = [
        value.get(key)
        for key in ("owner", "deadline", "due_date", "priority", "status")
        if value.get(key)
    ]
    return " / ".join(_text(item) for item in [action, *details] if item)


def _anchor(value: object) -> str:
    return _text(value).lower().replace(" ", "-")
