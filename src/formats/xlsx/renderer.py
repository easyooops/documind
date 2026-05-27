"""Styled XLSX writer using SpreadsheetML without an optional runtime dependency."""

# ruff: noqa: E501

from __future__ import annotations

import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from src.formats.base import DocumentRenderer


class XLSXRenderer(DocumentRenderer):
    @property
    def format_name(self) -> str:
        return "xlsx"

    @property
    def mime_type(self) -> str:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    @property
    def file_extension(self) -> str:
        return ".xlsx"

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
        path = output_dir / f"workbook_{uuid.uuid4().hex[:8]}.xlsx"
        if template_bytes:
            _render_in_uploaded_template(template_bytes, path, document_spec)
            return path
        sheets = _sheets(document_spec, design)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("[Content_Types].xml", _content_types(len(sheets)))
            package.writestr("_rels/.rels", _root_relationships())
            package.writestr("docProps/core.xml", _core(document_spec.get("title", "Workbook")))
            package.writestr("docProps/app.xml", _app(sheets))
            package.writestr("xl/workbook.xml", _workbook(sheets))
            package.writestr("xl/_rels/workbook.xml.rels", _workbook_relationships(len(sheets)))
            package.writestr("xl/styles.xml", _styles(design))
            for index, sheet in enumerate(sheets, 1):
                package.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet(sheet))
        return path


def _sheets(spec: dict, design: dict) -> list[dict]:
    sheets = []
    used_names: set[str] = set()
    for index, section in enumerate(spec.get("sections", []), 1):
        name = _sheet_name(section.get("title", f"Section {index}"), used_names)
        rows = [(1, [section.get("title", name)], 2)]
        row = 3
        if section.get("purpose"):
            rows.append((row, [section["purpose"]], 6))
            row += 2
        for block in section.get("blocks", []):
            kind = block.get("type")
            if kind == "table":
                rows.append((row, block.get("headers", []), 7))
                row += 1
                for cells in block.get("rows", []):
                    rows.append((row, cells, 8))
                    row += 1
                row += 1
            elif kind == "kpi_grid":
                rows.append((row, ["INDICATOR", "VALUE", "CONTEXT"], 7))
                row += 1
                for metric in block.get("items", []):
                    rows.append((row, [metric.get("label", ""), metric.get("value", ""), metric.get("context", "")], 8))
                    row += 1
            elif kind in {"callout", "paragraph", "quote"}:
                rows.append((row, [block.get("title", kind.title()), block.get("text", "")], 5))
                row += 2
            else:
                rows.append((row, [kind.replace("_", " ").title(), "; ".join(block.get("items", []))], 5))
                row += 2
        filter_range = None
        freeze = None
        for current_row, values, style in rows:
            if style == 7 and values:
                filter_range = f"A{current_row}:{_col(len(values))}{max(current_row + 1, row - 1)}"
                freeze = f"A{current_row + 1}"
                break
        sheets.append({"name": name, "rows": rows, "merge": ["A1:F1"], "freeze": freeze, "filter": filter_range})
    if sheets:
        return sheets
    return [
        {
            "name": _sheet_name(spec.get("title", "Workbook"), used_names),
            "rows": [(1, [spec.get("title", "Workbook")], 2), (3, [spec.get("executive_summary", "")], 6)],
            "merge": ["A1:F1", "A3:F4"],
            "freeze": None,
            "filter": None,
        }
    ]


def _render_in_uploaded_template(template_bytes: bytes, path: Path, spec: dict) -> None:
    replacements = {
        "{{title}}": str(spec.get("title", "")),
        "{{subtitle}}": str(spec.get("subtitle", "")),
        "{{executive_summary}}": str(spec.get("executive_summary", "")),
    }
    with zipfile.ZipFile(BytesIO(template_bytes)) as source:
        worksheet_names = [
            name for name in source.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
        first_sheet = worksheet_names[0] if worksheet_names else ""
        payloads = {}
        filled = 0
        for part in source.infolist():
            payload = source.read(part.filename)
            if part.filename.endswith(".xml"):
                xml_text = payload.decode("utf-8", errors="ignore")
                for marker, value in replacements.items():
                    xml_text = xml_text.replace(marker, escape(value))
                payload = xml_text.encode("utf-8")
            if part.filename in worksheet_names:
                payload, count = _fill_template_cells(payload, spec)
                filled += count
            payloads[part.filename] = payload
        if first_sheet and not filled:
            payloads[first_sheet] = _append_template_rows(payloads[first_sheet], spec)
        with zipfile.ZipFile(path, "w") as output:
            for part in source.infolist():
                output.writestr(part, payloads[part.filename])


def _fill_template_cells(worksheet_xml: bytes, spec: dict) -> tuple[bytes, int]:
    root = etree.fromstring(worksheet_xml)
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    values = _template_values(spec)
    index = 0
    for cell in root.xpath(".//*[local-name()='c'][@s]"):
        if index >= len(values):
            break
        has_value = cell.xpath("./*[local-name()='v' or local-name()='is']")
        if has_value:
            continue
        cell.set("t", "inlineStr")
        inline = etree.SubElement(cell, f"{{{namespace}}}is")
        text = etree.SubElement(inline, f"{{{namespace}}}t")
        text.text = values[index]
        index += 1
    if not index:
        return worksheet_xml, 0
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), index


def _template_values(spec: dict) -> list[str]:
    values = [str(spec.get("title", "")), str(spec.get("subtitle", ""))]
    values.extend(str(item.get("value", "")) for item in spec.get("metadata", []))
    values.append(str(spec.get("executive_summary", "")))
    for section in spec.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") == "table":
                values.extend(str(value) for row in block.get("rows", []) for value in row)
    return [value for value in values if value]


def _append_template_rows(worksheet_xml: bytes, spec: dict) -> bytes:
    root = etree.fromstring(worksheet_xml)
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet_data = root.find(f"{{{namespace}}}sheetData")
    if sheet_data is None:
        return worksheet_xml
    existing_rows = sheet_data.findall(f"{{{namespace}}}row")
    row_number = max((int(row.get("r", "0")) for row in existing_rows), default=0) + 2
    rows = [[str(spec.get("title", ""))], [str(spec.get("executive_summary", ""))]]
    for section in spec.get("sections", []):
        rows.append([str(section.get("title", ""))])
        for block in section.get("blocks", []):
            if block.get("type") == "table":
                rows.append([str(value) for value in block.get("headers", [])])
                rows.extend([[str(value) for value in values] for values in block.get("rows", [])])
    for values in rows:
        row = etree.SubElement(sheet_data, f"{{{namespace}}}row", r=str(row_number))
        for column, value in enumerate(values, 1):
            cell = etree.SubElement(row, f"{{{namespace}}}c", r=f"{_col(column)}{row_number}", t="inlineStr")
            inline = etree.SubElement(cell, f"{{{namespace}}}is")
            text = etree.SubElement(inline, f"{{{namespace}}}t")
            text.text = value
        row_number += 1
    dimension = root.find(f"{{{namespace}}}dimension")
    if dimension is not None:
        dimension.set("ref", f"A1:F{row_number - 1}")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _worksheet(sheet: dict) -> str:
    row_xml = []
    for row_number, values, style in sheet["rows"]:
        cells = "".join(_cell(row_number, index + 1, value, style) for index, value in enumerate(values))
        row_xml.append(f'<row r="{row_number}" ht="{32 if style in (1, 2) else 24}" customHeight="1">{cells}</row>')
    merges = sheet.get("merge", [])
    merged_xml = f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{item}"/>' for item in merges) + "</mergeCells>" if merges else ""
    frozen = (
        f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{_row(sheet["freeze"]) - 1}" topLeftCell="{sheet["freeze"]}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        if sheet.get("freeze") else '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
    )
    auto_filter = f'<autoFilter ref="{sheet["filter"]}"/>' if sheet.get("filter") else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'{frozen}<sheetFormatPr defaultRowHeight="20"/>'
        '<cols><col min="1" max="1" width="24" customWidth="1"/><col min="2" max="2" width="26" customWidth="1"/>'
        '<col min="3" max="6" width="28" customWidth="1"/></cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>{merged_xml}{auto_filter}'
        '<pageMargins left="0.4" right="0.4" top="0.65" bottom="0.65" header="0.3" footer="0.3"/>'
        '</worksheet>'
    )


def _cell(row: int, column: int, value: object, style: int) -> str:
    address = f"{_col(column)}{row}"
    if style == 8 and _numeric_value(value) is not None:
        return f'<c r="{address}" s="9"><v>{_numeric_value(value)}</v></c>'
    text = escape(str(value or ""))
    return f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _styles(design: dict) -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="4"><font><sz val="10"/><name val="Malgun Gothic"/></font><font><b/><sz val="10"/><color rgb="FF1F4E79"/><name val="Malgun Gothic"/></font><font><b/><sz val="18"/><color rgb="FF1F4E79"/><name val="Malgun Gothic"/></font><font><sz val="10"/><color rgb="FF404040"/><name val="Malgun Gothic"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9E2F3"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF4F6F8"/></patternFill></fill></fills>
<borders count="2"><border/><border><left style="thin"><color rgb="FFB7C5D6"/></left><right style="thin"><color rgb="FFB7C5D6"/></right><top style="thin"><color rgb="FFB7C5D6"/></top><bottom style="thin"><color rgb="FFB7C5D6"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="10"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def _workbook(sheets: list) -> str:
    sheet_xml = "".join(f'<sheet name="{escape(sheet["name"])}" sheetId="{index}" r:id="rId{index}"/>' for index, sheet in enumerate(sheets, 1))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="1"/></bookViews><sheets>{sheet_xml}</sheets></workbook>'''


def _workbook_relationships(count: int) -> str:
    relationships = "".join(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>' for index in range(1, count + 1))
    return f'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''


def _content_types(count: int) -> str:
    overrides = "".join(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for index in range(1, count + 1))
    return f'''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{overrides}<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>'''


def _root_relationships() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>'''


def _core(title: object) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(str(title))}</dc:title><dc:creator>DocuMind</dc:creator></cp:coreProperties>'''


def _app(sheets: list) -> str:
    names = "".join(f"<vt:lpstr>{escape(sheet['name'])}</vt:lpstr>" for sheet in sheets)
    return f'''<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>DocuMind</Application><TitlesOfParts><vt:vector size="{len(sheets)}" baseType="lpstr">{names}</vt:vector></TitlesOfParts></Properties>'''


def _find_blocks(spec: dict, kind: str) -> list[dict]:
    return [block for section in spec.get("sections", []) for block in section.get("blocks", []) if block.get("type") == kind]


def _sheet_name(value: object, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", str(value or "Section")).strip()[:31] or "Section"
    name, suffix = base, 2
    while name in used:
        name = f"{base[:27]} {suffix}"
        suffix += 1
    used.add(name)
    return name


def _col(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _row(cell: str) -> int:
    return int(re.sub(r"\D", "", cell))


def _color(value: object, fallback: str) -> str:
    candidate = str(value or fallback).lstrip("#").upper()
    return candidate if len(candidate) == 6 else fallback


def _numeric_value(value: object) -> str | None:
    candidate = str(value or "").strip().replace(",", "")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", candidate):
        return candidate
    return None
