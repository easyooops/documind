"""Agent configuration and prompt loader with format-aware resolution.

Model resolution:
- USE_DEFAULT_MODELS=true (from .env):
    Model name is overridden by DEFAULT_LLM_MODEL / DEFAULT_VLM_MODEL / DEFAULT_IMAGE_MODEL
    based on the agent's 'provider_type' field.
- USE_DEFAULT_MODELS=false:
    Each agent uses its own 'model' from its config JSON.

Path resolution (format_id parameter):
- If format_id is provided, look in formats/{format_id}/agents/configs/ first.
- Fallback to the shared agents/configs/ directory.
- Same logic applies to prompts.

All other settings (temperature, max_tokens, top_p, retry, parallel, etc.)
ALWAYS come from the individual agent config file regardless of the toggle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

_AGENTS_DIR = Path(__file__).parent
_CONFIGS_DIR = _AGENTS_DIR / "configs"
_PROMPTS_DIR = _AGENTS_DIR / "prompts"
_FORMATS_DIR = _AGENTS_DIR.parent / "formats"

_config_cache: dict[str, dict] = {}
_prompt_cache: dict[str, str] = {}


def _resolve_config_path(agent_name: str, format_id: str | None = None) -> Path | None:
    """Find the config JSON file, preferring format-specific path."""
    if format_id:
        fmt_path = _FORMATS_DIR / format_id / "agents" / "configs" / f"{agent_name}.json"
        if fmt_path.exists():
            return fmt_path

    shared_path = _CONFIGS_DIR / f"{agent_name}.json"
    if shared_path.exists():
        return shared_path

    return None


def _resolve_prompt_path(agent_name: str, format_id: str | None = None) -> Path | None:
    """Find the prompt markdown file, preferring format-specific path."""
    if format_id:
        fmt_path = _FORMATS_DIR / format_id / "agents" / "prompts" / f"{agent_name}.md"
        if fmt_path.exists():
            return fmt_path

    shared_path = _PROMPTS_DIR / f"{agent_name}.md"
    if shared_path.exists():
        return shared_path

    return None


def _cache_key(agent_name: str, format_id: str | None) -> str:
    """Generate a cache key incorporating format context."""
    if format_id:
        return f"{format_id}::{agent_name}"
    return agent_name


def load_agent_config(agent_name: str, format_id: str | None = None) -> dict[str, Any]:
    """Load agent configuration from its JSON file.

    Args:
        agent_name: The agent identifier (e.g. "layout_composer").
        format_id: Optional format (e.g. "pptx") to search format-specific configs first.

    The returned dict always contains the final 'model' to use,
    resolved via the USE_DEFAULT_MODELS toggle.
    """
    key = _cache_key(agent_name, format_id)
    if key in _config_cache:
        return _config_cache[key]

    config_path = _resolve_config_path(agent_name, format_id)
    if config_path is None:
        logger.warning("agent_config.not_found", agent=agent_name, format_id=format_id)
        config: dict[str, Any] = {}
    else:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

    # Resolve model name based on default toggle
    provider_type = config.get("provider_type", "llm")
    default_model = settings.get_default_model(provider_type)

    if default_model is not None:
        config["_resolved_model"] = default_model
    else:
        config["_resolved_model"] = config.get("llm", {}).get("model", "gpt-4o")

    _config_cache[key] = config
    return config


def load_agent_prompt(agent_name: str, format_id: str | None = None) -> str:
    """Load agent system prompt from a markdown file.

    Args:
        agent_name: The agent identifier.
        format_id: Optional format to search format-specific prompts first.
    """
    key = _cache_key(agent_name, format_id)
    if key in _prompt_cache:
        return _prompt_cache[key]

    prompt_path = _resolve_prompt_path(agent_name, format_id)
    if prompt_path is None:
        logger.warning("agent_prompt.not_found", agent=agent_name, format_id=format_id)
        return ""

    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()

    _prompt_cache[key] = prompt
    return prompt


def get_llm_for_agent(agent_name: str, format_id: str | None = None):
    """Create a LangChain ChatModel for the specified agent.

    Model name: from .env defaults (if enabled) or agent's own config.
    Parameters (temperature, max_tokens, top_p): always from agent config.

    Args:
        agent_name: The agent identifier.
        format_id: Optional format for format-specific config resolution.
    """
    from src.infrastructure.llm import create_llm

    config = load_agent_config(agent_name, format_id=format_id)
    llm_params = config.get("llm", {})

    model = config["_resolved_model"]
    temperature = llm_params.get("temperature", 0.7)
    max_tokens = llm_params.get("max_tokens", 4096)
    top_p = llm_params.get("top_p", 1.0)

    return create_llm(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
    )


def get_retry_config(agent_name: str, format_id: str | None = None) -> dict[str, int]:
    """Get retry configuration for the agent."""
    config = load_agent_config(agent_name, format_id=format_id)
    return config.get("retry", {"max_attempts": 2, "backoff_seconds": 1})


def reload_config(agent_name: str | None = None) -> None:
    """Clear cached config/prompt. None = clear all."""
    if agent_name:
        keys_to_remove = [k for k in _config_cache if k.endswith(agent_name) or k == agent_name]
        for k in keys_to_remove:
            _config_cache.pop(k, None)
        keys_to_remove = [k for k in _prompt_cache if k.endswith(agent_name) or k == agent_name]
        for k in keys_to_remove:
            _prompt_cache.pop(k, None)
    else:
        _config_cache.clear()
        _prompt_cache.clear()
