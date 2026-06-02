"""Visual LLM Judge for every rendered slide in a generated PPTX."""

from __future__ import annotations

import asyncio
import base64
import json
import zipfile
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.loader import get_llm_for_agent, load_agent_config
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "vlm_qa"
FORMAT_ID = "pptx"


async def vlm_quality_gate(state: DocuMindState) -> dict:
    """Judge HTML intent against rendered PPTX output for every slide."""
    iteration = state.get("qa_iterations", 0) + 1
    config = _as_dict(load_agent_config(AGENT_NAME, format_id=FORMAT_ID))
    threshold = _as_dict(config.get("qa")).get("fidelity_threshold", 0.85)
    max_concurrent = int(
        _as_dict(config.get("parallel")).get(
            "max_concurrent",
            _as_dict(config.get("qa")).get("max_concurrent", 6),
        )
    )
    html_screenshots = state.get("html_screenshots", [])
    pptx_screenshots = state.get("pptx_screenshots", [])
    slides_html = state.get("slides_html", [])
    slide_indices = [slide.get("index", index + 1) for index, slide in enumerate(slides_html)]
    pair_count = min(len(html_screenshots), len(pptx_screenshots), len(slide_indices))
    html_by_index = {
        slide.get("index", index + 1): str(slide.get("html", ""))
        for index, slide in enumerate(slides_html)
        if isinstance(slide, dict)
    }
    slide_type_by_index = {
        slide.get("index", index + 1): _as_dict(slide.get("metadata")).get("slide_type", "content")
        for index, slide in enumerate(slides_html)
        if isinstance(slide, dict)
    }
    ooxml_by_index = _extract_slide_ooxml(state.get("output_path"), slide_indices[:pair_count])
    rule_feedback = _as_dict(state.get("rule_based_feedback"))
    rule_score = float(rule_feedback.get("score", 1.0))
    template_profile = (
        _as_dict(_as_dict(_as_dict(state.get("master_context")).get("template")).get("visual_analysis"))
        .get("profile", {})
    )
    user_reference_images = _build_user_reference_images(state)

    if pair_count == 0:
        logger.warning("vlm_qa.no_rendered_pairs")
        feedback = {
            **rule_feedback,
            "passed": bool(rule_feedback.get("passed", True)),
            "visual_judge_status": "unavailable",
            "visual_issues": [
                "No paired HTML/PPTX slide images were available for VLM evaluation."
            ],
            "evaluated_slide_count": 0,
            "total_slide_count": len(slides_html),
        }
        return {
            "qa_iterations": iteration,
            "qa_feedback": feedback,
            "fidelity_score": rule_score,
            "fidelity_scores": state.get("fidelity_scores", []) + [rule_score],
            "current_phase": "qa",
        }

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def evaluate(index: int) -> dict:
        slide_index = slide_indices[index]
        slide_user_reference_images = _user_reference_images_for_slide(
            user_reference_images,
            slide_index,
            str(slide_type_by_index.get(slide_index, "content")),
        )
        async with semaphore:
            return await _judge_slide_pair(
                slide_index,
                html_by_index.get(slide_index, ""),
                ooxml_by_index.get(slide_index, ""),
                html_screenshots[index],
                pptx_screenshots[index],
                template_profile,
                slide_user_reference_images,
            )

    per_slide = await asyncio.gather(*(evaluate(index) for index in range(pair_count)))
    successful = [result for result in per_slide if result.get("status") == "evaluated"]
    visual_score = (
        sum(float(result.get("score", 0.0)) for result in successful) / len(successful)
        if successful
        else rule_score
    )
    combined_score = round(rule_score * 0.35 + visual_score * 0.65, 4)
    visual_passed = bool(successful) and all(
        bool(result.get("passed", False)) for result in successful
    ) and visual_score >= threshold
    all_slides_evaluated = len(successful) == pair_count == len(slides_html)
    passed = bool(rule_feedback.get("passed", True)) and visual_passed and all_slides_evaluated
    visual_fixes = [
        instruction
        for result in successful
        for instruction in result.get("fix_instructions", [])
    ]
    visual_issues = [
        issue
        for result in per_slide
        for issue in result.get("issues", [])
    ]
    rule_issues = list(rule_feedback.get("issues", []))
    per_slide_issues = _merge_per_slide_issues(
        _as_dict(rule_feedback.get("per_slide_issues")),
        per_slide,
    )
    status = "complete" if all_slides_evaluated else ("partial" if successful else "failed")
    feedback = {
        "passed": passed,
        "score": combined_score,
        "rule_score": rule_score,
        "visual_score": round(visual_score, 4),
        "visual_judge_status": status,
        "issues": rule_issues + visual_issues,
        "rule_issues": rule_issues,
        "visual_issues": visual_issues,
        "per_slide_issues": per_slide_issues,
        "issues_count": (
            int(rule_feedback.get("issues_count", len(rule_issues)))
            + len(visual_issues)
        ),
        "fix_instructions": list(rule_feedback.get("fix_instructions", [])) + visual_fixes,
        "category_scores": rule_feedback.get("category_scores", {}),
        "per_slide_scores": rule_feedback.get("per_slide_scores", []),
        "visual_per_slide": per_slide,
        "parallel_evaluation": True,
        "evaluation_inputs": {
            "per_slide_html": True,
            "per_slide_ooxml": bool(ooxml_by_index),
            "paired_images": True,
            "user_reference_images": bool(user_reference_images),
            "max_concurrent": max(1, max_concurrent),
        },
        "evaluated_slide_indices": [
            result["index"] for result in successful
        ],
        "evaluated_slide_count": len(successful),
        "total_slide_count": len(slides_html),
        "pptx_renderer": _as_dict(state.get("pptx_render_info")).get("renderer"),
    }

    logger.info(
        "vlm_qa.complete",
        score=combined_score,
        visual_score=round(visual_score, 4),
        passed=passed,
        evaluated=len(successful),
        total=len(slides_html),
        iteration=iteration,
    )
    return {
        "qa_iterations": iteration,
        "qa_feedback": feedback,
        "fidelity_score": combined_score,
        "fidelity_scores": state.get("fidelity_scores", []) + [combined_score],
        "current_phase": "qa",
    }


async def _judge_slide_pair(
    slide_index: int,
    html_code: str,
    ooxml_code: str,
    html_path: str,
    pptx_path: str,
    template_profile: dict,
    user_reference_images: list[dict] | None = None,
) -> dict:
    """Evaluate a single slide's code and HTML/PPTX raster pair with a visual-capable model."""
    try:
        html_bytes = Path(html_path).read_bytes()
        pptx_bytes = Path(pptx_path).read_bytes()
        prompt = (
            f"Evaluate slide {slide_index}. You have the generated HTML code, the converted "
            "PowerPoint OOXML slide XML, then image 1 as the intended HTML render and image 2 "
            "as the rendered PPTX result. Judge both code-level conversion quality and final "
            "visible quality: "
            "clipping/overflow, missing elements, alignment, typography readability, contrast, "
            "spacing balance, and consistency with the template profile when supplied. "
            "Inspect whether the HTML intent is represented in the OOXML and whether the two "
            "images confirm the conversion. Do not complain about content semantics unless text "
            "is lost or unreadable. If user-attached reference images are provided after the "
            "HTML/PPTX images, also check whether the generated slide respects relevant visual "
            "or content evidence from those references.\n\n"
            "Template visual profile: "
            f"{json.dumps(template_profile, ensure_ascii=False)[:2500]}\n\n"
            "Generated slide HTML:\n"
            f"{_truncate_code(html_code, 12000)}\n\n"
            "Converted slide OOXML:\n"
            f"{_truncate_code(ooxml_code, 12000)}\n\n"
            + (
                "The PPTX preserves the uploaded native OOXML master/layout background. "
                "Treat the PPTX master styling and template profile as authoritative; do not "
                "penalize a synthetic HTML background that differs from preserved template "
                "chrome. Evaluate the generated foreground content within that template.\n\n"
                if template_profile else ""
            )
            + "Return ONLY JSON: "
            '{"score": 0.0, "passed": false, "issues": ["Slide N: ..."], '
            '"fix_instructions": ["Slide N: ..."], "observations": ["..."]}'
        )
        message_content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64.b64encode(html_bytes).decode('ascii')}"
                },
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64.b64encode(pptx_bytes).decode('ascii')}"
                },
            },
        ]
        for reference in user_reference_images or []:
            data_url = _reference_image_data_url(reference)
            if not data_url:
                continue
            message_content.append({
                "type": "text",
                "text": str(reference.get("label", "User attached reference image")),
            })
            message_content.append({
                "type": "image_url",
                "image_url": {"url": data_url},
            })
        llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
        response = await llm.ainvoke([
            SystemMessage(content="You are a strict slide-by-slide visual presentation QA judge."),
            HumanMessage(content=message_content),
        ])
        result = parse_llm_json(response.content)
        if not isinstance(result, dict):
            raise ValueError("VLM result was not an object")
        score = max(0.0, min(1.0, float(result.get("score", 0.0))))
        return {
            "index": slide_index,
            "status": "evaluated",
            "score": score,
            "passed": bool(result.get("passed", score >= 0.85)),
            "issues": _strings(result.get("issues")),
            "fix_instructions": _strings(result.get("fix_instructions")),
            "observations": _strings(result.get("observations")),
        }
    except Exception as exc:
        logger.warning("vlm_qa.slide_failed", slide=slide_index, error=str(exc)[:200])
        return {
            "index": slide_index,
            "status": "failed",
            "score": 0.0,
            "passed": False,
            "issues": [f"Slide {slide_index}: visual/code Judge evaluation failed."],
            "fix_instructions": [],
        }


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _build_user_reference_images(state: DocuMindState) -> list[dict]:
    references = []
    for index, image in enumerate(state.get("_image_attachments", []) or [], start=1):
        if not isinstance(image, dict):
            continue
        record = {
            "label": str(
                image.get("description")
                or image.get("filename")
                or f"User attached reference image {index}"
            ),
            "mime_type": image.get("mime_type") or "image/png",
            "source": "user",
        }
        target_slide = _reference_target_slide_index(record["label"])
        if target_slide is not None:
            record["target_slide_index"] = target_slide
        if image.get("content"):
            record["content"] = image.get("content")
        elif image.get("path"):
            record["path"] = image.get("path")
        elif image.get("file_path"):
            record["file_path"] = image.get("file_path")
        if record.get("content") or record.get("path") or record.get("file_path"):
            references.append(record)
    return references[:4]


def _reference_target_slide_index(label: object) -> int | None:
    import re

    match = re.search(r"(?:slide|슬라이드|s)\s*#?\s*(\d+)", str(label or ""), re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _user_reference_images_for_slide(
    references: list[dict],
    slide_index: object,
    slide_type: str,
) -> list[dict]:
    try:
        normalized_index = int(slide_index)
    except (TypeError, ValueError):
        normalized_index = -1
    targeted = [
        reference for reference in references
        if reference.get("target_slide_index") == normalized_index
    ]
    if targeted:
        return targeted
    if slide_type in {"cover", "section"}:
        return references
    return []


def _reference_image_data_url(image: dict) -> str:
    raw_content = image.get("content")
    mime_type = str(image.get("mime_type") or "image/png")
    if isinstance(raw_content, str):
        try:
            raw_content = raw_content.encode("latin1")
        except UnicodeEncodeError:
            raw_content = None
    if isinstance(raw_content, bytes):
        return f"data:{mime_type};base64,{base64.b64encode(raw_content).decode('ascii')}"

    path = Path(str(image.get("path") or image.get("file_path") or ""))
    if not path.exists() or not path.is_file():
        try:
            from src.core.config import settings

            storage_path = Path(settings.storage_local_path) / str(image.get("file_path") or "")
        except Exception:
            storage_path = Path()
        if storage_path.exists() and storage_path.is_file():
            path = storage_path
        else:
            return ""
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        mime_type = "image/jpeg"
    elif suffix == ".webp":
        mime_type = "image/webp"
    elif suffix == ".gif":
        mime_type = "image/gif"
    elif suffix == ".svg":
        mime_type = "image/svg+xml"
    else:
        mime_type = "image/png"
    try:
        return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    except OSError:
        return ""


def _extract_slide_ooxml(output_path: object, slide_indices: list[object]) -> dict[object, str]:
    path = Path(str(output_path or ""))
    if not path.exists() or not path.is_file():
        return {}
    result: dict[object, str] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for position, slide_index in enumerate(slide_indices, start=1):
                member = f"ppt/slides/slide{position}.xml"
                if member not in names:
                    continue
                result[slide_index] = archive.read(member).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile):
        return {}
    return result


def _truncate_code(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    half = max(1, limit // 2)
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _merge_per_slide_issues(rule_issues: dict, visual_results: list[dict]) -> dict[int, list[str]]:
    merged: dict[int, list[str]] = {}
    for key, value in rule_issues.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, list):
            merged[idx] = [str(item) for item in value if str(item).strip()]
    for result in visual_results:
        try:
            idx = int(result.get("index"))
        except (TypeError, ValueError):
            continue
        for issue in result.get("issues", []):
            text = str(issue).strip()
            if text:
                merged.setdefault(idx, []).append(text)
    return merged
