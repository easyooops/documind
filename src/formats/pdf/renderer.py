"""Designed PDF renderer using PyMuPDF drawing primitives."""

# ruff: noqa: E501

from __future__ import annotations

import uuid
from pathlib import Path

from src.formats.base import DocumentRenderer


class PDFRenderer(DocumentRenderer):
    @property
    def format_name(self) -> str:
        return "pdf"

    @property
    def mime_type(self) -> str:
        return "application/pdf"

    @property
    def file_extension(self) -> str:
        return ".pdf"

    async def render(
        self,
        document_spec: dict,
        output_dir: Path,
        *,
        design_system: dict | None = None,
        template_bytes: bytes | None = None,
    ) -> Path:
        import fitz

        design = design_system or {}
        colors = {
            "primary": _rgb(design.get("primary", "#112738")),
            "secondary": _rgb(design.get("secondary", "#304A5D")),
            "accent": _rgb(design.get("accent", "#12A6A6")),
            "background": _rgb(design.get("background", "#F3F6F7")),
            "surface": _rgb(design.get("surface", "#FFFFFF")),
            "text": _rgb(design.get("text_primary", "#17242D")),
            "muted": _rgb(design.get("text_secondary", "#5B6974")),
        }
        fontfile = _font_file()
        font = "publish" if fontfile else "helv"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"document_{uuid.uuid4().hex[:8]}.pdf"
        pdf = fitz.open(stream=template_bytes, filetype="pdf") if template_bytes else fitz.open()
        populated_fields = _populate_form_fields(pdf, document_spec) if template_bytes else 0
        if not template_bytes:
            _cover(pdf, document_spec, colors, font, fontfile)
        elif populated_fields:
            pdf.set_metadata({"title": str(document_spec.get("title", "")), "author": "DocuMind"})
            pdf.save(path)
            pdf.close()
            return path
        _content_pages(
            pdf,
            document_spec,
            colors,
            font,
            fontfile,
            first_page_number=len(pdf) + 1,
        )
        pdf.set_metadata({"title": str(document_spec.get("title", "")), "author": "DocuMind"})
        pdf.subset_fonts()
        pdf.save(path)
        pdf.close()
        return path


def _populate_form_fields(pdf, spec: dict) -> int:
    fields = {
        "title": str(spec.get("title", "")),
        "subtitle": str(spec.get("subtitle", "")),
        "executive_summary": str(spec.get("executive_summary", "")),
        "summary": str(spec.get("executive_summary", "")),
    }
    for item in spec.get("metadata", []):
        fields[str(item.get("label", "")).strip().lower()] = str(item.get("value", ""))
    updated = 0
    for page in pdf:
        widgets = page.widgets()
        for widget in widgets or []:
            name = str(widget.field_name or "").strip().lower()
            if name in fields:
                widget.field_value = fields[name]
                widget.update()
                updated += 1
        for name, value in fields.items():
            for marker in (f"{{{{{name}}}}}", f"{{{{ {name} }}}}"):
                for rect in page.search_for(marker):
                    page.add_redact_annot(rect, fill=(1, 1, 1))
                    page.apply_redactions()
                    page.insert_text(
                        (rect.x0, rect.y1 - 2),
                        value,
                        fontsize=max(8, min(12, rect.height * 0.8)),
                        color=(0.1, 0.15, 0.2),
                    )
                    updated += 1
    return updated


def _content_pages(pdf, spec: dict, colors: dict, font: str, fontfile: str | None, first_page_number: int) -> None:
    import fitz

    page_number = first_page_number
    page = _base_page(pdf, str(spec.get("title", "")).upper(), page_number, colors, font, fontfile)
    y = 88.0
    summary = str(spec.get("executive_summary", "")).strip()
    if summary:
        _text(page, fitz.Rect(48, y, 547, y + 30), "Overview", 19, colors["primary"], font, fontfile)
        y += 38
        summary_height = _paragraph_panel_height(summary, width=457, fontsize=10, minimum=82, maximum=186)
        page.draw_rect(fitz.Rect(48, y, 547, y + summary_height), color=None, fill=colors["background"])
        page.draw_rect(fitz.Rect(48, y, 53, y + summary_height), color=None, fill=colors["accent"])
        _text(page, fitz.Rect(68, y + 17, 525, y + summary_height - 12), summary, 10, colors["text"], font, fontfile)
        y += summary_height + 20
    metadata = spec.get("metadata", [])[:4]
    if metadata:
        width = 240
        for index, item in enumerate(metadata):
            x = 48 if index % 2 == 0 else 307
            if index and index % 2 == 0:
                y += 62
            page.draw_rect(fitz.Rect(x, y, x + width, y + 48), color=None, fill=colors["background"])
            _text(page, fitz.Rect(x + 10, y + 8, x + width - 8, y + 21), str(item.get("label", "")), 7.5, colors["muted"], font, fontfile)
            _text(page, fitz.Rect(x + 10, y + 25, x + width - 8, y + 43), str(item.get("value", "")), 10, colors["primary"], font, fontfile)
        y += 76
    for index, section in enumerate(spec.get("sections", []), 1):
        if y > 675:
            page_number += 1
            page = _base_page(pdf, str(spec.get("title", "")).upper(), page_number, colors, font, fontfile)
            y = 88
        _text(page, fitz.Rect(48, y, 547, y + 34), f"{index:02d}  {section.get('title', '')}", 18, colors["primary"], font, fontfile)
        page.draw_rect(fitz.Rect(48, y + 36, 547, y + 38), color=None, fill=colors["accent"])
        y += 52
        purpose = str(section.get("purpose", "")).strip()
        if purpose:
            _text(page, fitz.Rect(48, y, 547, y + 34), purpose, 9, colors["muted"], font, fontfile)
            y += 40
        for block in section.get("blocks", []):
            if y > 675:
                page_number += 1
                page = _base_page(pdf, str(section.get("title", "")).upper(), page_number, colors, font, fontfile)
                y = 88
            y = _block(page, block, y, colors, font, fontfile)
        y += 16


def _cover(pdf, spec: dict, colors: dict, font: str, fontfile: str | None) -> None:
    import fitz

    page = pdf.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=colors["primary"], fill=colors["primary"])
    page.draw_rect(fitz.Rect(48, 64, 54, 756), color=colors["accent"], fill=colors["accent"])
    page.draw_rect(fitz.Rect(54, 64, 530, 65), color=colors["accent"], fill=colors["accent"])
    _text(page, fitz.Rect(78, 92, 515, 122), str(spec.get("document_type", "REPORT")).upper(), 10, colors["accent"], font, fontfile)
    _text(page, fitz.Rect(78, 175, 515, 335), str(spec.get("title", "")), 31, (1, 1, 1), font, fontfile)
    _text(page, fitz.Rect(80, 350, 500, 405), str(spec.get("subtitle", "")), 14, (0.82, 0.89, 0.92), font, fontfile)
    summary = str(spec.get("executive_summary", "")).strip()
    if summary:
        _text(page, fitz.Rect(80, 650, 505, 728), summary, 10, (0.82, 0.89, 0.92), font, fontfile)


def _summary_page(pdf, spec: dict, colors: dict, font: str, fontfile: str | None) -> None:
    import fitz

    page = _base_page(pdf, "EXECUTIVE SUMMARY", 2, colors, font, fontfile)
    _text(page, fitz.Rect(48, 94, 547, 132), "At a glance", 23, colors["primary"], font, fontfile)
    page.draw_rect(fitz.Rect(48, 146, 547, 235), color=None, fill=colors["background"])
    page.draw_rect(fitz.Rect(48, 146, 54, 235), color=None, fill=colors["accent"])
    _text(page, fitz.Rect(72, 168, 523, 214), str(spec.get("executive_summary", "")), 11, colors["text"], font, fontfile)
    x, y = 48, 266
    for item in spec.get("metadata", [])[:4]:
        page.draw_rect(fitz.Rect(x, y, x + 238, y + 76), color=None, fill=colors["surface"])
        page.draw_rect(fitz.Rect(x, y, x + 238, y + 3), color=None, fill=colors["accent"])
        _text(page, fitz.Rect(x + 14, y + 14, x + 224, y + 31), str(item.get("label", "")).upper(), 8, colors["muted"], font, fontfile)
        _text(page, fitz.Rect(x + 14, y + 39, x + 224, y + 65), str(item.get("value", "")), 12, colors["primary"], font, fontfile)
        x = 309 if x == 48 else 48
        if x == 48:
            y += 92
    _text(page, fitz.Rect(48, y + 118, 547, y + 151), "Document map", 17, colors["primary"], font, fontfile)
    map_y = y + 164
    for index, section in enumerate(spec.get("sections", [])[:5], 1):
        page.draw_circle((63, map_y + 8), 10, color=colors["accent"], fill=colors["accent"])
        _text(page, fitz.Rect(58, map_y + 2, 70, map_y + 16), str(index), 8, (1, 1, 1), font, fontfile)
        _text(page, fitz.Rect(87, map_y - 1, 510, map_y + 22), str(section.get("title", "")), 11, colors["text"], font, fontfile)
        map_y += 34


def _section_page(pdf, section: dict, index: int, colors: dict, font: str, fontfile: str | None) -> None:
    import fitz

    page = _base_page(pdf, f"{index:02d} / SECTION", index + 2, colors, font, fontfile)
    _text(page, fitz.Rect(48, 92, 547, 134), str(section.get("title", "")), 23, colors["primary"], font, fontfile)
    y = 148
    purpose = str(section.get("purpose", ""))
    if purpose:
        _text(page, fitz.Rect(48, y, 547, y + 36), purpose, 10, colors["muted"], font, fontfile)
        y += 48
    for block in section.get("blocks", []):
        if y > 695:
            page = _base_page(pdf, str(section.get("title", "")).upper(), len(pdf), colors, font, fontfile)
            y = 92
        y = _block(page, block, y, colors, font, fontfile)


def _block(page, block: dict, y: float, colors: dict, font: str, fontfile: str | None) -> float:
    import fitz

    kind = block.get("type")
    if kind == "kpi_grid":
        items = block.get("items", [])[:3]
        width = 155
        for index, item in enumerate(items):
            x = 48 + index * 168
            page.draw_rect(fitz.Rect(x, y, x + width, y + 86), color=None, fill=colors["background"])
            page.draw_rect(fitz.Rect(x, y, x + width, y + 3), color=None, fill=colors["accent"])
            _text(page, fitz.Rect(x + 12, y + 14, x + width - 8, y + 30), str(item.get("label", "")).upper(), 8, colors["muted"], font, fontfile)
            _text(page, fitz.Rect(x + 12, y + 35, x + width - 8, y + 61), str(item.get("value", "")), 18, colors["primary"], font, fontfile)
            _text(page, fitz.Rect(x + 12, y + 65, x + width - 8, y + 80), str(item.get("context", "")), 7, colors["muted"], font, fontfile)
        return y + 104
    if kind in {"callout", "quote"}:
        page.draw_rect(fitz.Rect(48, y, 547, y + 76), color=None, fill=colors["background"])
        page.draw_rect(fitz.Rect(48, y, 53, y + 76), color=None, fill=colors["accent"])
        if block.get("title"):
            _text(page, fitz.Rect(68, y + 12, 525, y + 30), str(block["title"]), 10, colors["primary"], font, fontfile)
        _text(page, fitz.Rect(68, y + 35, 525, y + 67), str(block.get("text", "")), 9, colors["text"], font, fontfile)
        return y + 93
    if kind == "table":
        headers, rows = block.get("headers", []), block.get("rows", [])[:7]
        count = max(1, len(headers))
        width = 499 / count
        height = 30
        for col, value in enumerate(headers):
            x = 48 + col * width
            page.draw_rect(fitz.Rect(x, y, x + width, y + height), color=None, fill=colors["primary"])
            _text(page, fitz.Rect(x + 7, y + 9, x + width - 5, y + 25), str(value), 8, (1, 1, 1), font, fontfile)
        for row_index, row in enumerate(rows):
            fill = colors["surface"] if row_index % 2 == 0 else colors["background"]
            row_y = y + height * (row_index + 1)
            for col in range(count):
                x = 48 + col * width
                page.draw_rect(fitz.Rect(x, row_y, x + width, row_y + height), color=(0.87, 0.9, 0.92), fill=fill, width=0.3)
                _text(page, fitz.Rect(x + 7, row_y + 8, x + width - 5, row_y + 26), str(row[col] if col < len(row) else ""), 7.5, colors["text"], font, fontfile)
        return y + height * (len(rows) + 1) + 22
    if kind in {"bullet_list", "timeline", "action_items"}:
        for item in block.get("items", []):
            page.draw_circle((55, y + 9), 3, color=colors["accent"], fill=colors["accent"])
            _text(page, fitz.Rect(67, y, 535, y + 27), str(item), 10, colors["text"], font, fontfile)
            y += 27
        return y + 14
    _text(page, fitz.Rect(48, y, 547, y + 55), str(block.get("text", "")), 10, colors["text"], font, fontfile)
    return y + 67


def _base_page(pdf, label: str, page_number: int, colors: dict, font: str, fontfile: str | None):
    import fitz

    page = pdf.new_page(width=595, height=842)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
    page.draw_rect(fitz.Rect(0, 0, 595, 56), color=None, fill=colors["primary"])
    page.draw_rect(fitz.Rect(48, 55, 547, 58), color=None, fill=colors["accent"])
    _text(page, fitz.Rect(48, 20, 500, 39), label, 9, (1, 1, 1), font, fontfile)
    _text(page, fitz.Rect(520, 805, 548, 823), f"{page_number:02d}", 9, colors["muted"], font, fontfile)
    return page


def _paragraph_panel_height(text: str, *, width: float, fontsize: float, minimum: float, maximum: float) -> float:
    """Allocate enough height for Korean or Latin prose in summary/callout panels."""
    units_per_line = max(12, int(width / fontsize))
    units = 0
    lines = 1
    for character in str(text):
        if character == "\n":
            lines += 1
            units = 0
            continue
        units += 2 if ord(character) > 127 else 1
        if units >= units_per_line:
            lines += 1
            units = 0
    estimated = 34 + (lines * fontsize * 1.48)
    return max(minimum, min(maximum, estimated))


def _text(page, rect, text: str, size: float, color: tuple, fontname: str, fontfile: str | None) -> None:
    kwargs = {"fontname": fontname, "fontsize": size, "color": color, "align": 0}
    if fontfile:
        kwargs["fontfile"] = fontfile
    page.insert_textbox(rect, str(text or ""), **kwargs)


def _font_file() -> str | None:
    choices = [
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    ]
    return str(next((candidate for candidate in choices if candidate.exists()), "")) or None


def _rgb(color: object) -> tuple[float, float, float]:
    value = str(color or "#000000").lstrip("#")
    if len(value) != 6:
        value = "000000"
    return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
