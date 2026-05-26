"""Phase B: HTML Generator — produces Constrained HTML with data-pptx-* attributes.

Each slide is generated in parallel. The HTML uses ONLY the allowed CSS subset
and includes data attributes that enable deterministic OOXML conversion.
"""

from __future__ import annotations

import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_config, load_agent_prompt
from src.core.logging import get_logger
from src.formats.pptx.css_spec import generate_css_spec_prompt
from src.formats.pptx.master_context import OBJECT_CATALOG
from src.schemas.agents import DocuMindState
from src.utils.language import output_language_instruction

logger = get_logger(__name__)

AGENT_NAME = "html_generator"
FORMAT_ID = "pptx"


class ElementUsageTracker:
    """Tracks which PPTX element types have been used across slides."""

    ALL_ELEMENTS = [
        "textbox", "icon", "kpi_card", "callout_box",
        "table", "chart_bar", "chart_line", "chart_pie", "chart_doughnut",
        "connector", "gradient_fill",
        "rounded_rect", "rect",
    ]

    def __init__(self):
        self.used: set[str] = set()

    def record(self, elements: list[str]) -> None:
        self.used.update(elements)

    def get_unused(self) -> list[str]:
        return [e for e in self.ALL_ELEMENTS if e not in self.used]

    def get_diversity_prompt(self) -> str:
        unused = self.get_unused()
        if not unused:
            return "All element types have been used. Continue with varied combinations."
        return (
            f"Elements NOT yet used in this presentation (prefer these):\n"
            f"  {', '.join(unused)}\n"
            f"Rule: Use at least 1 element from this list.\n"
            f"IMPORTANT: Prefer textboxes, icons, tables, charts over decorative shapes."
        )

    def to_dict(self) -> dict:
        return {"used": list(self.used), "unused": self.get_unused()}


async def html_generator_parallel(state: DocuMindState) -> dict:
    """Generate Constrained HTML for all slides in parallel."""
    logger.info("html_generator.start", qa_iteration=state.get("qa_iterations", 0))

    config = _as_dict(load_agent_config(AGENT_NAME, format_id=FORMAT_ID))
    max_parallel = _as_dict(config.get("parallel")).get("max_concurrent", 4)

    slide_blueprints = state.get("slide_blueprints", [])
    design_system = state.get("design_system", {})
    master_context = state.get("master_context", {})
    output_language = state.get("output_language", "ko_mixed")
    qa_feedback = state.get("qa_feedback", {})

    tracker = ElementUsageTracker()
    previous_usage = state.get("element_usage", {})
    if previous_usage and isinstance(previous_usage, dict):
        tracker.used = set(previous_usage.get("used", []))

    system_prompt = _build_system_prompt(design_system, master_context)

    slides_html: list[dict] = []

    for batch_start in range(0, len(slide_blueprints), max_parallel):
        batch = slide_blueprints[batch_start:batch_start + max_parallel]
        tasks = []

        for blueprint in batch:
            fix_instructions = _get_slide_fixes(blueprint.get("index", 0), qa_feedback)
            tasks.append(
                _generate_single_slide(
                    blueprint=blueprint,
                    design_system=design_system,
                    output_language=output_language,
                    system_prompt=system_prompt,
                    diversity_hint=tracker.get_diversity_prompt(),
                    fix_instructions=fix_instructions,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("html_generator.slide_error", error=str(result))
                continue
            slides_html.append(result)
            tracker.record(result.get("elements_used", []))

    logger.info("html_generator.complete", slides=len(slides_html))

    await _prefetch_icons(slides_html, design_system)
    await _generate_slide_images(slides_html)

    return {
        "slides_html": slides_html,
        "element_usage": tracker.to_dict(),
        "current_phase": "generating",
    }


async def _generate_single_slide(
    blueprint: dict,
    design_system: dict,
    output_language: str,
    system_prompt: str,
    diversity_hint: str,
    fix_instructions: list[str] | None = None,
) -> dict:
    """Generate Constrained HTML for a single slide."""
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    idx = blueprint.get("index", 1)

    context_parts = [
        output_language_instruction(output_language),
        f"\n## Slide #{idx} Blueprint",
        f"Type: {blueprint.get('slide_type', 'content')}",
        f"Title: {blueprint.get('title', '')}",
        f"Key Message: {blueprint.get('key_message', '')}",
        f"Purpose: {blueprint.get('purpose', '')}",
        f"Layout Hint: {blueprint.get('layout_hint', 'balanced')}",
        f"Suggested Elements: {', '.join(blueprint.get('suggested_elements', []))}",
    ]

    slide_type = blueprint.get("slide_type", "content")
    if slide_type in ("cover", "section"):
        context_parts.append("\n### LAYOUT NOTE: This is a cover/section slide — NO header/footer. Full-bleed design with dark gradient background.")
        context_parts.append("Generate the FULL slide including background.")
    else:
        context_parts.append(f"""
### BODY-ONLY GENERATION (Header/Footer/Background are AUTO-INJECTED by system)

DO NOT generate header, footer, or background elements.
Generate ONLY content elements within the body region: y:78 to y:514, x:40 to x:920.
The system will automatically prepend/append the fixed header/footer/background.

Available body area: 880px wide × 436px tall (starting at x:40, y:78)
""")

    if blueprint.get("content_blocks"):
        context_parts.append(f"\n### Content Blocks:\n{json.dumps(blueprint['content_blocks'], ensure_ascii=False, indent=2)}")

    if blueprint.get("content_elements"):
        context_parts.append(f"\n### Content:\n{json.dumps(blueprint['content_elements'], ensure_ascii=False, indent=2)}")

    if blueprint.get("data_points"):
        context_parts.append(f"\n### Data Points:\n{json.dumps(blueprint['data_points'], ensure_ascii=False, indent=2)}")

    context_parts.append(f"\n### Design Tokens:\n{json.dumps(design_system, ensure_ascii=False, indent=2)}")
    context_parts.append(f"\n### Diversity Requirement:\n{diversity_hint}")

    if fix_instructions:
        context_parts.append(
            f"\n### FIX REQUIRED (from VLM QA):\n"
            + "\n".join(f"- {fix}" for fix in fix_instructions)
        )

    context_parts.append(
        "\n\nGenerate the HTML for this slide. "
        "Output ONLY the HTML (starting with <div data-slide=...), no explanation."
    )

    context = "\n".join(context_parts)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context),
    ]

    response = await llm.ainvoke(messages)
    html = _extract_html(response.content)

    if slide_type not in ("cover", "section"):
        html = _inject_fixed_template(html, idx, blueprint, design_system)
    elif slide_type == "cover":
        html = _inject_cover_background(html, idx, design_system)

    elements_used = _extract_elements_used(html)

    return {
        "index": idx,
        "html": html,
        "elements_used": elements_used,
        "metadata": {
            "slide_type": blueprint.get("slide_type", "content"),
            "layout_hint": blueprint.get("layout_hint", "balanced"),
        },
    }


def _build_system_prompt(design_system: dict, master_context: dict) -> str:
    """Build the full system prompt including CSS spec, design context, and OOXML rules."""
    from src.formats.pptx.rulesets import get_ruleset

    css_spec = generate_css_spec_prompt()
    ruleset = get_ruleset()
    ooxml_rules = ruleset.get_generator_prompt_rules()

    base_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID) or ""

    design_section = ""
    if design_system:
        card_fills = design_system.get('card_fills', [])
        chart_colors = design_system.get('chart_colors', [])
        text_on_dark = design_system.get('text_on_dark', '#F1F5F9')
        cover_bg = design_system.get('cover_background', '')

        design_section = f"""
## Design System (USE ONLY THESE COLORS)
Primary: {design_system.get('primary', '#1E293B')}
Secondary: {design_system.get('secondary', '#475569')}
Accent: {design_system.get('accent', '#10B981')}
Background: {design_system.get('background', '#F8FAFC')}
Surface: {design_system.get('surface', '#FFFFFF')}
Text Primary: {design_system.get('text_primary', '#111827')}
Text Secondary: {design_system.get('text_secondary', '#6B7280')}
Text On Dark: {text_on_dark}
Card Fills (USE THESE for card backgrounds): {', '.join(card_fills) if card_fills else '#F1F5F9, #E2E8F0, #DBEAFE, #1E293B, #334155'}
Chart Colors: {', '.join(chart_colors) if chart_colors else '#3B82F6, #1E293B, #10B981, #F59E0B'}
Cover Background: {cover_bg}
Font: Pretendard

CRITICAL COLOR RULES:
- Card backgrounds: pick from Card Fills ONLY — never use #FFFFFF
- Text on dark cards: use Text On Dark color ({text_on_dark})
- Section labels and accent bars: use Accent color
- Body text: use Text Primary or Text Secondary
- Cover: use Cover Background gradient
"""
        hf = design_system.get("header_footer", {})
        if hf:
            design_section += """
## Template Auto-Injection (IMPORTANT)
For content slides: Header, footer, background are AUTO-INJECTED by the system.
You generate ONLY body content (y:78 to y:514).
DO NOT create any elements above y:78 or below y:514 for content slides.
The system guarantees pixel-identical header/footer/background across all content slides.
"""

    if base_prompt:
        return f"{base_prompt}\n\n{ooxml_rules}\n\n{css_spec}\n\n{design_section}"

    return f"""You are an expert PPT slide designer that generates Constrained HTML for PPTX conversion.

{ooxml_rules}

{css_spec}

{design_section}

## Output Format

Generate a single <div data-slide="N" ...> element containing all slide elements.
Each element MUST have:
- position:absolute with exact px coordinates
- data-pptx-type attribute
- data-pptx-shape attribute (for shapes)
- Inline styles using ONLY allowed CSS properties

## Example Output

<div data-slide="1" style="position:absolute;left:0;top:0;width:960px;height:540px">
  <div data-pptx-type="shape" data-pptx-shape="rect" data-pptx-z="0"
       style="position:absolute;left:0;top:0;width:960px;height:540px;background:linear-gradient(135deg,#1E293B,#475569)">
  </div>
  <div data-pptx-type="textbox" data-pptx-z="1"
       style="position:absolute;left:80px;top:200px;width:700px;height:90px;font-size:42px;font-weight:700;font-family:'Pretendard',sans-serif;color:#FFFFFF">
    Slide Title Here
  </div>
</div>

Output ONLY the HTML. No markdown fences, no explanation."""


def _inject_cover_background(html: str, slide_index: int, design_system: dict) -> str:
    """Ensure cover slide has the theme's cover_background applied."""
    import re

    cover_bg = design_system.get("cover_background", "")
    if not cover_bg:
        primary = design_system.get("primary", "#1E293B")
        cover_bg = f"linear-gradient(135deg, {primary} 0%, #0F172A 100%)"

    div_pattern = re.compile(
        r'(<div\s[^>]*data-pptx-type=[^>]*>(?:.*?</div>|[^<]*))',
        re.DOTALL,
    )
    top_pattern = re.compile(r'top:\s*(\d+(?:\.\d+)?)\s*px')
    width_pattern = re.compile(r'width:\s*(\d+(?:\.\d+)?)\s*px')
    height_pattern = re.compile(r'height:\s*(\d+(?:\.\d+)?)\s*px')

    has_full_bg = False
    for match in div_pattern.finditer(html):
        el = match.group(0)
        top_m = top_pattern.search(el)
        w_m = width_pattern.search(el)
        h_m = height_pattern.search(el)
        if top_m and w_m and h_m:
            if float(w_m.group(1)) >= 950 and float(h_m.group(1)) >= 530 and float(top_m.group(1)) <= 5:
                has_full_bg = True
                break

    if has_full_bg:
        old_bg_pattern = re.compile(
            r'(<div\s[^>]*data-pptx-type="shape"[^>]*style="[^"]*)'
            r'(background(?:-color)?:\s*[^;"]+)'
            r'([^"]*"[^>]*>)',
            re.DOTALL,
        )
        match = old_bg_pattern.search(html)
        if match:
            replacement = f'{match.group(1)}background:{cover_bg}{match.group(3)}'
            html = html[:match.start()] + replacement + html[match.end():]
    else:
        bg_el = (
            f'<div data-pptx-type="shape" data-pptx-shape="rect" '
            f'style="position:absolute;left:0;top:0;width:960px;height:540px;'
            f'background:{cover_bg}"></div>'
        )
        wrapper_pattern = re.compile(r'(<div\s+data-slide="?\d+"?[^>]*>)')
        m = wrapper_pattern.search(html)
        if m:
            insert_pos = m.end()
            html = html[:insert_pos] + f"\n  {bg_el}" + html[insert_pos:]

    return html


def _inject_fixed_template(html: str, slide_index: int, blueprint: dict, design_system: dict) -> str:
    """Inject fixed background/header/footer/accent bar into content slide HTML.

    The LLM generates ONLY body content. This function wraps it with the fixed
    template elements defined in slide_templates.json, using the theme colors
    from design_system.
    """
    import re

    accent = design_system.get("accent", "#2563EB")
    primary = design_system.get("primary", "#1E293B")
    body_bg = design_system.get("body_background", "#F8FAFC")
    text_primary = design_system.get("text_primary", primary)

    hf = design_system.get("header_footer", {})
    section_label = blueprint.get("section_label", "")
    title_text = blueprint.get("title", "")
    footer_left = hf.get("footer_left", "") if hf else ""
    footer_right_tpl = hf.get("footer_right", "Page {n}") if hf else "Page {n}"
    # Ensure single-line page number
    footer_right = footer_right_tpl.replace("{n}", str(slide_index)).replace("\n", " ").strip()

    body_elements = _extract_body_only(html)

    bg_html = (
        f'<div data-pptx-type="shape" style="position:absolute;left:0;top:0;'
        f'width:960px;height:540px;background-color:{body_bg}"></div>'
    )

    header_html = (
        f'<div data-pptx-type="textbox" style="position:absolute;left:40px;top:16px;'
        f'width:300px;height:14px;font-size:11px;font-weight:500;color:{accent}">'
        f'{section_label}</div>\n'
        f'  <div data-pptx-type="textbox" style="position:absolute;left:40px;top:32px;'
        f'width:860px;height:30px;font-size:22px;font-weight:700;color:{text_primary}">'
        f'{title_text}</div>\n'
        f'  <div data-pptx-type="shape" data-pptx-shape="rect" style="position:absolute;'
        f'left:40px;top:68px;width:48px;height:3px;background-color:{accent}"></div>'
    )

    footer_html = (
        f'<div data-pptx-type="textbox" style="position:absolute;left:40px;top:522px;'
        f'width:400px;height:14px;font-size:9px;font-weight:400;line-height:1.2;color:#94A3B8">{footer_left}</div>\n'
        f'  <div data-pptx-type="textbox" style="position:absolute;left:850px;top:522px;'
        f'width:80px;height:14px;font-size:9px;font-weight:400;line-height:1.2;color:#94A3B8;text-align:right">'
        f'{footer_right}</div>'
    )

    wrapper = (
        f'<div data-slide="{slide_index}" '
        f'style="position:absolute;left:0;top:0;width:960px;height:540px">'
    )

    parts = [
        wrapper, "\n",
        f"  {bg_html}\n",
        f"  {header_html}\n",
    ]

    for el in body_elements:
        parts.append(f"  {el}\n")

    parts.append(f"  {footer_html}\n")
    parts.append("</div>")

    return "".join(parts)


def _extract_body_only(html: str) -> list[str]:
    """Extract only body-region elements (y >= 72, y < 510) from generated HTML."""
    import re

    div_pattern = re.compile(
        r'(<div\s[^>]*data-pptx-type=[^>]*>(?:.*?</div>|[^<]*))',
        re.DOTALL,
    )
    top_pattern = re.compile(r'top:\s*(\d+(?:\.\d+)?)\s*px')
    width_pattern = re.compile(r'width:\s*(\d+(?:\.\d+)?)\s*px')
    height_pattern = re.compile(r'height:\s*(\d+(?:\.\d+)?)\s*px')

    body_elements = []
    for match in div_pattern.finditer(html):
        el_html = match.group(0)
        top_match = top_pattern.search(el_html)
        width_match = width_pattern.search(el_html)
        height_match = height_pattern.search(el_html)

        if not top_match:
            body_elements.append(el_html)
            continue

        top_val = float(top_match.group(1))
        width_val = float(width_match.group(1)) if width_match else 0
        height_val = float(height_match.group(1)) if height_match else 0

        if width_val >= 950 and height_val >= 530 and top_val <= 5:
            continue
        if top_val < 72:
            continue
        if top_val >= 515:
            continue

        body_elements.append(el_html)

    return body_elements


def _enforce_header_footer_consistency(
    slides_html: list[dict],
    blueprints: list[dict],
    design_system: dict,
) -> list[dict]:
    """Ensure all non-cover slides share the same header/footer background template."""
    import re

    hf = design_system.get("header_footer", {})
    if not hf:
        return slides_html

    cover_indices = set()
    for bp in blueprints:
        if bp.get("slide_type") in ("cover", "section"):
            cover_indices.add(bp.get("index", 0))

    template_header = None
    template_footer = None
    template_bg = None

    for slide in sorted(slides_html, key=lambda s: s.get("index", 0)):
        idx = slide.get("index", 0)
        if idx in cover_indices:
            continue
        html = slide.get("html", "")
        if not html:
            continue
        header_els, footer_els, bg_el = _extract_layout_regions(html)
        if header_els:
            template_header = header_els
        if footer_els:
            template_footer = footer_els
        if bg_el:
            template_bg = bg_el
        if template_header and template_footer:
            break

    if not template_header and not template_footer:
        return slides_html

    result = []
    for slide in slides_html:
        idx = slide.get("index", 0)
        if idx in cover_indices:
            result.append(slide)
            continue

        html = slide.get("html", "")
        if not html:
            result.append(slide)
            continue

        updated_html = _inject_template_regions(
            html, idx, template_header, template_footer, template_bg
        )
        slide["html"] = updated_html
        result.append(slide)

    return result


def _extract_layout_regions(html: str) -> tuple[list[str], list[str], str | None]:
    """Extract header (y < 75), footer (y > 510), and background elements."""
    import re

    header_elements = []
    footer_elements = []
    bg_element = None

    div_pattern = re.compile(
        r'(<div\s[^>]*data-pptx-type=[^>]*>(?:.*?</div>|[^<]*))',
        re.DOTALL,
    )
    top_pattern = re.compile(r'top:\s*(\d+(?:\.\d+)?)\s*px')
    width_pattern = re.compile(r'width:\s*(\d+(?:\.\d+)?)\s*px')
    height_pattern = re.compile(r'height:\s*(\d+(?:\.\d+)?)\s*px')

    for match in div_pattern.finditer(html):
        el_html = match.group(0)
        top_match = top_pattern.search(el_html)
        width_match = width_pattern.search(el_html)
        height_match = height_pattern.search(el_html)

        if not top_match:
            continue

        top_val = float(top_match.group(1))
        width_val = float(width_match.group(1)) if width_match else 0
        height_val = float(height_match.group(1)) if height_match else 0

        if width_val >= 950 and height_val >= 530 and top_val <= 5:
            bg_element = el_html
            continue

        if top_val < 75:
            header_elements.append(el_html)
        elif top_val >= 510:
            footer_elements.append(el_html)

    return header_elements, footer_elements, bg_element


def _inject_template_regions(
    html: str,
    slide_index: int,
    template_header: list[str] | None,
    template_footer: list[str] | None,
    template_bg: str | None,
) -> str:
    """Replace header/footer in the slide HTML with template versions."""
    import re

    top_pattern = re.compile(r'top:\s*(\d+(?:\.\d+)?)\s*px')
    width_pattern = re.compile(r'width:\s*(\d+(?:\.\d+)?)\s*px')
    height_pattern = re.compile(r'height:\s*(\d+(?:\.\d+)?)\s*px')

    div_pattern = re.compile(
        r'<div\s[^>]*data-pptx-type=[^>]*>(?:.*?</div>|[^<]*)',
        re.DOTALL,
    )

    body_elements = []
    for match in div_pattern.finditer(html):
        el_html = match.group(0)
        top_match = top_pattern.search(el_html)
        width_match = width_pattern.search(el_html)
        height_match = height_pattern.search(el_html)

        if not top_match:
            body_elements.append(el_html)
            continue

        top_val = float(top_match.group(1))
        width_val = float(width_match.group(1)) if width_match else 0
        height_val = float(height_match.group(1)) if height_match else 0

        if width_val >= 950 and height_val >= 530 and top_val <= 5:
            continue
        if top_val < 75 or top_val >= 510:
            continue
        body_elements.append(el_html)

    slide_wrapper_match = re.search(r'<div\s+data-slide="?\d+"?\s[^>]*>', html)
    wrapper_open = slide_wrapper_match.group(0) if slide_wrapper_match else (
        f'<div data-slide="{slide_index}" style="position:absolute;left:0;top:0;width:960px;height:540px">'
    )

    parts = [wrapper_open, "\n"]

    if template_bg:
        parts.append(f"  {template_bg}\n")
    if template_header:
        for h in template_header:
            page_replaced = re.sub(r'>\s*\d+\s*<', f'>{slide_index}<', h)
            parts.append(f"  {page_replaced}\n")
    for el in body_elements:
        parts.append(f"  {el}\n")
    if template_footer:
        for f_el in template_footer:
            page_replaced = re.sub(r'>\s*\d+\s*<', f'>{slide_index}<', f_el)
            parts.append(f"  {page_replaced}\n")

    parts.append("</div>")
    return "".join(parts)


def _extract_html(content: str) -> str:
    """Extract HTML from LLM response, stripping any markdown fences."""
    content = content.strip()
    if content.startswith("```html"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    if not content.startswith("<"):
        start = content.find("<div")
        if start >= 0:
            content = content[start:]
    return content


def _extract_elements_used(html: str) -> list[str]:
    """Parse data-pptx-shape values from generated HTML."""
    import re
    shapes = re.findall(r'data-pptx-shape="([^"]+)"', html)
    types = re.findall(r'data-pptx-type="([^"]+)"', html)
    chart_types = re.findall(r'data-pptx-chart-type="([^"]+)"', html)
    elements = list(set(shapes + [f"chart_{t}" for t in chart_types]))
    if "table" in types:
        elements.append("table")
    if "connector" in types:
        elements.append("connector")
    return elements


def _get_slide_fixes(slide_index: int, qa_feedback: dict) -> list[str] | None:
    """Extract fix instructions for a specific slide from QA feedback."""
    if not qa_feedback or not isinstance(qa_feedback, dict):
        return None
    fixes = qa_feedback.get("fix_instructions", [])
    slide_fixes = [
        f for f in fixes
        if f"Slide {slide_index}" in f or f"slide {slide_index}" in f or "all slides" in f.lower()
    ]
    return slide_fixes if slide_fixes else None


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


async def _prefetch_icons(slides_html: list[dict], design_system: dict) -> None:
    """Pre-download all icons used in slides from Iconify API and convert to PNG."""
    import re
    from src.utils.iconify import fetch_icon_png, RECOMMENDED_ICONS

    icon_pattern = re.compile(r'data-pptx-icon="([^"]+)"')
    accent_color = (design_system.get("accent", "#1E293B") or "#1E293B").lstrip("#")

    icons_to_fetch = set()
    for slide in slides_html:
        html = slide.get("html", "")
        matches = icon_pattern.findall(html)
        icons_to_fetch.update(matches)

    if not icons_to_fetch:
        return

    tasks = []
    for icon_name in icons_to_fetch:
        tasks.append(fetch_icon_png(icon_name, color=accent_color, size=32))

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("iconify.prefetch_complete", count=len(icons_to_fetch))


async def _generate_slide_images(slides_html: list[dict]) -> None:
    """Generate images for slides that have data-pptx-image-gen attributes."""
    import re
    from src.utils.image_gen import generate_image

    image_pattern = re.compile(r'data-pptx-image-gen="([^"]+)"')

    prompts_to_generate = set()
    for slide in slides_html:
        html = slide.get("html", "")
        matches = image_pattern.findall(html)
        prompts_to_generate.update(matches)

    if not prompts_to_generate:
        return

    tasks = []
    for prompt in prompts_to_generate:
        tasks.append(generate_image(prompt, style="professional"))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    success_count = sum(1 for r in results if r and not isinstance(r, Exception))
    logger.info("image_gen.batch_complete", total=len(prompts_to_generate), success=success_count)
