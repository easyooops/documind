"""Lightweight native-template inspection used before LLM template adaptation."""

# ruff: noqa: E501

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path

from lxml import etree

_W_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def analyze_template(content: bytes, filename: str) -> dict:
    extension = Path(filename).suffix.lower()
    profile = {"filename": filename, "extension": extension, "source": "uploaded_native_template"}
    if extension in {".docx", ".xlsx", ".hwpx"}:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = archive.namelist()
                profile["package_parts"] = names[:30]
                theme_name = next((name for name in names if "theme" in name.lower()), None)
                if theme_name:
                    profile["contains_theme"] = True
                readable = []
                for name in names:
                    if name.endswith(".xml") and len(readable) < 4:
                        text = archive.read(name).decode("utf-8", errors="ignore")
                        readable.extend(re.findall(r"(?:[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{6})", text)[:4])
                profile["detected_colors"] = readable[:8]
                if extension == ".docx" and "word/document.xml" in names:
                    profile.update(_analyze_docx_structure(archive.read("word/document.xml")))
                if extension == ".hwpx" and "Contents/section0.xml" in names:
                    profile.update(_analyze_hwpx_structure(archive.read("Contents/section0.xml")))
                if extension == ".xlsx":
                    sheets = [
                        _analyze_xlsx_structure(archive.read(name))
                        for name in names
                        if name.startswith("xl/worksheets/") and name.endswith(".xml")
                    ]
                    profile["worksheets"] = sheets[:20]
        except zipfile.BadZipFile:
            profile["warning"] = "Template package could not be inspected."
    elif extension == ".md":
        text = content.decode("utf-8", errors="ignore")
        profile["heading_levels"] = sorted(set(re.findall(r"^(#{1,6})\s", text, flags=re.MULTILINE)))
        profile["has_tables"] = "|" in text
    elif extension == ".pdf":
        profile["reference_mode"] = "visual_layout_reference"
        try:
            import fitz

            document = fitz.open(stream=content, filetype="pdf")
            fields = []
            for page in document:
                fields.extend(
                    str(widget.field_name)
                    for widget in (page.widgets() or [])
                    if widget.field_name
                )
            document.close()
            profile["form_fields"] = fields[:80]
            if fields:
                profile["template_mode"] = "populate_uploaded_form"
        except (ImportError, RuntimeError, ValueError):
            pass
    return profile


def _analyze_docx_structure(document_xml: bytes) -> dict:
    """Expose the visible form labels so planning can fill the supplied Word form."""
    root = etree.fromstring(document_xml)
    tables: list[dict] = []
    visible_text: list[str] = []
    for table in root.xpath(".//w:tbl", namespaces=_W_NS):
        rows: list[list[str]] = []
        for row in table.xpath("./w:tr", namespaces=_W_NS):
            cells = [
                "".join(cell.xpath(".//w:t/text()", namespaces=_W_NS)).strip()
                for cell in row.xpath("./w:tc", namespaces=_W_NS)
            ]
            rows.append(cells)
            visible_text.extend(value for value in cells if value)
        tables.append({"rows": rows[:20]})
    controls = []
    for control in root.xpath(".//w:sdt", namespaces=_W_NS):
        tags = control.xpath("./w:sdtPr/w:tag/@w:val", namespaces=_W_NS)
        aliases = control.xpath("./w:sdtPr/w:alias/@w:val", namespaces=_W_NS)
        controls.append(
            {
                "tag": tags[0] if tags else "",
                "alias": aliases[0] if aliases else "",
                "display_text": "".join(
                    control.xpath(".//w:sdtContent//w:t/text()", namespaces=_W_NS)
                ).strip(),
            }
        )
    return {
        "document_text": visible_text[:40],
        "tables": tables[:5],
        "content_controls": controls[:40],
        "template_mode": "populate_uploaded_form",
    }


def _analyze_hwpx_structure(section_xml: bytes) -> dict:
    root = etree.fromstring(section_xml)
    text = [
        value.strip()
        for value in root.xpath(".//*[local-name()='t']/text()")
        if value.strip()
    ]
    placeholders = [
        value for value in text
        if "(" in value or "\u25e6\u25e6" in value or "{{" in value
    ]
    tables = []
    for table in root.xpath(".//*[local-name()='tbl']"):
        rows = []
        blank_cells = 0
        for row in table.xpath(".//*[local-name()='tr']"):
            cells = []
            for cell in row.xpath("./*[local-name()='tc']"):
                value = "".join(cell.xpath(".//*[local-name()='t']/text()")).strip()
                cells.append(value)
                blank_cells += int(not value)
            rows.append(cells)
        tables.append({"rows": rows[:10], "blank_cells": blank_cells})
    return {
        "document_text": text[:80],
        "placeholders": placeholders[:30],
        "tables": tables[:10],
        "template_mode": "populate_uploaded_form",
    }


def _analyze_xlsx_structure(sheet_xml: bytes) -> dict:
    root = etree.fromstring(sheet_xml)
    values = [
        value.strip()
        for value in root.xpath(".//*[local-name()='t']/text()")
        if value.strip()
    ]
    return {
        "document_text": values[:60],
        "placeholders": [value for value in values if "{{" in value and "}}" in value][:30],
        "styled_blank_cells": len(
            root.xpath(".//*[local-name()='c'][@s and not(*[local-name()='v' or local-name()='is'])]")
        ),
        "template_mode": "populate_uploaded_form",
    }
