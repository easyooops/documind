"""PPTX Template Analyzer - parses uploaded .pptx/.potx and extracts design system."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pptx import Presentation
from sqlalchemy import select

from src.agents.loader import get_llm_for_agent, load_agent_prompt
from src.core.logging import get_logger
from src.infrastructure.database import get_session_factory
from src.infrastructure.models import Template
from src.infrastructure.storage import create_storage_backend
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "template_analysis"
FORMAT_ID = "pptx"
EMU_PER_PX = 9525


async def template_analyzer(state: DocuMindState) -> dict:
    """Analyze uploaded PPTX template files and expose style/layout contracts downstream."""
    logger.info("template_analysis.start")

    template_id = state.get("template_id")
    if not template_id:
        return {"template_profile": None, "current_phase": "designing"}

    template = await _load_template_record(str(template_id))
    if not template:
        logger.warning("template_analysis.not_found", template_id=template_id)
        return {"template_profile": None, "current_phase": "designing"}

    try:
        template_bytes = await create_storage_backend().load(template.file_path)
        structural_profile = _extract_pptx_profile(template_bytes, template.filename)
        try:
            template_profile = await _interpret_template_profile(structural_profile)
        except Exception as exc:
            logger.warning(
                "template_analysis.interpret_failed",
                template_id=template_id,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            template_profile = {}
        template_profile = _merge_template_profiles(structural_profile, template_profile)
        await _persist_template_analysis(str(template_id), template_profile)
    except Exception as exc:
        logger.warning(
            "template_analysis.failed",
            template_id=template_id,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        template_profile = {
            "filename": template.filename,
            "analysis_status": "failed",
            "visual_description": "Template file could not be parsed; use generated design system.",
            "design_keywords": ["fallback"],
        }

    logger.info(
        "template_analysis.complete",
        layouts_count=len(template_profile.get("layout_patterns", [])),
        masters_count=template_profile.get("structure", {}).get("masters_count", 0),
    )
    return {"template_profile": template_profile, "current_phase": "designing"}


async def _load_template_record(template_id: str) -> Template | None:
    async with get_session_factory()() as db:
        result = await db.execute(select(Template).where(Template.id == template_id))
        return result.scalar_one_or_none()


async def _persist_template_analysis(template_id: str, analysis: dict) -> None:
    async with get_session_factory()() as db:
        result = await db.execute(select(Template).where(Template.id == template_id))
        template = result.scalar_one_or_none()
        if template:
            template.analysis = analysis
            template.status = "analyzed"
            await db.commit()


def _extract_pptx_profile(template_bytes: bytes, filename: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".pptx", delete=False) as tmp:
        tmp.write(template_bytes)
        tmp_path = Path(tmp.name)

    try:
        prs = Presentation(str(tmp_path))
        colors: Counter[str] = Counter()
        fonts: Counter[str] = Counter()
        font_sizes: Counter[int] = Counter()
        layout_patterns = []
        masters = []

        for master_index, master in enumerate(prs.slide_masters, start=1):
            master_shapes = [_shape_profile(shape, colors, fonts, font_sizes) for shape in master.shapes]
            masters.append(
                {
                    "index": master_index,
                    "name": getattr(master, "name", f"Master {master_index}"),
                    "shape_count": len(master.shapes),
                    "large_background_shapes": [
                        shape for shape in master_shapes if _is_background_shape(shape)
                    ][:5],
                    "accent_shapes": [
                        shape for shape in master_shapes if shape.get("role_hint") == "accent"
                    ][:8],
                }
            )

        layout_profiles = []
        for layout_index, layout in enumerate(prs.slide_layouts, start=1):
            placeholders = []
            shapes = []
            for shape in layout.shapes:
                profile = _shape_profile(shape, colors, fonts, font_sizes)
                shapes.append(profile)
                if shape.is_placeholder:
                    placeholders.append(profile)

            pattern = _infer_layout_pattern(placeholders)
            layout_patterns.append(pattern)
            layout_profiles.append(
                {
                    "index": layout_index,
                    "name": getattr(layout, "name", f"Layout {layout_index}"),
                    "pattern": pattern,
                    "placeholders": placeholders[:12],
                    "background_shapes": [shape for shape in shapes if _is_background_shape(shape)][:4],
                    "accent_shapes": [
                        shape for shape in shapes if shape.get("role_hint") == "accent"
                    ][:8],
                }
            )

        slide_profiles = []
        for slide_index, slide in enumerate(prs.slides, start=1):
            shapes = [_shape_profile(shape, colors, fonts, font_sizes) for shape in slide.shapes]
            slide_profiles.append(
                {
                    "index": slide_index,
                    "layout_name": getattr(slide.slide_layout, "name", ""),
                    "shape_count": len(slide.shapes),
                    "background_shapes": [shape for shape in shapes if _is_background_shape(shape)][:3],
                    "accent_shapes": [
                        shape for shape in shapes if shape.get("role_hint") == "accent"
                    ][:6],
                    "text_shapes": [shape for shape in shapes if shape.get("has_text")][:8],
                }
            )

        palette = [f"#{color}" for color, _ in colors.most_common(12)]
        font_names = [font for font, _ in fonts.most_common(6)]
        heading_sizes = [size for size, _ in font_sizes.most_common(6)]
        unique_patterns = list(dict.fromkeys(layout_patterns))

        return {
            "filename": filename,
            "analysis_status": "parsed",
            "structure": {
                "masters_count": len(prs.slide_masters),
                "layouts_count": len(prs.slide_layouts),
                "sample_slides_count": len(prs.slides),
                "slide_width_px": _emu_to_px(prs.slide_width),
                "slide_height_px": _emu_to_px(prs.slide_height),
            },
            "color_palette": _palette_to_tokens(palette),
            "typography": {
                "heading_font": font_names[0] if font_names else "Aptos",
                "body_font": font_names[1] if len(font_names) > 1 else (font_names[0] if font_names else "Aptos"),
                "observed_fonts": font_names,
                "heading_sizes": heading_sizes[:4] or [40, 30, 22, 18],
                "body_size": _median_size(heading_sizes) or 14,
            },
            "slide_master": _default_master_contract(),
            "masters": masters[:3],
            "layout_profiles": layout_profiles[:12],
            "sample_slides": slide_profiles[:6],
            "layout_patterns": unique_patterns or ["top-title-grid"],
            "style_rules": {
                "background_pattern": _infer_background_pattern(colors),
                "accent_usage": _infer_accent_usage(layout_profiles),
                "spacing_unit": 20,
                "template_similarity_target": "Preserve master regions, layout rhythm, palette, typography, background/accent treatment, and placeholder proportions.",
            },
            "visual_description": "Parsed PPTX template structure, masters, layouts, colors, fonts, placeholders, backgrounds, and accent shapes.",
            "design_keywords": _infer_design_keywords(palette, unique_patterns),
        }
    finally:
        tmp_path.unlink(missing_ok=True)


async def _interpret_template_profile(structural_profile: dict) -> dict:
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                "Analyze this parsed PPTX template profile. "
                "Return JSON that preserves actual extracted values and adds concise guidance "
                "for downstream layout/style/code agents.\n\n"
                f"{json.dumps(structural_profile, ensure_ascii=False, indent=2)[:12000]}"
            )
        ),
    ]
    response = await llm.ainvoke(messages)
    parsed = parse_llm_json(response.content, fallback={})
    return parsed if isinstance(parsed, dict) else {}


def _merge_template_profiles(structural: dict, interpreted: dict) -> dict:
    merged = {**structural, **{k: v for k, v in interpreted.items() if v not in (None, "", [])}}
    merged["raw_extracted_profile"] = structural
    merged["template_contract"] = {
        "use_template_as_design_basis": True,
        "preserve_master_layout": True,
        "preserve_background_style": True,
        "preserve_typography": True,
        "preserve_palette": True,
        "adapt_content_to_similar_layouts": True,
    }
    return merged


def _shape_profile(shape: Any, colors: Counter[str], fonts: Counter[str], font_sizes: Counter[int]) -> dict:
    position = {
        "x": _emu_to_px(shape.left),
        "y": _emu_to_px(shape.top),
        "w": _emu_to_px(shape.width),
        "h": _emu_to_px(shape.height),
    }
    profile = {
        "name": getattr(shape, "name", ""),
        "shape_type": str(getattr(shape, "shape_type", "")),
        "position": position,
        "is_placeholder": bool(getattr(shape, "is_placeholder", False)),
        "has_text": bool(getattr(shape, "has_text_frame", False) and shape.has_text_frame),
    }
    if shape.is_placeholder:
        try:
            profile["placeholder_type"] = str(shape.placeholder_format.type)
            profile["placeholder_idx"] = shape.placeholder_format.idx
        except ValueError:
            pass

    fill_color = _extract_fill_color(shape)
    if fill_color:
        colors[fill_color] += 1
        profile["fill_color"] = f"#{fill_color}"

    line_color = _extract_line_color(shape)
    if line_color:
        colors[line_color] += 1
        profile["line_color"] = f"#{line_color}"

    if profile["has_text"]:
        text_samples = []
        for paragraph in shape.text_frame.paragraphs[:3]:
            for run in paragraph.runs[:4]:
                text = (run.text or "").strip()
                if text:
                    text_samples.append(text[:80])
                if run.font.name:
                    fonts[run.font.name] += 1
                if run.font.size:
                    font_sizes[_emu_to_pt(run.font.size)] += 1
                font_color = _extract_font_color(run)
                if font_color:
                    colors[font_color] += 1
        if text_samples:
            profile["text_samples"] = text_samples[:4]

    profile["role_hint"] = _infer_shape_role(profile)
    return profile


def _extract_fill_color(shape: Any) -> str | None:
    try:
        fill = shape.fill
        if fill and fill.fore_color and fill.fore_color.rgb:
            return str(fill.fore_color.rgb)
    except (AttributeError, TypeError):
        return None
    return None


def _extract_line_color(shape: Any) -> str | None:
    try:
        if shape.line and shape.line.color and shape.line.color.rgb:
            return str(shape.line.color.rgb)
    except (AttributeError, TypeError):
        return None
    return None


def _extract_font_color(run: Any) -> str | None:
    try:
        if run.font.color and run.font.color.rgb:
            return str(run.font.color.rgb)
    except (AttributeError, TypeError):
        return None
    return None


def _infer_shape_role(profile: dict) -> str:
    pos = profile["position"]
    if pos["w"] >= 860 and pos["h"] >= 440:
        return "background"
    if pos["h"] <= 8 or pos["w"] <= 8:
        return "accent"
    if profile.get("is_placeholder"):
        placeholder = str(profile.get("placeholder_type", "")).lower()
        if "title" in placeholder:
            return "title_placeholder"
        if "body" in placeholder or "object" in placeholder:
            return "body_placeholder"
    return "shape"


def _is_background_shape(profile: dict) -> bool:
    return profile.get("role_hint") == "background"


def _infer_layout_pattern(placeholders: list[dict]) -> str:
    body_placeholders = [
        item
        for item in placeholders
        if item.get("role_hint") in {"body_placeholder", "shape"}
        and item.get("position", {}).get("y", 0) >= 100
    ]
    if len(body_placeholders) >= 4:
        return "card-grid-4"
    if len(body_placeholders) == 3:
        return "card-grid-3"
    if len(body_placeholders) == 2:
        lefts = sorted(item["position"]["x"] for item in body_placeholders)
        if lefts[-1] - lefts[0] > 250:
            return "two-column"
    if body_placeholders:
        pos = body_placeholders[0]["position"]
        if pos["w"] > 600:
            return "top-title-grid"
    return "hero-gradient"


def _palette_to_tokens(palette: list[str]) -> dict:
    colors = palette or ["#17324d", "#2f5f8f", "#2fb7c8", "#f5f7fa", "#111827"]
    return {
        "primary": colors[0],
        "secondary": colors[1] if len(colors) > 1 else colors[0],
        "accent": colors[2] if len(colors) > 2 else colors[0],
        "background": colors[3] if len(colors) > 3 else "#f5f7fa",
        "text": colors[4] if len(colors) > 4 else "#111827",
        "observed": colors[:12],
    }


def _default_master_contract() -> dict:
    return {
        "header": {"x": 60, "y": 36, "w": 840, "h": 76},
        "body": {"x": 60, "y": 128, "w": 840, "h": 356},
        "footer": {"x": 60, "y": 500, "w": 840, "h": 26},
        "title_anchor": {"x": 60, "y": 38, "w": 820, "h": 66, "max_lines": 2},
        "usage": "Map template placeholders into this fixed DocuMind master coordinate system.",
    }


def _infer_background_pattern(colors: Counter[str]) -> str:
    if len(colors) >= 5:
        return "theme-colored solid or layered backgrounds"
    return "solid"


def _infer_accent_usage(layout_profiles: list[dict]) -> str:
    accent_count = sum(len(layout.get("accent_shapes", [])) for layout in layout_profiles)
    if accent_count >= 6:
        return "repeating accent bars, dividers, or geometric shapes"
    return "minimal divider and color-accent usage"


def _infer_design_keywords(palette: list[str], patterns: list[str]) -> list[str]:
    keywords = ["template-based", "master-layout", "corporate"]
    if any(pattern in {"two-column", "card-grid-3", "card-grid-4"} for pattern in patterns):
        keywords.append("structured-grid")
    if len(palette) >= 4:
        keywords.append("theme-palette")
    return keywords


def _median_size(sizes: list[int]) -> int | None:
    if not sizes:
        return None
    ordered = sorted(sizes)
    return ordered[len(ordered) // 2]


def _emu_to_px(value: int | None) -> int:
    return round((value or 0) / EMU_PER_PX)


def _emu_to_pt(value: int | None) -> int:
    return round((value or 0) / 12700)
