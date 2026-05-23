"""Lenient JSON parsing helpers for LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def parse_llm_json(raw: str, *, fallback: Any | None = None) -> Any:
    """Parse JSON from an LLM response with small, deterministic repairs."""
    candidates = _candidates(raw)
    last_error: Exception | None = None

    for candidate in candidates:
        for repaired in (candidate, _repair_json(candidate)):
            try:
                return json.loads(repaired)
            except json.JSONDecodeError as exc:
                last_error = exc

    if fallback is not None:
        return fallback
    if last_error:
        raise last_error
    raise json.JSONDecodeError("No JSON object found", raw, 0)


def _candidates(raw: str) -> list[str]:
    text = raw.strip()
    candidates: list[str] = []

    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    if text:
        candidates.append(text)

    obj_start = text.find("{")
    obj_end = text.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        candidates.append(text[obj_start : obj_end + 1])

    arr_start = text.find("[")
    arr_end = text.rfind("]")
    if arr_start >= 0 and arr_end > arr_start:
        candidates.append(text[arr_start : arr_end + 1])

    deduped = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def _repair_json(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_-]*)(\s*:)", r'\1"\2"\3', text)
    text = text.replace("None", "null").replace("True", "true").replace("False", "false")

    if text.count("{") > text.count("}"):
        text += "}" * (text.count("{") - text.count("}"))
    if text.count("[") > text.count("]"):
        text += "]" * (text.count("[") - text.count("]"))
    return text
