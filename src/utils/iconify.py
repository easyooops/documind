"""Iconify API integration for downloading SVG icons and converting to PNG for PPTX slides.

Uses the free Iconify API (https://api.iconify.design) — no API key required.
Supports 200,000+ icons from Material Design, Phosphor, Tabler, Lucide, etc.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Optional

import httpx

from src.core.logging import get_logger

logger = get_logger(__name__)

ICONIFY_API_BASE = "https://api.iconify.design"
ICON_CACHE_DIR = Path("data/cache/icons")
ICON_DB_PATH = ICON_CACHE_DIR / "icons.db"
DEFAULT_ICON_SIZE = 32
DEFAULT_ICON_COLORS = (
    "1E293B",
    "FFFFFF",
    "0F766E",
    "10B981",
    "3B82F6",
    "F43F5E",
    "F59E0B",
)

ICON_SETS = {
    "mdi": "Material Design Icons",
    "ph": "Phosphor Icons",
    "tabler": "Tabler Icons",
    "lucide": "Lucide Icons",
    "carbon": "Carbon Icons",
    "fluent": "Fluent UI Icons",
    "heroicons": "Hero Icons",
    "bi": "Bootstrap Icons",
}

RECOMMENDED_ICONS = {
    "database": "mdi:database",
    "chart": "mdi:chart-bar",
    "chart_line": "mdi:chart-line",
    "chart_pie": "mdi:chart-pie",
    "people": "mdi:account-group",
    "person": "mdi:account",
    "globe": "mdi:earth",
    "shield": "mdi:shield-check",
    "rocket": "mdi:rocket-launch",
    "target": "mdi:target",
    "gear": "mdi:cog",
    "settings": "mdi:cog",
    "money": "mdi:currency-usd",
    "calendar": "mdi:calendar",
    "clock": "mdi:clock-outline",
    "document": "mdi:file-document",
    "building": "mdi:office-building",
    "graph": "mdi:graph",
    "checkmark": "mdi:check-circle",
    "check": "mdi:check-circle",
    "warning": "mdi:alert",
    "star": "mdi:star",
    "heart": "mdi:heart",
    "cloud": "mdi:cloud",
    "cloud_upload": "mdi:cloud-upload",
    "lock": "mdi:lock",
    "link": "mdi:link",
    "mail": "mdi:email",
    "phone": "mdi:phone",
    "lightbulb": "mdi:lightbulb",
    "idea": "mdi:lightbulb",
    "trending_up": "mdi:trending-up",
    "trending_down": "mdi:trending-down",
    "arrow_right": "mdi:arrow-right",
    "arrow_left": "mdi:arrow-left",
    "code": "mdi:code-tags",
    "server": "mdi:server",
    "cpu": "mdi:cpu-64-bit",
    "network": "mdi:network",
    "api": "mdi:api",
    "pipeline": "mdi:pipe",
    "data_flow": "mdi:transit-connection-variant",
    "analytics": "mdi:google-analytics",
    "dashboard": "mdi:view-dashboard",
    "layers": "mdi:layers",
    "cube": "mdi:cube",
    "puzzle": "mdi:puzzle",
    "key": "mdi:key",
    "search": "mdi:magnify",
    "filter": "mdi:filter",
    "download": "mdi:download",
    "upload": "mdi:upload",
    "refresh": "mdi:refresh",
    "play": "mdi:play-circle",
    "stop": "mdi:stop-circle",
    "flash": "mdi:flash",
    "fire": "mdi:fire",
    "tree": "mdi:file-tree",
    "folder": "mdi:folder",
    "terminal": "mdi:console",
    "robot": "mdi:robot",
    "brain": "mdi:brain",
    "chip": "mdi:chip",
    "wifi": "mdi:wifi",
    "bluetooth": "mdi:bluetooth",
    "battery": "mdi:battery",
    "camera": "mdi:camera",
    "video": "mdi:video",
    "microphone": "mdi:microphone",
    "speaker": "mdi:speaker",
    "thumbs_up": "mdi:thumb-up",
    "thumbs_down": "mdi:thumb-down",
    "bookmark": "mdi:bookmark",
    "tag": "mdi:tag",
    "flag": "mdi:flag",
    "pin": "mdi:map-marker",
    "home": "mdi:home",
    "wrench": "mdi:wrench",
    "hammer": "mdi:hammer",
    "scissors": "mdi:content-cut",
    "megaphone": "mdi:bullhorn",
    "trophy": "mdi:trophy",
    "crown": "mdi:crown",
    "diamond": "mdi:diamond-stone",
    "infinity": "mdi:infinity",
    "recycle": "mdi:recycle",
    "leaf": "mdi:leaf",
    "water": "mdi:water",
    "sun": "mdi:white-balance-sunny",
    "moon": "mdi:moon-waxing-crescent",
}

# Fallback mapping: LLM often uses Lucide/Feather icon names — map to MDI equivalents
ICON_NAME_ALIASES = {
    "zap": "flash",
    "alert-triangle": "alert",
    "bar-chart-2": "chart-bar",
    "bar-chart": "chart-bar",
    "x-circle": "close-circle",
    "x": "close",
    "check-circle": "check-circle",
    "alert-circle": "alert-circle-outline",
    "arrow-right": "arrow-right",
    "arrow-left": "arrow-left",
    "arrow-up": "arrow-up",
    "arrow-down": "arrow-down",
    "chevron-right": "chevron-right",
    "chevron-left": "chevron-left",
    "external-link": "open-in-new",
    "trash": "delete",
    "trash-2": "delete",
    "edit": "pencil",
    "edit-2": "pencil",
    "edit-3": "pencil",
    "plus-circle": "plus-circle",
    "minus-circle": "minus-circle",
    "info": "information",
    "file-text": "file-document",
    "file": "file",
    "users": "account-group",
    "user": "account",
    "user-check": "account-check",
    "activity": "pulse",
    "trending-up": "trending-up",
    "trending-down": "trending-down",
    "pie-chart": "chart-pie",
    "bar-chart-3": "chart-bar",
    "line-chart": "chart-line",
    "layout": "view-dashboard",
    "layers": "layers",
    "package": "package-variant",
    "box": "cube",
    "grid": "view-grid",
    "list": "format-list-bulleted",
    "clock": "clock-outline",
    "calendar": "calendar",
    "map-pin": "map-marker",
    "navigation": "navigation",
    "globe": "earth",
    "wifi": "wifi",
    "bluetooth": "bluetooth",
    "monitor": "monitor",
    "smartphone": "cellphone",
    "tablet": "tablet",
    "cpu": "cpu-64-bit",
    "hard-drive": "harddisk",
    "server": "server",
    "database": "database",
    "cloud": "cloud",
    "cloud-upload": "cloud-upload",
    "cloud-download": "cloud-download",
    # Non-existent/variant MDI names observed in runtime logs
    "cloud-network": "cloud",
    "building-network": "office-building",
    "document-check": "file-document",
    "download-simple": "download",
    "server-check": "server",
    "lock": "lock",
    "unlock": "lock-open",
    "shield": "shield-check",
    "key": "key",
    "eye": "eye",
    "eye-off": "eye-off",
    "bell": "bell",
    "message-circle": "message",
    "mail": "email",
    "send": "send",
    "share": "share-variant",
    "bookmark": "bookmark",
    "heart": "heart",
    "star": "star",
    "thumbs-up": "thumb-up",
    "thumbs-down": "thumb-down",
    "flag": "flag",
    "award": "trophy",
    "target": "target",
    "crosshair": "crosshairs",
    "settings": "cog",
    "tool": "wrench",
    "scissors": "content-cut",
    "copy": "content-copy",
    "clipboard": "clipboard",
    "terminal": "console",
    "code": "code-tags",
    "git-branch": "source-branch",
    "git-merge": "source-merge",
    "sparkles": "creation",
    "lightning": "flash",
    "bolt": "flash",
    "fire": "fire",
    "sun": "white-balance-sunny",
    "moon": "moon-waxing-crescent",
    "mountain": "image-filter-hdr",
    "building": "office-building",
    "home": "home",
    "briefcase": "briefcase",
    "dollar-sign": "currency-usd",
    "credit-card": "credit-card",
    "shopping-cart": "cart",
    "truck": "truck",
    "plane": "airplane",
    "rocket": "rocket-launch",
    "anchor": "anchor",
    "compass": "compass",
    "map": "map",
    "maximize": "fullscreen",
    "minimize": "fullscreen-exit",
    "refresh-cw": "refresh",
    "rotate-cw": "rotate-right",
    "save": "content-save",
    "printer": "printer",
    "image": "image",
    "camera": "camera",
    "video": "video",
    "music": "music",
    "headphones": "headphones",
    "mic": "microphone",
    "volume-2": "volume-high",
    "power": "power",
    "battery": "battery",
    "radio": "radio",
    "aperture": "aperture",
    "circle": "circle",
    "square": "square",
    "triangle": "triangle",
    "hexagon": "hexagon",
    "octagon": "octagon",
}


@dataclass(frozen=True)
class IconAsset:
    """Registered HTML/PPTX icon artifact pair."""

    icon_id: str
    alias: str
    color: str
    size: int
    html_path: Path | None
    pptx_path: Path | None
    html_type: str = "svg"
    pptx_type: str = "png"
    source: str = "iconify"


SAFE_ICON_FALLBACKS = (
    "lightbulb",
    "target",
    "layers",
    "chart-line",
    "brain",
    "rocket",
    "database",
    "shield",
)


def normalize_icon_id(icon_name: str) -> str:
    """Normalize planner-friendly aliases into one Iconify identifier."""
    normalized = (icon_name or "").strip().replace("_", "-")
    icon_id = RECOMMENDED_ICONS.get(normalized, normalized)
    if ":" not in icon_id:
        icon_id = f"mdi:{icon_id}"
    prefix, name = icon_id.split(":", 1)
    if prefix == "mdi":
        name = ICON_NAME_ALIASES.get(name, name)
    return f"{prefix}:{name}"


def normalize_icon_color(color: str | None, fallback: str = "1E293B") -> str:
    """Return a six-character cache-safe RGB value."""
    value = (color or fallback).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) < 6 or any(char not in "0123456789abcdefABCDEF" for char in value[:6]):
        return fallback.upper()
    return value[:6].upper()


def _icon_cache_path(icon_id: str, color: str, size: int, extension: str) -> Path:
    suffix = "_png" if extension == "png" else ""
    key = hashlib.md5(f"{icon_id}_{color}_{size}{suffix}".encode()).hexdigest()
    return ICON_CACHE_DIR / f"{key}.{extension}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect_icon_db() -> sqlite3.Connection:
    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ICON_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_icon_registry() -> None:
    """Create the local icon registry used to map HTML and PPTX assets."""
    with _connect_icon_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS icons (
                icon_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                color TEXT NOT NULL,
                size INTEGER NOT NULL,
                html_path TEXT,
                html_type TEXT NOT NULL DEFAULT 'svg',
                pptx_path TEXT,
                pptx_type TEXT NOT NULL DEFAULT 'png',
                source TEXT NOT NULL DEFAULT 'iconify',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (icon_id, color, size)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_icons_alias ON icons(alias)")
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(icons)").fetchall()
        }
        if "source" not in columns:
            conn.execute("ALTER TABLE icons ADD COLUMN source TEXT NOT NULL DEFAULT 'iconify'")


def _path_or_none(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.exists() else None


def _record_to_asset(row: sqlite3.Row | None) -> IconAsset | None:
    if row is None:
        return None
    return IconAsset(
        icon_id=str(row["icon_id"]),
        alias=str(row["alias"]),
        color=str(row["color"]),
        size=int(row["size"]),
        html_path=_path_or_none(row["html_path"]),
        pptx_path=_path_or_none(row["pptx_path"]),
        html_type=str(row["html_type"]),
        pptx_type=str(row["pptx_type"]),
        source=str(row["source"]) if "source" in row.keys() else "iconify",
    )


def _get_registered_icon(icon_name: str, color: str, size: int) -> IconAsset | None:
    ensure_icon_registry()
    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    with _connect_icon_db() as conn:
        row = conn.execute(
            """
            SELECT icon_id, alias, color, size, html_path, html_type, pptx_path, pptx_type, source
            FROM icons
            WHERE icon_id = ? AND color = ? AND size = ?
            """,
            (icon_id, color, size),
        ).fetchone()
    asset = _record_to_asset(row)
    if asset and (asset.html_path or asset.pptx_path):
        return asset
    return None


def _upsert_icon_record(
    icon_name: str,
    color: str,
    size: int,
    *,
    html_path: Path | None,
    pptx_path: Path | None,
    html_type: str = "svg",
    pptx_type: str = "png",
    source: str = "iconify",
) -> IconAsset:
    ensure_icon_registry()
    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    with _connect_icon_db() as conn:
        conn.execute(
            """
            INSERT INTO icons (
                icon_id, alias, color, size, html_path, html_type,
                pptx_path, pptx_type, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(icon_id, color, size) DO UPDATE SET
                alias = excluded.alias,
                html_path = COALESCE(excluded.html_path, icons.html_path),
                html_type = excluded.html_type,
                pptx_path = COALESCE(excluded.pptx_path, icons.pptx_path),
                pptx_type = excluded.pptx_type,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                icon_id,
                icon_name,
                color,
                size,
                str(html_path) if html_path else None,
                html_type,
                str(pptx_path) if pptx_path else None,
                pptx_type,
                source,
                _utc_now(),
            ),
        )
    return IconAsset(
        icon_id=icon_id,
        alias=icon_name,
        color=color,
        size=size,
        html_path=html_path if html_path and html_path.exists() else None,
        pptx_path=pptx_path if pptx_path and pptx_path.exists() else None,
        html_type=html_type,
        pptx_type=pptx_type,
        source=source,
    )


def _is_white_icon(color: str) -> bool:
    color = normalize_icon_color(color)
    return color.upper() in {"FFFFFF", "F8FAFC", "F9FAFB"}


def _transparent_png(png_data: bytes, requested_color: str) -> bytes:
    """Normalize converted icon PNGs so PPTX never receives a white tile."""
    if _is_white_icon(requested_color):
        return png_data

    try:
        from PIL import Image
    except ImportError:
        return png_data

    try:
        with Image.open(io.BytesIO(png_data)) as image:
            rgba = image.convert("RGBA")
            pixels = rgba.load()
            width, height = rgba.size
            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]
                    if a and r >= 245 and g >= 245 and b >= 245:
                        pixels[x, y] = (r, g, b, 0)
            output = io.BytesIO()
            rgba.save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return png_data


async def fetch_icon_svg(icon_name: str, color: str = "1E293B", size: int = 48) -> Optional[bytes]:
    """Fetch an SVG icon from Iconify API.

    Args:
        icon_name: Short name (e.g., 'database') or full Iconify ID (e.g., 'mdi:database')
        color: Hex color without '#' prefix
        size: Icon size in pixels

    Returns:
        SVG bytes or None if fetch failed
    """
    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    prefix, name = icon_id.split(":", 1)

    url = f"{ICONIFY_API_BASE}/{prefix}/{name}.svg?color=%23{color}&width={size}&height={size}"
    cache_path = _icon_cache_path(icon_id, color, size, "svg")

    if cache_path.exists():
        return cache_path.read_bytes()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                svg_data = response.content
                ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(svg_data)
                logger.info("iconify.fetched", icon=icon_id, size=size)
                return svg_data
            else:
                logger.warning("iconify.fetch_failed", icon=icon_id, status=response.status_code)
                return None
    except Exception as e:
        logger.warning("iconify.error", icon=icon_id, error=str(e)[:100])
        return None


async def fetch_icon_png(icon_name: str, color: str = "1E293B", size: int = 48) -> Optional[bytes]:
    """Fetch icon SVG and convert to PNG for PowerPoint compatibility.

    Uses svglib+reportlab (pure Python, cross-platform) for SVG→PNG conversion.
    Falls back to cairosvg if available, then Pillow placeholder.
    """
    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    png_cache_path = _icon_cache_path(icon_id, color, size, "png")
    if png_cache_path.exists():
        png_data = _transparent_png(png_cache_path.read_bytes(), color)
        png_cache_path.write_bytes(png_data)
        return png_data

    svg_data = await fetch_icon_svg(icon_name, color, size)
    if not svg_data:
        return None

    # Method 1: svglib + reportlab (pure Python, best cross-platform)
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        svg_path = _icon_cache_path(icon_id, color, size, "svg")
        if not svg_path.exists():
            svg_path.write_bytes(svg_data)

        drawing = svg2rlg(str(svg_path))
        if drawing:
            render_size = max(size, 64)
            sx = render_size / drawing.width if drawing.width else 1
            sy = render_size / drawing.height if drawing.height else 1
            drawing.width = render_size
            drawing.height = render_size
            drawing.scale(sx, sy)
            png_data = renderPM.drawToString(drawing, fmt="PNG", dpi=72)
            png_data = _transparent_png(png_data, color)
            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            png_cache_path.write_bytes(png_data)
            logger.info("iconify.png_converted", icon=icon_id, method="svglib")
            return png_data
    except Exception as e:
        logger.debug("iconify.svglib_failed", icon=icon_id, error=str(e)[:60])

    # Method 2: cairosvg
    try:
        import cairosvg
        png_data = cairosvg.svg2png(
            bytestring=svg_data,
            output_width=size,
            output_height=size,
            background_color=None,
        )
        png_data = _transparent_png(png_data, color)
        png_cache_path.write_bytes(png_data)
        return png_data
    except (ImportError, Exception):
        pass

    logger.warning("iconify.png_conversion_failed", icon=icon_id)
    return None


async def ensure_icon_assets(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
    *,
    force: bool = False,
) -> IconAsset:
    """Ensure both browser-friendly SVG and PPTX-friendly PNG assets exist."""
    color = normalize_icon_color(color)
    size = int(size or DEFAULT_ICON_SIZE)
    if not force:
        registered = _get_registered_icon(icon_name, color, size)
        if registered:
            return registered

    icon_id = normalize_icon_id(icon_name)
    svg_path = _icon_cache_path(icon_id, color, size, "svg")
    png_path = _icon_cache_path(icon_id, color, size, "png")

    svg_data = await fetch_icon_svg(icon_name, color=color, size=size)
    if not svg_data and not svg_path.exists():
        fallback = get_fallback_icon_path(icon_name, color=color, size=size)
        return _upsert_icon_record(
            icon_name,
            color,
            size,
            html_path=fallback,
            pptx_path=fallback,
            html_type="png",
            source="fallback",
        )

    png_data = await fetch_icon_png(icon_name, color=color, size=size)
    if not png_data and not png_path.exists():
        fallback = get_fallback_icon_path(icon_name, color=color, size=size)
        return _upsert_icon_record(
            icon_name,
            color,
            size,
            html_path=svg_path if svg_path.exists() else fallback,
            pptx_path=fallback,
            html_type="svg" if svg_path.exists() else "png",
            source="fallback",
        )

    return _upsert_icon_record(
        icon_name,
        color,
        size,
        html_path=svg_path if svg_path.exists() else None,
        pptx_path=png_path if png_path.exists() else None,
    )


async def ensure_safe_icon_assets(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
) -> tuple[str, IconAsset]:
    """Resolve an icon to a guaranteed visible Iconify-backed asset.

    If the requested icon is unknown or only produced a generated fallback, a
    curated safe icon is used instead. This prevents blank/square placeholders
    from entering HTML or PPTX output.
    """
    candidates = [icon_name, *SAFE_ICON_FALLBACKS]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = (candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        asset = await ensure_icon_assets(normalized, color=color, size=size)
        if asset.html_path and asset.pptx_path and (
            asset.source != "fallback" or asset.html_type == "svg"
        ):
            return normalized, asset

    fallback_name = SAFE_ICON_FALLBACKS[0]
    return fallback_name, await ensure_icon_assets(fallback_name, color=color, size=size)


async def preload_recommended_icons(
    *,
    colors: list[str] | tuple[str, ...] | None = None,
    size: int = DEFAULT_ICON_SIZE,
    limit: int | None = None,
    force: bool = False,
    concurrency: int = 8,
) -> dict[str, int]:
    """Warm the icon registry on server startup.

    The operation is idempotent: registered icons with existing SVG and PNG assets
    are skipped, so subsequent server starts avoid repeated network calls.
    """
    ensure_icon_registry()
    selected_colors = tuple(normalize_icon_color(color) for color in (colors or DEFAULT_ICON_COLORS))
    icon_names = list(RECOMMENDED_ICONS.keys())
    if limit is not None:
        icon_names = icon_names[: max(0, limit)]

    pairs = [(icon_name, color) for icon_name in icon_names for color in selected_colors]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    skipped = 0
    created = 0
    failed = 0

    async def _ensure(icon_name: str, color: str) -> None:
        nonlocal skipped, created, failed
        if not force and _get_registered_icon(icon_name, color, size):
            skipped += 1
            return
        async with semaphore:
            try:
                asset = await ensure_icon_assets(icon_name, color=color, size=size, force=force)
                if asset.pptx_path or asset.html_path:
                    created += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                logger.debug("iconify.preload_failed", icon=icon_name, color=color, error=str(exc)[:100])

    await asyncio.gather(*(_ensure(icon_name, color) for icon_name, color in pairs))
    logger.info("iconify.preload_complete", total=len(pairs), created=created, skipped=skipped, failed=failed)
    return {"total": len(pairs), "created": created, "skipped": skipped, "failed": failed}


def get_icon_asset_path(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
    *,
    target: Literal["html", "pptx"] = "pptx",
) -> Optional[Path]:
    """Get a registered icon artifact path for the requested rendering target."""
    asset = _get_registered_icon(icon_name, color, size)
    if asset:
        if target == "html":
            return asset.html_path or asset.pptx_path
        return asset.pptx_path or asset.html_path

    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    extension = "svg" if target == "html" else "png"
    path = _icon_cache_path(icon_id, color, size, extension)
    if path.exists():
        return path
    return None


def get_icon_path(icon_name: str, color: str = "1E293B", size: int = 48) -> Optional[Path]:
    """Get cached icon path synchronously (for use in mapper). Returns None if not cached.

    Searches for the exact rendered color and size, so preview and PPTX output
    cannot silently disagree about icon styling.
    """
    return get_icon_asset_path(icon_name, color=color, size=size, target="pptx") or get_icon_asset_path(
        icon_name, color=color, size=size, target="html"
    )


def get_icon_assets_sync(
    icon_name: str,
    color: str = "1E293B",
    size: int = DEFAULT_ICON_SIZE,
) -> IconAsset | None:
    """Return a registered icon asset pair without performing network I/O."""
    return _get_registered_icon(icon_name, color, size)


def get_fallback_icon_path(icon_name: str, color: str = "1E293B", size: int = 32) -> Path | None:
    """Create a simple visible fallback icon when Iconify is unavailable."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    cache_path = _icon_cache_path(f"fallback-v2:{icon_id}", color, size, "png")
    if cache_path.exists():
        return cache_path

    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    image_size = max(size, 32)
    img = Image.new("RGBA", (image_size, image_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    stroke = max(2, image_size // 12)
    _draw_fallback_symbol(draw, icon_id.split(":", 1)[1], rgb, image_size, stroke)
    img.save(cache_path, format="PNG")
    return cache_path


def _draw_fallback_symbol(draw, icon_name: str, rgb: tuple[int, int, int], size: int, stroke: int) -> None:
    """Draw a transparent, non-boxy semantic fallback symbol."""
    color = rgb + (255,)
    m = max(4, size // 7)
    c = size / 2
    name = icon_name.replace("_", "-")

    if "target" in name:
        draw.ellipse([m, m, size - m, size - m], outline=color, width=stroke)
        draw.ellipse([size * 0.34, size * 0.34, size * 0.66, size * 0.66], outline=color, width=stroke)
        draw.line([c, m, c, size - m], fill=color, width=max(1, stroke // 2))
        draw.line([m, c, size - m, c], fill=color, width=max(1, stroke // 2))
        return
    if "layer" in name:
        draw.polygon([(c, m), (size - m, c * 0.85), (c, size * 0.62), (m, c * 0.85)], outline=color)
        draw.line([m, c * 1.05, c, size * 0.82, size - m, c * 1.05], fill=color, width=stroke)
        return
    if "chart" in name or "trend" in name:
        points = [(m, size - m), (size * 0.38, size * 0.62), (size * 0.58, size * 0.7), (size - m, m)]
        draw.line(points, fill=color, width=stroke, joint="curve")
        draw.line([size - m, m, size - m, size * 0.34], fill=color, width=stroke)
        draw.line([size - m, m, size * 0.66, m], fill=color, width=stroke)
        return
    if "database" in name:
        draw.ellipse([m, m, size - m, size * 0.36], outline=color, width=stroke)
        draw.line([m, size * 0.18, m, size * 0.76], fill=color, width=stroke)
        draw.line([size - m, size * 0.18, size - m, size * 0.76], fill=color, width=stroke)
        draw.ellipse([m, size * 0.58, size - m, size - m], outline=color, width=stroke)
        return
    if "shield" in name:
        draw.polygon([(c, m), (size - m, size * 0.28), (size * 0.76, size * 0.72), (c, size - m), (size * 0.24, size * 0.72), (m, size * 0.28)], outline=color)
        draw.line([size * 0.34, c, size * 0.46, size * 0.64, size * 0.68, size * 0.38], fill=color, width=stroke)
        return
    if "rocket" in name:
        draw.polygon([(c, m), (size * 0.72, size * 0.56), (c, size - m), (size * 0.28, size * 0.56)], outline=color)
        draw.ellipse([size * 0.42, size * 0.32, size * 0.58, size * 0.48], outline=color, width=stroke)
        return
    if "brain" in name:
        draw.arc([m, m, c + stroke, c + stroke], 90, 300, fill=color, width=stroke)
        draw.arc([c - stroke, m, size - m, c + stroke], 240, 90, fill=color, width=stroke)
        draw.arc([m, c - stroke, c + stroke, size - m], 60, 260, fill=color, width=stroke)
        draw.arc([c - stroke, c - stroke, size - m, size - m], 280, 120, fill=color, width=stroke)
        draw.line([c, size * 0.22, c, size * 0.78], fill=color, width=max(1, stroke // 2))
        return

    draw.line([c, m, c, size * 0.58], fill=color, width=stroke)
    draw.ellipse([size * 0.32, size * 0.18, size * 0.68, size * 0.54], outline=color, width=stroke)
    draw.line([size * 0.36, size * 0.78, size * 0.64, size * 0.78], fill=color, width=stroke)
    draw.line([size * 0.4, size - m, size * 0.6, size - m], fill=color, width=stroke)


def get_available_icon_names() -> list[str]:
    """Return list of all recommended icon short names."""
    return sorted(RECOMMENDED_ICONS.keys())
