"""SDK icon helpers without SQLite or startup preloading.

The service runtime keeps an icon registry in SQLite. The PyPI SDK deliberately
uses deterministic file-cache paths and lightweight fallback PNGs instead, so
document generation has no local database bootstrap step.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from src.core.logging import get_logger

logger = get_logger(__name__)

ICON_CACHE_DIR = Path("data/cache/icons")
DEFAULT_ICON_SIZE = 32
DEFAULT_ICON_COLORS = ("1E293B", "FFFFFF", "0F766E", "10B981", "3B82F6")
SAFE_ICON_FALLBACKS = ("document", "layers", "chart-line", "database", "shield")

RECOMMENDED_ICONS = {
    "database": "mdi:database",
    "chart": "mdi:chart-bar",
    "chart_line": "mdi:chart-line",
    "people": "mdi:account-group",
    "person": "mdi:account",
    "globe": "mdi:earth",
    "shield": "mdi:shield-check",
    "rocket": "mdi:rocket-launch",
    "target": "mdi:target",
    "gear": "mdi:cog",
    "document": "mdi:file-document",
    "building": "mdi:office-building",
    "check": "mdi:check-circle",
    "warning": "mdi:alert",
    "cloud": "mdi:cloud",
    "lock": "mdi:lock",
    "search": "mdi:magnify",
    "server": "mdi:server",
    "api": "mdi:api",
    "analytics": "mdi:google-analytics",
    "layers": "mdi:layers",
    "brain": "mdi:brain",
}

ICON_NAME_ALIASES = {
    "bar-chart": "chart-bar",
    "bar-chart-2": "chart-bar",
    "line-chart": "chart-line",
    "pie-chart": "chart-pie",
    "users": "account-group",
    "user": "account",
    "settings": "cog",
    "zap": "flash",
    "alert-triangle": "alert",
}


@dataclass
class IconAsset:
    icon_id: str
    alias: str
    color: str
    size: int
    html_path: Path | None = None
    pptx_path: Path | None = None
    html_type: str = "png"
    pptx_type: str = "png"
    source: str = "fallback"


def normalize_icon_id(icon_name: str) -> str:
    normalized = (icon_name or "").strip().replace("_", "-")
    icon_id = RECOMMENDED_ICONS.get(normalized, normalized)
    if ":" not in icon_id:
        icon_id = f"mdi:{icon_id}"
    prefix, name = icon_id.split(":", 1)
    if prefix == "mdi":
        name = ICON_NAME_ALIASES.get(name, name)
    return f"{prefix}:{name}"


def normalize_icon_color(color: str | None, fallback: str = "1E293B") -> str:
    value = (color or fallback).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) < 6 or any(char not in "0123456789abcdefABCDEF" for char in value[:6]):
        return fallback.upper()
    return value[:6].upper()


def _icon_cache_path(icon_id: str, color: str, size: int, extension: str) -> Path:
    key = hashlib.md5(f"{icon_id}_{color}_{size}_{extension}".encode()).hexdigest()
    return ICON_CACHE_DIR / f"{key}.{extension}"


def ensure_icon_registry() -> None:
    """Compatibility no-op for the SDK runtime."""
    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)


async def preload_recommended_icons(
    *,
    colors: list[str] | tuple[str, ...] | None = None,
    size: int = DEFAULT_ICON_SIZE,
    limit: int | None = None,
    force: bool = False,
    concurrency: int = 8,
    progress_callback: Callable[..., None] | None = None,
) -> dict[str, int]:
    """SDK builds do not warm remote icon registries."""
    ensure_icon_registry()
    selected = list(RECOMMENDED_ICONS)[:limit] if limit is not None else list(RECOMMENDED_ICONS)
    total = len(selected) * len(colors or DEFAULT_ICON_COLORS)
    if progress_callback:
        progress_callback(total=total, completed=0, created=0, skipped=total, failed=0)
        progress_callback(total=total, completed=total, created=0, skipped=total, failed=0)
    return {"total": total, "created": 0, "skipped": total, "failed": 0}


async def ensure_safe_icon_assets(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
) -> tuple[str, IconAsset]:
    safe_name = icon_name or SAFE_ICON_FALLBACKS[0]
    path = get_fallback_icon_path(safe_name, color=color, size=size)
    return safe_name, IconAsset(
        icon_id=normalize_icon_id(safe_name),
        alias=safe_name,
        color=normalize_icon_color(color),
        size=size,
        html_path=path,
        pptx_path=path,
        source="fallback",
    )


def get_icon_asset_path(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
    *,
    target: Literal["html", "pptx"] = "pptx",
) -> Path | None:
    icon_id = normalize_icon_id(icon_name)
    path = _icon_cache_path(f"fallback:{icon_id}", normalize_icon_color(color), size, "png")
    return path if path.exists() else None


def get_icon_path(icon_name: str, color: str = "1E293B", size: int = 48) -> Path | None:
    return get_icon_asset_path(icon_name, color=color, size=size, target="pptx")


def get_icon_assets_sync(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
) -> IconAsset | None:
    path = get_icon_asset_path(icon_name, color=color, size=size)
    if not path:
        return None
    return IconAsset(
        icon_id=normalize_icon_id(icon_name),
        alias=icon_name,
        color=normalize_icon_color(color),
        size=size,
        html_path=path,
        pptx_path=path,
    )


def get_fallback_icon_path(icon_name: str, color: str = "1E293B", size: int = 32) -> Path | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    cache_path = _icon_cache_path(f"fallback:{icon_id}", color, size, "png")
    if cache_path.exists():
        return cache_path

    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    image_size = max(size, 32)
    image = Image.new("RGBA", (image_size, image_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    stroke = max(2, image_size // 12)
    margin = max(4, image_size // 7)
    center = image_size / 2
    name = icon_id.split(":", 1)[1]

    if "database" in name:
        draw.ellipse([margin, margin, image_size - margin, image_size * 0.36], outline=rgb, width=stroke)
        draw.line([margin, image_size * 0.18, margin, image_size * 0.76], fill=rgb, width=stroke)
        draw.line([image_size - margin, image_size * 0.18, image_size - margin, image_size * 0.76], fill=rgb, width=stroke)
        draw.ellipse([margin, image_size * 0.58, image_size - margin, image_size - margin], outline=rgb, width=stroke)
    elif "chart" in name or "trend" in name:
        points = [
            (margin, image_size - margin),
            (image_size * 0.38, image_size * 0.62),
            (image_size * 0.58, image_size * 0.7),
            (image_size - margin, margin),
        ]
        draw.line(points, fill=rgb, width=stroke)
        draw.line([image_size - margin, margin, image_size - margin, image_size * 0.34], fill=rgb, width=stroke)
        draw.line([image_size - margin, margin, image_size * 0.66, margin], fill=rgb, width=stroke)
    elif "shield" in name:
        draw.polygon(
            [(center, margin), (image_size - margin, image_size * 0.28), (image_size * 0.75, image_size - margin), (center, image_size * 0.82), (image_size * 0.25, image_size - margin), (margin, image_size * 0.28)],
            outline=rgb,
        )
    else:
        draw.rounded_rectangle(
            [margin, margin, image_size - margin, image_size - margin],
            radius=max(4, image_size // 6),
            outline=rgb,
            width=stroke,
        )
        draw.line([center, margin * 1.8, center, image_size - margin * 1.8], fill=rgb, width=stroke)
        draw.line([margin * 1.8, center, image_size - margin * 1.8, center], fill=rgb, width=stroke)

    image.save(cache_path, format="PNG")
    logger.debug("iconify.sdk_fallback_created", icon=icon_id, path=str(cache_path))
    return cache_path


def list_recommended_icon_names() -> list[str]:
    return sorted(RECOMMENDED_ICONS)
