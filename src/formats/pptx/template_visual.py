"""Visual interpretation of uploaded presentation templates."""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent
from src.core.config import settings
from src.core.logging import get_logger
from src.formats.pptx.visual_renderer import render_pptx_images
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)


async def analyze_template_visuals(
    template_bytes: bytes,
    filename: str,
    ooxml_analysis: dict,
) -> dict:
    """Render representative slides and obtain a VLM design profile."""
    scratch_root = Path(settings.storage_local_path) / "template-analysis"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="documind_template_visual_", dir=str(scratch_root)
    ) as output_name:
        rendered = await render_pptx_images(
            template_bytes,
            Path(output_name),
            prefix="template",
            max_slides=6,
        )
        paths = rendered.get("paths", [])
        if not paths:
            return {
                "status": "unavailable",
                "renderer": rendered.get("renderer"),
                "slide_count": 0,
                "summary": "Template slides could not be rasterized for visual analysis.",
            }

        content: list[dict] = [{
            "type": "text",
            "text": (
                "Analyze these rendered slides from an uploaded PowerPoint template. "
                "Infer the visual design system that new slides must reproduce, beyond raw OOXML. "
                "Describe the cover treatment separately from content slides, including "
                "composition, "
                "background treatment, color relationships, typography hierarchy, header/footer, "
                "spacing rhythm, card/chart treatment, imagery style, and what must not be "
                "introduced. "
                "Use the OOXML evidence below only as supporting metadata.\n\n"
                f"Filename: {filename}\n"
                "OOXML theme evidence: "
                f"{json.dumps(ooxml_analysis.get('theme', {}), ensure_ascii=False)}\n\n"
                "Return ONLY JSON with keys: summary, style_keywords, dominant_colors, "
                "cover_style, "
                "body_style, typography, chrome, spacing, component_style, forbidden_deviations, "
                "planning_guidance."
            ),
        }]
        for path in paths:
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            })

        try:
            llm = get_llm_for_agent("vlm_qa", format_id="pptx")
            response = await llm.ainvoke([
                SystemMessage(
                    content=(
                        "You are a presentation template art director extracting a reusable "
                        "style guide."
                    )
                ),
                HumanMessage(content=content),
            ])
            result = parse_llm_json(response.content)
            profile = result if isinstance(result, dict) else {"summary": str(result)}
            return {
                "status": "analyzed",
                "renderer": rendered.get("renderer"),
                "true_render": rendered.get("true_render", False),
                "slide_count": len(paths),
                "profile": profile,
            }
        except Exception as exc:
            logger.warning("template_visual.analysis_failed", error=str(exc)[:200])
            return {
                "status": "failed",
                "renderer": rendered.get("renderer"),
                "true_render": rendered.get("true_render", False),
                "slide_count": len(paths),
                "summary": "Template images were rendered but visual model analysis failed.",
            }
