"""Minimal styled OWPML/HWPX package writer for Hancom-compatible documents."""

# ruff: noqa: E501

from __future__ import annotations

import uuid
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from src.formats.base import DocumentRenderer
from src.formats.rich_document.spec import iter_text

_BASE_TEMPLATE = Path(__file__).parent / "assets" / "public_form_base.hwpx"
_REQUIRED_PARTS = {
    "mimetype",
    "version.xml",
    "Contents/header.xml",
    "Contents/section0.xml",
    "Contents/content.hpf",
    "META-INF/container.xml",
}


class HWPXRenderer(DocumentRenderer):
    @property
    def format_name(self) -> str:
        return "hwp"

    @property
    def mime_type(self) -> str:
        return "application/hwp+zip"

    @property
    def file_extension(self) -> str:
        return ".hwpx"

    async def render(
        self,
        document_spec: dict,
        output_dir: Path,
        *,
        design_system: dict | None = None,
        template_bytes: bytes | None = None,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"document_{uuid.uuid4().hex[:8]}.hwpx"
        if template_bytes:
            _render_in_uploaded_template(template_bytes, path, document_spec)
            _validate_hwpx_package(path)
            return path
        if not _BASE_TEMPLATE.exists():
            raise RuntimeError("A valid HWPX base template is required for native HWPX generation.")
        _render_new_document(_BASE_TEMPLATE.read_bytes(), path, document_spec)
        _validate_hwpx_package(path)
        return path


def _render_in_uploaded_template(template_bytes: bytes, path: Path, spec: dict) -> None:
    """Populate an HWPX form while preserving the uploaded native package and styles."""
    preview_text = "\n".join(iter_text(spec)).encode("utf-8")
    with zipfile.ZipFile(BytesIO(template_bytes)) as source, zipfile.ZipFile(path, "w") as output:
        for part in source.infolist():
            payload = source.read(part.filename)
            if part.filename.startswith("Contents/section") and part.filename.endswith(".xml"):
                payload = _populate_template_section(payload, spec)
            elif part.filename == "Preview/PrvText.txt":
                payload = preview_text
            output.writestr(part, payload)


def _render_new_document(base_bytes: bytes, path: Path, spec: dict) -> None:
    preview_text = "\n".join(iter_text(spec)).encode("utf-8")
    with zipfile.ZipFile(BytesIO(base_bytes)) as source, zipfile.ZipFile(path, "w") as output:
        for part in source.infolist():
            payload = source.read(part.filename)
            if part.filename == "Contents/section0.xml":
                payload = _new_section_from_base(payload, spec)
            elif part.filename == "Contents/content.hpf":
                payload = _replace_package_title(payload, spec)
            elif part.filename == "Preview/PrvText.txt":
                payload = preview_text
            output.writestr(part, payload)


def _validate_hwpx_package(path: Path) -> None:
    """Fail before delivery when the generated OWPML package is structurally unsafe."""
    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            names = {part.filename for part in infos}
            missing = sorted(_REQUIRED_PARTS - names)
            if missing:
                raise ValueError(f"Generated HWPX package is missing required parts: {', '.join(missing)}")
            if not infos or infos[0].filename != "mimetype" or infos[0].compress_type != zipfile.ZIP_STORED:
                raise ValueError("Generated HWPX package must store an uncompressed mimetype as its first entry.")
            if package.read("mimetype").decode("ascii", errors="ignore").strip() != "application/hwp+zip":
                raise ValueError("Generated HWPX package has an invalid mimetype.")
            for name in (
                "version.xml",
                "Contents/header.xml",
                "Contents/content.hpf",
                "META-INF/container.xml",
            ):
                etree.fromstring(package.read(name))
            section_parts = sorted(
                name for name in names
                if name.startswith("Contents/section") and name.endswith(".xml")
            )
            if not section_parts:
                raise ValueError("Generated HWPX package has no document section.")
            for name in section_parts:
                section = etree.fromstring(package.read(name))
                if len(section.xpath(".//*[local-name()='secPr']")) != 1:
                    raise ValueError(f"Generated HWPX section has invalid section properties: {name}")
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, UnicodeDecodeError) as exc:
        raise ValueError("Generated HWPX package failed structural validation.") from exc


def _new_section_from_base(section_xml: bytes, spec: dict) -> bytes:
    root = etree.fromstring(section_xml)
    paragraphs = root.xpath("./*[local-name()='p']")
    heading = paragraphs[0]
    table_wrapper = paragraphs[1]
    table_template = root.xpath(".//*[local-name()='tbl']")[1]
    body = next(
        paragraph for paragraph in paragraphs[3:]
        if not paragraph.xpath(".//*[local-name()='tbl']")
    )
    for child in list(root):
        root.remove(child)
    content = [
        str(spec.get("document_type", "HWPX")),
        str(spec.get("title", "")),
        str(spec.get("subtitle", "")),
    ]
    content.extend(
        f"{item.get('label', '')} | {item.get('value', '')}"
        for item in spec.get("metadata", [])
    )
    content.extend(["", "\uc694\uc57d", str(spec.get("executive_summary", ""))])
    for index, text in enumerate(value for value in content if value):
        paragraph = _content_paragraph(
            heading if index in {0, 1, 4} else body,
            text,
            preserve_section=index == 0,
        )
        root.append(paragraph)
    for section in spec.get("sections", []):
        for text in (str(section.get("title", "")), str(section.get("purpose", ""))):
            if text:
                paragraph = _content_paragraph(
                    heading if text == str(section.get("title", "")) else body,
                    text,
                )
                root.append(paragraph)
        for block in section.get("blocks", []):
            if block.get("type") == "table" and block.get("headers"):
                root.append(_table_paragraph(table_wrapper, table_template, block))
                continue
            for text in _block_lines(block):
                if text:
                    paragraph = _content_paragraph(body, text)
                    root.append(paragraph)
    if len(root.xpath(".//*[local-name()='secPr']")) != 1:
        raise ValueError("Generated HWPX section must retain exactly one section-properties block.")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _content_paragraph(template, text: str, *, preserve_section: bool = False):
    paragraph = deepcopy(template)
    paragraph.set("id", str(uuid.uuid4().int % 2_000_000_000))
    if not preserve_section:
        for section_properties in paragraph.xpath(".//*[local-name()='secPr']"):
            parent = section_properties.getparent()
            if parent is not None:
                parent.remove(section_properties)
    _set_paragraph_text(paragraph, text)
    return paragraph


def _table_paragraph(wrapper_template, table_template, block: dict):
    wrapper = deepcopy(wrapper_template)
    wrapper.set("id", str(uuid.uuid4().int % 2_000_000_000))
    for section_properties in wrapper.xpath(".//*[local-name()='secPr']"):
        parent = section_properties.getparent()
        if parent is not None:
            parent.remove(section_properties)
    run = wrapper.xpath("./*[local-name()='run']")[0]
    for child in list(run):
        run.remove(child)
    table = deepcopy(table_template)
    rows = table.xpath("./*[local-name()='tr']")
    if not rows:
        return wrapper
    headers = [str(value) for value in block.get("headers", [])]
    data_rows = [[str(value) for value in row] for row in block.get("rows", [])]
    template_row = deepcopy(rows[-1])
    for row in rows[2:]:
        table.remove(row)
    _write_table_row(rows[0], headers)
    if data_rows:
        _write_table_row(rows[1], data_rows[0])
        for values in data_rows[1:]:
            row = deepcopy(template_row)
            _write_table_row(row, values)
            table.append(row)
    table.set("rowCnt", str(len(table.xpath("./*[local-name()='tr']"))))
    run.append(table)
    for element in wrapper.xpath(".//*[@id]"):
        element.set("id", str(uuid.uuid4().int % 2_000_000_000))
    return wrapper


def _write_table_row(row, values: list[str]) -> None:
    for index, cell in enumerate(row.xpath("./*[local-name()='tc']")):
        _set_cell_text(cell, values[index] if index < len(values) else "")


def _block_lines(block: dict) -> list[str]:
    kind = block.get("type")
    if kind == "table":
        return [
            " | ".join(str(value) for value in block.get("headers", [])),
            *[" | ".join(str(value) for value in row) for row in block.get("rows", [])],
        ]
    if kind == "kpi_grid":
        return [
            f"{item.get('label', '')} | {item.get('value', '')} | {item.get('context', '')}"
            for item in block.get("items", [])
        ]
    if kind in {"bullet_list", "timeline", "action_items"}:
        return [f"- {item}" for item in block.get("items", [])]
    return [str(block.get("title", "")), str(block.get("text", ""))]


def _replace_package_title(content_xml: bytes, spec: dict) -> bytes:
    root = etree.fromstring(content_xml)
    titles = root.xpath(".//*[local-name()='title']")
    if titles:
        titles[0].text = str(spec.get("title", ""))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _populate_template_section(section_xml: bytes, spec: dict) -> bytes:
    root = etree.fromstring(section_xml)
    product_name = _metadata_value(spec, ("\uc81c\ud488\uba85", "product name")) or str(spec.get("title", ""))
    model_name = _metadata_value(spec, ("\ubaa8\ub378\uba85", "model")) or "DM-1000"
    summary = str(spec.get("executive_summary", "")).strip()
    content_values = _narrative_values(spec)
    for node in root.xpath(".//*[local-name()='t']"):
        value = (node.text or "").strip()
        if "\u25e6\u25e6(\uc81c\ud488\uba85)" in value:
            node.text = (node.text or "").replace("\u25e6\u25e6(\uc81c\ud488\uba85)", product_name)
        elif "\ubb3c\ud488\uc2dd\ubcc4\ubc88\ud638\uc640 \ubaa8\ub378\uba85 \ud3ec\ud568" in value:
            node.text = f"{product_name} / {model_name}"
        elif value.startswith("{{") and value.endswith("}}"):
            node.text = _template_placeholder(value, spec)
    substitutions = [
        ("\ub300\ud45c\uae30\uc220\uc744 \uc801\uc6a9\ud55c \uc81c\ud488", summary or product_name),
        ("\uc801\uc6a9\uae30\uc220\uc5d0 \ub530\ub978 \uc81c\ud488\uc758 \ud2b9\uc9d5", content_values[0] if content_values else summary),
    ]
    for marker, replacement in substitutions:
        if replacement:
            _replace_matching_paragraph(root, marker, replacement)
    _populate_blank_table_cells(root, spec)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _metadata_value(spec: dict, labels: tuple[str, ...]) -> str:
    for item in spec.get("metadata", []):
        label = str(item.get("label", "")).lower()
        if any(candidate.lower() in label for candidate in labels):
            return str(item.get("value", "")).strip()
    return ""


def _narrative_values(spec: dict) -> list[str]:
    values = []
    for section in spec.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") in {"paragraph", "callout", "quote"} and block.get("text"):
                values.append(str(block["text"]))
    return values


def _template_placeholder(value: str, spec: dict) -> str:
    key = value.strip("{} ").lower()
    if key in {"title", "\uc81c\ud488\uba85"}:
        return str(spec.get("title", ""))
    if key in {"summary", "executive_summary", "\uc694\uc57d"}:
        return str(spec.get("executive_summary", ""))
    return value


def _populate_blank_table_cells(root, spec: dict) -> None:
    rows = [
        row
        for section in spec.get("sections", [])
        for block in section.get("blocks", [])
        if block.get("type") == "table"
        for row in block.get("rows", [])
    ]
    values = [str(value) for row in rows for value in row]
    if not values:
        values = [
            str(item.get("value", ""))
            for item in spec.get("metadata", [])
            if item.get("value")
        ]
    index = 0
    for cell in root.xpath(".//*[local-name()='tc']"):
        text_nodes = cell.xpath(".//*[local-name()='t']")
        visible = "".join((node.text or "") for node in text_nodes).strip()
        if visible or index >= len(values):
            continue
        _set_cell_text(cell, values[index])
        index += 1


def _replace_matching_paragraph(root, marker: str, replacement: str) -> None:
    for paragraph in root.xpath(".//*[local-name()='p']"):
        text = "".join(paragraph.xpath(".//*[local-name()='t']/text()"))
        if marker in text:
            _set_paragraph_text(paragraph, replacement)
            return


def _set_cell_text(cell, text: str) -> None:
    paragraphs = cell.xpath(".//*[local-name()='p']")
    if paragraphs:
        _set_paragraph_text(paragraphs[0], text)


def _set_paragraph_text(paragraph, text: str) -> None:
    text_nodes = paragraph.xpath(".//*[local-name()='t']")
    if text_nodes:
        text_nodes[0].text = str(text)
        for node in text_nodes[1:]:
            node.text = ""
        return
    runs = paragraph.xpath(".//*[local-name()='run']")
    if runs:
        text_node = etree.Element("{http://www.hancom.co.kr/hwpml/2011/paragraph}t")
        text_node.text = str(text)
        runs[0].insert(0, text_node)


def _section(spec: dict) -> str:
    paragraphs = [
        _paragraph(str(spec.get("document_type", "보고서")).upper(), 2, 2),
        _paragraph(str(spec.get("title", "")), 1, 1),
        _paragraph(str(spec.get("subtitle", "")), 3, 3),
    ]
    for item in spec.get("metadata", []):
        paragraphs.append(_paragraph(f"{item.get('label', '')}  |  {item.get('value', '')}", 4, 4))
    paragraphs.extend([
        _paragraph("요약", 2, 2),
        _paragraph(str(spec.get("executive_summary", "")), 5, 5),
    ])
    for section in spec.get("sections", []):
        paragraphs.append(_paragraph(str(section.get("title", "")), 2, 2))
        for block in section.get("blocks", []):
            paragraphs.extend(_block(block))
    if spec.get("sources"):
        paragraphs.append(_paragraph("참고 자료", 2, 2))
        paragraphs.extend(_paragraph(str(item), 0, 0) for item in spec["sources"])
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        + "".join(paragraphs)
        + "</hs:sec>"
    )


def _block(block: dict) -> list[str]:
    kind = block.get("type")
    if kind == "kpi_grid":
        output = [_paragraph("핵심 지표", 4, 4)]
        output.extend(
            _paragraph(f"{item.get('label', '')}  |  {item.get('value', '')}  |  {item.get('context', '')}", 5, 5)
            for item in block.get("items", [])
        )
        return output
    if kind == "table":
        output = [_paragraph("  |  ".join(str(item) for item in block.get("headers", [])), 6, 6)]
        output.extend(_paragraph("  |  ".join(str(item) for item in row), 0, 0) for row in block.get("rows", []))
        return output
    if kind in {"callout", "quote"}:
        return [_paragraph(str(block.get("title", "검토 사항")), 4, 4), _paragraph(str(block.get("text", "")), 5, 5)]
    if kind in {"bullet_list", "timeline", "action_items"}:
        return [_paragraph(f"- {item}", 0, 0) for item in block.get("items", [])]
    return [_paragraph(str(block.get("text", "")), 0, 0)]


def _paragraph(text: str, char_ref: int, para_ref: int) -> str:
    return (
        f'<hp:p id="{uuid.uuid4().int % 100000000}" paraPrIDRef="{para_ref}" styleIDRef="0">'
        f'<hp:run charPrIDRef="{char_ref}"><hp:t>{escape(text)}</hp:t></hp:run></hp:p>'
    )


def _header(design: dict) -> str:
    primary = _color(design.get("primary"), "173A59")
    accent = _color(design.get("accent"), "007F83")
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" version="1.4" secCnt="1">
<hh:refList>
<hh:fontfaces itemCnt="1"><hh:fontface lang="HANGUL" fontCnt="2"><hh:font id="0" face="함초롬바탕" type="TTF"/><hh:font id="1" face="함초롬돋움" type="TTF"/></hh:fontface></hh:fontfaces>
<hh:borderFills itemCnt="1"><hh:borderFill id="1" threeD="0" shadow="0" centerLine="NONE"><hh:slash type="NONE"/><hh:backSlash type="NONE"/><hh:leftBorder type="NONE"/><hh:rightBorder type="NONE"/><hh:topBorder type="NONE"/><hh:bottomBorder type="NONE"/><hh:diagonal type="NONE"/></hh:borderFill></hh:borderFills>
<hh:charProperties itemCnt="7">
<hh:charPr id="0" height="1000" textColor="#17252F" shadeColor="none" fontRef="0"/>
<hh:charPr id="1" height="2600" textColor="#{primary}" bold="1" fontRef="1"/>
<hh:charPr id="2" height="1500" textColor="#{primary}" bold="1" fontRef="1"/>
<hh:charPr id="3" height="1100" textColor="#536673" fontRef="0"/>
<hh:charPr id="4" height="900" textColor="#{accent}" bold="1" fontRef="1"/>
<hh:charPr id="5" height="1000" textColor="#17252F" shadeColor="#F3F6F8" fontRef="0"/>
<hh:charPr id="6" height="1000" textColor="#FFFFFF" shadeColor="#{primary}" bold="1" fontRef="1"/>
</hh:charProperties>
<hh:paraProperties itemCnt="7"><hh:paraPr id="0"/><hh:paraPr id="1" align="CENTER"/><hh:paraPr id="2" align="LEFT"/><hh:paraPr id="3" align="CENTER"/><hh:paraPr id="4" align="LEFT"/><hh:paraPr id="5" align="LEFT"/><hh:paraPr id="6" align="LEFT"/></hh:paraProperties>
<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="Normal" engName="Normal" paraPrIDRef="0" charPrIDRef="0"/></hh:styles>
</hh:refList></hh:head>'''


def _content_hpf(spec: dict) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" version="2.0" unique-identifier="uuid">
<opf:metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(str(spec.get("title", "")))}</dc:title><dc:creator>DocuMind</dc:creator></opf:metadata>
<opf:manifest><opf:item id="header" href="header.xml" media-type="application/xml"/><opf:item id="section0" href="section0.xml" media-type="application/xml"/></opf:manifest>
<opf:spine><opf:itemref idref="section0"/></opf:spine></opf:package>'''


def _version() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="0" buildNumber="1" os="1" xmlVersion="1.4" application="Hancom Office Hangul" appVersion="11, 0, 0, 8362 WIN32LEWindows_10"/>'''


def _settings() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?><ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" caretPosition="0"/>'''


def _container() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles><ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/><ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/><ocf:rootfile full-path="META-INF/container.rdf" media-type="application/rdf+xml"/></ocf:rootfiles></ocf:container>'''


def _manifest() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>'''


def _container_rdf() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about=""><pkg:hasPart xmlns:pkg="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" rdf:resource="Contents/header.xml"/><pkg:hasPart xmlns:pkg="http://www.hancom.co.kr/hwpml/2016/meta/pkg#" rdf:resource="Contents/section0.xml"/><rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#Document"/></rdf:Description><rdf:Description rdf:about="Contents/header.xml"><rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#HeaderFile"/></rdf:Description><rdf:Description rdf:about="Contents/section0.xml"><rdf:type rdf:resource="http://www.hancom.co.kr/hwpml/2016/meta/pkg#SectionFile"/></rdf:Description></rdf:RDF>'''


def _color(value: object, fallback: str) -> str:
    candidate = str(value or fallback).replace("#", "").upper()
    return candidate if len(candidate) == 6 else fallback
