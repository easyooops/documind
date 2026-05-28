"""Iconify API integration for downloading SVG icons and converting to PNG for PPTX slides.

Uses the free Iconify API (https://api.iconify.design) — no API key required.
Supports 200,000+ icons from Material Design, Phosphor, Tabler, Lucide, etc.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import httpx

from src.core.logging import get_logger

logger = get_logger(__name__)

ICONIFY_API_BASE = "https://api.iconify.design"
ICON_CACHE_DIR = Path("data/cache/icons")

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
    svg_data = await fetch_icon_svg(icon_name, color, size)
    if not svg_data:
        return None

    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    png_cache_path = _icon_cache_path(icon_id, color, size, "png")
    if png_cache_path.exists():
        return png_cache_path.read_bytes()

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
            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            png_cache_path.write_bytes(png_data)
            logger.info("iconify.png_converted", icon=icon_id, method="svglib")
            return png_data
    except Exception as e:
        logger.debug("iconify.svglib_failed", icon=icon_id, error=str(e)[:60])

    # Method 2: cairosvg
    try:
        import cairosvg
        png_data = cairosvg.svg2png(bytestring=svg_data, output_width=size, output_height=size)
        png_cache_path.write_bytes(png_data)
        return png_data
    except (ImportError, Exception):
        pass

    logger.warning("iconify.png_conversion_failed", icon=icon_id)
    return None


def get_icon_path(icon_name: str, color: str = "1E293B", size: int = 48) -> Optional[Path]:
    """Get cached icon path synchronously (for use in mapper). Returns None if not cached.

    Searches for the exact rendered color and size, so preview and PPTX output
    cannot silently disagree about icon styling.
    """
    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    for extension in ("png", "svg"):
        path = _icon_cache_path(icon_id, color, size, extension)
        if path.exists():
            return path
    return None


def get_fallback_icon_path(icon_name: str, color: str = "1E293B", size: int = 32) -> Path | None:
    """Create a simple visible fallback icon when Iconify is unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    icon_id = normalize_icon_id(icon_name)
    color = normalize_icon_color(color)
    cache_path = _icon_cache_path(f"fallback:{icon_id}", color, size, "png")
    if cache_path.exists():
        return cache_path

    ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    image_size = max(size, 32)
    img = Image.new("RGBA", (image_size, image_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    stroke = max(2, image_size // 12)
    margin = max(4, image_size // 8)
    draw.rounded_rectangle(
        [margin, margin, image_size - margin, image_size - margin],
        radius=max(4, image_size // 6),
        outline=rgb + (255,),
        width=stroke,
    )
    label = icon_id.split(":", 1)[1][:1].upper()
    try:
        font = ImageFont.truetype("arial.ttf", image_size // 2)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    draw.text(
        ((image_size - (bbox[2] - bbox[0])) / 2, (image_size - (bbox[3] - bbox[1])) / 2 - 1),
        label,
        fill=rgb + (255,),
        font=font,
    )
    img.save(cache_path, format="PNG")
    return cache_path


def get_available_icon_names() -> list[str]:
    """Return list of all recommended icon short names."""
    return sorted(RECOMMENDED_ICONS.keys())
