"""Phase C: Render & Convert — Playwright capture + deterministic PPTX build."""

from __future__ import annotations

import asyncio
import base64
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger
from src.formats.pptx.color_utils import (
    choose_legible_text_color,
    contrast_ratio,
    extract_colors,
    relative_luminance,
)
from src.formats.pptx.mapper.engine import CSStoOOXMLEngine
from src.formats.pptx.visual_renderer import render_pptx_images
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

_capture_executor = None


def _get_executor():
    global _capture_executor
    if _capture_executor is None:
        _capture_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="capture")
    return _capture_executor


async def render_and_convert(state: DocuMindState) -> dict:
    """Capture HTML slides as screenshots and convert to PPTX via deterministic mapper."""
    logger.info("render_convert.start", iteration=state.get("qa_iterations", 0))

    slides_html = _normalize_slide_icon_layouts(state.get("slides_html", []))
    title = state.get("title", "Presentation")

    if not slides_html:
        logger.error("render_convert.no_html")
        return {"errors": ["No HTML slides to convert"], "current_phase": "error"}

    previous_output = state.get("output_path")
    _cleanup_file(previous_output)

    html_screenshots = await _capture_slides(slides_html)

    output_dir = Path(settings.storage_local_path)
    engine = CSStoOOXMLEngine()
    output_path = engine.build_presentation(
        slides_html,
        output_dir,
        title=title,
        template_bytes=state.get("_template_bytes"),
    )
    pptx_render_info = await render_pptx_images(
        str(output_path),
        output_dir / "captures",
        prefix=f"pptx_{uuid.uuid4().hex[:6]}",
    )

    html_preview_path = _save_html_preview(slides_html, output_dir)

    logger.info(
        "render_convert.complete",
        output=str(output_path),
        html_screenshots=len(html_screenshots),
        pptx_screenshots=len(pptx_render_info.get("paths", [])),
        pptx_renderer=pptx_render_info.get("renderer"),
    )
    return {
        "output_path": str(output_path),
        "html_preview_path": html_preview_path,
        "html_screenshots": html_screenshots,
        "pptx_screenshots": pptx_render_info.get("paths", []),
        "pptx_render_info": pptx_render_info,
        "screenshots_count": min(
            len(html_screenshots), len(pptx_render_info.get("paths", []))
        ),
        "current_phase": "converting",
    }


async def _capture_slides(slides_html: list[dict]) -> list[str]:
    """Capture each slide HTML as a 960x540 PNG screenshot via Playwright."""
    screenshots = []
    output_dir = Path(settings.storage_local_path) / "captures"
    output_dir.mkdir(parents=True, exist_ok=True)

    for slide_data in slides_html:
        html = slide_data.get("html", "")
        if not html:
            continue

        idx = slide_data.get("index", len(screenshots) + 1)
        output_file = output_dir / f"slide_{idx}_{uuid.uuid4().hex[:6]}.png"

        full_html = _wrap_slide_html(html)

        try:
            loop = asyncio.get_running_loop()
            img_bytes = await loop.run_in_executor(
                _get_executor(), _screenshot_sync, full_html
            )
            if img_bytes:
                output_file.write_bytes(img_bytes)
                screenshots.append(str(output_file))
        except Exception as e:
            logger.warning("render_convert.capture_error", slide=idx, error=str(e)[:200])

    return screenshots


def _wrap_slide_html(slide_html: str) -> str:
    """Wrap a slide div in a complete HTML document for rendering."""
    slide_html = _embed_cached_icons(slide_html)
    slide_html = _embed_local_images(slide_html)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:960px; height:540px; overflow:hidden; font-family:'Pretendard',system-ui,sans-serif; }}
</style>
</head>
<body>{slide_html}</body>
    </html>"""


def _normalize_slide_icon_layouts(slides_html: list[dict]) -> list[dict]:
    """Normalize slide geometry before render/convert."""
    normalized = []
    for slide in slides_html:
        copied = dict(slide)
        copied["html"] = _normalize_slide_html(str(slide.get("html", "")))
        normalized.append(copied)
    return normalized


def _normalize_slide_html(slide_html: str) -> str:
    """Stabilize HTML so preview and PPTX use the same non-overlapping boxes."""
    from bs4 import BeautifulSoup

    slide_html = _normalize_legacy_icon_nodes(slide_html)
    soup = BeautifulSoup(slide_html, "html.parser")
    wrapper = soup.find(attrs={"data-slide": True})
    if not wrapper:
        return str(soup)

    elements = [
        node for node in wrapper.find_all(recursive=False)
        if getattr(node, "attrs", {}).get("data-pptx-type")
    ]
    for node in elements:
        styles = _parse_style(str(node.attrs.get("style", "")))
        _clamp_element_box(node, styles)
        _normalize_textbox_lists(node)
        _normalize_text_alignment_attrs(node, styles)
        _fit_text_element(node, styles)
        node.attrs["style"] = _style_to_string(styles)

    _enforce_text_contrast(elements)
    _resolve_content_overlaps(elements)
    return str(soup)


def _normalize_legacy_icon_nodes(slide_html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(slide_html, "html.parser")
    for node in list(soup.find_all(attrs={"data-pptx-icon": True})):
        if str(node.attrs.get("data-pptx-type", "")) == "icon":
            continue
        style = str(node.attrs.get("style", ""))
        if "background-color" in style or "background:" in style:
            # Background-bearing textboxes are legacy compound cards. Leave them
            # intact rather than moving the card itself; new prompts forbid this.
            continue
        icon_name = str(node.attrs.get("data-pptx-icon", ""))
        if not icon_name:
            continue
        left = _style_number(style, "left")
        top = _style_number(style, "top")
        width = _style_number(style, "width")
        height = _style_number(style, "height")
        if width < 36 or height < 18:
            continue

        size = _icon_size_for_preview(node, style)
        gap = 10
        icon_top = top + max(0, (height - size) / 2)
        icon_style = (
            f"position:absolute;left:{left}px;top:{icon_top}px;"
            f"width:{size}px;height:{size}px;color:{_style_value(style, 'color', '#1E293B')}"
        )
        icon = soup.new_tag("div")
        icon["data-pptx-type"] = "icon"
        icon["data-pptx-icon"] = icon_name
        icon["data-pptx-icon-placement"] = str(
            node.attrs.get("data-pptx-icon-placement", "card_lead_left")
        )
        icon["style"] = icon_style
        node.insert_before(icon)

        shift = size + gap
        if width > shift + 20:
            node["style"] = _replace_style_numbers(
                style,
                {
                    "left": left + shift,
                    "width": max(1, width - shift),
                },
            )
            for attr in (
                "data-pptx-icon",
                "data-pptx-icon-layout",
                "data-pptx-icon-size",
                "data-pptx-icon-placement",
            ):
                node.attrs.pop(attr, None)
    return str(soup)


def _embed_cached_icons(slide_html: str) -> str:
    """Render previews with the same cached icon artifacts used by PPTX output."""
    from bs4 import BeautifulSoup

    from src.utils.iconify import get_fallback_icon_path, get_icon_asset_path, normalize_icon_color

    soup = BeautifulSoup(slide_html, "html.parser")
    for node in soup.find_all(attrs={"data-pptx-icon": True}):
        style = str(node.attrs.get("style", ""))
        if not _should_embed_icon(node, style):
            continue
        color_match = re.search(r"(?:^|;)\s*color\s*:\s*(#[0-9a-fA-F]{3,8})", style)
        color = normalize_icon_color(color_match.group(1) if color_match else "1E293B")
        icon_name = str(node.attrs["data-pptx-icon"])
        path = get_icon_asset_path(icon_name, color=color, size=32, target="html")
        if not path:
            path = get_fallback_icon_path(icon_name, color=color, size=32)
        if not path:
            continue
        mime_type = "image/png" if path.suffix.lower() == ".png" else "image/svg+xml"
        icon_data = base64.b64encode(path.read_bytes()).decode("ascii")
        image = soup.new_tag("img", src=f"data:{mime_type};base64,{icon_data}")
        if str(node.attrs.get("data-pptx-type", "")) == "icon":
            image["style"] = _icon_element_preview_style(style)
            node.replace_with(image)
            continue
        layout = _icon_layout_for_preview(node, style)
        icon_size = _icon_size_for_preview(node, style)
        image["style"] = _icon_preview_style(layout, icon_size)
        node.insert(0, image)
        node["style"] = _reserve_preview_icon_space(style, layout, icon_size)
    return str(soup)


def _embed_local_images(slide_html: str) -> str:
    """Render local visual asset image nodes in HTML previews/screenshots."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(slide_html, "html.parser")
    for node in soup.find_all(attrs={"data-pptx-image-path": True}):
        if str(node.attrs.get("data-pptx-type", "")) != "image":
            continue
        path = Path(str(node.attrs.get("data-pptx-image-path", "")))
        if not path.exists() or not path.is_file():
            continue
        mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        image_data = base64.b64encode(path.read_bytes()).decode("ascii")
        style = str(node.attrs.get("style", ""))
        style = _remove_preview_image_background(style)
        node.attrs["style"] = (
            f"{style};background-image:url(data:{mime_type};base64,{image_data});"
            "background-size:contain;background-repeat:no-repeat;background-position:center"
        )
        node.attrs.pop("data-pptx-image-path", None)
    return str(soup)


def _remove_preview_image_background(style: str) -> str:
    declarations = []
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, _, value = declaration.partition(":")
        if name.strip().lower() in {"background", "background-image"}:
            continue
        declarations.append(f"{name.strip()}:{value.strip()}")
    return ";".join(declarations)


def _style_number(style: str, property_name: str) -> float:
    match = re.search(rf"(?:^|;)\s*{property_name}\s*:\s*(-?\d+(?:\.\d+)?)px", style)
    return float(match.group(1)) if match else 0.0


def _parse_style(style: str) -> dict[str, str]:
    parsed = {}
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, _, value = declaration.partition(":")
        parsed[name.strip().lower()] = value.strip()
    return parsed


def _style_to_string(styles: dict[str, str]) -> str:
    return ";".join(f"{name}:{value}" for name, value in styles.items() if value != "")


def _style_px(styles: dict[str, str], name: str, fallback: float = 0.0) -> float:
    raw = styles.get(name, "")
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(raw).replace("px", ""))
    return float(match.group(1)) if match else fallback


def _set_style_px(styles: dict[str, str], name: str, value: float) -> None:
    styles[name] = f"{value:g}px"


def _extract_color(value: str) -> str | None:
    colors = _extract_colors(value)
    return colors[0] if colors else None


def _extract_colors(value: str) -> list[str]:
    return extract_colors(value)


def _contrast_ratio(color_a: str, color_b: str) -> float:
    return contrast_ratio(color_a, color_b)


def _relative_luminance(hex_color: str) -> float:
    return relative_luminance(hex_color)


def _clamp_element_box(node, styles: dict[str, str]) -> None:
    pptx_type = str(node.attrs.get("data-pptx-type", ""))
    region = str(node.attrs.get("data-pptx-region", ""))
    left = _style_px(styles, "left")
    top = _style_px(styles, "top")
    width = _style_px(styles, "width", 100)
    height = _style_px(styles, "height", 50)

    is_background = (
        region == "background"
        or (pptx_type == "shape" and width >= 950 and height >= 530 and left <= 5 and top <= 5)
    )
    if is_background:
        return

    left = max(0, min(left, 956))
    top = max(0, min(top, 536))
    width = max(1, min(width, 960 - left))
    height = max(1, min(height, 540 - top))
    _set_style_px(styles, "left", left)
    _set_style_px(styles, "top", top)
    _set_style_px(styles, "width", width)
    _set_style_px(styles, "height", height)


def _fit_text_element(node, styles: dict[str, str]) -> None:
    pptx_type = str(node.attrs.get("data-pptx-type", ""))
    if pptx_type not in {"textbox", "shape"}:
        return
    if str(node.attrs.get("data-pptx-region", "")) in {"header", "footer"}:
        styles.setdefault("overflow", "hidden")
        return

    text = _direct_text(node)
    if not text:
        return

    width = _style_px(styles, "width", 100)
    height = _style_px(styles, "height", 50)
    if width <= 0 or height <= 0:
        return

    font_size = _style_px(styles, "font-size", 16)
    line_height = _line_height_ratio(styles.get("line-height", "1.35"))
    pad_top, pad_right, pad_bottom, pad_left = _padding_box(styles)
    min_size = 9 if font_size <= 28 else 18

    current = font_size
    while current >= min_size:
        lines = _wrap_text(text, current, max(1, width - pad_left - pad_right))
        if len(lines) * current * line_height <= max(1, height - pad_top - pad_bottom):
            break
        current -= 1

    if current < font_size:
        styles["font-size"] = f"{max(min_size, current):g}px"
    styles.setdefault("overflow", "hidden")


def _enforce_text_contrast(elements: list) -> None:
    """Force text colors to pass contrast against owned or backing fills."""
    parsed = [
        {"node": node, "styles": _parse_style(str(node.attrs.get("style", ""))), "box": None}
        for node in elements
    ]
    for item in parsed:
        item["box"] = _box(item["styles"])

    for index, item in enumerate(parsed):
        node = item["node"]
        styles = item["styles"]
        if str(node.attrs.get("data-pptx-type", "")) not in {"textbox", "shape"}:
            continue
        if str(node.attrs.get("data-pptx-region", "")) in {"background", "footer"}:
            continue
        text = _direct_text(node)
        if not text:
            continue

        bg_colors = _node_background_colors(styles)
        if not bg_colors:
            bg_colors = _backing_fill_colors(item, parsed[:index])
        if not bg_colors:
            continue

        current = _extract_color(styles.get("color", "")) or "1e293b"
        font_size = _style_px(styles, "font-size", 16)
        font_weight = str(styles.get("font-weight", "400"))
        is_bold = font_weight in {"bold", "600", "700", "800", "900"} or (
            font_weight.isdigit() and int(font_weight) >= 600
        )
        threshold = 3.0 if font_size >= 24 or (is_bold and font_size >= 18.5) else 4.5
        if all(_contrast_ratio(current, bg) >= threshold for bg in bg_colors):
            _enforce_inline_text_contrast(
                node,
                bg_colors,
                font_size_px=font_size,
                bold=is_bold,
            )
            continue

        best = choose_legible_text_color(
            current,
            bg_colors,
            font_size_px=font_size,
            bold=is_bold,
        )
        styles["color"] = f"#{best}"
        _enforce_inline_text_contrast(
            node,
            bg_colors,
            font_size_px=font_size,
            bold=is_bold,
        )
        node.attrs["style"] = _style_to_string(styles)


def _enforce_inline_text_contrast(
    node,
    bg_colors: list[str],
    *,
    font_size_px: float,
    bold: bool,
) -> None:
    for child in node.descendants:
        attrs = getattr(child, "attrs", None)
        if not attrs or attrs.get("data-pptx-type"):
            continue
        child_styles = _parse_style(str(attrs.get("style", "")))
        child_color = _extract_color(child_styles.get("color", ""))
        if not child_color:
            continue
        child_font_size = _style_px(child_styles, "font-size", font_size_px)
        font_weight = str(child_styles.get("font-weight", ""))
        child_bold = bold or font_weight in {"bold", "600", "700", "800", "900"} or (
            font_weight.isdigit() and int(font_weight) >= 600
        )
        threshold = 3.0 if child_font_size >= 24 or (child_bold and child_font_size >= 18.5) else 4.5
        if all(_contrast_ratio(child_color, bg) >= threshold for bg in bg_colors):
            continue
        child_styles["color"] = "#" + choose_legible_text_color(
            child_color,
            bg_colors,
            font_size_px=child_font_size,
            bold=child_bold,
        )
        attrs["style"] = _style_to_string(child_styles)


def _node_background_colors(styles: dict[str, str]) -> list[str]:
    colors: list[str] = []
    for name in ("background-color", "background"):
        value = styles.get(name, "")
        if not value:
            continue
        if value.strip().lower() in {"transparent", "none", "inherit", "initial"}:
            continue
        colors.extend(_extract_colors(value))
    return colors


def _backing_fill_colors(item: dict, prior_items: list[dict]) -> list[str]:
    text_box = item["box"]
    if not text_box:
        return []
    candidates: list[tuple[float, list[str]]] = []
    for prior in prior_items:
        node = prior["node"]
        pptx_type = str(node.attrs.get("data-pptx-type", ""))
        if pptx_type not in {"shape", "textbox"}:
            continue
        colors = _node_background_colors(prior["styles"])
        if not colors:
            continue
        box = prior["box"]
        if not box:
            continue
        overlap = _overlap_ratio(text_box, box)
        contains_center = _box_contains_center(box, text_box)
        is_full_background = box[2] >= 900 and box[3] >= 480 and box[0] <= 10 and box[1] <= 10
        dark_partial_backing = overlap >= 0.15 and any(
            _relative_luminance(color) < 0.25 for color in colors
        )
        if overlap >= 0.45 or contains_center or is_full_background or dark_partial_backing:
            score = (
                overlap
                + (0.25 if contains_center else 0)
                + (0.1 if is_full_background else 0)
                + (0.15 if dark_partial_backing else 0)
            )
            candidates.append((score, colors))
    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _box_contains_center(container: tuple[float, float, float, float], inner: tuple[float, float, float, float]) -> bool:
    cx = inner[0] + inner[2] / 2
    cy = inner[1] + inner[3] / 2
    return container[0] <= cx <= container[0] + container[2] and container[1] <= cy <= container[1] + container[3]


def _normalize_text_alignment_attrs(node, styles: dict[str, str]) -> None:
    pptx_type = str(node.attrs.get("data-pptx-type", ""))
    if pptx_type not in {"textbox", "shape"}:
        return
    if str(node.attrs.get("data-pptx-region", "")) in {"background", "header", "footer"}:
        return

    text = _direct_text(node)
    if not text:
        return

    role = _text_role_for_preview(node, styles, text)
    node.attrs.setdefault("data-pptx-text-role", role)

    if not node.attrs.get("data-pptx-text-align"):
        if styles.get("text-align") in {"left", "center", "right", "justify"}:
            node.attrs["data-pptx-text-align"] = styles["text-align"]
        elif role in {"kpi_value", "kpi_label", "badge"}:
            node.attrs["data-pptx-text-align"] = "center"
        else:
            node.attrs["data-pptx-text-align"] = "left"

    if not node.attrs.get("data-pptx-text-valign"):
        if styles.get("vertical-align") in {"top", "middle", "bottom"}:
            node.attrs["data-pptx-text-valign"] = styles["vertical-align"]
        elif styles.get("align-items") in {"center", "middle"}:
            node.attrs["data-pptx-text-valign"] = "middle"
        elif role in {"kpi_value", "kpi_label", "badge", "card_title", "caption"}:
            node.attrs["data-pptx-text-valign"] = "middle"
        else:
            node.attrs["data-pptx-text-valign"] = "top"

    has_css_padding = any(
        styles.get(name)
        for name in ("padding", "padding-top", "padding-right", "padding-bottom", "padding-left")
    )
    if not node.attrs.get("data-pptx-text-padding") and not has_css_padding:
        defaults = {
            "kpi_value": "0px 4px 0px 4px",
            "kpi_label": "1px 4px 1px 4px",
            "badge": "1px 8px 1px 8px",
            "card_title": "2px 4px 2px 4px",
            "caption": "1px 4px 1px 4px",
        }
        if role in defaults:
            node.attrs["data-pptx-text-padding"] = defaults[role]


def _text_role_for_preview(node, styles: dict[str, str], text: str) -> str:
    raw = str(node.attrs.get("data-pptx-text-role", "") or "").strip().lower().replace("-", "_")
    if raw:
        return raw
    if node.attrs.get("data-pptx-list"):
        return "list"
    font_size = _style_px(styles, "font-size", 16)
    font_weight = str(styles.get("font-weight", "400"))
    height = _style_px(styles, "height", 0)
    compact_value = str(text or "").strip()
    if font_size >= 24 or (
        len(compact_value) <= 24
        and re.search(r"(?:[$€₩¥]\s?\d|\d+(?:\.\d+)?\s?%|\d+\s?배|\d+\s?x)", compact_value, re.I)
    ):
        return "kpi_value"
    if height and height <= 30 and font_size <= 12:
        return "caption"
    is_bold = font_weight in {"bold", "600", "700", "800", "900"} or (
        font_weight.isdigit() and int(font_weight) >= 600
    )
    if is_bold and height and height <= 48:
        return "card_title"
    return "body"


def _direct_text(node) -> str:
    texts = []
    for child in node.children:
        if hasattr(child, "attrs") and child.get("data-pptx-type"):
            continue
        if hasattr(child, "name") and str(child.name).lower() in {"ul", "ol"}:
            items = [
                li.get_text(" ", strip=True)
                for li in child.find_all("li", recursive=False)
                if li.get_text(" ", strip=True)
            ]
            if items:
                texts.append("\n".join(items))
            continue
        if isinstance(child, str):
            text = child.strip()
        elif hasattr(child, "get_text"):
            text = child.get_text(" ", strip=True)
        else:
            text = ""
        if text:
            texts.append(text)
    return "\n".join(texts)


def _normalize_textbox_lists(node) -> None:
    pptx_type = str(node.attrs.get("data-pptx-type", ""))
    if pptx_type not in {"textbox", "shape"}:
        return
    if str(node.attrs.get("data-pptx-region", "")) in {"header", "footer"}:
        return

    explicit_list = str(node.attrs.get("data-pptx-list", "")).lower() in {
        "bullet",
        "bullets",
        "unordered",
    }
    ordered_list = str(node.attrs.get("data-pptx-list", "")).lower() in {
        "number",
        "numbered",
        "ordered",
    }
    list_items: list[str] = []

    for list_node in node.find_all(["ul", "ol"], recursive=False):
        ordered_list = ordered_list or str(list_node.name).lower() == "ol"
        for li in list_node.find_all("li", recursive=False):
            text = li.get_text(" ", strip=True)
            if text:
                list_items.append(text)
        list_node.decompose()

    if list_items:
        node.attrs["data-pptx-list"] = "numbered" if ordered_list else "bullet"
        node.string = _format_list_lines(list_items, ordered=ordered_list)
        return

    text = _direct_text(node)
    if not text:
        return
    lines = [line.strip() for line in text.replace("\\n", "\n").split("\n") if line.strip()]
    if len(lines) < 2:
        return
    if explicit_list or ordered_list or _looks_like_list_lines(lines):
        node.attrs["data-pptx-list"] = "numbered" if ordered_list else "bullet"
        node.string = _format_list_lines(lines, ordered=ordered_list)


def _looks_like_list_lines(lines: list[str]) -> bool:
    marker_count = sum(1 for line in lines if _list_marker_match(line))
    if marker_count >= max(2, len(lines) - 1):
        return True
    short_lines = sum(1 for line in lines if len(_strip_list_marker(line)) <= 42)
    return len(lines) >= 3 and short_lines == len(lines)


def _format_list_lines(lines: list[str], *, ordered: bool) -> str:
    formatted = []
    for index, line in enumerate(lines, start=1):
        body = _strip_list_marker(line)
        if not body:
            continue
        marker = f"{index}." if ordered else "\u2022"
        formatted.append(f"{marker} {body}")
    return "\n".join(formatted)


def _strip_list_marker(line: str) -> str:
    return re.sub(
        r"^\s*(?:[\u2022\u2023\u25E6\u2043\u2219\-*+]|[0-9]+[.)]|[A-Za-z][.)])\s*",
        "",
        str(line),
    ).strip()


def _list_marker_match(line: str):
    return re.match(
        r"^\s*(?:[\u2022\u2023\u25E6\u2043\u2219\-*+]|[0-9]+[.)]|[A-Za-z][.)])\s+",
        str(line),
    )


def _padding_box(styles: dict[str, str]) -> tuple[float, float, float, float]:
    values = _padding_values(styles.get("padding", ""))
    top, right, bottom, left = values or (4.0, 8.0, 4.0, 8.0)
    top = _style_px(styles, "padding-top", top)
    right = _style_px(styles, "padding-right", right)
    bottom = _style_px(styles, "padding-bottom", bottom)
    left = _style_px(styles, "padding-left", left)
    return top, right, bottom, left


def _padding_values(value: str) -> tuple[float, float, float, float] | None:
    nums = [
        float(match.group(1))
        for match in re.finditer(r"(-?\d+(?:\.\d+)?)\s*px?", str(value or ""))
    ]
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0], nums[0], nums[0], nums[0]
    if len(nums) == 2:
        return nums[0], nums[1], nums[0], nums[1]
    if len(nums) == 3:
        return nums[0], nums[1], nums[2], nums[1]
    return nums[0], nums[1], nums[2], nums[3]


def _line_height_ratio(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return 1.35
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100
        except ValueError:
            return 1.35
    if text.endswith("px"):
        return 1.35
    if text.endswith("em"):
        text = text[:-2]
    try:
        return max(1.0, min(float(text), 2.2))
    except ValueError:
        return 1.35


def _wrap_text(text: str, font_size: float, width: float) -> list[str]:
    char_ratio = 0.86 if any("\uac00" <= char <= "\ud7a3" for char in text) else 0.58
    chars_per_line = max(4, int(width / max(1, font_size * char_ratio)))
    lines: list[str] = []
    for raw in text.replace("\\n", "\n").split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        while len(raw) > chars_per_line:
            break_at = raw.rfind(" ", 0, chars_per_line + 1)
            if break_at < max(4, chars_per_line // 2):
                break_at = chars_per_line
            lines.append(raw[:break_at].strip())
            raw = raw[break_at:].strip()
        if raw:
            lines.append(raw)
    return lines or [text.strip()]


def _resolve_content_overlaps(elements: list) -> None:
    boxes = []
    for node in elements:
        styles = _parse_style(str(node.attrs.get("style", "")))
        if not _is_overlap_candidate(node, styles):
            continue
        boxes.append({"node": node, "styles": styles, "box": _box(styles)})

    boxes.sort(key=lambda item: (item["box"][1], item["box"][0]))
    placed: list[dict] = []
    gap = 8
    for item in boxes:
        box = item["box"]
        for prior in placed:
            overlap = _overlap_ratio(box, prior["box"])
            if overlap <= 0.08:
                continue
            left, top, width, height = box
            prior_left, prior_top, prior_width, prior_height = prior["box"]
            moved_top = prior_top + prior_height + gap
            if moved_top + height <= 526:
                top = moved_top
            elif prior_left + prior_width + gap + width <= 920:
                left = prior_left + prior_width + gap
            else:
                height = max(24, min(height, 526 - top))
            box = (left, top, width, height)
        item["box"] = box
        _set_style_px(item["styles"], "left", box[0])
        _set_style_px(item["styles"], "top", box[1])
        _set_style_px(item["styles"], "width", box[2])
        _set_style_px(item["styles"], "height", box[3])
        item["node"].attrs["style"] = _style_to_string(item["styles"])
        placed.append(item)


def _is_overlap_candidate(node, styles: dict[str, str]) -> bool:
    pptx_type = str(node.attrs.get("data-pptx-type", ""))
    if pptx_type not in {"textbox", "table", "chart", "image"}:
        return False
    if str(node.attrs.get("data-pptx-region", "")) in {"background", "header", "footer"}:
        return False
    width = _style_px(styles, "width")
    height = _style_px(styles, "height")
    top = _style_px(styles, "top")
    return width > 8 and height > 8 and 70 <= top < 510


def _box(styles: dict[str, str]) -> tuple[float, float, float, float]:
    return (
        _style_px(styles, "left"),
        _style_px(styles, "top"),
        _style_px(styles, "width", 100),
        _style_px(styles, "height", 50),
    )


def _overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x_overlap = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    y_overlap = max(0, min(ay + ah, by + bh) - max(ay, by))
    if x_overlap <= 0 or y_overlap <= 0:
        return 0.0
    area = x_overlap * y_overlap
    return area / max(1, min(aw * ah, bw * bh))


def _style_value(style: str, property_name: str, fallback: str = "") -> str:
    match = re.search(rf"(?:^|;)\s*{property_name}\s*:\s*([^;]+)", style)
    return match.group(1).strip() if match else fallback


def _replace_style_numbers(style: str, replacements: dict[str, float]) -> str:
    updated = style
    for property_name, value in replacements.items():
        replacement = f"{property_name}:{value:g}px"
        pattern = rf"({property_name}\s*:\s*)[-\d.]+px"
        if re.search(pattern, updated):
            updated = re.sub(pattern, replacement, updated)
        else:
            updated = f"{updated};{replacement}"
    return updated


def _should_embed_icon(node, style: str) -> bool:
    if str(node.attrs.get("data-pptx-type", "")) == "icon":
        return _style_number(style, "width") >= 1 and _style_number(style, "height") >= 1
    width = _style_number(style, "width")
    height = _style_number(style, "height")
    explicit_slot = bool(node.attrs.get("data-pptx-icon-layout") or node.attrs.get("data-pptx-icon-size"))
    if explicit_slot:
        return width >= 24 and height >= 18
    return width >= 120 and height >= 80


def _icon_layout_for_preview(node, style: str) -> str:
    layout = str(node.attrs.get("data-pptx-icon-layout", "top-left"))
    if layout not in {"top-left", "inline-left", "badge-top-right", "metric-left"}:
        layout = "top-left"
    if layout == "top-left" and _style_number(style, "height") < 64:
        return "inline-left"
    return layout


def _icon_size_for_preview(node, style: str) -> int:
    raw_size = node.attrs.get("data-pptx-icon-size")
    try:
        requested = int(str(raw_size)) if raw_size else 0
    except ValueError:
        requested = 0
    height = _style_number(style, "height")
    if requested:
        return min(44, max(16, requested))
    if height < 64:
        return min(28, max(16, int(height * 0.75)))
    return min(44, max(24, int(height * 0.25)))


def _icon_element_preview_style(style: str) -> str:
    additions = "display:block;object-fit:contain"
    if "position" not in style:
        additions = f"position:absolute;{additions}"
    return f"{style};{additions}"


def _icon_preview_style(layout: str, icon_size: int) -> str:
    base = f"position:absolute;width:{icon_size}px;height:{icon_size}px;display:block"
    if layout == "inline-left":
        return f"{base};left:6px;top:50%;transform:translateY(-50%)"
    if layout == "badge-top-right":
        return f"{base};right:12px;top:12px"
    return f"{base};left:12px;top:12px"


def _reserve_preview_icon_space(style: str, layout: str, icon_size: int) -> str:
    if layout == "inline-left" and "padding-left" not in style:
        return f"{style};padding-left:{icon_size + 14}px"
    if layout != "badge-top-right" and "padding-top" not in style:
        return f"{style};padding-top:{icon_size + 20}px"
    return style


def _screenshot_sync(html: str) -> bytes | None:
    """Synchronously capture HTML as PNG."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_screenshot_async(html))
    finally:
        loop.close()


async def _screenshot_async(html: str) -> bytes | None:
    """Render HTML to 960x540 PNG using Playwright."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 960, "height": 540})
            await page.set_content(html, wait_until="networkidle")
            screenshot = await page.screenshot(type="png")
            await browser.close()
            return screenshot
    except Exception as e:
        logger.warning("render_convert.playwright_error", error=str(e)[:200])
        return None


def _save_html_preview(slides_html: list[dict], output_dir: Path) -> str:
    """Save combined HTML preview for all slides."""
    output_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex[:8]
    preview_path = output_dir / f"preview_{file_id}.html"

    slides_content = "\n".join(
        _embed_local_images(_embed_cached_icons(slide.get("html", "")))
        for slide in sorted(slides_html, key=lambda s: s.get("index", 0))
    )

    html = f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"/>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#1a1a2e; display:flex; flex-direction:column; align-items:center; gap:24px; padding:24px; font-family:'Pretendard',system-ui,sans-serif; }}
[data-slide] {{ border-radius:8px; box-shadow:0 4px 20px rgba(0,0,0,0.3); }}
</style>
<script>
function renderTables(){{document.querySelectorAll('[data-pptx-table-data]').forEach(function(el){{try{{var d=JSON.parse(el.getAttribute('data-pptx-table-data'));if(!d)return;var h=d.headers||[],rows=d.rows||[];var t='<table style="width:100%;height:100%;border-collapse:collapse;font-size:11px;font-family:Pretendard,sans-serif">';if(h.length){{t+='<tr>';h.forEach(function(c){{t+='<th style="background:#1e293b;color:#fff;padding:6px 8px;text-align:center;font-weight:600">'+c+'</th>';}});t+='</tr>';}}rows.forEach(function(r,i){{t+='<tr>';(Array.isArray(r)?r:Object.values(r)).forEach(function(c){{t+='<td style="padding:5px 8px;border-bottom:1px solid #e5e7eb;background:'+(i%2?'#f9fafb':'#fff')+'">'+c+'</td>';}});t+='</tr>';}});t+='</table>';el.innerHTML=t;}}catch(e){{}}}});}};
function renderCharts(){{document.querySelectorAll('[data-pptx-chart-data]').forEach(function(el){{try{{var d=JSON.parse(el.getAttribute('data-pptx-chart-data'));if(!d||!d.length)return;var max=Math.max.apply(null,d.map(function(i){{return parseFloat(i.value)||0;}}));var html='<div style="display:flex;flex-direction:column;justify-content:flex-end;align-items:stretch;height:100%;padding:8px;gap:4px;font-family:Pretendard,sans-serif;font-size:10px">';d.forEach(function(item){{var pct=max>0?((parseFloat(item.value)||0)/max*100):0;html+='<div style="display:flex;align-items:center;gap:6px"><span style="min-width:60px;text-align:right;color:#64748b">'+item.label+'</span><div style="flex:1;background:#e2e8f0;border-radius:3px;height:18px;position:relative"><div style="width:'+pct+'%;height:100%;background:#3b82f6;border-radius:3px"></div></div><span style="min-width:36px;color:#1e293b;font-weight:500">'+item.value+'</span></div>';}});html+='</div>';el.innerHTML=html;}}catch(e){{}}}});}};
document.addEventListener('DOMContentLoaded',function(){{renderTables();renderCharts();}});
</script>
</head><body>
{slides_content}
</body></html>"""

    preview_path.write_text(html, encoding="utf-8")
    return str(preview_path)


def _cleanup_file(path: str | None) -> None:
    """Remove a file if it exists."""
    if not path:
        return
    try:
        f = Path(path)
        if f.exists():
            f.unlink()
    except OSError:
        pass
