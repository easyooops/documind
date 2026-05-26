"""Phase C: Render & Convert — Playwright capture + deterministic PPTX build."""

from __future__ import annotations

import asyncio
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger
from src.formats.pptx.mapper.engine import CSStoOOXMLEngine
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

    slides_html = state.get("slides_html", [])
    title = state.get("title", "Presentation")

    if not slides_html:
        logger.error("render_convert.no_html")
        return {"errors": ["No HTML slides to convert"], "current_phase": "error"}

    previous_output = state.get("output_path")
    _cleanup_file(previous_output)

    html_screenshots = await _capture_slides(slides_html)

    output_dir = Path(settings.storage_local_path)
    engine = CSStoOOXMLEngine()
    output_path = engine.build_presentation(slides_html, output_dir, title=title)

    html_preview_path = _save_html_preview(slides_html, output_dir)

    logger.info("render_convert.complete", output=str(output_path), screenshots=len(html_screenshots))
    return {
        "output_path": str(output_path),
        "html_preview_path": html_preview_path,
        "html_screenshots": html_screenshots,
        "current_phase": "converting",
    }


async def _capture_slides(slides_html: list[dict]) -> list[str]:
    """Capture each slide HTML as a 960x540 PNG screenshot via Playwright."""
    screenshots = []
    output_dir = Path(settings.storage_local_path) / "captures"
    output_dir.mkdir(parents=True, exist_ok=True)

    for slide_data in slides_html[:12]:
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
        slide.get("html", "") for slide in sorted(slides_html, key=lambda s: s.get("index", 0))
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
document.addEventListener('DOMContentLoaded',function(){{renderTables();renderCharts();renderIcons();}});
function renderIcons(){{document.querySelectorAll('[data-pptx-icon]').forEach(function(el){{var name=el.getAttribute('data-pptx-icon');if(!name)return;name=name.replace(/_/g,'-');var color=getComputedStyle(el).color||'#1e293b';var hex=color.startsWith('#')?color.slice(1):'1e293b';var url='https://api.iconify.design/mdi/'+name+'.svg?color=%23'+hex+'&width=16&height=16';var img=document.createElement('img');img.src=url;img.style.cssText='width:16px;height:16px;vertical-align:middle;margin-right:4px;display:inline-block';img.onerror=function(){{this.style.display='none'}};el.insertBefore(img,el.firstChild);}});}};
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
