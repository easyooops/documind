"""Iconify API integration for downloading SVG icons and converting to PNG for PPTX slides.

Uses the free Iconify API (https://api.iconify.design) — no API key required.
Supports 200,000+ icons from Material Design, Phosphor, Tabler, Lucide, etc.
"""

from __future__ import annotations

import io
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


async def fetch_icon_svg(icon_name: str, color: str = "1E293B", size: int = 48) -> Optional[bytes]:
    """Fetch an SVG icon from Iconify API.

    Args:
        icon_name: Short name (e.g., 'database') or full Iconify ID (e.g., 'mdi:database')
        color: Hex color without '#' prefix
        size: Icon size in pixels

    Returns:
        SVG bytes or None if fetch failed
    """
    icon_name = icon_name.replace("_", "-")
    icon_id = RECOMMENDED_ICONS.get(icon_name, icon_name)
    if ":" not in icon_id:
        icon_id = f"mdi:{icon_id}"

    prefix, name = icon_id.split(":", 1)
    # Apply alias mapping for commonly misnamed icons (Lucide/Feather → MDI)
    if prefix == "mdi" and name in ICON_NAME_ALIASES:
        name = ICON_NAME_ALIASES[name]
        icon_id = f"mdi:{name}"

    url = f"{ICONIFY_API_BASE}/{prefix}/{name}.svg?color=%23{color}&width={size}&height={size}"

    cache_key = hashlib.md5(f"{icon_id}_{color}_{size}".encode()).hexdigest()
    cache_path = ICON_CACHE_DIR / f"{cache_key}.svg"

    if cache_path.exists():
        return cache_path.read_bytes()

    try:
        import ssl
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
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

    icon_name_clean = icon_name.replace("_", "-")
    icon_id = RECOMMENDED_ICONS.get(icon_name_clean, icon_name_clean)
    if ":" not in icon_id:
        icon_id = f"mdi:{icon_id}"
    prefix, name = icon_id.split(":", 1)
    if prefix == "mdi" and name in ICON_NAME_ALIASES:
        name = ICON_NAME_ALIASES[name]
        icon_id = f"mdi:{name}"

    png_cache_key = hashlib.md5(f"{icon_id}_{color}_{size}_png".encode()).hexdigest()
    png_cache_path = ICON_CACHE_DIR / f"{png_cache_key}.png"
    if png_cache_path.exists():
        return png_cache_path.read_bytes()

    # Method 1: svglib + reportlab (pure Python, best cross-platform)
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        import tempfile

        # Write SVG to temp file for svglib
        svg_cache_key = hashlib.md5(f"{icon_id}_{color}_{size}".encode()).hexdigest()
        svg_path = ICON_CACHE_DIR / f"{svg_cache_key}.svg"
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

    # Method 3: Pillow colored circle placeholder (last resort)
    try:
        from PIL import Image, ImageDraw
        import io
        img_size = max(size, 64)
        img = Image.new("RGBA", (img_size, img_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r = int(color[0:2], 16) if len(color) >= 6 else 30
        g = int(color[2:4], 16) if len(color) >= 6 else 41
        b = int(color[4:6], 16) if len(color) >= 6 else 59
        margin = img_size // 6
        draw.ellipse([margin, margin, img_size - margin, img_size - margin], fill=(r, g, b, 255))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        png_data = buffer.getvalue()
        png_cache_path.write_bytes(png_data)
        return png_data
    except ImportError:
        return None


def get_icon_path(icon_name: str, color: str = "1E293B", size: int = 48) -> Optional[Path]:
    """Get cached icon path synchronously (for use in mapper). Returns None if not cached.

    Searches for PNG first (best PowerPoint compatibility), then SVG as fallback.
    Tries multiple color variants and sizes to find a cached icon.
    """
    icon_name = icon_name.replace("_", "-")
    icon_id = RECOMMENDED_ICONS.get(icon_name, icon_name)
    if ":" not in icon_id:
        icon_id = f"mdi:{icon_id}"

    prefix, name = icon_id.split(":", 1)
    if prefix == "mdi" and name in ICON_NAME_ALIASES:
        name = ICON_NAME_ALIASES[name]
        icon_id = f"mdi:{name}"

    common_colors = [color, "1E293B", "10B981", "2563EB", "F59E0B", "6366F1", "059669", "ffffff", "000000"]
    common_sizes = [size, 32, 48, 24]

    # Search PNG first (PowerPoint compatible)
    for test_size in common_sizes:
        for test_color in common_colors:
            png_key = hashlib.md5(f"{icon_id}_{test_color}_{test_size}_png".encode()).hexdigest()
            png_path = ICON_CACHE_DIR / f"{png_key}.png"
            if png_path.exists():
                return png_path

    # Fallback: search SVG
    for test_size in common_sizes:
        for test_color in common_colors:
            cache_key = hashlib.md5(f"{icon_id}_{test_color}_{test_size}".encode()).hexdigest()
            cache_path = ICON_CACHE_DIR / f"{cache_key}.svg"
            if cache_path.exists():
                return cache_path

    return None


def get_available_icon_names() -> list[str]:
    """Return list of all recommended icon short names."""
    return sorted(RECOMMENDED_ICONS.keys())
