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
    """Convert legacy textbox icons into explicit icon elements before render/convert."""
    normalized = []
    for slide in slides_html:
        copied = dict(slide)
        copied["html"] = _normalize_legacy_icon_nodes(str(slide.get("html", "")))
        normalized.append(copied)
    return normalized


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


def _style_number(style: str, property_name: str) -> float:
    match = re.search(rf"(?:^|;)\s*{property_name}\s*:\s*(\d+(?:\.\d+)?)px", style)
    return float(match.group(1)) if match else 0.0


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
        _embed_cached_icons(slide.get("html", ""))
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
