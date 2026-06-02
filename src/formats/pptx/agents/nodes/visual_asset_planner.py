"""Optional visual asset sub-agent for PPTX slide image elements.

The node classifies whether the deck needs diagram/image assets, chooses one of
the supported rendering methods, materializes PNG files, and stores them in
state for the HTML generator to insert.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import importlib
import json
import math
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image, ImageDraw, ImageFont

from src.agents.loader import get_llm_for_agent, load_agent_config, load_agent_prompt
from src.core.config import settings
from src.core.logging import get_logger
from src.schemas.agents import DocuMindState
from src.utils.json_repair import parse_llm_json

logger = get_logger(__name__)

AGENT_NAME = "visual_asset_planner"
FORMAT_ID = "pptx"

METHOD_IMAGE = "image_model"
METHOD_DIAGRAMS = "diagrams_image"
LEGACY_METHOD_MERMAID = "mermaid_image"
ALLOWED_METHODS = {METHOD_IMAGE, METHOD_DIAGRAMS}
MAX_DIAGRAMS_NODES = 24
MAX_DIAGRAMS_EDGES = 36
MAX_DIAGRAMS_CLUSTERS = 10
MAX_DIAGRAMS_CLUSTER_DEPTH = 6
DIAGRAMS_RENDER_SCALE = 2
MIN_DIAGRAM_PNG_WIDTH = 2400
MIN_DIAGRAM_PNG_HEIGHT = 1500
DIAGRAMS_DPI = 300


async def visual_asset_planner(state: DocuMindState) -> dict:
    """Classify requested slide visual assets and render them before HTML generation."""
    user_query = state.get("user_query", "")
    slide_blueprints = state.get("slide_blueprints", [])

    if _negative_visual_asset_signal(user_query):
        logger.info("visual_asset_planner.skip_negative_signal")
        return {
            "visual_asset_plan": {
                "enabled": False,
                "reason": "User asked to replace architecture/diagram content with ordinary content.",
            },
            "visual_assets": [],
            "current_phase": "visual_asset_planning",
        }

    quick_signal = _quick_visual_signal(user_query, slide_blueprints)
    logger.info(
        "visual_asset_planner.signal",
        quick_signal=quick_signal,
        reserved_slots=_reserved_visual_slot_count(slide_blueprints),
        slide_count=len(slide_blueprints),
    )
    if not quick_signal:
        logger.info("visual_asset_planner.skip_no_signal")
        return {
            "visual_asset_plan": {"enabled": False, "reason": "No visual asset intent."},
            "visual_assets": [],
            "current_phase": "visual_asset_planning",
        }

    logger.info("visual_asset_planner.start")
    plan = await _llm_plan(user_query, slide_blueprints, state)
    normalized = _normalize_plan(
        plan,
        user_query,
        slide_blueprints,
        state.get("slide_revision_instructions", {}),
    )

    if not normalized.get("enabled"):
        logger.warning(
            "visual_asset_planner.llm_plan_required_no_fallback",
            reason=normalized.get("reason", ""),
        )
        return {
            "visual_asset_plan": normalized,
            "visual_assets": [],
            "current_phase": "visual_asset_planning",
        }

    missing_reserved_slides = _missing_reserved_visual_asset_slides(
        normalized,
        slide_blueprints,
        state.get("slide_revision_instructions", {}),
    )
    if missing_reserved_slides:
        logger.warning(
            "visual_asset_planner.llm_missing_reserved_slots_no_fallback",
            missing_slides=missing_reserved_slides,
        )

    output_dir = Path(settings.storage_local_path) / "visual-assets"
    output_dir.mkdir(parents=True, exist_ok=True)

    assets = []
    for asset in normalized.get("assets", []):
        rendered = await _render_asset(asset, output_dir)
        if rendered:
            assets.append(rendered)
        else:
            logger.warning(
                "visual_asset_planner.render_missing",
                slide=asset.get("slide_index"),
                asset_id=asset.get("id"),
                method=asset.get("method"),
            )

    logger.info("visual_asset_planner.complete", assets=len(assets))
    return {
        "visual_asset_plan": normalized,
        "visual_assets": assets,
        "current_phase": "visual_asset_planning",
    }


async def _llm_plan(user_query: str, slide_blueprints: list[dict], state: DocuMindState) -> dict:
    llm = get_llm_for_agent(AGENT_NAME, format_id=FORMAT_ID)
    config = load_agent_config(AGENT_NAME, format_id=FORMAT_ID)
    retry_config = config.get("retry", {})
    max_attempts = max(1, int(retry_config.get("max_attempts", 2) or 2))
    backoff_seconds = max(0, float(retry_config.get("backoff_seconds", 0) or 0))
    system_prompt = load_agent_prompt(AGENT_NAME, format_id=FORMAT_ID)
    if not system_prompt:
        system_prompt = "Classify slide visual asset needs. Return only JSON."

    compact_blueprints = [
        {
            "index": bp.get("index"),
            "slide_type": bp.get("slide_type"),
            "title": bp.get("title"),
            "purpose": bp.get("purpose"),
            "key_message": bp.get("key_message"),
            "content_blocks": bp.get("content_blocks", []),
            "content_elements": bp.get("content_elements", []),
            "data_points": bp.get("data_points", []),
            "suggested_elements": bp.get("suggested_elements", []),
            "layout_plan": bp.get("layout_plan", {}),
            "visual_asset_slots": _visual_asset_slots_for_blueprint(bp),
        }
        for bp in slide_blueprints
        if isinstance(bp, dict)
    ]
    context = {
        "user_query": user_query,
        "slides": compact_blueprints,
        "required_visual_asset_slots": _reserved_visual_slot_records(slide_blueprints),
        "fallback_policy": (
            "disabled: all diagrams_image assets must be planned by this LLM response "
            "with slide-specific diagrams_nodes and diagrams_edges."
        ),
        "presentation_strategy": state.get("presentation_strategy", {}),
    }

    last_plan: dict | None = None
    last_issues: list[str] = []
    for attempt in range(1, max_attempts + 1):
        request_context = dict(context)
        if last_issues:
            request_context["repair_required"] = {
                "attempt": attempt,
                "issues": last_issues,
                "instruction": (
                    "Return a complete corrected JSON object only. Do not summarize. "
                    "Every diagrams_image asset must include method, slide_index, "
                    "description, diagrams_nodes, diagrams_edges, and placement when a "
                    "reserved slot exists. Do not reuse the same topology across slides."
                ),
            }
        try:
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=json.dumps(request_context, ensure_ascii=False, indent=2)),
            ])
            raw_content = str(response.content or "")
            result = parse_llm_json(raw_content)
            if not isinstance(result, dict):
                last_plan = None
                last_issues = [f"Parsed JSON was {type(result).__name__}, expected object."]
            else:
                last_plan = result
                last_issues = _llm_plan_validation_issues(
                    result,
                    user_query,
                    slide_blueprints,
                    state.get("slide_revision_instructions", {}),
                )
        except Exception as exc:
            raw_text = str(locals().get("raw_content", ""))
            last_plan = None
            last_issues = [
                (
                    f"JSON parse failed: {type(exc).__name__}. "
                    f"response_chars={len(raw_text)} "
                    f"likely_truncated={_looks_like_truncated_json(raw_text)}"
                )
            ]

        if not last_issues:
            logger.info("visual_asset_planner.llm_plan_validated", attempt=attempt)
            return last_plan or {}

        logger.warning(
            "visual_asset_planner.llm_plan_invalid_retry",
            attempt=attempt,
            max_attempts=max_attempts,
            issues=last_issues[:6],
        )
        if attempt < max_attempts and backoff_seconds:
            await asyncio.sleep(backoff_seconds)

    logger.warning(
        "visual_asset_planner.llm_plan_failed_validation",
        attempts=max_attempts,
        issues=last_issues[:8],
    )
    return {
        "enabled": False,
        "reason": "LLM visual asset plan failed validation after retries: " + "; ".join(last_issues[:4]),
        "assets": [],
    }


def _llm_plan_validation_issues(
    plan: dict,
    user_query: str,
    slide_blueprints: list[dict],
    slide_revision_instructions: dict | None = None,
) -> list[str]:
    issues: list[str] = []
    if not isinstance(plan.get("assets"), list):
        return ["Missing assets array."]

    raw_assets = [asset for asset in plan.get("assets", []) if isinstance(asset, dict)]
    if not raw_assets:
        return ["No asset objects returned despite visual asset signal."]

    for index, asset in enumerate(raw_assets, start=1):
        method = str(asset.get("method") or "").strip()
        if method == LEGACY_METHOD_MERMAID:
            method = METHOD_DIAGRAMS
        slide_index = asset.get("slide_index", "?")
        if method not in ALLOWED_METHODS:
            issues.append(f"Asset {index} slide {slide_index}: invalid or missing method.")
            continue
        if method == METHOD_DIAGRAMS:
            nodes = _normalize_diagrams_nodes(asset.get("diagrams_nodes"))
            edges = _normalize_diagrams_edges(asset.get("diagrams_edges"))
            if not nodes:
                issues.append(f"Asset {index} slide {slide_index}: missing diagrams_nodes.")
            if not edges:
                issues.append(f"Asset {index} slide {slide_index}: missing diagrams_edges.")

    normalized = _normalize_plan(plan, user_query, slide_blueprints, slide_revision_instructions)
    if not normalized.get("enabled"):
        issues.append("No valid normalized assets remained after strict validation.")

    missing_slides = _missing_reserved_visual_asset_slides(
        normalized,
        slide_blueprints,
        slide_revision_instructions,
    )
    if missing_slides:
        issues.append(
            "Reserved visual slots missing LLM assets for slides: "
            + ", ".join(str(slide) for slide in missing_slides)
        )

    duplicate_slides = _duplicate_diagram_topology_slides(normalized)
    if duplicate_slides:
        issues.append(
            "Duplicate diagrams topology reused across slides: "
            + ", ".join(str(slide) for slide in duplicate_slides)
        )
    return issues


def _duplicate_diagram_topology_slides(plan: dict) -> list[int]:
    seen: dict[str, int] = {}
    duplicates: list[int] = []
    for asset in plan.get("assets", []):
        if not isinstance(asset, dict) or asset.get("method") != METHOD_DIAGRAMS:
            continue
        try:
            slide_index = int(asset.get("slide_index", 0))
        except (TypeError, ValueError):
            continue
        signature = json.dumps(
            {
                "nodes": [
                    {
                        "label": node.get("label"),
                        "provider": node.get("provider"),
                        "service": node.get("service"),
                    }
                    for node in asset.get("diagrams_nodes", [])
                    if isinstance(node, dict)
                ],
                "edges": [
                    {
                        "from": edge.get("from"),
                        "to": edge.get("to"),
                        "label": edge.get("label", ""),
                    }
                    for edge in asset.get("diagrams_edges", [])
                    if isinstance(edge, dict)
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if not signature or signature == '{"edges": [], "nodes": []}':
            continue
        if signature in seen and seen[signature] != slide_index:
            duplicates.extend([seen[signature], slide_index])
        else:
            seen[signature] = slide_index
    return sorted(set(duplicates))


def _looks_like_truncated_json(raw: str) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    return (
        text.count("{") > text.count("}")
        or text.count("[") > text.count("]")
        or text.endswith((",", ":", "[", "{"))
    )


def _visual_asset_slots_for_blueprint(blueprint: dict) -> list[dict]:
    slots = []
    placements = blueprint.get("layout_plan", {}).get("element_placements", [])
    if not isinstance(placements, list):
        return slots
    for placement in placements:
        if not isinstance(placement, dict):
            continue
        if not _is_visual_asset_placement(placement):
            continue
        normalized = _normalize_placement(placement)
        if not normalized:
            continue
        slots.append({
            "id": str(placement.get("id") or f"visual_asset_slot_{len(slots) + 1}"),
            "asset_role": str(placement.get("asset_role") or "visual_asset"),
            "element": str(placement.get("element") or "image"),
            "placement": normalized,
            "slot_constraints": _slot_constraints(normalized),
        })
    return slots


def _reserved_visual_slot_records(slide_blueprints: list[dict]) -> list[dict]:
    records = []
    for blueprint in slide_blueprints:
        if not isinstance(blueprint, dict):
            continue
        slots = _visual_asset_slots_for_blueprint(blueprint)
        if not slots:
            continue
        records.append({
            "slide_index": blueprint.get("index"),
            "slide_type": blueprint.get("slide_type"),
            "title": blueprint.get("title"),
            "purpose": blueprint.get("purpose"),
            "key_message": blueprint.get("key_message"),
            "content_blocks": blueprint.get("content_blocks", []),
            "content_elements": blueprint.get("content_elements", []),
            "data_points": blueprint.get("data_points", []),
            "slots": slots,
        })
    return records


def _slot_constraints(placement: dict | None) -> dict:
    profile = _slot_render_profile(placement, 0)
    width = _num((placement or {}).get("w"), 520)
    height = _num((placement or {}).get("h"), 330)
    aspect = width / max(height, 1)
    return {
        "slot_width_px": round(width, 2),
        "slot_height_px": round(height, 2),
        "slot_aspect_ratio": round(aspect, 3),
        "recommended_direction": profile["recommended_direction"],
        "max_recommended_nodes": profile["max_recommended_nodes"],
        "layout_guidance": profile["layout_guidance"],
    }


def _slot_render_profile(placement: Any, node_count: int = 0) -> dict:
    placement = placement if isinstance(placement, dict) else {}
    width = max(1.0, _num(placement.get("w"), 520))
    height = max(1.0, _num(placement.get("h"), 330))
    aspect = width / height
    if aspect < 0.85:
        recommended_direction = "TB"
        max_nodes = 5
        ranksep = "0.45"
        nodesep = "0.25"
        pad = "0.05"
        node_fontsize = "10"
        edge_fontsize = "8"
        guidance = "Tall/narrow slot: use a compact vertical stack, avoid clusters, keep labels short."
    elif aspect > 1.45:
        recommended_direction = "LR"
        max_nodes = 8
        ranksep = "0.55"
        nodesep = "0.35"
        pad = "0.08"
        node_fontsize = "11"
        edge_fontsize = "9"
        guidance = "Wide slot: use a left-to-right flow with at most two rows and concise labels."
    else:
        recommended_direction = "LR" if node_count > 4 else "TB"
        max_nodes = 6
        ranksep = "0.5"
        nodesep = "0.3"
        pad = "0.08"
        node_fontsize = "11"
        edge_fontsize = "9"
        guidance = "Balanced slot: keep topology compact and avoid deep nesting."

    graph_width = max(1.0, min(10.0, width / 96))
    graph_height = max(1.0, min(7.5, height / 96))
    return {
        "aspect": aspect,
        "recommended_direction": recommended_direction,
        "max_recommended_nodes": max_nodes,
        "layout_guidance": guidance,
        "graph_size": f"{graph_width:.2f},{graph_height:.2f}!",
        "ranksep": ranksep,
        "nodesep": nodesep,
        "pad": pad,
        "node_fontsize": node_fontsize,
        "edge_fontsize": edge_fontsize,
    }


def _slot_aware_diagrams_direction(value: Any, placement: dict | None, node_count: int) -> str:
    direction = _normalize_diagrams_direction(value)
    profile = _slot_render_profile(placement, node_count)
    aspect = profile["aspect"]
    if aspect < 0.85 and direction in {"LR", "RL"}:
        return "TB"
    if aspect > 1.45 and direction in {"TB", "BT"} and node_count > 3:
        return "LR"
    return direction


def _missing_reserved_visual_asset_slides(
    normalized: dict,
    slide_blueprints: list[dict],
    slide_revision_instructions: dict | None = None,
) -> list[int]:
    requested = _normalize_slide_instruction_map(slide_revision_instructions)
    reserved = set()
    for record in _reserved_visual_slot_records(slide_blueprints):
        try:
            slide_index = int(record.get("slide_index", 0))
        except (TypeError, ValueError):
            continue
        if requested and slide_index not in requested:
            continue
        reserved.add(slide_index)
    planned = {
        int(asset.get("slide_index", 0))
        for asset in normalized.get("assets", [])
        if isinstance(asset, dict) and str(asset.get("slide_index", "")).isdigit()
    }
    return sorted(reserved - planned)


def _normalize_plan(
    plan: dict,
    user_query: str,
    slide_blueprints: list[dict],
    slide_revision_instructions: dict | None = None,
) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("assets"), list):
        return {
            "enabled": False,
            "reason": "LLM did not return a valid visual asset plan; fallback is disabled.",
            "assets": [],
        }

    assets = []
    slide_revision_instructions = _normalize_slide_instruction_map(slide_revision_instructions)
    valid_indices = {
        int(bp.get("index", 0))
        for bp in slide_blueprints
        if isinstance(bp, dict) and str(bp.get("index", "")).isdigit()
    }
    for raw in plan.get("assets", []):
        if not isinstance(raw, dict):
            continue
        method = str(raw.get("method", "")).strip()
        if method == LEGACY_METHOD_MERMAID:
            method = METHOD_DIAGRAMS
        if method not in ALLOWED_METHODS:
            logger.warning(
                "visual_asset_planner.llm_asset_invalid_method",
                method=method,
                slide=raw.get("slide_index"),
            )
            continue
        description = str(raw.get("description") or raw.get("title") or user_query).strip()
        slide_index = _infer_visual_asset_slide_index(
            raw,
            description,
            user_query,
            valid_indices,
            slide_revision_instructions,
            method,
        )
        if slide_index is None:
            slide_index = _default_slide_index(slide_blueprints)
        if not description:
            continue
        placement = _asset_slot_placement(slide_index, slide_blueprints) or _normalize_placement(
            raw.get("placement")
        )
        diagrams_nodes = _normalize_diagrams_nodes(raw.get("diagrams_nodes"))
        diagrams_edges = _normalize_diagrams_edges(raw.get("diagrams_edges"))
        if method == METHOD_DIAGRAMS and (not diagrams_nodes or not diagrams_edges):
            logger.warning(
                "visual_asset_planner.llm_asset_missing_topology",
                slide=slide_index,
                title=str(raw.get("title") or "")[:80],
                has_nodes=bool(diagrams_nodes),
                has_edges=bool(diagrams_edges),
            )
            continue

        assets.append({
            "id": raw.get("id") or f"asset_{slide_index}_{len(assets) + 1}",
            "slide_index": slide_index,
            "asset_type": str(raw.get("asset_type") or "diagram"),
            "method": method,
            "title": str(raw.get("title") or "Visual asset")[:80],
            "description": description[:800],
            "diagrams_provider": _normalize_diagrams_provider(
                raw.get("diagrams_provider") or raw.get("provider"),
                user_query,
                raw,
            ),
            "diagrams_direction": _slot_aware_diagrams_direction(
                raw.get("diagrams_direction"),
                placement,
                len(diagrams_nodes),
            ),
            "diagrams_clusters": _normalize_diagrams_clusters(raw.get("diagrams_clusters")),
            "diagrams_nodes": diagrams_nodes,
            "diagrams_edges": diagrams_edges,
            "mermaid": _clean_mermaid(str(raw.get("mermaid") or "")),
            "image_prompt": str(raw.get("image_prompt") or description)[:1200],
            "placement": placement,
            "render_profile": _slot_render_profile(placement, len(diagrams_nodes)),
        })

    return {
        "enabled": bool(plan.get("enabled", bool(assets))) and bool(assets),
        "reason": str(plan.get("reason") or "Visual assets requested."),
        "assets": assets,
    }


def _fallback_plan(
    user_query: str,
    slide_blueprints: list[dict],
    slide_revision_instructions: dict | None = None,
) -> dict:
    if not _quick_visual_signal(user_query, slide_blueprints):
        return {"enabled": False, "reason": "No visual asset intent.", "assets": []}

    method = _select_method(user_query, {})
    slide_index = _default_visual_slide_index(
        slide_blueprints,
        _normalize_slide_instruction_map(slide_revision_instructions),
    )
    description = _fallback_description(user_query, method)
    asset = {
        "id": f"asset_{slide_index}_1",
        "slide_index": slide_index,
        "asset_type": "architecture" if method == METHOD_DIAGRAMS else "diagram",
        "method": method,
        "title": "Architecture Diagram" if method == METHOD_DIAGRAMS else "Visual Diagram",
        "description": description,
        "diagrams_provider": _normalize_diagrams_provider(None, user_query, {}),
        "diagrams_direction": "LR",
        "diagrams_clusters": _fallback_diagrams_clusters(user_query, method),
        "diagrams_nodes": _fallback_diagrams_nodes(user_query, method),
        "diagrams_edges": _fallback_diagrams_edges(user_query, method),
        "mermaid": _fallback_mermaid(user_query, method),
        "image_prompt": description,
        "placement": _asset_slot_placement(slide_index, slide_blueprints)
        or {"x": 360, "y": 112, "w": 520, "h": 330},
    }
    return {
        "enabled": True,
        "reason": "Detected explicit slide visual asset intent.",
        "assets": [asset],
    }


def _fallback_visual_slot_plan(
    user_query: str,
    slide_blueprints: list[dict],
    slide_revision_instructions: dict | None = None,
) -> dict:
    """Create diagrams assets when slide planning already reserved visual slots."""
    assets = []
    slide_revision_instructions = _normalize_slide_instruction_map(slide_revision_instructions)
    for blueprint in slide_blueprints:
        if not isinstance(blueprint, dict):
            continue
        try:
            slide_index = int(blueprint.get("index", 0))
        except (TypeError, ValueError):
            continue
        placement = _asset_slot_placement(slide_index, slide_blueprints)
        if not placement:
            continue
        if slide_revision_instructions and slide_index not in slide_revision_instructions:
            continue
        description = _fallback_description(
            " ".join([
                str(blueprint.get("title") or ""),
                str(blueprint.get("key_message") or ""),
                user_query,
            ]),
            METHOD_DIAGRAMS,
        )
        assets.append({
            "id": f"asset_{slide_index}_{len(assets) + 1}",
            "slide_index": slide_index,
            "asset_type": "architecture",
            "method": METHOD_DIAGRAMS,
            "title": str(blueprint.get("title") or "Architecture Diagram")[:80],
            "description": description,
            "diagrams_provider": _normalize_diagrams_provider(None, description, blueprint),
            "diagrams_direction": "LR",
            "diagrams_clusters": _fallback_diagrams_clusters(description, METHOD_DIAGRAMS),
            "diagrams_nodes": _fallback_diagrams_nodes(description, METHOD_DIAGRAMS),
            "diagrams_edges": _fallback_diagrams_edges(description, METHOD_DIAGRAMS),
            "mermaid": _fallback_mermaid(description, METHOD_DIAGRAMS),
            "image_prompt": description,
            "placement": placement,
        })
    return {
        "enabled": bool(assets),
        "reason": "Slide blueprint reserved rendered diagram/image slots.",
        "assets": assets,
    }


def _reserved_visual_slot_count(slide_blueprints: list[dict]) -> int:
    count = 0
    for blueprint in slide_blueprints:
        if not isinstance(blueprint, dict):
            continue
        placements = blueprint.get("layout_plan", {}).get("element_placements", [])
        if not isinstance(placements, list):
            continue
        for placement in placements:
            if not isinstance(placement, dict):
                continue
            if _is_visual_asset_placement(placement):
                count += 1
    return count


def _is_visual_asset_placement(placement: dict) -> bool:
    return (
        str(placement.get("asset_role") or "") == "visual_asset"
        or str(placement.get("element") or "").lower() in {"diagram", "image"}
    )


def _merge_missing_reserved_slot_assets(
    normalized: dict,
    user_query: str,
    slide_blueprints: list[dict],
    slide_revision_instructions: dict | None = None,
) -> dict:
    """Ensure every planner-reserved visual slot receives a rendered asset."""
    if not normalized.get("enabled"):
        return normalized
    slot_plan = _fallback_visual_slot_plan(
        user_query,
        slide_blueprints,
        slide_revision_instructions,
    )
    if not slot_plan.get("enabled"):
        return normalized

    assets = [asset for asset in normalized.get("assets", []) if isinstance(asset, dict)]
    existing_slides = {
        int(asset.get("slide_index", 0))
        for asset in assets
        if str(asset.get("slide_index", "")).isdigit()
    }
    missing_assets = [
        asset
        for asset in slot_plan.get("assets", [])
        if int(asset.get("slide_index", 0)) not in existing_slides
    ]
    if not missing_assets:
        return normalized

    logger.info(
        "visual_asset_planner.add_missing_slot_assets",
        existing_assets=len(assets),
        added_assets=len(missing_assets),
        missing_slides=[asset.get("slide_index") for asset in missing_assets],
    )
    merged = dict(normalized)
    merged["assets"] = assets + missing_assets
    merged["enabled"] = True
    merged["reason"] = (
        str(normalized.get("reason") or "Visual assets requested.")
        + " Added fallback assets for reserved visual slots."
    )
    return merged


async def _render_asset(asset: dict, output_dir: Path) -> dict | None:
    method = asset.get("method")
    asset_id = _safe_id(str(asset.get("id") or uuid.uuid4().hex[:8]))
    fingerprint = hashlib.md5(
        json.dumps(asset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    output_path = output_dir / f"{asset_id}_{fingerprint}.png"

    if method == METHOD_DIAGRAMS and not _has_explicit_diagrams_topology(asset):
        logger.warning(
            "visual_asset_planner.render_skip_missing_llm_topology",
            asset_id=asset_id,
            slide=asset.get("slide_index"),
        )
        return None

    render_metadata: dict[str, Any] = {}
    if output_path.exists() and _valid_png(output_path):
        render_metadata = {"renderer": "cache_hit", "cache_hit": True}
        logger.info(
            "visual_asset_planner.render_cache_hit",
            asset_id=asset_id,
            slide=asset.get("slide_index"),
            method=method,
            output=str(output_path),
        )
    else:
        logger.info(
            "visual_asset_planner.render_start",
            asset_id=asset_id,
            slide=asset.get("slide_index"),
            method=method,
            output=str(output_path),
        )
        if method == METHOD_IMAGE:
            generated = await _render_image_model_asset(asset)
            if generated and generated.exists():
                output_path.write_bytes(generated.read_bytes())
                render_metadata = {"renderer": "image_model"}
            else:
                _render_concept_placeholder(asset, output_path)
                render_metadata = {"renderer": "concept_placeholder"}
        elif method == METHOD_DIAGRAMS:
            try:
                render_metadata = _render_diagrams_asset(asset, output_path)
            except BaseException as exc:
                logger.warning(
                    "visual_asset_planner.diagrams_native_guarded",
                    error=str(exc)[:200],
                )
                render_metadata = {
                    "renderer": "diagrams_unavailable",
                    "renderer_reason": type(exc).__name__,
                }
            if render_metadata.get("renderer") != "diagrams" or not _valid_png(output_path):
                logger.warning(
                    "visual_asset_planner.diagrams_native_required_no_fallback",
                    asset_id=asset_id,
                    slide=asset.get("slide_index"),
                    reason=render_metadata.get("renderer_reason", ""),
                )
                return None
        else:
            _render_concept_placeholder(asset, output_path)
            render_metadata = {"renderer": "concept_placeholder"}

    if not output_path.exists():
        return None
    _ensure_minimum_png_resolution(output_path)
    if method == METHOD_DIAGRAMS:
        _fit_png_canvas_to_placement(output_path, asset.get("placement"))
        _add_diagram_image_border(output_path)

    rendered = dict(asset)
    rendered["path"] = str(output_path)
    rendered["mime_type"] = "image/png"
    if render_metadata:
        rendered.update(render_metadata)
    if method == METHOD_DIAGRAMS:
        topology_path = output_path.with_suffix(".diagrams.json")
        topology_path.write_text(
            json.dumps(_safe_diagrams_topology(asset), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rendered["diagrams_topology_path"] = str(topology_path)
    try:
        with Image.open(output_path) as image:
            width, height = image.size
    except Exception:
        width, height = 0, 0
    logger.info(
        "visual_asset_planner.render_complete",
        asset_id=asset_id,
        slide=asset.get("slide_index"),
        method=method,
        renderer=rendered.get("renderer", ""),
        path=str(output_path),
        width=width,
        height=height,
    )
    return rendered


def _has_explicit_diagrams_topology(asset: dict) -> bool:
    return bool(asset.get("diagrams_nodes")) and bool(asset.get("diagrams_edges"))


async def _render_image_model_asset(asset: dict) -> Path | None:
    try:
        from src.utils.image_gen import generate_image

        return await generate_image(
            asset.get("image_prompt", asset.get("description", "")),
            width=512,
            height=512,
            style="professional",
        )
    except Exception as exc:
        logger.warning("visual_asset_planner.image_model_failed", error=str(exc)[:200])
        return None


def _render_diagrams_asset(asset: dict, output_path: Path) -> dict:
    """Render an architecture asset with mingrammer/diagrams when available."""
    if importlib.util.find_spec("diagrams") is None:
        logger.warning("visual_asset_planner.diagrams_package_missing")
        return {"renderer": "diagrams_unavailable", "renderer_reason": "diagrams_package_missing"}
    dot_path = _find_graphviz_dot()
    if dot_path is None:
        logger.warning("visual_asset_planner.graphviz_dot_missing")
        return {"renderer": "diagrams_unavailable", "renderer_reason": "graphviz_dot_missing"}

    try:
        from diagrams import Cluster, Diagram, Edge

        topology = _safe_diagrams_topology(asset)
        node_specs = topology["nodes"]
        edge_specs = topology["edges"]
        cluster_specs = topology["clusters"]
        if not node_specs:
            return {"renderer": "diagrams_unavailable", "renderer_reason": "no_nodes"}
        nodes_by_id: dict[str, Any] = {}
        clusters_by_parent: dict[str | None, list[dict]] = {}
        for cluster in cluster_specs:
            parent = str(cluster.get("parent") or "").strip() or None
            clusters_by_parent.setdefault(parent, []).append(cluster)

        def build_nodes(cluster_id: str | None) -> None:
            for spec in node_specs:
                if (str(spec.get("cluster") or "").strip() or None) != cluster_id:
                    continue
                node_id = str(spec.get("id") or "").strip()
                if node_id and node_id not in nodes_by_id:
                    nodes_by_id[node_id] = _create_diagrams_node(spec)

        visited_clusters: set[str] = set()

        def build_clusters(
            parent_id: str | None,
            lineage: tuple[str, ...] = (),
            depth: int = 0,
        ) -> None:
            if depth > MAX_DIAGRAMS_CLUSTER_DEPTH:
                return
            for cluster in clusters_by_parent.get(parent_id, []):
                cluster_id = str(cluster.get("id") or "").strip()
                if not cluster_id or cluster_id in visited_clusters or cluster_id in lineage:
                    continue
                visited_clusters.add(cluster_id)
                with Cluster(str(cluster.get("label") or cluster_id)):
                    build_nodes(cluster_id)
                    build_clusters(cluster_id, (*lineage, cluster_id), depth + 1)

        filename = output_path.with_suffix("")
        font_name = _graphviz_font_name()
        render_profile = _slot_render_profile(asset.get("placement"), len(node_specs))
        with _temporary_graphviz_path(dot_path):
            with Diagram(
                topology["title"],
                show=False,
                filename=str(filename),
                outformat="png",
                direction=topology["direction"],
                graph_attr={
                    "bgcolor": "transparent",
                    "pad": render_profile["pad"],
                    "ranksep": render_profile["ranksep"],
                    "nodesep": render_profile["nodesep"],
                    "splines": "ortho",
                    "concentrate": "true",
                    "dpi": str(DIAGRAMS_DPI),
                    "fontname": font_name,
                    "size": render_profile["graph_size"],
                    "ratio": "compress",
                    "margin": "0",
                },
                node_attr={"fontsize": render_profile["node_fontsize"], "fontname": font_name},
                edge_attr={"fontsize": render_profile["edge_fontsize"], "fontname": font_name, "color": "#475569"},
            ):
                build_nodes(None)
                build_clusters(None)
                # Any cluster removed by cycle/depth guards still gets its nodes rendered.
                for spec in node_specs:
                    node_id = str(spec.get("id") or "").strip()
                    if node_id and node_id not in nodes_by_id:
                        nodes_by_id[node_id] = _create_diagrams_node(spec)
                for edge in edge_specs:
                    source = nodes_by_id.get(edge.get("from"))
                    target = nodes_by_id.get(edge.get("to"))
                    if not source or not target:
                        continue
                    source >> Edge(
                        label=str(edge.get("label") or ""),
                        color=str(edge.get("color") or "#475569"),
                        style=str(edge.get("style") or "solid"),
                    ) >> target

        generated = Path(f"{filename}.png")
        if generated.exists() and generated != output_path:
            output_path.write_bytes(generated.read_bytes())
        if output_path.exists() and output_path.stat().st_size > 0:
            return {
                "renderer": "diagrams",
                "renderer_package": "diagrams",
                "graphviz_dot_path": dot_path,
                "render_profile": render_profile,
            }
        return {"renderer": "diagrams_unavailable", "renderer_reason": "empty_output"}
    except RecursionError as exc:
        logger.warning(
            "visual_asset_planner.diagrams_native_recursion_guarded",
            error=str(exc)[:200],
        )
        return {"renderer": "diagrams_unavailable", "renderer_reason": "recursion_guarded"}
    except BaseException as exc:
        logger.warning(
            "visual_asset_planner.diagrams_native_failed_fallback",
            error=str(exc)[:200],
        )
        return {"renderer": "diagrams_unavailable", "renderer_reason": type(exc).__name__}


def _find_graphviz_dot() -> str | None:
    """Find Graphviz dot from PATH or the project-local portable Graphviz bundle."""
    path_dot = shutil.which("dot")
    if path_dot:
        return path_dot

    repo_root = _repo_root()
    for dot_path in (repo_root / ".tools" / "graphviz").glob("**/dot.exe"):
        if dot_path.is_file():
            return str(dot_path)
    return None


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return current.parents[5]


@contextmanager
def _temporary_graphviz_path(dot_path: str):
    dot_dir = str(Path(dot_path).parent)
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = dot_dir + os.pathsep + original_path
    try:
        yield
    finally:
        os.environ["PATH"] = original_path


def _create_diagrams_node(spec: dict) -> Any:
    try:
        node_class = _resolve_diagrams_node_class(spec)
    except BaseException:
        module = importlib.import_module("diagrams.generic.blank")
        node_class = getattr(module, "Blank")
    label = str(spec.get("label") or spec.get("id") or "Node")
    try:
        return node_class(label)
    except BaseException:
        module = importlib.import_module("diagrams.generic.blank")
        return getattr(module, "Blank")(label)


def _resolve_diagrams_node_class(spec: dict) -> Any:
    for module_name, class_name in _diagrams_node_candidates(spec):
        try:
            module = importlib.import_module(module_name)
            return getattr(module, class_name)
        except (ImportError, AttributeError):
            continue
    module = importlib.import_module("diagrams.generic.blank")
    return getattr(module, "Blank")


def _diagrams_node_candidates(spec: dict) -> list[tuple[str, str]]:
    provider = _normalize_diagrams_provider(spec.get("provider"), "", spec)
    label = str(spec.get("label") or spec.get("id") or "")
    service = _normalize_diagrams_service(
        spec.get("service") or spec.get("type") or spec.get("node_type") or label
    )
    inferred_provider = _provider_for_label(label)
    inferred_service = _diagrams_service_for_label(label, inferred_provider)
    generic_service = _diagrams_service_for_label(label, "generic")

    keys = [
        (provider, service),
        (provider, inferred_service),
        (inferred_provider, inferred_service),
        ("generic", service),
        ("generic", generic_service),
        ("generic", _generic_shape_for_label(label)),
        ("generic", "rectangle"),
    ]
    if provider == "mixed":
        keys.insert(0, (inferred_provider, inferred_service))

    seen = set()
    candidates = []
    node_map = _diagrams_node_map()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        mapped = node_map.get(key)
        if mapped:
            candidates.append(mapped)
    candidates.append(("diagrams.generic.blank", "Blank"))
    return candidates


def _render_diagrams_fallback_asset(asset: dict, output_path: Path) -> None:
    topology = _safe_diagrams_topology(asset)
    graph = {
        "nodes": {node["id"]: node["label"] for node in topology["nodes"]},
        "edges": topology["edges"],
    }
    _draw_graph_png(
        graph,
        output_path,
        title=topology["title"],
        palette={
            "background": "#FFFFFF",
            "panel": "#F8FAFC",
            "primary": "#0F172A",
            "accent": "#2563EB",
            "node": "#EFF6FF",
            "node_border": "#93C5FD",
            "text": "#0F172A",
            "muted": "#475569",
        },
        architecture_style=True,
    )


def _valid_png(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _ensure_minimum_png_resolution(path: Path) -> None:
    """Keep generated diagram assets crisp when they are scaled inside PPTX boxes."""
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width >= MIN_DIAGRAM_PNG_WIDTH and height >= MIN_DIAGRAM_PNG_HEIGHT:
                return
            scale = max(
                MIN_DIAGRAM_PNG_WIDTH / max(width, 1),
                MIN_DIAGRAM_PNG_HEIGHT / max(height, 1),
            )
            target = (max(1, int(width * scale)), max(1, int(height * scale)))
            resampling = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
            image.convert("RGBA").resize(target, resampling).save(path, format="PNG")
    except Exception as exc:
        logger.warning("visual_asset_planner.diagram_upscale_failed", error=str(exc)[:200])


def _fit_png_canvas_to_placement(path: Path, placement: Any) -> None:
    placement = placement if isinstance(placement, dict) else {}
    slot_w = _num(placement.get("w"), 0)
    slot_h = _num(placement.get("h"), 0)
    if slot_w <= 0 or slot_h <= 0:
        return
    target_aspect = slot_w / max(slot_h, 1)
    try:
        with Image.open(path) as source:
            image = source.convert("RGBA")
        width, height = image.size
        if width <= 0 or height <= 0:
            return
        current_aspect = width / height
        if abs(current_aspect - target_aspect) < 0.03:
            return
        if current_aspect > target_aspect:
            canvas_w = width
            canvas_h = max(height, int(round(width / target_aspect)))
        else:
            canvas_h = height
            canvas_w = max(width, int(round(height * target_aspect)))
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 0))
        canvas.alpha_composite(image, ((canvas_w - width) // 2, (canvas_h - height) // 2))
        canvas.save(path, format="PNG")
    except Exception as exc:
        logger.warning("visual_asset_planner.diagram_canvas_fit_failed", error=str(exc)[:200])


def _add_diagram_image_border(path: Path) -> None:
    """Bake a subtle boundary into rendered diagrams for HTML/PPTX parity."""
    try:
        with Image.open(path) as source:
            image = source.convert("RGBA")
        width, height = image.size
        if width <= 1 or height <= 1:
            return
        border_px = max(2, min(5, round(min(width, height) / 520)))
        draw = ImageDraw.Draw(image)
        color = (148, 163, 184, 170)
        for offset in range(border_px):
            draw.rectangle(
                (offset, offset, width - 1 - offset, height - 1 - offset),
                outline=color,
            )
        image.save(path, format="PNG")
    except Exception as exc:
        logger.warning("visual_asset_planner.diagram_border_failed", error=str(exc)[:200])


def _graphviz_font_name() -> str:
    """Prefer fonts that cover Korean labels before falling back to Latin-only faces."""
    font_candidates = [
        ("C:/Windows/Fonts/malgun.ttf", "Malgun Gothic"),
        ("C:/Windows/Fonts/malgunbd.ttf", "Malgun Gothic"),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK KR"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK KR"),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "NanumGothic"),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", "Apple SD Gothic Neo"),
    ]
    for path, family in font_candidates:
        if Path(path).exists():
            return family
    return "Arial"


def _render_concept_placeholder(asset: dict, output_path: Path) -> None:
    image = Image.new("RGB", (1024, 640), "#F8FAFC")
    draw = ImageDraw.Draw(image)
    title_font = _font(42, bold=True)
    body_font = _font(24)
    small_font = _font(18)

    draw.rounded_rectangle(
        (48, 48, 976, 592),
        radius=28,
        fill="#FFFFFF",
        outline="#CBD5E1",
        width=2,
    )
    draw.ellipse((96, 116, 260, 280), fill="#DBEAFE", outline="#60A5FA", width=4)
    draw.rounded_rectangle((320, 128, 880, 210), radius=16, fill="#1E293B")
    draw.text((352, 148), asset.get("title", "Generated visual"), font=title_font, fill="#FFFFFF")
    lines = _wrap_text(asset.get("description", ""), body_font, 720)
    y = 262
    for line in lines[:5]:
        draw.text((168, y), line, font=body_font, fill="#334155")
        y += 38
    draw.text((168, 520), "Image model fallback preview", font=small_font, fill="#64748B")
    image.save(output_path, format="PNG")


def _draw_graph_png(
    graph: dict,
    output_path: Path,
    *,
    title: str,
    palette: dict,
    architecture_style: bool,
) -> None:
    scale = DIAGRAMS_RENDER_SCALE
    base_width, base_height = 1024, 640
    width, height = base_width * scale, base_height * scale

    def sc(value: float) -> int:
        return int(round(value * scale))

    def spoint(point: tuple[float, float]) -> tuple[float, float]:
        return point[0] * scale, point[1] * scale

    image = Image.new("RGB", (width, height), palette["background"])
    draw = ImageDraw.Draw(image)
    title_font = _font(34 * scale, bold=True)
    label_font = _font(20 * scale, bold=True)
    edge_font = _font(15 * scale)

    draw.rounded_rectangle(
        (sc(24), sc(24), sc(1000), sc(616)),
        radius=sc(18),
        fill=palette["panel"],
        outline="#CBD5E1",
        width=sc(1),
    )
    draw.text((sc(48), sc(42)), title[:58], font=title_font, fill=palette["primary"])

    nodes = graph["nodes"]
    edges = graph["edges"]
    positions = _layout_nodes(nodes, edges, width=base_width, height=base_height)

    if architecture_style:
        tiers = _tier_bounds(positions)
        tier_names = ["Presentation", "Application", "Data"]
        for idx, bounds in enumerate(tiers):
            x1, y1, x2, y2 = bounds
            draw.rounded_rectangle(
                (sc(x1), sc(y1), sc(x2), sc(y2)),
                radius=sc(12),
                fill="#FFFFFF",
                outline="#E2E8F0",
                width=sc(2),
            )
            draw.text(
                (sc(x1 + 14), sc(y1 + 10)),
                tier_names[idx],
                font=edge_font,
                fill=palette["muted"],
            )

    for edge in edges:
        start = positions.get(edge["from"])
        end = positions.get(edge["to"])
        if not start or not end:
            continue
        _draw_arrow(
            draw,
            spoint(start),
            spoint(end),
            palette["accent"],
            width=sc(4),
            scale=scale,
        )
        if edge.get("label"):
            mx = (start[0] + end[0]) / 2
            my = (start[1] + end[1]) / 2 - 18
            draw.text((sc(mx - 30), sc(my)), edge["label"][:24], font=edge_font, fill=palette["muted"])

    for node_id, label in nodes.items():
        cx, cy = positions[node_id]
        box = (sc(cx - 92), sc(cy - 38), sc(cx + 92), sc(cy + 38))
        fill = palette["node"]
        outline = palette["node_border"]
        if architecture_style and _is_cloud_label(label):
            fill, outline = "#E0F2FE", "#38BDF8"
        elif architecture_style and _is_data_label(label):
            fill, outline = "#ECFDF5", "#34D399"
        draw.rounded_rectangle(box, radius=sc(14), fill=fill, outline=outline, width=sc(3))
        if architecture_style:
            _draw_service_glyph(draw, sc(cx - 72), sc(cy - 18), label, palette["accent"], scale=scale)
            text_x = sc(cx - 42)
            text_w = sc(122)
        else:
            text_x = sc(cx - 74)
            text_w = sc(148)
        wrapped = _wrap_text(label, label_font, text_w)
        text_y = sc(cy) - min(len(wrapped), 2) * sc(12)
        for line in wrapped[:2]:
            draw.text((text_x, text_y), line, font=label_font, fill=palette["text"])
            text_y += sc(24)

    image.save(output_path, format="PNG")


def _parse_mermaid(mermaid: str) -> dict:
    nodes: dict[str, str] = {}
    edges: list[dict] = []
    if not mermaid:
        mermaid = "graph LR\n  A[User] --> B[Service]\n  B --> C[Data]"

    for raw_line in mermaid.splitlines():
        line = raw_line.strip().rstrip(";")
        if not line or line.startswith(("graph ", "flowchart ", "subgraph", "end", "%%")):
            continue
        chain = _parse_mermaid_chain(line)
        if chain:
            for node_id, label in chain["nodes"].items():
                nodes[node_id] = label
            edges.extend(chain["edges"])
            continue
        match = re.search(
            r"([A-Za-z0-9_]+)(?:\[(.*?)\]|\((.*?)\)|\{(.*?)\})?\s*"
            r"[-=.]+(?:\|([^|]+)\|)?[->.]+\s*"
            r"([A-Za-z0-9_]+)(?:\[(.*?)\]|\((.*?)\)|\{(.*?)\})?",
            line,
        )
        if match:
            source = match.group(1)
            source_label = next((g for g in match.group(2, 3, 4) if g), source)
            label = match.group(5) or ""
            target = match.group(6)
            target_label = next((g for g in match.group(7, 8, 9) if g), target)
            nodes[source] = _clean_label(source_label)
            nodes[target] = _clean_label(target_label)
            edges.append({"from": source, "to": target, "label": _clean_label(label)})
            continue

        node_match = re.search(r"([A-Za-z0-9_]+)(?:\[(.*?)\]|\((.*?)\)|\{(.*?)\})", line)
        if node_match:
            node_id = node_match.group(1)
            node_label = next((g for g in node_match.group(2, 3, 4) if g), node_id)
            nodes[node_id] = _clean_label(node_label)

    edges = _dedupe_edges(edges)
    if not nodes:
        nodes = {"A": "User", "B": "Service", "C": "Data"}
        edges = [{"from": "A", "to": "B", "label": ""}, {"from": "B", "to": "C", "label": ""}]

    return {"nodes": nodes, "edges": edges}


def _parse_mermaid_chain(line: str) -> dict | None:
    if line.count("--") < 2 and line.count("==") < 2 and line.count("-.") < 2:
        return None
    node_pattern = re.compile(r"([A-Za-z0-9_]+)(?:\[(.*?)\]|\((.*?)\)|\{(.*?)\})?")
    matches = list(node_pattern.finditer(line))
    if len(matches) < 3:
        return None

    parsed_nodes = []
    for match in matches:
        node_id = match.group(1)
        label = next((g for g in match.group(2, 3, 4) if g), node_id)
        parsed_nodes.append((node_id, _clean_label(label)))

    edges = []
    for index in range(len(parsed_nodes) - 1):
        source = parsed_nodes[index][0]
        target = parsed_nodes[index + 1][0]
        segment = line[matches[index].end():matches[index + 1].start()]
        if "--" not in segment and "==" not in segment and "-." not in segment:
            continue
        label_match = re.search(r"\|([^|]+)\|", segment)
        edges.append({
            "from": source,
            "to": target,
            "label": _clean_label(label_match.group(1) if label_match else ""),
        })

    if not edges:
        return None
    return {"nodes": dict(parsed_nodes), "edges": edges}


def _dedupe_edges(edges: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for edge in edges:
        key = (edge.get("from"), edge.get("to"), edge.get("label", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _layout_nodes(
    nodes: dict[str, str],
    edges: list[dict],
    *,
    width: int,
    height: int,
) -> dict[str, tuple[float, float]]:
    ids = list(nodes.keys())
    if not ids:
        return {}

    incoming = {node_id: 0 for node_id in ids}
    outgoing = {node_id: [] for node_id in ids}
    for edge in edges:
        if edge["from"] in outgoing and edge["to"] in incoming:
            outgoing[edge["from"]].append(edge["to"])
            incoming[edge["to"]] += 1

    levels: dict[str, int] = {}
    frontier = [node_id for node_id, count in incoming.items() if count == 0] or [ids[0]]
    for node_id in frontier:
        levels[node_id] = 0
    while frontier:
        current = frontier.pop(0)
        for target in outgoing.get(current, []):
            next_level = levels.get(current, 0) + 1
            if next_level > levels.get(target, -1):
                levels[target] = next_level
                frontier.append(target)
    for node_id in ids:
        levels.setdefault(node_id, 0)

    max_level = max(levels.values()) if levels else 0
    grouped: dict[int, list[str]] = {}
    for node_id, level in levels.items():
        grouped.setdefault(level, []).append(node_id)

    if max_level <= 1 and len(ids) > 4:
        return _grid_layout(ids, width=width, height=height)

    positions = {}
    left = 150
    right = width - 150
    usable_x = right - left
    for level, level_nodes in grouped.items():
        if len(level_nodes) > 4:
            cols = math.ceil(len(level_nodes) / 4)
            level_width = min(usable_x / max(max_level + 1, 1), 220)
            base_x = left + (usable_x * level / max(max_level, 1)) - (level_width / 2)
            for idx, node_id in enumerate(level_nodes):
                col = idx // 4
                row = idx % 4
                x = base_x + (col + 0.5) * (level_width / cols)
                y = 156 + row * 108
                positions[node_id] = (
                    min(max(x, 110), width - 110),
                    min(max(y, 120), height - 96),
                )
            continue

        x = left + (usable_x * level / max(max_level, 1))
        spacing = min(118, 380 / max(len(level_nodes), 1))
        total_h = spacing * (len(level_nodes) - 1)
        start_y = 320 - total_h / 2
        for idx, node_id in enumerate(level_nodes):
            y = start_y + spacing * idx
            positions[node_id] = (
                min(max(x, 110), width - 110),
                min(max(y, 120), height - 96),
            )
    return positions


def _grid_layout(
    node_ids: list[str],
    *,
    width: int,
    height: int,
) -> dict[str, tuple[float, float]]:
    count = len(node_ids)
    cols = min(4, max(2, math.ceil(math.sqrt(count))))
    rows = math.ceil(count / cols)
    left = 150
    right = width - 150
    top = 160
    bottom = height - 108
    cell_w = (right - left) / max(cols - 1, 1)
    cell_h = (bottom - top) / max(rows - 1, 1)
    positions = {}
    for idx, node_id in enumerate(node_ids):
        row = idx // cols
        col = idx % cols
        positions[node_id] = (left + col * cell_w, top + row * cell_h)
    return positions


def _tier_bounds(positions: dict[str, tuple[float, float]]) -> list[tuple[int, int, int, int]]:
    if not positions:
        return [(64, 104, 960, 240), (64, 252, 960, 388), (64, 400, 960, 572)]
    return [(64, 104, 960, 240), (64, 252, 960, 388), (64, 400, 960, 572)]


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: str,
    width: int,
    *,
    scale: int = 1,
) -> None:
    sx, sy = start
    ex, ey = end
    angle = math.atan2(ey - sy, ex - sx)
    sx += math.cos(angle) * 96 * scale
    sy += math.sin(angle) * 42 * scale
    ex -= math.cos(angle) * 96 * scale
    ey -= math.sin(angle) * 42 * scale
    draw.line((sx, sy, ex, ey), fill=color, width=width)
    head = 14 * scale
    left = angle + math.pi * 0.82
    right = angle - math.pi * 0.82
    points = [
        (ex, ey),
        (ex + math.cos(left) * head, ey + math.sin(left) * head),
        (ex + math.cos(right) * head, ey + math.sin(right) * head),
    ]
    draw.polygon(points, fill=color)


def _draw_service_glyph(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    label: str,
    color: str,
    *,
    scale: int = 1,
) -> None:
    def sc(value: float) -> int:
        return int(round(value * scale))

    label_lower = label.lower()
    if _is_data_label(label):
        draw.ellipse((x, y, x + sc(32), y + sc(10)), outline=color, width=sc(3))
        draw.rectangle((x, y + sc(5), x + sc(32), y + sc(28)), outline=color, width=sc(3))
        draw.ellipse((x, y + sc(20), x + sc(32), y + sc(30)), outline=color, width=sc(3))
    elif "user" in label_lower or "client" in label_lower:
        draw.ellipse((x + sc(8), y, x + sc(24), y + sc(16)), outline=color, width=sc(3))
        draw.arc((x + sc(2), y + sc(14), x + sc(30), y + sc(42)), 200, 340, fill=color, width=sc(3))
    else:
        draw.rounded_rectangle((x, y, x + sc(32), y + sc(30)), radius=sc(6), outline=color, width=sc(3))
        draw.line((x + sc(6), y + sc(10), x + sc(26), y + sc(10)), fill=color, width=sc(2))
        draw.line((x + sc(6), y + sc(20), x + sc(26), y + sc(20)), fill=color, width=sc(2))


def _provider_for_label(label: str) -> str:
    text = label.lower()
    if any(token in text for token in ("aws", "ec2", "rds", "s3", "lambda", "cloudfront")):
        return "aws"
    if any(token in text for token in ("azure", "app service", "vm", "cosmos", "blob")):
        return "azure"
    if any(token in text for token in ("gcp", "google", "cloud run", "bigquery", "cloud sql")):
        return "gcp"
    if any(token in text for token in ("kubernetes", "pod", "service", "ingress", "cluster")):
        return "kubernetes"
    return "mixed"


def _diagrams_topology(asset: dict) -> dict:
    method = str(asset.get("method") or METHOD_DIAGRAMS)
    provider = _normalize_diagrams_provider(
        asset.get("diagrams_provider") or asset.get("provider"),
        asset.get("description", ""),
        asset,
    )
    clusters = _normalize_diagrams_clusters(asset.get("diagrams_clusters"))
    nodes = _normalize_diagrams_nodes(asset.get("diagrams_nodes"))
    edges = _normalize_diagrams_edges(asset.get("diagrams_edges"))
    if nodes and provider in {"aws", "azure", "gcp", "kubernetes"}:
        for node in nodes:
            label = str(node.get("label") or "")
            if node.get("provider") == "generic" and "user" not in label.lower():
                node["provider"] = provider
                node["service"] = _diagrams_service_for_label(label, provider)

    return {
        "title": str(asset.get("title") or "Architecture Diagram")[:80],
        "provider": provider,
        "direction": _normalize_diagrams_direction(asset.get("diagrams_direction")),
        "clusters": clusters,
        "nodes": nodes,
        "edges": edges,
    }


def _safe_diagrams_topology(asset: dict) -> dict:
    """Return a bounded, acyclic diagrams topology that native diagrams can consume."""
    try:
        topology = _diagrams_topology(asset)
    except BaseException as exc:
        logger.warning(
            "visual_asset_planner.diagrams_topology_guarded",
            error=str(exc)[:200],
        )
        topology = {
            "title": str(asset.get("title") or "Architecture Diagram")[:80],
            "provider": _normalize_diagrams_provider(
                asset.get("diagrams_provider") or asset.get("provider"),
                str(asset.get("description") or ""),
                asset,
            ),
            "direction": _normalize_diagrams_direction(asset.get("diagrams_direction")),
            "clusters": [],
            "nodes": [],
            "edges": [],
        }
    return _sanitize_diagrams_topology(topology, asset)


def _sanitize_diagrams_topology(topology: dict, asset: dict | None = None) -> dict:
    title = str(topology.get("title") or (asset or {}).get("title") or "Architecture Diagram")[:80]
    provider = _normalize_diagrams_provider(
        topology.get("provider") or (asset or {}).get("diagrams_provider"),
        str((asset or {}).get("description") or ""),
        asset or {},
    )
    direction = _normalize_diagrams_direction(topology.get("direction"))
    clusters = _sanitize_diagrams_clusters(topology.get("clusters", []))
    nodes = _sanitize_diagrams_nodes(topology.get("nodes", []), clusters, asset or {})
    edges = _sanitize_diagrams_edges(topology.get("edges", []), nodes)

    return {
        "title": title,
        "provider": provider,
        "direction": direction,
        "clusters": clusters,
        "nodes": nodes,
        "edges": edges,
    }


def _sanitize_diagrams_clusters(raw_clusters: Any) -> list[dict]:
    clusters = []
    seen = set()
    for cluster in raw_clusters if isinstance(raw_clusters, list) else []:
        if not isinstance(cluster, dict):
            continue
        cluster_id = _safe_id(str(cluster.get("id") or cluster.get("label") or ""))
        if not cluster_id or cluster_id in seen:
            continue
        seen.add(cluster_id)
        clusters.append({
            "id": cluster_id,
            "label": str(cluster.get("label") or cluster_id).strip()[:80],
            "parent": _safe_id(str(cluster.get("parent") or "")) if cluster.get("parent") else "",
        })
        if len(clusters) >= MAX_DIAGRAMS_CLUSTERS:
            break

    cluster_ids = {cluster["id"] for cluster in clusters}
    parent_by_id = {
        cluster["id"]: cluster["parent"] if cluster["parent"] in cluster_ids else ""
        for cluster in clusters
    }
    for cluster in clusters:
        cluster_id = cluster["id"]
        if cluster["parent"] not in cluster_ids or cluster["parent"] == cluster_id:
            cluster["parent"] = ""

    # Detach any remaining parent that would create a cycle or excessive depth.
    parent_by_id = {cluster["id"]: cluster["parent"] for cluster in clusters}
    for cluster in clusters:
        current = cluster["parent"]
        seen_chain = {cluster["id"]}
        depth = 0
        while current:
            if current in seen_chain or depth >= MAX_DIAGRAMS_CLUSTER_DEPTH:
                cluster["parent"] = ""
                break
            seen_chain.add(current)
            current = parent_by_id.get(current, "")
            depth += 1
    return clusters


def _sanitize_diagrams_nodes(raw_nodes: Any, clusters: list[dict], asset: dict) -> list[dict]:
    cluster_ids = {cluster["id"] for cluster in clusters}
    nodes = []
    seen = set()
    for raw in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(raw, dict):
            continue
        node_id = _safe_id(str(raw.get("id") or raw.get("label") or ""))
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        label = str(raw.get("label") or node_id).strip()[:80]
        provider = _normalize_diagrams_provider(
            raw.get("provider"),
            str(asset.get("description") or ""),
            raw,
        )
        service = _normalize_diagrams_service(
            raw.get("service") or raw.get("type") or raw.get("node_type") or label
        )
        cluster = _safe_id(str(raw.get("cluster") or "")) if raw.get("cluster") else ""
        nodes.append({
            "id": node_id,
            "label": label,
            "provider": provider,
            "service": service or _diagrams_service_for_label(label, provider),
            "cluster": cluster if cluster in cluster_ids else "",
        })
        if len(nodes) >= MAX_DIAGRAMS_NODES:
            break
    return nodes


def _sanitize_diagrams_edges(raw_edges: Any, nodes: list[dict]) -> list[dict]:
    node_ids = {node["id"] for node in nodes}
    edges = []
    seen = set()
    for raw in raw_edges if isinstance(raw_edges, list) else []:
        if not isinstance(raw, dict):
            continue
        source = _safe_id(str(raw.get("from") or raw.get("source") or ""))
        target = _safe_id(str(raw.get("to") or raw.get("target") or ""))
        if not source or not target or source == target:
            continue
        if source not in node_ids or target not in node_ids:
            continue
        key = (source, target, str(raw.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        style = str(raw.get("style") or "solid").lower().strip()
        if style not in {"solid", "dashed", "dotted", "bold"}:
            style = "solid"
        edges.append({
            "from": source,
            "to": target,
            "label": str(raw.get("label") or "").strip()[:50],
            "color": str(raw.get("color") or "#475569").strip()[:24],
            "style": style,
        })
        if len(edges) >= MAX_DIAGRAMS_EDGES:
            break
    return edges


def _normalize_diagrams_provider(value: Any, user_query: str, raw: dict) -> str:
    provider = str(value or "").lower().strip()
    aliases = {
        "aws": "aws",
        "amazon": "aws",
        "amazon web services": "aws",
        "azure": "azure",
        "microsoft azure": "azure",
        "gcp": "gcp",
        "google": "gcp",
        "google cloud": "gcp",
        "google cloud platform": "gcp",
        "k8s": "kubernetes",
        "kubernetes": "kubernetes",
        "general": "generic",
        "basic": "generic",
        "generic": "generic",
        "mixed": "mixed",
    }
    if provider in aliases:
        return aliases[provider]

    text = " ".join([user_query, json.dumps(raw, ensure_ascii=False)]).lower()
    detected = []
    if any(token in text for token in ("aws", "amazon web services", "ec2", "rds", "s3")):
        detected.append("aws")
    if any(token in text for token in ("azure", "app service", "cosmos", "blob storage")):
        detected.append("azure")
    if any(token in text for token in ("gcp", "google cloud", "bigquery", "cloud run")):
        detected.append("gcp")
    if any(token in text for token in ("kubernetes", "k8s", "pod", "ingress")):
        detected.append("kubernetes")
    if len(set(detected)) > 1:
        return "mixed"
    if detected:
        return detected[0]
    return "generic"


def _normalize_diagrams_direction(value: Any) -> str:
    direction = str(value or "LR").upper().strip()
    return direction if direction in {"LR", "RL", "TB", "BT"} else "LR"


def _normalize_diagrams_clusters(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    clusters = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cluster_id = _safe_id(str(item.get("id") or item.get("label") or ""))
        if not cluster_id:
            continue
        parent = str(item.get("parent") or "").strip()
        clusters.append({
            "id": cluster_id,
            "label": str(item.get("label") or cluster_id).strip()[:80],
            "parent": _safe_id(parent) if parent else "",
        })
    return clusters


def _normalize_diagrams_nodes(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    nodes = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        node_id = _safe_id(str(item.get("id") or item.get("mermaid_id") or item.get("label") or ""))
        if not node_id:
            continue
        label = str(item.get("label") or node_id).strip()
        provider = _normalize_diagrams_provider(
            item.get("provider") or item.get("library"),
            "",
            item,
        )
        service = _normalize_diagrams_service(
            item.get("service") or item.get("shape") or item.get("type") or label
        )
        nodes.append({
            "id": node_id,
            "label": label[:80],
            "provider": provider,
            "service": service or _diagrams_service_for_label(label, provider),
            "cluster": _safe_id(str(item.get("cluster") or "")) if item.get("cluster") else "",
        })
    return nodes


def _normalize_diagrams_edges(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    edges = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = _safe_id(str(item.get("from") or item.get("source") or ""))
        target = _safe_id(str(item.get("to") or item.get("target") or ""))
        if not source or not target:
            continue
        style = str(item.get("style") or "solid").lower().strip()
        if style not in {"solid", "dashed", "dotted", "bold"}:
            style = "solid"
        edges.append({
            "from": source,
            "to": target,
            "label": str(item.get("label") or "").strip()[:50],
            "color": str(item.get("color") or "#475569").strip()[:24],
            "style": style,
        })
    return _dedupe_edges(edges)


def _normalize_diagrams_service(value: Any) -> str:
    text = _normalize_shape_id(str(value or ""))
    aliases = {
        "application_load_balancer": "alb",
        "load_balancer": "elb",
        "elastic_load_balancing": "elb",
        "route_53": "route53",
        "cloud_front": "cloudfront",
        "auto_scaling": "autoscaling",
        "auto_scaling_group": "autoscaling",
        "nat_gateway": "natgateway",
        "internet_gateway": "internetgateway",
        "api_gateway": "apigateway",
        "app_services": "appservice",
        "app_service": "appservice",
        "virtual_machine": "vm",
        "virtual_machines": "vm",
        "sql_database": "sqldatabase",
        "sql_databases": "sqldatabase",
        "storage_accounts": "storage",
        "cloud_sql": "cloudsql",
        "cloud_storage": "storage",
        "google_kubernetes_engine": "gke",
        "kubernetes_services": "aks",
        "svc": "service",
        "deploy": "deployment",
        "ing": "ingress",
        "vector_store": "database",
        "vectorstore": "database",
        "semantic_index": "database",
        "semantic_search": "search",
        "retrieval": "search",
        "retriever": "search",
        "context_retriever": "search",
        "context_manager": "context",
        "prompt_context": "context",
        "prompt": "context",
        "embedding": "embedding",
        "embeddings": "embedding",
        "embedder": "embedding",
        "agent_execution": "agent",
        "ai_agent": "agent",
        "llm": "model",
        "model": "model",
        "validator": "validator",
        "validation": "validator",
        "response": "output",
        "output": "output",
        "document": "document",
        "documents": "document",
        "docs": "document",
    }
    return aliases.get(text, text)


def _diagrams_service_for_label(label: str, provider: str) -> str:
    if provider == "aws":
        return _normalize_diagrams_service(_aws_service_for_label(label))
    if provider == "azure":
        return _normalize_diagrams_service(_azure_service_for_label(label))
    if provider == "gcp":
        return _normalize_diagrams_service(_gcp_service_for_label(label))
    if provider == "kubernetes":
        return _normalize_diagrams_service(_kubernetes_service_for_label(label))
    if provider == "mixed":
        inferred = _provider_for_label(label)
        if inferred == "mixed":
            return _normalize_diagrams_service(_generic_shape_for_label(label))
        return _diagrams_service_for_label(label, inferred)
    return _normalize_diagrams_service(_generic_shape_for_label(label))


def _fallback_diagrams_clusters(user_query: str, method: str) -> list[dict]:
    if method != METHOD_DIAGRAMS:
        return []
    provider = _normalize_diagrams_provider(None, user_query, {})
    if provider == "aws":
        return [
            {"id": "region", "label": "AWS Region", "parent": ""},
            {"id": "vpc", "label": "VPC", "parent": "region"},
            {"id": "public", "label": "Public Subnets", "parent": "vpc"},
            {"id": "private_app", "label": "Private App Subnets", "parent": "vpc"},
            {"id": "private_data", "label": "Private Data Subnets", "parent": "vpc"},
        ]
    if provider == "azure":
        return [
            {"id": "region", "label": "Azure Region", "parent": ""},
            {"id": "vnet", "label": "Virtual Network", "parent": "region"},
        ]
    if provider == "gcp":
        return [
            {"id": "region", "label": "Google Cloud Region", "parent": ""},
            {"id": "vpc", "label": "VPC Network", "parent": "region"},
        ]
    return [{"id": "system", "label": "System Boundary", "parent": ""}]


def _fallback_diagrams_nodes(user_query: str, method: str) -> list[dict]:
    if method != METHOD_DIAGRAMS:
        return []
    provider = _normalize_diagrams_provider(None, user_query, {})
    if provider == "aws":
        return [
            _diagrams_node("users", "Users", "generic", "users"),
            _diagrams_node("dns", "Route 53 / CloudFront", "aws", "cloudfront"),
            _diagrams_node("alb", "Application Load Balancer", "aws", "alb", "public"),
            _diagrams_node("web", "Web Tier EC2 Auto Scaling", "aws", "ec2", "private_app"),
            _diagrams_node("app", "Application Services", "aws", "ecs", "private_app"),
            _diagrams_node("db", "RDS Multi-AZ", "aws", "rds", "private_data"),
            _diagrams_node("s3", "S3 Object Storage", "aws", "s3", "private_data"),
        ]
    content_nodes = _content_diagram_nodes(user_query, provider)
    if content_nodes:
        return content_nodes
    return [
        _diagrams_node("client", "Client", "generic", "users"),
        _diagrams_node("edge", "Edge / Gateway", provider, "elb"),
        _diagrams_node("app", "Application Service", provider, "appservice"),
        _diagrams_node("data", "Data Store", provider, "database"),
    ]


def _diagrams_node(
    node_id: str,
    label: str,
    provider: str,
    service: str,
    cluster: str = "",
) -> dict:
    return {
        "id": node_id,
        "label": label,
        "provider": provider,
        "service": service,
        "cluster": cluster,
    }


def _fallback_diagrams_edges(user_query: str, method: str) -> list[dict]:
    if method != METHOD_DIAGRAMS:
        return []
    provider = _normalize_diagrams_provider(None, user_query, {})
    if provider == "aws":
        return [
            {"from": "users", "to": "dns", "label": "HTTPS", "color": "#2563EB", "style": "solid"},
            {"from": "dns", "to": "alb", "label": "TLS", "color": "#2563EB", "style": "solid"},
            {"from": "alb", "to": "web", "label": "", "color": "#475569", "style": "solid"},
            {"from": "web", "to": "app", "label": "API", "color": "#475569", "style": "solid"},
            {"from": "app", "to": "db", "label": "SQL", "color": "#16A34A", "style": "solid"},
            {"from": "app", "to": "s3", "label": "Objects", "color": "#16A34A", "style": "dashed"},
        ]
    content_nodes = _content_diagram_nodes(user_query, provider)
    if len(content_nodes) >= 2:
        edges = []
        for index, source in enumerate(content_nodes[:-1]):
            target = content_nodes[index + 1]
            edges.append({
                "from": source["id"],
                "to": target["id"],
                "label": _fallback_edge_label(source["label"], target["label"], index),
                "color": "#2563EB" if index == 0 else "#475569",
                "style": "solid",
            })
        return edges
    return [
        {"from": "client", "to": "edge", "label": "HTTPS", "color": "#2563EB", "style": "solid"},
        {"from": "edge", "to": "app", "label": "", "color": "#475569", "style": "solid"},
        {"from": "app", "to": "data", "label": "Data", "color": "#16A34A", "style": "solid"},
    ]


def _content_diagram_nodes(text: str, provider: str) -> list[dict]:
    labels = _content_diagram_labels(text)
    if len(labels) < 3:
        return []

    nodes = []
    used_ids: set[str] = set()
    for index, label in enumerate(labels[:8], start=1):
        base_id = _normalize_shape_id(label) or f"concept_{index}"
        node_id = _safe_id(base_id)[:36] or f"concept_{index}"
        if node_id in used_ids:
            node_id = f"{node_id}_{index}"
        used_ids.add(node_id)

        node_provider = provider if provider in {"azure", "gcp", "kubernetes"} else "generic"
        if any(token in label.lower() for token in ("user", "request", "client", "customer")):
            node_provider = "generic"
        nodes.append(
            _diagrams_node(
                node_id,
                label,
                node_provider,
                _diagrams_service_for_label(label, node_provider),
            )
        )
    return nodes


def _content_diagram_labels(text: str) -> list[str]:
    lowered = str(text or "").lower()
    labels: list[str] = []

    rules = [
        (("user request", "natural language", "request", "client", "customer", "요청", "자연어"), "User Request"),
        (("input", "ingest", "intake", "입력"), "Input Intake"),
        (("planner", "planning", "plan", "계획"), "Planning Agent"),
        (("langgraph", "langchain"), "LangGraph Engine"),
        (("agent", "agentic", "에이전트"), "Agent Orchestration"),
        (("llm", "model", "reasoning", "모델"), "LLM Reasoning"),
        (("retrieve", "retrieval", "search", "rag", "검색"), "Retrieval Search"),
        (("template", "schema", "템플릿"), "Template Schema"),
        (("pptx", "powerpoint", "slide"), "PPTX Pipeline"),
        (("rich document", "docx", "hwp", "xlsx", "markdown", "문서"), "Native Document Output"),
        (("rest", "api", "sdk", "cli"), "API / SDK"),
        (("web ui", "web", "saas", "ui"), "Web UI"),
        (("qa", "quality", "validation", "검증", "품질"), "Quality Validation"),
        (("data", "storage", "database", "store", "데이터"), "Data Store"),
        (("workflow", "pipeline", "flow", "파이프라인"), "Workflow Pipeline"),
        (("operate", "operation", "cost", "운영", "비용"), "Operations Control"),
        (("time", "latency", "delay", "시간"), "Cycle Time"),
        (("approval", "review", "collaboration", "검토", "협업"), "Review Loop"),
        (("output", "export", "generation", "생성", "결과"), "Generated Output"),
    ]
    for tokens, label in rules:
        if any(token in lowered for token in tokens):
            _append_unique_label(labels, label)

    for phrase in _fallback_label_phrases(text):
        _append_unique_label(labels, phrase)
        if len(labels) >= 6:
            break

    if len(labels) == 1:
        labels.append("Processing Core")
    if len(labels) == 2:
        labels.append("Generated Output")
    return labels[:8]


def _fallback_label_phrases(text: str) -> list[str]:
    phrases = []
    for raw in re.split(r"[\n\r|:;,.>]+", str(text or "")):
        phrase = _clean_label(raw)
        phrase = re.sub(r"\s+", " ", phrase).strip(" -_/")
        if not phrase or len(phrase) < 4 or len(phrase) > 42:
            continue
        if phrase.lower().startswith(("user request", "diagrams-package", "user ")):
            continue
        if phrase.lower() in {"architecture diagram", "visual diagram"}:
            continue
        phrases.append(phrase[:42])
    return phrases


def _append_unique_label(labels: list[str], label: str) -> None:
    if label and label not in labels:
        labels.append(label)


def _fallback_edge_label(source: str, target: str, index: int) -> str:
    joined = f"{source} {target}".lower()
    if any(token in joined for token in ("request", "input", "client", "user")):
        return "Input"
    if any(token in joined for token in ("validation", "qa", "review")):
        return "Check"
    if any(token in joined for token in ("data", "storage", "database")):
        return "Data"
    if any(token in joined for token in ("output", "document", "pptx")):
        return "Render"
    return "Flow" if index == 0 else ""


def _diagrams_node_map() -> dict[tuple[str, str], tuple[str, str]]:
    return {
        ("generic", "users"): ("diagrams.onprem.client", "Users"),
        ("generic", "user"): ("diagrams.onprem.client", "User"),
        ("generic", "client"): ("diagrams.onprem.client", "Client"),
        ("generic", "rectangle"): ("diagrams.generic.blank", "Blank"),
        ("generic", "database"): ("diagrams.generic.database", "SQL"),
        ("generic", "cylinder"): ("diagrams.generic.database", "SQL"),
        ("generic", "storage"): ("diagrams.generic.storage", "Storage"),
        ("generic", "cloud"): ("diagrams.generic.network", "Router"),
        ("generic", "elb"): ("diagrams.generic.network", "Router"),
        ("generic", "apigateway"): ("diagrams.generic.network", "Router"),
        ("generic", "gateway"): ("diagrams.generic.network", "Router"),
        ("generic", "network"): ("diagrams.generic.network", "Switch"),
        ("generic", "appservice"): ("diagrams.generic.compute", "Rack"),
        ("generic", "service"): ("diagrams.generic.compute", "Rack"),
        ("generic", "compute_engine"): ("diagrams.generic.compute", "Rack"),
        ("generic", "vm"): ("diagrams.generic.compute", "Rack"),
        ("generic", "process"): ("diagrams.programming.flowchart", "Action"),
        ("generic", "action"): ("diagrams.programming.flowchart", "Action"),
        ("generic", "agent"): ("diagrams.programming.flowchart", "PredefinedProcess"),
        ("generic", "model"): ("diagrams.programming.flowchart", "PredefinedProcess"),
        ("generic", "context"): ("diagrams.programming.flowchart", "InternalStorage"),
        ("generic", "search"): ("diagrams.programming.flowchart", "Inspection"),
        ("generic", "embedding"): ("diagrams.programming.flowchart", "Preparation"),
        ("generic", "document"): ("diagrams.programming.flowchart", "Document"),
        ("generic", "docs"): ("diagrams.programming.flowchart", "MultipleDocuments"),
        ("generic", "output"): ("diagrams.programming.flowchart", "InputOutput"),
        ("generic", "validator"): ("diagrams.programming.flowchart", "Inspection"),
        ("generic", "decision"): ("diagrams.programming.flowchart", "Decision"),
        ("generic", "rhombus"): ("diagrams.programming.flowchart", "Decision"),
        ("generic", "start_end"): ("diagrams.programming.flowchart", "StartEnd"),
        ("aws", "route53"): ("diagrams.aws.network", "Route53"),
        ("aws", "cloudfront"): ("diagrams.aws.network", "CloudFront"),
        ("aws", "elb"): ("diagrams.aws.network", "ElasticLoadBalancing"),
        ("aws", "alb"): ("diagrams.aws.network", "ElbApplicationLoadBalancer"),
        ("aws", "apigateway"): ("diagrams.aws.network", "APIGateway"),
        ("aws", "natgateway"): ("diagrams.aws.network", "NATGateway"),
        ("aws", "internetgateway"): ("diagrams.aws.network", "InternetGateway"),
        ("aws", "vpc"): ("diagrams.aws.network", "VPC"),
        ("aws", "ec2"): ("diagrams.aws.compute", "EC2"),
        ("aws", "autoscaling"): ("diagrams.aws.compute", "AutoScaling"),
        ("aws", "ecs"): ("diagrams.aws.compute", "ECS"),
        ("aws", "eks"): ("diagrams.aws.compute", "EKS"),
        ("aws", "lambda"): ("diagrams.aws.compute", "Lambda"),
        ("aws", "rds"): ("diagrams.aws.database", "RDS"),
        ("aws", "aurora"): ("diagrams.aws.database", "Aurora"),
        ("aws", "dynamodb"): ("diagrams.aws.database", "Dynamodb"),
        ("aws", "elasticache"): ("diagrams.aws.database", "Elasticache"),
        ("aws", "s3"): ("diagrams.aws.storage", "S3"),
        ("aws", "waf"): ("diagrams.aws.security", "WAF"),
        ("aws", "cloudwatch"): ("diagrams.aws.management", "Cloudwatch"),
        ("azure", "appservice"): ("diagrams.azure.compute", "AppServices"),
        ("azure", "function_apps"): ("diagrams.azure.compute", "FunctionApps"),
        ("azure", "vm"): ("diagrams.azure.compute", "VM"),
        ("azure", "aks"): ("diagrams.azure.compute", "KubernetesServices"),
        ("azure", "load_balancers"): ("diagrams.azure.network", "LoadBalancers"),
        ("azure", "application_gateways"): ("diagrams.azure.network", "ApplicationGateway"),
        ("azure", "virtual_networks"): ("diagrams.azure.network", "VirtualNetworks"),
        ("azure", "sqldatabase"): ("diagrams.azure.database", "SQLDatabases"),
        ("azure", "azure_cosmos_db"): ("diagrams.azure.database", "CosmosDb"),
        ("azure", "storage"): ("diagrams.azure.storage", "StorageAccounts"),
        ("gcp", "compute_engine"): ("diagrams.gcp.compute", "ComputeEngine"),
        ("gcp", "cloud_run"): ("diagrams.gcp.compute", "Run"),
        ("gcp", "app_engine"): ("diagrams.gcp.compute", "AppEngine"),
        ("gcp", "gke"): ("diagrams.gcp.compute", "KubernetesEngine"),
        ("gcp", "cloud_load_balancing"): ("diagrams.gcp.network", "LoadBalancing"),
        ("gcp", "cloudsql"): ("diagrams.gcp.database", "SQL"),
        ("gcp", "bigquery"): ("diagrams.gcp.analytics", "Bigquery"),
        ("gcp", "storage"): ("diagrams.gcp.storage", "Storage"),
        ("gcp", "pubsub"): ("diagrams.gcp.analytics", "Pubsub"),
        ("kubernetes", "pod"): ("diagrams.k8s.compute", "Pod"),
        ("kubernetes", "deployment"): ("diagrams.k8s.compute", "Deploy"),
        ("kubernetes", "statefulset"): ("diagrams.k8s.compute", "StatefulSet"),
        ("kubernetes", "service"): ("diagrams.k8s.network", "Service"),
        ("kubernetes", "ingress"): ("diagrams.k8s.network", "Ingress"),
        ("kubernetes", "namespace"): ("diagrams.k8s.group", "NS"),
    }


def _aws_service_for_label(label: str) -> str:
    text = label.lower()
    service_tokens = (
        ("cloudfront", "cloudfront"),
        ("route 53", "route_53"),
        ("route53", "route_53"),
        ("alb", "elastic_load_balancing"),
        ("load balancer", "elastic_load_balancing"),
        ("elb", "elastic_load_balancing"),
        ("ec2", "ec2"),
        ("auto scaling", "auto_scaling"),
        ("lambda", "lambda"),
        ("api gateway", "api_gateway"),
        ("api", "api_gateway"),
        ("rds", "rds"),
        ("aurora", "aurora"),
        ("dynamodb", "dynamodb"),
        ("s3", "s3"),
        ("eks", "elastic_kubernetes_service"),
        ("ecs", "elastic_container_service"),
        ("vpc", "vpc"),
        ("subnet", "vpc"),
        ("database", "rds"),
        ("db", "rds"),
        ("storage", "s3"),
        ("user", "users"),
        ("client", "users"),
    )
    for token, service in service_tokens:
        if token in text:
            return service
    return "general"


def _azure_service_for_label(label: str) -> str:
    text = label.lower()
    service_tokens = (
        ("app service", "app_services"),
        ("function", "function_apps"),
        ("vm", "virtual_machine"),
        ("virtual machine", "virtual_machine"),
        ("load balancer", "load_balancers"),
        ("application gateway", "application_gateways"),
        ("sql", "sql_database"),
        ("cosmos", "azure_cosmos_db"),
        ("blob", "storage_accounts"),
        ("storage", "storage_accounts"),
        ("vnet", "virtual_networks"),
        ("virtual network", "virtual_networks"),
        ("aks", "kubernetes_services"),
        ("kubernetes", "kubernetes_services"),
        ("api", "api_management_services"),
        ("user", "users"),
    )
    for token, service in service_tokens:
        if token in text:
            return service
    return "cloud_services"


def _gcp_service_for_label(label: str) -> str:
    text = label.lower()
    service_tokens = (
        ("compute", "compute_engine"),
        ("vm", "compute_engine"),
        ("cloud run", "cloud_run"),
        ("app engine", "app_engine"),
        ("load balancer", "cloud_load_balancing"),
        ("cloud sql", "cloud_sql"),
        ("sql", "cloud_sql"),
        ("bigquery", "bigquery"),
        ("storage", "cloud_storage"),
        ("bucket", "cloud_storage"),
        ("gke", "google_kubernetes_engine"),
        ("kubernetes", "google_kubernetes_engine"),
        ("pubsub", "pubsub"),
        ("pub/sub", "pubsub"),
        ("api", "api_gateway"),
    )
    for token, service in service_tokens:
        if token in text:
            return service
    return "cloud"


def _kubernetes_service_for_label(label: str) -> str:
    text = label.lower()
    service_tokens = (
        ("pod", "pod"),
        ("service", "svc"),
        ("ingress", "ing"),
        ("deployment", "deploy"),
        ("stateful", "sts"),
        ("daemon", "ds"),
        ("config", "cm"),
        ("secret", "secret"),
        ("namespace", "ns"),
        ("node", "node"),
        ("cluster", "cluster"),
    )
    for token, service in service_tokens:
        if token in text:
            return service
    return "pod"


def _generic_shape_for_label(label: str) -> str:
    text = label.lower()
    if any(token in text for token in ("user", "client", "actor", "customer")):
        return "users"
    if any(token in text for token in ("document", "doc", "retrieved docs", "query log", "schema")):
        return "document"
    if any(token in text for token in ("database", "db", "storage", "data", "vector store", "index")):
        return "database"
    if any(token in text for token in ("search", "retrieval", "retriever", "semantic")):
        return "search"
    if any(token in text for token in ("embedding", "embed", "chunk")):
        return "embedding"
    if any(token in text for token in ("agent", "llm", "model", "langchain", "langgraph")):
        return "agent"
    if any(token in text for token in ("context", "prompt", "assembly", "manager")):
        return "context"
    if any(token in text for token in ("validate", "validation", "verified", "verifier", "inspection")):
        return "validator"
    if any(token in text for token in ("output", "response", "result")):
        return "output"
    if any(token in text for token in ("decision", "choice", "gateway")):
        return "decision"
    if any(token in text for token in ("cloud", "external")):
        return "cloud"
    if any(token in text for token in ("service", "process", "execution", "workflow", "pipeline")):
        return "process"
    return "process"


def _quick_visual_signal(user_query: str, slide_blueprints: list[dict]) -> bool:
    if _negative_visual_asset_signal(user_query):
        return False
    if _reserved_visual_slot_count(slide_blueprints) > 0:
        return True

    text = " ".join([
        user_query,
        json.dumps(slide_blueprints, ensure_ascii=False)[:6000],
    ]).lower()
    korean_signals = ("아키텍처", "인프라", "서비스 구조", "다이어그램", "이미지")
    if any(token in text for token in korean_signals):
        return True
    signals = (
        "architecture", "architect", "aws", "azure", "gcp", "cloud", "infra",
        "infrastructure", "network", "service topology", "diagram", "diagrams",
        "mermaid", "flowchart", "workflow", "process", "system design",
        "3-tier", "three tier", "tier", "아키텍", "아키텍처", "인프라", "서비스 구조",
        "구조도", "구성도", "다이어그램", "머메이드", "이미지", "그림",
    )
    return any(signal in text for signal in signals)


def _negative_visual_asset_signal(user_query: str) -> bool:
    """Detect revision requests that explicitly remove architecture/diagram assets."""
    text = str(user_query or "").lower()
    if not text:
        return False
    visual_terms = (
        "아키텍처", "아키텍쳐", "다이어그램", "구성도", "구조도",
        "architecture", "diagram", "flowchart",
    )
    replacement_terms = (
        "말고", "빼고", "제외", "삭제", "없애", "일반 내용", "일반내용",
        "텍스트 내용", "내용으로 변경", "not", "without", "instead of", "remove",
        "ordinary content", "general content",
    )
    return any(term in text for term in visual_terms) and any(
        term in text for term in replacement_terms
    )


def _select_method(user_query: str, raw: dict) -> str:
    text = " ".join([
        user_query,
        json.dumps(raw, ensure_ascii=False),
    ]).lower()
    if any(token in text for token in ("아키텍처", "인프라", "3티어", "3 티어", "클라우드")):
        return METHOD_DIAGRAMS
    architecture_signals = (
        "aws", "azure", "gcp", "cloud", "vpc", "subnet", "load balancer",
        "elb", "ec2", "rds", "eks", "ecs", "lambda", "architecture",
        "infrastructure", "network", "3-tier", "three tier", "diagrams",
        "아키텍", "인프라", "3티어", "3 티어", "vpc", "서브넷",
    )
    image_signals = (
        "photo", "illustration", "background", "concept image", "hero image",
        "mood", "사진", "일러스트", "배경 이미지", "컨셉 이미지",
    )
    if any(signal in text for signal in architecture_signals):
        return METHOD_DIAGRAMS
    if any(signal in text for signal in image_signals):
        return METHOD_IMAGE
    return METHOD_DIAGRAMS


def _fallback_description(user_query: str, method: str) -> str:
    if method == METHOD_DIAGRAMS:
        return (
            "Diagrams-package technical architecture diagram that shows users, edge/load "
            "balancing, application services, and a data tier with clear connection flow. "
            f"User request: {user_query}"
        )
    if method == METHOD_IMAGE:
        return f"Professional presentation visual for: {user_query}"
    return f"Clean deterministic diagram for: {user_query}"


def _fallback_mermaid(user_query: str, method: str) -> str:
    text = user_query.lower()
    aws_tier_signals = ("aws", "3-tier", "3 티어", "3티어")
    is_architecture_method = method == METHOD_DIAGRAMS
    if is_architecture_method or any(signal in text for signal in aws_tier_signals):
        return (
            "graph LR\n"
            "  U[Users] --> CF[CloudFront / Route 53]\n"
            "  CF --> ALB[Application Load Balancer]\n"
            "  ALB --> WEB[Web Tier - EC2 / Auto Scaling]\n"
            "  WEB --> APP[Application Tier - API Services]\n"
            "  APP --> DB[(Data Tier - RDS Multi-AZ)]\n"
            "  APP --> S3[S3 Object Storage]\n"
        )
    return "graph LR\n  A[Input] --> B[Processing]\n  B --> C[Decision]\n  C --> D[Output]\n"


def _default_slide_index(slide_blueprints: list[dict]) -> int:
    for bp in slide_blueprints:
        if isinstance(bp, dict) and bp.get("slide_type") not in {"cover", "section"}:
            try:
                return int(bp.get("index", 2))
            except (TypeError, ValueError):
                return 2
    return 1


def _default_visual_slide_index(
    slide_blueprints: list[dict],
    slide_revision_instructions: dict[int, str],
) -> int:
    visual_targets = [
        index for index, instruction in slide_revision_instructions.items()
        if _has_visual_asset_signal(instruction)
    ]
    if len(visual_targets) == 1:
        return visual_targets[0]
    return _default_slide_index(slide_blueprints)


def _infer_visual_asset_slide_index(
    raw: dict,
    description: str,
    user_query: str,
    valid_indices: set[int],
    slide_revision_instructions: dict[int, str],
    method: str,
) -> int | None:
    explicit_index = _coerce_slide_index(raw.get("slide_index"), valid_indices)
    if not slide_revision_instructions:
        return explicit_index

    asset_text = " ".join([
        str(raw.get("title") or ""),
        description,
        str(raw.get("image_prompt") or ""),
        str(raw.get("mermaid") or ""),
    ])
    best_index = _best_matching_slide_instruction(asset_text, slide_revision_instructions)
    if best_index is not None:
        return best_index

    visual_targets = [
        index for index, instruction in slide_revision_instructions.items()
        if _has_visual_asset_signal(instruction)
    ]
    if method == METHOD_DIAGRAMS and len(visual_targets) == 1:
        return visual_targets[0]
    if explicit_index in slide_revision_instructions:
        return explicit_index
    return explicit_index


def _best_matching_slide_instruction(
    asset_text: str,
    slide_revision_instructions: dict[int, str],
) -> int | None:
    asset_tokens = set(_meaningful_tokens(asset_text))
    if not asset_tokens:
        return None
    best_index = None
    best_score = 0
    for index, instruction in slide_revision_instructions.items():
        instruction_tokens = set(_meaningful_tokens(instruction))
        score = len(asset_tokens & instruction_tokens)
        if score > best_score:
            best_score = score
            best_index = index
    return best_index if best_score >= 2 else None


def _meaningful_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9가-힣]{2,}", str(text).lower())
    stopwords = {
        "slide", "슬라이드", "변경", "수정", "추가", "적용", "해주세요", "해줘",
        "다이어그램", "diagram", "image", "이미지", "architecture", "아키텍처",
    }
    return [token for token in tokens if token not in stopwords]


def _has_visual_asset_signal(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        signal in lowered
        for signal in (
            "diagram", "architecture", "flowchart", "workflow", "mermaid",
            "다이어그램", "아키텍처", "구조도", "흐름도", "이미지", "재생성",
        )
    )


def _normalize_slide_instruction_map(value: object) -> dict[int, str]:
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for key, instruction in value.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        text = str(instruction or "").strip()
        if text:
            normalized[index] = text
    return normalized


def _coerce_slide_index(value: Any, valid_indices: set[int]) -> int | None:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    if valid_indices and index not in valid_indices:
        return None
    return index


def _normalize_placement(raw: Any) -> dict:
    placement = raw if isinstance(raw, dict) else {}
    x = _num(placement.get("x"), 360)
    y = _num(placement.get("y"), 112)
    w = _num(placement.get("w"), 520)
    h = _num(placement.get("h"), 330)
    x = max(40, min(x, 900))
    y = max(78, min(y, 500))
    w = max(220, min(w, 920 - x))
    h = max(160, min(h, 514 - y))
    return {"x": round(x), "y": round(y), "w": round(w), "h": round(h)}


def _asset_slot_placement(slide_index: int, slide_blueprints: list[dict]) -> dict | None:
    """Use the slide planner's explicit diagram/image slot as the asset box."""
    for blueprint in slide_blueprints:
        if not isinstance(blueprint, dict):
            continue
        try:
            index = int(blueprint.get("index", 0))
        except (TypeError, ValueError):
            continue
        if index != slide_index:
            continue
        placements = blueprint.get("layout_plan", {}).get("element_placements", [])
        if not isinstance(placements, list):
            return None
        candidates = [
            placement for placement in placements
            if isinstance(placement, dict)
            and (
                str(placement.get("asset_role") or "") == "visual_asset"
                or str(placement.get("element") or "").lower() in {"diagram", "image"}
            )
        ]
        candidates.sort(key=lambda item: 0 if str(item.get("asset_role") or "") == "visual_asset" else 1)
        for placement in candidates:
            normalized = _placement_from_layout_item(placement)
            if normalized:
                return normalized
    return None


def _placement_from_layout_item(item: dict) -> dict | None:
    x = _optional_num(item.get("x", item.get("left")))
    y = _optional_num(item.get("y", item.get("top")))
    w = _optional_num(item.get("w", item.get("width")))
    h = _optional_num(item.get("h", item.get("height")))
    if None in {x, y, w, h}:
        return None
    return _normalize_placement({"x": x, "y": y, "w": w, "h": h})


def _optional_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_shape_id(value: str) -> str:
    value = str(value or "").strip()
    value = value.replace("mxgraph.", "")
    value = value.split(".")[-1]
    value = re.sub(r"[^A-Za-z0-9_ -]+", "", value)
    return re.sub(r"[\s-]+", "_", value).lower()


def _num(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clean_mermaid(value: str) -> str:
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:mermaid)?", "", value).strip()
        value = re.sub(r"```$", "", value).strip()
    return value


def _clean_label(value: str) -> str:
    value = html.unescape(str(value or "")).strip()
    value = value.strip('"').strip("'")
    return re.sub(r"\s+", " ", value)


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned or uuid.uuid4().hex[:8]


def _is_cloud_label(label: str) -> bool:
    cloud_tokens = ("cloud", "cdn", "route", "lb", "balancer", "gateway")
    return any(token in label.lower() for token in cloud_tokens)


def _is_data_label(label: str) -> bool:
    data_tokens = ("data", "db", "rds", "s3", "storage", "database")
    return any(token in label.lower() for token in data_tokens)


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = re.split(r"\s+", str(text or "").strip())
    if not words:
        return []
    lines = []
    current = ""
    probe = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(probe)
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend([
            "C:/Windows/Fonts/malgunbd.ttf",
            "C:/Windows/Fonts/NotoSansKR-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ])
    candidates.extend([
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()
