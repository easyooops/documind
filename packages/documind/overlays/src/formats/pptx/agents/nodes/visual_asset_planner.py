"""SDK visual asset planner.

Diagram/Graphviz-style intent is converted to image-model asset attempts. If
the image model is not configured or cannot be reached, no asset is inserted
and the deck continues with native layout/content only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

METHOD_IMAGE = "image_model"
METHOD_DIAGRAMS = "diagrams_image"
LEGACY_METHOD_MERMAID = "mermaid_image"


async def visual_asset_planner(state: DocuMindState) -> dict:
    user_query = str(state.get("user_query", ""))
    slide_blueprints = [
        slide for slide in state.get("slide_blueprints", [])
        if isinstance(slide, dict)
    ]
    targets = _target_slides(user_query, slide_blueprints)
    if not targets:
        return {
            "visual_asset_plan": {
                "enabled": False,
                "reason": "No SDK image-model or diagram-converted visual intent.",
                "diagrams_renderer": "image_model_only",
                "image_model_required": False,
            },
            "visual_assets": [],
            "current_phase": "visual_asset_planning",
        }

    output_dir = Path(settings.storage_local_path) / "visual_assets"
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    skipped = 0
    for blueprint in targets:
        asset = _asset_for_blueprint(user_query, blueprint)
        rendered = await _render_asset(asset, output_dir)
        if rendered:
            assets.append(rendered)
        else:
            skipped += 1

    return {
        "visual_asset_plan": {
            "enabled": bool(assets),
            "method": METHOD_IMAGE,
            "diagrams_renderer": "image_model_only",
            "reason": (
                "SDK converted diagram/Graphviz visual intent to image-model assets."
                if assets else
                "Image-model asset generation was unavailable; continuing without image assets."
            ),
            "assets": assets,
            "skipped_assets": skipped,
        },
        "visual_assets": assets,
        "current_phase": "visual_asset_planning",
    }


def _target_slides(user_query: str, slide_blueprints: list[dict]) -> list[dict]:
    reserved = [
        slide for slide in slide_blueprints
        if _has_reserved_visual_slot(slide)
        and str(slide.get("slide_type", "")).lower() not in {"cover", "section"}
    ]
    if reserved:
        return reserved[:3]
    if not (_explicit_image_signal(user_query) or _diagram_visual_signal(user_query)):
        return []
    for slide in slide_blueprints:
        if str(slide.get("slide_type", "")).lower() not in {"cover", "section"}:
            return [slide]
    return slide_blueprints[:1]


def _has_reserved_visual_slot(blueprint: dict) -> bool:
    payload = json.dumps(blueprint, ensure_ascii=False).lower()
    return (
        '"asset_role": "visual_asset"' in payload
        or '"asset_role":"visual_asset"' in payload
        or '"visual_asset"' in payload
    )


def _explicit_image_signal(text: str) -> bool:
    lowered = text.lower()
    image_terms = (
        "image",
        "illustration",
        "visual",
        "graphic",
        "picture",
        "photo",
        "이미지",
        "그림",
        "일러스트",
        "비주얼",
    )
    negative_terms = ("without image", "no image", "이미지 없이", "그림 없이")
    return any(term in lowered for term in image_terms) and not any(
        term in lowered for term in negative_terms
    )


def _diagram_visual_signal(text: str) -> bool:
    lowered = text.lower()
    diagram_terms = (
        "diagram",
        "diagrams",
        "flowchart",
        "architecture",
        "topology",
        "network",
        "system design",
        "infra",
        "infrastructure",
        "aws",
        "azure",
        "gcp",
        "kubernetes",
        "graphviz",
        "mermaid",
        "다이어그램",
        "아키텍처",
        "구성도",
        "흐름도",
        "토폴로지",
        "인프라",
    )
    negative_terms = (
        "without diagram",
        "no diagram",
        "diagram 없이",
        "다이어그램 없이",
        "구성도 없이",
    )
    return any(term in lowered for term in diagram_terms) and not any(
        term in lowered for term in negative_terms
    )


def _asset_for_blueprint(user_query: str, blueprint: dict) -> dict:
    slide_index = int(blueprint.get("index") or 1)
    title = str(blueprint.get("title") or f"Slide {slide_index}")
    key_message = str(blueprint.get("key_message") or blueprint.get("purpose") or "")
    description = " ".join(part for part in (title, key_message, user_query) if part).strip()
    placement = _first_visual_placement(blueprint)
    return {
        "id": f"sdk_image_{slide_index}",
        "method": METHOD_IMAGE,
        "slide_index": slide_index,
        "asset_type": "image",
        "title": title,
        "description": description,
        "image_prompt": (
            "Professional presentation visual converted from diagram intent when relevant. "
            "Clean composition, no text, no logos, no UI chrome. "
            f"Subject: {description}"
        ),
        "placement": placement,
    }


def _first_visual_placement(blueprint: dict) -> dict:
    for key in ("layout_plan", "placement", "placements"):
        value = blueprint.get(key)
        found = _find_placement(value)
        if found:
            return found
    return {"left": 520, "top": 150, "width": 340, "height": 240}


def _find_placement(value: Any) -> dict | None:
    if isinstance(value, dict):
        if str(value.get("asset_role", "")).lower() == "visual_asset":
            return _normalize_placement(value)
        for child in value.values():
            found = _find_placement(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_placement(child)
            if found:
                return found
    return None


def _normalize_placement(value: dict) -> dict:
    return {
        "left": _number(value.get("left"), 520),
        "top": _number(value.get("top"), 150),
        "width": _number(value.get("width"), 340),
        "height": _number(value.get("height"), 240),
    }


def _number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


async def _render_asset(asset: dict, output_dir: Path) -> dict | None:
    asset_id = _safe_id(str(asset.get("id") or uuid.uuid4().hex[:8]))
    fingerprint = hashlib.md5(
        json.dumps(asset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    output_path = output_dir / f"{asset_id}_{fingerprint}.png"

    if output_path.exists():
        rendered = dict(asset)
        rendered.update({
            "path": str(output_path),
            "mime_type": "image/png",
            "renderer": "cache_hit",
            "cache_hit": True,
        })
        return rendered

    generated = await _render_image_model_asset(asset)
    if not generated or not generated.exists():
        logger.info(
            "visual_asset_planner.sdk_image_model_unavailable_skip_asset",
            asset_id=asset_id,
            slide=asset.get("slide_index"),
        )
        return None

    output_path.write_bytes(generated.read_bytes())
    rendered = dict(asset)
    rendered.update({
        "path": str(output_path),
        "mime_type": "image/png",
        "renderer": "image_model",
    })
    return rendered


async def _render_image_model_asset(asset: dict) -> Path | None:
    try:
        from src.utils.image_gen import generate_image

        return await generate_image(
            asset.get("image_prompt") or asset.get("description", ""),
            width=512,
            height=512,
            style="professional",
        )
    except Exception as exc:
        logger.warning("visual_asset_planner.sdk_image_model_failed", error=str(exc)[:200])
        return None


def _safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)
    return cleaned[:80] or "asset"
