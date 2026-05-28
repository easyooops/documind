"""Phase B: HTML Generator — produces Constrained HTML with data-pptx-* attributes.

Each slide is generated in parallel. The HTML uses ONLY the allowed CSS subset
and includes data attributes that enable deterministic OOXML conversion.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_config, load_agent_prompt
from src.core.logging import get_logger
from src.formats.pptx.css_spec import generate_css_spec_prompt
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
    base_slides = {
        slide.get("index"): slide
        for slide in state.get("_base_slides_html", [])
        if isinstance(slide, dict) and slide.get("index") is not None
    }
    changed_indices = set(state.get("changed_slide_indices", []))
    parent_blueprints = {
        blueprint.get("index"): blueprint
        for blueprint in state.get("_base_version", {}).get("slide_plan", [])
        if isinstance(blueprint, dict)
    }
    revision_instruction = state.get("revision_instruction", state.get("user_query", ""))
    revision_scope = state.get("revision_scope", "minimal_patch")

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
            index = blueprint.get("index", 0)
            if base_slides and index not in changed_indices:
                prior = dict(base_slides[index])
                prior["metadata"] = {
                    "slide_type": blueprint.get("slide_type", "content"),
                    "layout_hint": blueprint.get("layout_hint", "balanced"),
                    "layout_plan": blueprint.get("layout_plan", {}),
                }
                slides_html.append(prior)
                tracker.record(prior.get("elements_used", []))
                continue
            fix_instructions = _get_slide_fixes(blueprint.get("index", 0), qa_feedback)
            tasks.append(
                _generate_single_slide(
                    blueprint=blueprint,
                    design_system=design_system,
                    output_language=output_language,
                    system_prompt=system_prompt,
                    diversity_hint=tracker.get_diversity_prompt(),
                    fix_instructions=fix_instructions,
                    original_html=base_slides.get(index, {}).get("html"),
                    original_blueprint=parent_blueprints.get(index),
                    revision_instruction=revision_instruction,
                    revision_scope=revision_scope,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error("html_generator.slide_error", error=str(result))
                continue
            slides_html.append(result)
            tracker.record(result.get("elements_used", []))

    if base_slides:
        slides_html = _reuse_parent_regions(
            slides_html, slide_blueprints, base_slides, changed_indices
        )

    logger.info("html_generator.complete", slides=len(slides_html))

    await _prefetch_icons(slides_html, design_system)
    await _generate_slide_images(slides_html)

    return {
        "slides_html": slides_html,
        "element_usage": tracker.to_dict(),
        "current_phase": "generating",
    }


def _reuse_parent_regions(
    slides_html: list[dict],
    blueprints: list[dict],
    base_slides: dict[int, dict],
    changed_indices: set[int],
) -> list[dict]:
    """Copy immutable background/header/footer regions from the selected parent version."""
    cover_indices = {
        bp.get("index") for bp in blueprints if bp.get("slide_type") in ("cover", "section")
    }
    reference_html = ""
    for index in sorted(base_slides):
        if index not in cover_indices and base_slides[index].get("html"):
            reference_html = base_slides[index]["html"]
            break
    if not reference_html:
        return slides_html

    header, footer, background = _extract_layout_regions(reference_html)
    if not header and not footer and not background:
        return slides_html

    for slide in slides_html:
        index = slide.get("index")
        if index in cover_indices or index not in changed_indices:
            continue
        if slide.get("html"):
            slide["html"] = _inject_template_regions(
                slide["html"], index, header, footer, background
            )
    return slides_html


async def _generate_single_slide(
    blueprint: dict,
    design_system: dict,
    output_language: str,
    system_prompt: str,
    diversity_hint: str,
    fix_instructions: list[str] | None = None,
    original_html: str | None = None,
    original_blueprint: dict | None = None,
    revision_instruction: str = "",
    revision_scope: str = "minimal_patch",
) -> dict:
    """Generate Constrained HTML for a single slide."""
    from src.formats.pptx.rulesets import get_ruleset

    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    idx = blueprint.get("index", 1)
    layout_plan = blueprint.get("layout_plan", {})
    body_layout = get_ruleset().get_body_layout(layout_plan.get("body_layout_id", ""))
    body_region = _get_master_layout(design_system).get(
        "body_region", {"x": 40, "y": 78, "w": 880, "h": 436}
    )
    if body_layout:
        body_layout = {**body_layout, "body_region": body_region}

    if original_html and revision_scope == "minimal_patch":
        patched_html = await _patch_existing_slide(
            llm=llm,
            slide_index=idx,
            original_html=original_html,
            original_blueprint=original_blueprint or {},
            proposed_blueprint=blueprint,
            revision_instruction=revision_instruction,
            fix_instructions=fix_instructions or [],
        )
        if patched_html is not None:
            return {
                "index": idx,
                "html": patched_html,
                "elements_used": _extract_elements_used(patched_html),
                "metadata": {
                    "slide_type": blueprint.get("slide_type", "content"),
                    "layout_hint": (original_blueprint or blueprint).get(
                        "layout_hint", "balanced"
                    ),
                    "layout_plan": (original_blueprint or blueprint).get("layout_plan", {}),
                },
            }

    context_parts = [
        output_language_instruction(output_language),
        f"\n## Slide #{idx} Blueprint",
        f"Type: {blueprint.get('slide_type', 'content')}",
        f"Title: {blueprint.get('title', '')}",
        f"Key Message: {blueprint.get('key_message', '')}",
        f"Purpose: {blueprint.get('purpose', '')}",
        f"Layout Hint: {blueprint.get('layout_hint', 'balanced')}",
        f"Suggested Elements: {', '.join(blueprint.get('suggested_elements', []))}",
        f"Approved Layout Plan: {json.dumps(layout_plan, ensure_ascii=False)}",
    ]
    if body_layout:
        context_parts.append(
            "\n### Approved Standard Body Layout (binding)\n"
            f"{json.dumps(body_layout, ensure_ascii=False, indent=2)}"
        )

    slide_type = blueprint.get("slide_type", "content")
    if original_html and revision_scope == "minimal_patch":
        original_body = "\n".join(_extract_body_only(original_html))
        context_parts.extend([
            "\n### SURGICAL REVISION MODE (MANDATORY)",
            f"User correction request: {revision_instruction}",
            "This slide already exists. Preserve its body composition, element count, positions, "
            "dimensions, colors, typography, icons, chart/table structure, and all text not directly "
            "named by the correction. Change the smallest possible fragment needed to satisfy the "
            "request. Do NOT redesign or rewrite the whole slide.",
            (
                "\n### Existing Blueprint (preserve unless explicitly targeted)\n"
                f"{json.dumps(original_blueprint or {}, ensure_ascii=False, indent=2)}"
            ),
            "\n### Existing Body HTML (edit minimally and reuse)\n" + original_body,
        ])
    elif original_html:
        context_parts.extend([
            "\n### EXISTING DOCUMENT REVISION",
            f"Revision scope: {revision_scope}",
            f"User request: {revision_instruction}",
            "The user requested a substantial content or composition change on this slide. "
            "You may regenerate the body to fulfill the request. Continue using the locked deck "
            "design system; header, footer, and document-wide visual identity are injected by "
            "the system and must remain consistent.",
        ])

    if slide_type in ("cover", "section"):
        context_parts.append("\n### LAYOUT NOTE: This is a cover/section slide — NO header/footer. Full-bleed design with dark gradient background.")
        context_parts.append("Generate the FULL slide including background.")
    else:
        context_parts.append(f"""
### BODY-ONLY GENERATION (Header/Footer/Background are AUTO-INJECTED by system)

DO NOT generate header, footer, or background elements.
Generate ONLY content elements within the body region: y:{body_region['y']} to y:{body_region['y'] + body_region['h']}, x:{body_region['x']} to x:{body_region['x'] + body_region['w']}.
The system will automatically prepend/append the fixed header/footer/background.

Available body area: {body_region['w']}px wide × {body_region['h']}px tall (starting at x:{body_region['x']}, y:{body_region['y']})
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
            "\n### FIX REQUIRED (from VLM QA):\n"
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
            "layout_plan": blueprint.get("layout_plan", {}),
        },
    }


async def _patch_existing_slide(
    llm,
    slide_index: int,
    original_html: str,
    original_blueprint: dict,
    proposed_blueprint: dict,
    revision_instruction: str,
    fix_instructions: list[str],
) -> str | None:
    """Apply text/data replacements to an existing slide without rebuilding its structure."""
    from html import escape

    from src.utils.json_repair import parse_llm_json

    prompt = f"""You are making a surgical correction to existing slide #{slide_index}.
The user requested:
{revision_instruction}

Preserve ALL unchanged text, HTML structure, element positions, styles, colors, header, footer,
background, icons, tables, and charts. A targeted factual/wording correction must be handled as
exact replacements in the existing HTML, not as a rewrite.

Original blueprint:
{json.dumps(original_blueprint, ensure_ascii=False, indent=2)}

Candidate content update from planning (use only the specifically required differences):
{json.dumps(proposed_blueprint, ensure_ascii=False, indent=2)}

Existing full slide HTML:
{original_html}

Additional validation fixes, only if relevant to the requested correction:
{json.dumps(fix_instructions, ensure_ascii=False)}

Return ONLY JSON:
{{
  "requires_relayout": false,
  "replacements": [
    {{"old": "exact existing HTML text or attribute value", "new": "replacement value"}}
  ]
}}
Set requires_relayout=true only if the user explicitly asks to add/remove/rearrange visual
elements or change the layout. Never set it merely to improve design."""
    try:
        response = await llm.ainvoke([
            SystemMessage(content="You edit existing slide HTML with minimal exact replacements."),
            HumanMessage(content=prompt),
        ])
        result = parse_llm_json(response.content)
        if not isinstance(result, dict):
            return original_html
        if result.get("requires_relayout") is True:
            return None
        updated_html = original_html
        for replacement in result.get("replacements", []):
            if not isinstance(replacement, dict):
                continue
            old = str(replacement.get("old", ""))
            new = str(replacement.get("new", ""))
            if not old or old == new:
                continue
            if old in updated_html:
                updated_html = updated_html.replace(old, new, 1)
            elif escape(old) in updated_html:
                updated_html = updated_html.replace(escape(old), escape(new), 1)
        return updated_html
    except Exception as exc:
        logger.warning("html_generator.surgical_patch_failed", slide=slide_index, error=str(exc)[:200])
        return original_html


def _build_system_prompt(design_system: dict, master_context: dict) -> str:
    """Build the full system prompt including CSS spec, design context, and OOXML rules."""
    from src.formats.pptx.rulesets import get_ruleset

    css_spec = generate_css_spec_prompt()
    ruleset = get_ruleset()
    ooxml_rules = ruleset.get_generator_prompt_rules()
    planner_layout_rules = ruleset.get_planner_layout_rules()

    base_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID) or ""

    design_section = ""
    if design_system:
        card_fills = design_system.get('card_fills', [])
        chart_colors = design_system.get('chart_colors', [])
        text_on_dark = design_system.get('text_on_dark', '#F1F5F9')
        cover_bg = design_system.get('cover_background', '')
        font_heading = design_system.get('font_heading', 'Pretendard')
        font_body = design_system.get('font_body', font_heading)

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
Heading Font: {font_heading}
Body Font: {font_body}

CRITICAL COLOR RULES:
- Card backgrounds: pick from Card Fills. White is allowed only with a visible
  1px neutral boundary when it sits on a white or near-white background.
- Text on dark cards: use Text On Dark color ({text_on_dark})
- Section labels and accent bars: use Accent color
- Body text: use Text Primary or Text Secondary
- Cover: use Cover Background gradient
- Use Heading Font and Body Font exactly; do not substitute a default font
"""
        if master_context.get("source") == "template":
            design_section += """
## Uploaded Template Legibility Rules
- The final PPTX inherits the uploaded template backdrop, which may be white.
- Never place white or pale text directly on a light cover/background.
- Keep independent content boxes, tables, and charts separated by at least 12px.
- Do not create a second title element in the body; the header title is injected once.
"""
        master_layout = _get_master_layout(design_system)
        if master_layout:
            design_section += """
## Template Auto-Injection (IMPORTANT)
For content slides: Header, footer, background are AUTO-INJECTED by the system.
You generate ONLY body content within the selected master layout body_region.
DO NOT create elements in the selected fixed header/footer zones for content slides.
The system guarantees pixel-identical header/footer/background across all content slides.
"""
            design_section += (
                "\nSelected deck-level master layout (binding):\n"
                f"{json.dumps(master_layout, ensure_ascii=False, indent=2)}\n"
            )

    if base_prompt:
        return (
            f"{base_prompt}\n\n{planner_layout_rules}\n\n{ooxml_rules}\n\n"
            f"{css_spec}\n\n{design_section}"
        )

    return f"""You are an expert PPT slide designer that generates Constrained HTML for PPTX conversion.

{planner_layout_rules}

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


def _get_master_layout(design_system: dict) -> dict:
    """Return a resolved deck master, falling back to the standard default."""
    master_layout = design_system.get("master_layout", {})
    if master_layout:
        return master_layout
    from src.formats.pptx.rulesets import get_ruleset

    return get_ruleset().resolve_master_layout()


def _zone_element(zone: dict, element_name: str, fallback: dict) -> dict:
    """Read a positioned master-zone element with a backwards-compatible fallback."""
    element = zone.get("elements", {}).get(element_name, {})
    return element if element else fallback


def _box_style(box: dict) -> str:
    """Convert a catalog position record into deterministic CSS geometry."""
    return (
        f"left:{box['x']}px;top:{box['y']}px;"
        f"width:{box['w']}px;height:{box['h']}px;"
    )


def _inject_fixed_template(html: str, slide_index: int, blueprint: dict, design_system: dict) -> str:
    """Inject fixed background/header/footer/accent bar into content slide HTML.

    The LLM generates ONLY body content. This function wraps it with the fixed
    template elements defined in slide_templates.json, using the theme colors
    from design_system.
    """
    accent = design_system.get("accent", "#2563EB")
    primary = design_system.get("primary", "#1E293B")
    body_bg = design_system.get("body_background", "#F8FAFC")
    text_primary = design_system.get("text_primary", primary)
    font_heading = design_system.get("font_heading", "Pretendard")
    font_body = design_system.get("font_body", font_heading)
    master_layout = _get_master_layout(design_system)
    header_zone = master_layout.get("header", {})
    footer_zone = master_layout.get("footer", {})
    body_region = master_layout.get("body_region", {"x": 40, "y": 78, "w": 880, "h": 436})
    section_box = _zone_element(
        header_zone, "section_label", {"x": 40, "y": 16, "w": 300, "h": 14}
    )
    title_box = _zone_element(
        header_zone, "slide_title", {"x": 40, "y": 32, "w": 860, "h": 30}
    )
    accent_box = _zone_element(
        header_zone, "accent_bar", {"x": 40, "y": 68, "w": 48, "h": 3}
    )
    footer_left_box = _zone_element(
        footer_zone, "footer_left", {"x": 40, "y": 522, "w": 400, "h": 14}
    )
    footer_right_box = _zone_element(
        footer_zone, "footer_right", {"x": 850, "y": 522, "w": 80, "h": 14}
    )

    hf = design_system.get("header_footer", {})
    section_label = blueprint.get("section_label", "")
    title_text = blueprint.get("title", "")
    title_font_size = _header_title_font_size(title_text)
    footer_left = hf.get("footer_left", "") if hf else ""
    footer_right_tpl = hf.get("footer_right", "Page {n}") if hf else "Page {n}"
    # Ensure single-line page number
    footer_right = footer_right_tpl.replace("{n}", str(slide_index)).replace("\n", " ").strip()

    body_elements = _extract_body_only(html, body_region)

    bg_html = (
        f'<div data-pptx-region="background" data-pptx-type="shape" style="position:absolute;left:0;top:0;'
        f'width:960px;height:540px;background-color:{body_bg}"></div>'
    )

    header_html = (
        f'<div data-pptx-region="header" data-pptx-type="textbox" style="position:absolute;{_box_style(section_box)}'
        f"font-size:11px;font-weight:500;font-family:'{font_body}';color:{accent}\">"
        f'{section_label}</div>\n'
        f'  <div data-pptx-region="header" data-pptx-type="textbox" style="position:absolute;{_box_style(title_box)}'
        f"font-size:{title_font_size}px;font-weight:700;font-family:'{font_heading}';color:{text_primary}\">"
        f'{title_text}</div>\n'
        f'  <div data-pptx-region="header" data-pptx-type="shape" data-pptx-shape="rect" style="position:absolute;'
        f'{_box_style(accent_box)}background-color:{accent}"></div>'
    )

    footer_html = (
        f'<div data-pptx-region="footer" data-pptx-type="textbox" style="position:absolute;{_box_style(footer_left_box)}'
        f"font-size:9px;font-weight:400;line-height:1.2;font-family:'{font_body}';color:#94A3B8\">{footer_left}</div>\n"
        f'  <div data-pptx-region="footer" data-pptx-type="textbox" style="position:absolute;{_box_style(footer_right_box)}'
        f"font-size:9px;font-weight:400;line-height:1.2;font-family:'{font_body}';color:#94A3B8;text-align:right\">"
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


def _header_title_font_size(title: str) -> int:
    """Keep assertive, long Korean headings within the one-line template header."""
    title_length = len(title.strip())
    if title_length <= 36:
        return 22
    if title_length <= 48:
        return 20
    return 18


def _extract_body_only(html: str, body_region: dict | None = None) -> list[str]:
    """Extract only body-region elements (y >= 72, y < 510) from generated HTML."""
    region = body_region or {"y": 72, "h": 443}
    min_y = float(region.get("y", 72)) - 6
    max_y = float(region.get("y", 72)) + float(region.get("h", 443)) + 2
    body_elements = []
    for element in _top_level_slide_elements(html):
        region = element.attrs.get("data-pptx-region")
        top_val = _style_px(element, "top")
        width_val = _style_px(element, "width")
        height_val = _style_px(element, "height")
        if region in {"background", "header", "footer"}:
            continue
        if width_val >= 950 and height_val >= 530 and top_val <= 5:
            continue
        if top_val < min_y or top_val >= max_y:
            continue
        body_elements.append(str(element))
    return body_elements


def _enforce_header_footer_consistency(
    slides_html: list[dict],
    blueprints: list[dict],
    design_system: dict,
) -> list[dict]:
    """Ensure all non-cover slides share the same header/footer background template."""
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
    header_elements = []
    footer_elements = []
    bg_element = None

    for element in _top_level_slide_elements(html):
        region = element.attrs.get("data-pptx-region")
        top_val = _style_px(element, "top")
        width_val = _style_px(element, "width")
        height_val = _style_px(element, "height")

        if region == "background" or (
            width_val >= 950 and height_val >= 530 and top_val <= 5
        ):
            bg_element = str(element)
            continue

        if region == "header" or top_val < 75:
            header_elements.append(str(element))
        elif region == "footer" or top_val >= 510:
            footer_elements.append(str(element))

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

    body_elements = _extract_body_only(html)
    wrapper_open = (
        f'<div data-slide="{slide_index}" '
        f'style="position:absolute;left:0;top:0;width:960px;height:540px">'
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
            page_replaced = re.sub(r"(Page\s*)\d+", rf"\g<1>{slide_index}", f_el)
            page_replaced = re.sub(r">\s*\d+\s*<", f">{slide_index}<", page_replaced)
            parts.append(f"  {page_replaced}\n")

    parts.append("</div>")
    return "".join(parts)


def _top_level_slide_elements(html: str) -> list:
    """Return complete slide children without truncating nested HTML elements."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.find(attrs={"data-slide": True})
    if not wrapper:
        return []
    return [
        element for element in wrapper.find_all(recursive=False)
        if getattr(element, "attrs", {}).get("data-pptx-type")
    ]


def _style_px(element, name: str) -> float:
    """Read a pixel position value from an inline-style HTML element."""
    import re

    style = str(element.attrs.get("style", ""))
    match = re.search(rf"(?:^|;)\s*{name}\s*:\s*(-?\d+(?:\.\d+)?)px", style)
    return float(match.group(1)) if match else 0.0


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
    """Pre-download, validate, and materialize all icons used in slides."""

    from bs4 import BeautifulSoup

    from src.formats.pptx.agents.nodes.render_convert import _normalize_legacy_icon_nodes
    from src.utils.iconify import ensure_safe_icon_assets, normalize_icon_color

    fallback_color = normalize_icon_color("#1E293B")
    materialized_count = 0
    resolved_count = 0
    for slide in slides_html:
        slide["html"] = _normalize_legacy_icon_nodes(str(slide.get("html", "")))
        soup = BeautifulSoup(slide.get("html", ""), "html.parser")
        for node in soup.find_all(attrs={"data-pptx-icon": True}):
            style = str(node.attrs.get("style", ""))
            color_match = re.search(r"(?:^|;)\s*color\s*:\s*(#[0-9a-fA-F]{3,8})", style)
            color = normalize_icon_color(color_match.group(1) if color_match else fallback_color)
            requested_icon = str(node.attrs["data-pptx-icon"])
            safe_icon, asset = await ensure_safe_icon_assets(requested_icon, color=color, size=32)
            if safe_icon != requested_icon:
                resolved_count += 1
                node.attrs["data-pptx-icon"] = safe_icon
            if str(node.attrs.get("data-pptx-type", "")) == "icon" and asset.html_path:
                _materialize_html_icon_node(node, asset.html_path)
                materialized_count += 1
        slide["html"] = str(soup)

    logger.info(
        "iconify.prefetch_complete",
        materialized=materialized_count,
        resolved=resolved_count,
    )


def _materialize_html_icon_node(node, icon_path) -> None:
    """Make standalone icon elements visible in raw HTML before conversion."""
    path = icon_path
    if not path or not path.exists():
        return
    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/svg+xml"
    data_uri = base64.b64encode(path.read_bytes()).decode("ascii")
    style = str(node.attrs.get("style", ""))
    style = _remove_style_properties(style, {"background", "background-color"})
    node.attrs["style"] = (
        f"{style};background-color:transparent;"
        f"background-image:url(data:{mime_type};base64,{data_uri});"
        "background-size:contain;background-repeat:no-repeat;background-position:center"
    )


def _remove_style_properties(style: str, property_names: set[str]) -> str:
    declarations = []
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, _, value = declaration.partition(":")
        if name.strip().lower() in property_names:
            continue
        declarations.append(f"{name.strip()}:{value.strip()}")
    return ";".join(declarations)


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
