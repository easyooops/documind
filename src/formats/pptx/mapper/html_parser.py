"""HTML Parser — extracts element tree from Constrained HTML for OOXML mapping."""

from __future__ import annotations

import json
import re
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)


class ParsedElement:
    """Represents a single HTML element extracted for PPTX conversion."""

    def __init__(
        self,
        pptx_type: str,
        pptx_shape: str | None,
        position: dict,
        styles: dict,
        text_content: str,
        children: list["ParsedElement"],
        attributes: dict,
        text_runs: list[dict] | None = None,
    ):
        self.pptx_type = pptx_type
        self.pptx_shape = pptx_shape
        self.position = position
        self.styles = styles
        self.text_content = text_content
        self.children = children
        self.attributes = attributes
        self.text_runs = text_runs or []

    @property
    def z_order(self) -> int:
        return int(self.attributes.get("data-pptx-z", "0") or "0")

    @property
    def fill_type(self) -> str | None:
        return self.attributes.get("data-pptx-fill")

    @property
    def shadow_type(self) -> str | None:
        return self.attributes.get("data-pptx-shadow")

    @property
    def rotation(self) -> float:
        rotate = self.attributes.get("data-pptx-rotate", "0")
        try:
            return float(rotate)
        except (ValueError, TypeError):
            return 0.0

    @property
    def chart_type(self) -> str | None:
        return self.attributes.get("data-pptx-chart-type")

    @property
    def chart_data(self) -> list | None:
        raw = self.attributes.get("data-pptx-chart-data")
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    @property
    def chart_options(self) -> dict:
        return _json_attr(self.attributes.get("data-pptx-chart-options"), {})

    @property
    def table_data(self) -> dict | None:
        raw = self.attributes.get("data-pptx-table-data")
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    if parsed and isinstance(parsed[0], list):
                        return {"headers": parsed[0], "rows": parsed[1:]}
                    return {"headers": [], "rows": parsed}
                return None
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @property
    def table_options(self) -> dict:
        return _json_attr(self.attributes.get("data-pptx-table-options"), {})

    @property
    def shape_options(self) -> dict:
        return _json_attr(self.attributes.get("data-pptx-shape-options"), {})


def _json_attr(raw: Any, default: dict) -> dict:
    if not raw:
        return default
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else default
    except (json.JSONDecodeError, TypeError):
        return default


def parse_slide_html(html: str) -> list[ParsedElement]:
    """Parse a slide's HTML into a flat list of elements for PPTX conversion.

    Uses BeautifulSoup to parse the HTML and extract all data-pptx-* elements.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    elements = []
    pptx_nodes = soup.find_all(attrs={"data-pptx-type": True})

    for node in pptx_nodes:
        element = _parse_node(node)
        if element:
            elements.append(element)

    elements.sort(key=lambda e: e.z_order)
    return elements


def _parse_node(node) -> ParsedElement | None:
    """Convert a BeautifulSoup node into a ParsedElement."""
    pptx_type = node.get("data-pptx-type", "shape")
    pptx_shape = node.get("data-pptx-shape")

    style_str = node.get("style", "")
    styles = _parse_inline_styles(style_str)

    position = {
        "left": _extract_px(styles.get("left", "0")),
        "top": _extract_px(styles.get("top", "0")),
        "width": _extract_px(styles.get("width", "100")),
        "height": _extract_px(styles.get("height", "50")),
    }

    text_content = _extract_text_content(node, pptx_type)
    text_runs = _extract_text_runs(node, pptx_type)

    attributes = {}
    for attr_name, attr_value in node.attrs.items():
        if attr_name.startswith("data-pptx-"):
            if isinstance(attr_value, list):
                attr_value = " ".join(str(v) for v in attr_value)
            attributes[attr_name] = attr_value

    children = []
    child_nodes = node.find_all(attrs={"data-pptx-type": True}, recursive=False)
    for child in child_nodes:
        child_elem = _parse_node(child)
        if child_elem:
            children.append(child_elem)

    return ParsedElement(
        pptx_type=pptx_type,
        pptx_shape=pptx_shape,
        position=position,
        styles=styles,
        text_content=text_content,
        children=children,
        attributes=attributes,
        text_runs=text_runs,
    )


def _parse_inline_styles(style_str: str) -> dict:
    """Parse CSS inline style string into a dict."""
    styles = {}
    if not style_str:
        return styles

    for declaration in style_str.split(";"):
        declaration = declaration.strip()
        if ":" not in declaration:
            continue
        prop, _, value = declaration.partition(":")
        styles[prop.strip().lower()] = value.strip()

    return styles


def _extract_px(value: str) -> float:
    """Extract numeric px value from a CSS value string."""
    if not value:
        return 0.0
    match = re.match(r"([-\d.]+)", value.replace("px", "").strip())
    if match:
        return float(match.group(1))
    return 0.0


def _extract_text_content(node, pptx_type: str) -> str:
    """Extract meaningful text content from a node.

    For textbox elements, gets the direct text.
    For other types, skips nested data-pptx elements.
    """
    if pptx_type in ("table", "chart", "image", "connector", "icon"):
        return ""

    texts = []
    for child in node.children:
        if hasattr(child, "attrs") and child.get("data-pptx-type"):
            continue
        if hasattr(child, "get_text"):
            text = child.get_text(separator="\n", strip=True)
            if text:
                texts.append(text)
        elif isinstance(child, str) and child.strip():
            texts.append(child.strip())

    return _normalize_text_lines("\n".join(texts))


def _normalize_text_lines(text: str) -> str:
    """Recover list-like line breaks that LLMs often flatten in HTML text."""
    if not text:
        return ""
    normalized = text.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+([•▸▶→▪◦◆◇✓])\s+", r"\n\1 ", normalized)
    normalized = re.sub(r"\s+(\d+[.)])\s+", r"\n\1 ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return "\n".join(line.strip() for line in normalized.split("\n") if line.strip())


def _extract_text_runs(node, pptx_type: str) -> list[dict]:
    """Extract text runs with inline formatting (bold, italic, color, size).

    Returns a list of dicts: [{"text": str, "bold": bool, "italic": bool, "color": str, "size": str}]
    """
    if pptx_type in ("table", "chart", "image", "connector", "icon"):
        return []

    runs = []
    _walk_for_runs(node, runs, {})
    return runs


def _walk_for_runs(node, runs: list, inherited_styles: dict) -> None:
    """Recursively walk child nodes collecting text runs with formatting."""
    for child in node.children:
        if hasattr(child, "attrs") and child.get("data-pptx-type"):
            continue

        if isinstance(child, str):
            text = child.strip()
            if text:
                runs.append({
                    "text": text,
                    "bold": inherited_styles.get("bold", False),
                    "italic": inherited_styles.get("italic", False),
                    "color": inherited_styles.get("color", ""),
                    "size": inherited_styles.get("size", ""),
                })
            continue

        if not hasattr(child, "name"):
            continue

        tag = child.name.lower() if child.name else ""
        child_styles = dict(inherited_styles)

        if tag in ("b", "strong"):
            child_styles["bold"] = True
        if tag in ("i", "em"):
            child_styles["italic"] = True

        inline_style = child.get("style", "")
        if inline_style:
            style_dict = _parse_inline_styles(inline_style)
            fw = style_dict.get("font-weight", "")
            if fw in ("bold", "700", "800", "900") or (fw.isdigit() and int(fw) >= 700):
                child_styles["bold"] = True
            if style_dict.get("font-style") == "italic":
                child_styles["italic"] = True
            if style_dict.get("color"):
                child_styles["color"] = style_dict["color"]
            if style_dict.get("font-size"):
                child_styles["size"] = style_dict["font-size"]

        inner_text = child.get_text(separator="\n", strip=True) if hasattr(child, "get_text") else ""

        if child.find_all(recursive=False) if hasattr(child, "find_all") else False:
            _walk_for_runs(child, runs, child_styles)
        elif inner_text:
            runs.append({
                "text": inner_text,
                "bold": child_styles.get("bold", False),
                "italic": child_styles.get("italic", False),
                "color": child_styles.get("color", ""),
                "size": child_styles.get("size", ""),
            })

