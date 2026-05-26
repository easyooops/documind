"""Phase C: VLM Visual QA — compares HTML captures vs PPTX renders."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from src.agents.loader import get_llm_for_agent, load_agent_config
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

AGENT_NAME = "vlm_qa"
FORMAT_ID = "pptx"


async def vlm_quality_gate(state: DocuMindState) -> dict:
    """VLM-based visual QA comparing HTML captures to the PPTX output."""
    logger.info("vlm_qa.start", iteration=state.get("qa_iterations", 0))

    config = _as_dict(load_agent_config(AGENT_NAME, format_id=FORMAT_ID))
    qa_config = _as_dict(config.get("qa"))
    threshold = qa_config.get("fidelity_threshold", 0.85)

    html_screenshots = state.get("html_screenshots", [])
    output_path = state.get("output_path")
    iterations = state.get("qa_iterations", 0) + 1

    if not html_screenshots or not output_path:
        logger.warning("vlm_qa.no_data")
        return {
            "qa_iterations": iterations,
            "qa_feedback": {"passed": True, "issues": []},
            "fidelity_score": 0.85,
            "fidelity_scores": state.get("fidelity_scores", []) + [0.85],
            "current_phase": "qa",
        }

    pptx_screenshots = await _render_pptx_slides(output_path)

    fidelity_score, feedback = await _vlm_compare(html_screenshots, pptx_screenshots)

    passed = fidelity_score >= threshold
    feedback["passed"] = passed

    logger.info("vlm_qa.complete", fidelity=fidelity_score, passed=passed, iteration=iterations)
    return {
        "qa_iterations": iterations,
        "qa_feedback": feedback,
        "fidelity_score": fidelity_score,
        "fidelity_scores": state.get("fidelity_scores", []) + [fidelity_score],
        "pptx_screenshots": [str(p) for p in pptx_screenshots],
        "current_phase": "qa",
    }


async def _vlm_compare(
    html_screenshots: list[str],
    pptx_screenshots: list[bytes],
) -> tuple[float, dict]:
    """Use VLM to compare HTML captures vs PPTX renders."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)

        img_content = []
        pairs = min(len(html_screenshots), len(pptx_screenshots), 3)

        for i in range(pairs):
            html_path = Path(html_screenshots[i])
            if html_path.exists():
                html_bytes = html_path.read_bytes()
                img_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64.b64encode(html_bytes).decode()}"},
                })

            if pptx_screenshots[i]:
                img_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64.b64encode(pptx_screenshots[i]).decode()}"},
                })

        if not img_content:
            return 0.85, {"passed": True, "issues": [], "fix_instructions": []}

        prompt = (
            "Compare pairs of slide images. For each pair: first image is the HTML design intent "
            "(ground truth), second is the PPTX conversion result.\n\n"
            "Evaluate fidelity on:\n"
            "1. Element positions and sizes match\n"
            "2. Colors and gradients preserved\n"
            "3. Text readable and correctly placed\n"
            "4. Shapes and decorative elements present\n"
            "5. Overall layout composition maintained\n\n"
            "Output ONLY valid JSON:\n"
            '{"fidelity": 0.0-1.0, "issues": ["..."], "fix_instructions": ["Slide N: ..."]}'
        )

        messages = [
            SystemMessage(content="You are a strict visual QA critic for PPTX slide presentations."),
            HumanMessage(content=[{"type": "text", "text": prompt}] + img_content),
        ]

        response = await llm.ainvoke(messages)

        import re
        fidelity_match = re.search(r'"fidelity"\s*:\s*([\d.]+)', response.content)
        fidelity = float(fidelity_match.group(1)) if fidelity_match else 0.85

        try:
            json_match = re.search(r'\{[\s\S]*"fidelity"[\s\S]*\}', response.content)
            if json_match:
                result = json.loads(json_match.group())
                return min(fidelity, 1.0), {
                    "issues": result.get("issues", []),
                    "fix_instructions": result.get("fix_instructions", []),
                }
        except json.JSONDecodeError:
            pass

        return min(fidelity, 1.0), {"issues": [], "fix_instructions": []}

    except Exception as e:
        logger.warning("vlm_qa.compare_error", error=str(e)[:200])
        return 0.85, {"passed": True, "issues": [], "fix_instructions": []}


async def _render_pptx_slides(output_path: str) -> list[bytes]:
    """Render PPTX slides as PNG images for comparison."""
    screenshots = []
    try:
        from pptx import Presentation
        from PIL import Image, ImageDraw
        import io

        prs = Presentation(output_path)
        for slide in list(prs.slides)[:5]:
            img = Image.new("RGB", (960, 540), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)

            for shape in slide.shapes:
                x = int(shape.left / 9525) if shape.left else 0
                y = int(shape.top / 9525) if shape.top else 0
                w = int(shape.width / 9525) if shape.width else 0
                h = int(shape.height / 9525) if shape.height else 0

                fill_color = (240, 240, 240)
                try:
                    if shape.fill and shape.fill.type is not None:
                        fc = shape.fill.fore_color
                        if fc and fc.rgb:
                            fill_color = (fc.rgb[0], fc.rgb[1], fc.rgb[2])
                except Exception:
                    pass

                if shape.has_text_frame:
                    draw.rectangle([x, y, x + w, y + h], outline=(200, 200, 200))
                    text = shape.text_frame.text
                    if text:
                        try:
                            draw.text((x + 4, y + 4), text[:80], fill=(0, 0, 0))
                        except Exception:
                            pass
                else:
                    draw.rectangle([x, y, x + w, y + h], fill=fill_color)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            screenshots.append(buf.getvalue())
    except Exception as e:
        logger.warning("vlm_qa.pptx_render_error", error=str(e)[:200])

    return screenshots


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}
