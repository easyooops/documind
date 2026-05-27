"""Styled native DOCX renderer implemented with WordprocessingML."""

# ruff: noqa: E501

from __future__ import annotations

import ast
import uuid
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from src.formats.base import DocumentRenderer

_W_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_NS = {"w": _W_URI}
_W = f"{{{_W_URI}}}"


class DOCXRenderer(DocumentRenderer):
    @property
    def format_name(self) -> str:
        return "docx"

    @property
    def mime_type(self) -> str:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    @property
    def file_extension(self) -> str:
        return ".docx"

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
        path = output_dir / f"document_{uuid.uuid4().hex[:8]}.docx"
        if template_bytes:
            _render_in_uploaded_template(template_bytes, path, document_spec)
            return path
        primary = _color(design.get("primary"), "12304A")
        accent = _color(design.get("accent"), "17A2A4")
        background = _color(design.get("background"), "F3F6F8")
        heading_font = escape(str(design.get("font_heading", "Malgun Gothic")))
        body_font = escape(str(design.get("font_body", "Malgun Gothic")))

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", _content_types())
            package.writestr("_rels/.rels", _root_rels())
            package.writestr("docProps/core.xml", _core(document_spec.get("title", "Document")))
            package.writestr("docProps/app.xml", _app())
            package.writestr("word/_rels/document.xml.rels", _document_rels())
            package.writestr("word/styles.xml", _styles(primary, accent, heading_font, body_font))
            package.writestr("word/header1.xml", _header(document_spec, primary, accent))
            package.writestr("word/footer1.xml", _footer(accent))
            package.writestr(
                "word/document.xml",
                _document_xml(document_spec, primary, accent, background),
            )
        return path


def _render_in_uploaded_template(template_bytes: bytes, path: Path, spec: dict) -> None:
    """Populate an uploaded DOCX while preserving its package, theme and form layout."""
    try:
        with zipfile.ZipFile(BytesIO(template_bytes)) as template:
            populated = _populate_template_document(template.read("word/document.xml"), spec)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
                for part in template.infolist():
                    content = populated if part.filename == "word/document.xml" else template.read(part.filename)
                    output.writestr(part, content)
    except (KeyError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise ValueError("Uploaded DOCX template cannot be populated as a native Word form.") from exc


def _populate_template_document(document_xml: bytes, spec: dict) -> bytes:
    root = etree.fromstring(document_xml)
    _replace_template_placeholders(root, spec)
    _populate_content_controls(root, spec)
    for table in root.xpath(".//w:tbl", namespaces=_W_NS):
        rows = table.xpath("./w:tr", namespaces=_W_NS)
        for row_index, row in enumerate(rows):
            cells = row.xpath("./w:tc", namespaces=_W_NS)
            for cell_index, cell in enumerate(cells):
                value = _template_field_value(_node_text(cell), spec)
                if not value:
                    continue
                if cell_index + 1 < len(cells) and not _node_text(cells[cell_index + 1]):
                    _set_cell_lines(cells[cell_index + 1], value)
                    continue
                if row_index + 1 < len(rows):
                    following = rows[row_index + 1].xpath("./w:tc", namespaces=_W_NS)
                    if cell_index < len(following):
                        _set_cell_lines(following[cell_index], value)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _replace_template_placeholders(root: etree._Element, spec: dict) -> None:
    replacements = {
        "{{title}}": str(spec.get("title", "")),
        "{{subtitle}}": str(spec.get("subtitle", "")),
        "{{executive_summary}}": str(spec.get("executive_summary", "")),
    }
    for item in spec.get("metadata", []):
        replacements[f"{{{{{str(item.get('label', '')).strip()}}}}}"] = _value_text(
            item.get("value", "")
        )
    for section in spec.get("sections", []):
        replacements[f"{{{{{str(section.get('title', '')).strip()}}}}}"] = "\n".join(
            _block_lines(section.get("blocks", []))
        )
    for paragraph in root.xpath(".//w:p", namespaces=_W_NS):
        original = _node_text(paragraph)
        updated = original
        for marker, value in replacements.items():
            updated = updated.replace(marker, value)
        if updated != original:
            _set_paragraph_text(paragraph, updated)


def _populate_content_controls(root: etree._Element, spec: dict) -> None:
    for control in root.xpath(".//w:sdt", namespaces=_W_NS):
        tags = control.xpath("./w:sdtPr/w:tag/@w:val", namespaces=_W_NS)
        aliases = control.xpath("./w:sdtPr/w:alias/@w:val", namespaces=_W_NS)
        candidates = [*tags, *aliases, _node_text(control)]
        lines: list[str] = []
        for candidate in candidates:
            if _label_key(candidate) in {"title", "documenttitle", "\uc81c\ubaa9", "\ubb38\uc11c\uc81c\ubaa9"}:
                lines = [str(spec.get("title", ""))]
            elif _label_key(candidate) in {"summary", "executivesummary", "\uc694\uc57d", "\ubcf4\uace0\uc694\uc57d"}:
                lines = [str(spec.get("executive_summary", ""))]
            else:
                lines = _template_field_value(candidate, spec)
            if lines:
                break
        contents = control.xpath("./w:sdtContent", namespaces=_W_NS)
        if lines and contents:
            _set_content_lines(contents[0], lines)


def _template_field_value(label: str, spec: dict) -> list[str]:
    key = _label_key(label)
    if not key:
        return []
    metadata_mapping = {
        "\uc18c\uc18d": ("\ubcf4\uace0\ubd80\uc11c", "\uc791\uc131\ubd80\uc11c", "\uc18c\uc18d", "\ubd80\uc11c"),
        "\uc8fc\ucc28": ("\ubcf4\uace0\uae30\uac04", "\uc8fc\ucc28", "\uae30\uac04"),
        "\uc791\uc131\uc77c": ("\uc791\uc131\uc77c", "\ubcf4\uace0\uc77c", "\uc77c\uc790"),
        "\uc791\uc131\uc790": ("\uc791\uc131\uc790", "\ubcf4\uace0\uc790", "\ub2f4\ub2f9\uc790"),
    }
    for marker, candidate_labels in metadata_mapping.items():
        if marker in key:
            value = _metadata_value(spec, candidate_labels)
            return [value] if value else []

    section_aliases = {
        "\uc9c0\ub09c\uc8fc\uc8fc\uc694\uc5c5\ubb34": ("\uc9c0\ub09c\uc8fc", "\uc804\uc8fc", "\uc2e4\uc801", "\ud575\uc2ec\uc694\uc57d"),
        "\uae08\uc8fc\uc8fc\uc694\uc5c5\ubb34": ("\uae08\uc8fc", "\uc8fc\uc694\uc5c5\ubb34", "\ucd94\uc9c4\ud604\ud669"),
        "\uc9c0\ub09c\uc8fc\uc758\uc0ac\uacb0\uc815\uc0ac\ud56d": ("\uc9c0\ub09c\uc8fc\uc758\uc0ac\uacb0\uc815", "\uc758\uc0ac\uacb0\uc815", "\uc774\uc288"),
        "\uae08\uc8fc\uc758\uc0ac\uacb0\uc815\ub17c\uc758\uc0ac\ud56d": ("\uae08\uc8fc\uc758\uc0ac\uacb0\uc815", "\ub17c\uc758", "\ud611\uc870", "\uc774\uc288"),
        "\uc774\ubc88\uc8fc\ubbf8\uacb0\uc815\uc0ac\ud56d": ("\ubbf8\uacb0\uc815", "\ubbf8\uacb0", "\uc774\uc288", "\ud611\uc870"),
    }
    for marker, aliases in section_aliases.items():
        if marker in key:
            return _section_lines(spec, aliases)
    return _section_lines(spec, (key,))


def _metadata_value(spec: dict, candidate_labels: tuple[str, ...]) -> str:
    candidates = {_label_key(item) for item in candidate_labels}
    for item in spec.get("metadata", []):
        label = _label_key(str(item.get("label", "")))
        if any(candidate in label or label in candidate for candidate in candidates):
            return _value_text(item.get("value", ""))
    return ""


def _section_lines(spec: dict, aliases: tuple[str, ...]) -> list[str]:
    normalized_aliases = tuple(_label_key(alias) for alias in aliases)
    for section in spec.get("sections", []):
        title = _label_key(str(section.get("title", "")))
        if any(alias in title or title in alias for alias in normalized_aliases if alias):
            lines = _block_lines(section.get("blocks", []))
            if lines:
                return lines[:8]
    return []


def _block_lines(blocks: list) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind in {"paragraph", "callout", "quote"}:
            text = _value_text(block.get("text", ""))
            if text:
                lines.append(text)
        elif kind in {"bullet_list", "timeline", "action_items"}:
            lines.extend(_value_text(item) for item in block.get("items", []) if _value_text(item))
        elif kind == "table":
            lines.extend(" | ".join(_value_text(item) for item in row) for row in block.get("rows", []))
        elif kind == "kpi_grid":
            lines.extend(
                f"{_value_text(item.get('label', ''))}: {_value_text(item.get('value', ''))}"
                for item in block.get("items", [])
            )
    return [line for line in lines if line.strip()]


def _node_text(node: etree._Element) -> str:
    return "".join(node.xpath(".//w:t/text()", namespaces=_W_NS)).strip()


def _label_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _set_cell_lines(cell: etree._Element, lines: list[str]) -> None:
    if not lines:
        return
    paragraphs = cell.xpath("./w:p", namespaces=_W_NS)
    prototype = paragraphs[0] if paragraphs else None
    for child in list(cell):
        if child.tag != f"{_W}tcPr":
            cell.remove(child)
    for line in lines:
        paragraph = deepcopy(prototype) if prototype is not None else etree.Element(f"{_W}p")
        _set_paragraph_text(paragraph, line)
        cell.append(paragraph)


def _set_content_lines(content: etree._Element, lines: list[str]) -> None:
    paragraphs = content.xpath(".//w:p", namespaces=_W_NS)
    prototype = paragraphs[0] if paragraphs else None
    for child in list(content):
        content.remove(child)
    for line in lines:
        paragraph = deepcopy(prototype) if prototype is not None else etree.Element(f"{_W}p")
        _set_paragraph_text(paragraph, line)
        content.append(paragraph)


def _set_paragraph_text(paragraph: etree._Element, text: str) -> None:
    run_properties = paragraph.xpath("./w:r[1]/w:rPr", namespaces=_W_NS)
    for child in list(paragraph):
        if child.tag != f"{_W}pPr":
            paragraph.remove(child)
    run = etree.SubElement(paragraph, f"{_W}r")
    if run_properties:
        run.append(deepcopy(run_properties[0]))
    value = etree.SubElement(run, f"{_W}t")
    value.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    value.text = text


def _value_text(value: object) -> str:
    value = _action_data(value) or value
    if not isinstance(value, dict):
        return str(value)
    text = str(
        value.get("action")
        or value.get("task")
        or value.get("title")
        or value.get("description")
        or value.get("text")
        or ""
    )
    labels = {
        "owner": "\ub2f4\ub2f9",
        "due_date": "\uae30\ud55c",
        "priority": "\uc6b0\uc120\uc21c\uc704",
        "status": "\uc0c1\ud0dc",
    }
    details = [
        f"{labels[key]}: {value[key]}"
        for key in labels
        if value.get(key) not in (None, "")
    ]
    if not text:
        text = " / ".join(
            f"{key}: {field_value}" for key, field_value in value.items() if field_value
        )
    return f"{text} ({' | '.join(details)})" if details else text


def _action_data(value: object) -> dict | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return None
    try:
        candidate = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _document_xml(spec: dict, primary: str, accent: str, background: str) -> str:
    korean = spec.get("language", "ko_mixed") != "en"
    summary_label = "보고 요약" if korean else "Executive Summary"
    body = [
        _paragraph(_display_label(str(spec.get("document_type", "REPORT"))), "Eyebrow"),
        _paragraph(str(spec.get("title", "Document")), "Title"),
        _paragraph(str(spec.get("subtitle", "")), "Subtitle"),
        _metadata_table(spec.get("metadata", []), primary, background),
        _paragraph(summary_label, "Heading1"),
        _callout("", str(spec.get("executive_summary", "")), accent, background),
    ]
    for section in spec.get("sections", []):
        body.append(_paragraph(str(section.get("title", "")), "Heading1"))
        for block in section.get("blocks", []):
            body.extend(_block(block, primary, accent, background, korean))
    section_properties = (
        '<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
        '<w:footerReference w:type="default" r:id="rIdFooter"/>'
        '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1080" w:right="1120" '
        'w:bottom="1080" w:left="1120" w:header="620" w:footer="620"/>'
        '<w:cols w:space="720"/></w:sectPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{''.join(body)}{section_properties}</w:body></w:document>"
    )


def _block(block: dict, primary: str, accent: str, background: str, korean: bool) -> list[str]:
    kind = block.get("type")
    if kind == "paragraph":
        return [_paragraph(str(block.get("text", "")), "BodyText")]
    if kind in {"callout", "quote"}:
        return [_callout(str(block.get("title", "")), str(block.get("text", "")), accent, background)]
    if kind == "action_items" and any(_action_data(item) for item in block.get("items", [])):
        return [_action_table(block.get("items", []), primary, korean)]
    if kind in {"bullet_list", "timeline", "action_items"}:
        return [_paragraph(_value_text(item), "ListBullet") for item in block.get("items", [])]
    if kind == "kpi_grid":
        return [_kpi_table(block.get("items", []), primary, accent)]
    if kind == "table":
        return [_table(block.get("headers", []), block.get("rows", []), primary)]
    return []


def _paragraph(text: str, style: str) -> str:
    return (
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def _metadata_table(items: list, primary: str, background: str) -> str:
    cells = []
    for item in items[:4]:
        cells.append(
            _cell(
                _paragraph(_display_label(str(item.get("label", ""))), "MetaLabel")
                + _paragraph(str(item.get("value", "")), "MetaValue"),
                background,
            )
        )
    return _simple_table([cells], widths=[2150] * max(1, len(cells)), border="D8DEE3")


def _kpi_table(items: list, primary: str, accent: str) -> str:
    cells = []
    for item in items[:4]:
        content = (
            _paragraph(_display_label(str(item.get("label", ""))), "MetaLabel")
            + _paragraph(str(item.get("value", "-")), "KPI")
            + _paragraph(str(item.get("context", "")), "Caption")
        )
        cells.append(_cell(content, "F5F6F7"))
    return _simple_table([cells], widths=[2100] * max(1, len(cells)), border="D8DEE3")


def _callout(title: str, text: str, accent: str, background: str) -> str:
    content = (_paragraph(title, "CalloutTitle") if title else "") + _paragraph(text, "Callout")
    return _simple_table([[_cell(content, background)]], widths=[8600], border="D8DEE3")


def _table(headers: list, rows: list, primary: str) -> str:
    table_rows = []
    if headers:
        table_rows.append([_cell(_paragraph(_value_text(item), "TableHeader"), primary) for item in headers])
    for index, row in enumerate(rows):
        fill = "FFFFFF" if index % 2 == 0 else "F3F6F8"
        table_rows.append([_cell(_paragraph(_value_text(item), "TableBody"), fill) for item in row])
    return _simple_table(table_rows, widths=[max(1000, 8600 // max(1, len(headers))) for _ in headers], border="D7E0E6")


def _action_table(items: list, primary: str, korean: bool) -> str:
    headers = (
        ["\uc5c5\ubb34\ub0b4\uc6a9", "\ub2f4\ub2f9\uc790", "\uc644\ub8cc\uae30\ud55c", "\uc6b0\uc120\uc21c\uc704"]
        if korean
        else ["Action", "Owner", "Due Date", "Priority"]
    )
    rows = []
    for item in items:
        data = _action_data(item)
        if data:
            rows.append(
                [
                    data.get("action") or data.get("task") or data.get("title") or data.get("text") or "",
                    data.get("owner", ""),
                    data.get("due_date") or data.get("deadline") or "",
                    data.get("priority") or data.get("status") or "",
                ]
            )
        else:
            rows.append([str(item), "", "", ""])
    return _table(headers, rows, primary)


def _cell(content: str, fill: str) -> str:
    return f'<w:tc><w:tcPr><w:shd w:fill="{fill}"/><w:tcMar><w:top w:w="120" w:type="dxa"/><w:start w:w="140" w:type="dxa"/><w:bottom w:w="120" w:type="dxa"/><w:end w:w="140" w:type="dxa"/></w:tcMar></w:tcPr>{content}</w:tc>'


def _simple_table(rows: list[list[str]], widths: list[int], border: str) -> str:
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    rendered_rows = "".join("<w:tr>" + "".join(cells) + "</w:tr>" for cells in rows)
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        f'<w:tblBorders><w:top w:val="single" w:sz="8" w:color="{border}"/>'
        f'<w:bottom w:val="single" w:sz="8" w:color="{border}"/>'
        f'<w:insideH w:val="single" w:sz="4" w:color="{border}"/>'
        '<w:left w:val="nil"/><w:right w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
        '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/></w:tblCellMar>'
        f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{rendered_rows}</w:tbl><w:p/>'
    )


def _styles(primary: str, accent: str, heading_font: str, body_font: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{body_font}" w:hAnsi="{body_font}" w:eastAsia="{body_font}"/><w:sz w:val="20"/><w:color w:val="24323D"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:before="500" w:after="180"/></w:pPr><w:rPr><w:rFonts w:ascii="{heading_font}" w:eastAsia="{heading_font}"/><w:b/><w:color w:val="{primary}"/><w:sz w:val="46"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:pPr><w:spacing w:after="420"/></w:pPr><w:rPr><w:color w:val="52606D"/><w:sz w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Eyebrow"><w:name w:val="Eyebrow"/><w:pPr><w:spacing w:before="220" w:after="90"/></w:pPr><w:rPr><w:b/><w:color w:val="{accent}"/><w:sz w:val="16"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:spacing w:before="420" w:after="150"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:color="D8DEE3" w:space="8"/></w:pBdr></w:pPr><w:rPr><w:rFonts w:ascii="{heading_font}" w:eastAsia="{heading_font}"/><w:b/><w:color w:val="{primary}"/><w:sz w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="BodyText"><w:name w:val="Body"/><w:pPr><w:spacing w:after="130" w:line="310" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="Bullet"/><w:pPr><w:ind w:left="360" w:hanging="230"/><w:spacing w:after="90"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="MetaLabel"><w:name w:val="Meta Label"/><w:rPr><w:b/><w:color w:val="{accent}"/><w:sz w:val="15"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="MetaValue"><w:name w:val="Meta Value"/><w:rPr><w:b/><w:color w:val="{primary}"/><w:sz w:val="19"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="KPI"><w:name w:val="KPI"/><w:rPr><w:b/><w:color w:val="{primary}"/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="Caption"/><w:rPr><w:color w:val="52606D"/><w:sz w:val="16"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="CalloutTitle"><w:name w:val="Callout Title"/><w:rPr><w:b/><w:color w:val="{primary}"/><w:sz w:val="21"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Callout"><w:name w:val="Callout"/><w:rPr><w:color w:val="24323D"/><w:sz w:val="19"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableHeader"><w:name w:val="Table Header"/><w:rPr><w:b/><w:color w:val="FFFFFF"/><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableBody"><w:name w:val="Table Body"/><w:rPr><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Source"><w:name w:val="Source"/><w:rPr><w:color w:val="52606D"/><w:sz w:val="16"/></w:rPr></w:style>
</w:styles>'''


def _header(spec: dict, primary: str, accent: str) -> str:
    title = escape(str(spec.get("title", "Document")))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:color="D8DEE3" w:space="8"/></w:pBdr></w:pPr><w:r><w:rPr><w:b/><w:color w:val="{primary}"/><w:sz w:val="16"/></w:rPr><w:t>{title}</w:t></w:r></w:p></w:hdr>'''


def _footer(accent: str) -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="right"/><w:pBdr><w:top w:val="single" w:sz="6" w:color="D8DEE3" w:space="6"/></w:pBdr></w:pPr><w:r><w:rPr><w:color w:val="687581"/><w:sz w:val="16"/></w:rPr><w:t>DocuMind  |  </w:t></w:r><w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p></w:ftr>'''


def _content_types() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>'''


def _root_rels() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'''


def _document_rels() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>'''


def _core(title: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(str(title))}</dc:title><dc:creator>DocuMind</dc:creator></cp:coreProperties>'''


def _app() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>DocuMind</Application></Properties>'''


def _color(value: object, fallback: str) -> str:
    candidate = str(value or fallback).replace("#", "").upper()
    return candidate if len(candidate) == 6 else fallback


def _display_label(value: str) -> str:
    return value if any("\uac00" <= char <= "\ud7a3" for char in value) else value.upper()
