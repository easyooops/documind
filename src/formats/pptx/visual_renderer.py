"""Render PPTX slides to raster images for template and quality analysis."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)


async def render_pptx_images(
    source: bytes | str | Path,
    output_dir: Path,
    *,
    prefix: str = "pptx",
    max_slides: int | None = None,
) -> dict[str, Any]:
    """Render a PowerPoint file into one PNG per slide.

    LibreOffice plus PyMuPDF provides a true rasterization path. A lightweight
    OOXML fallback keeps visual analysis available when either runtime
    dependency is absent.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _render_pptx_images_sync,
        source,
        output_dir,
        prefix,
        max_slides,
    )


def _render_pptx_images_sync(
    source: bytes | str | Path,
    output_dir: Path,
    prefix: str,
    max_slides: int | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="documind_pptx_render_", dir=str(output_dir)
    ) as temp_name:
        temp_dir = Path(temp_name)
        input_path = _materialize_source(source, temp_dir)
        rendered = _render_with_libreoffice(input_path, temp_dir, output_dir, prefix, max_slides)
        if rendered:
            return {
                "paths": rendered,
                "renderer": "libreoffice_pdf",
                "true_render": True,
            }

        fallback = _render_ooxml_preview(input_path, output_dir, prefix, max_slides)
        return {
            "paths": fallback,
            "renderer": "ooxml_preview",
            "true_render": False,
        }


def _materialize_source(source: bytes | str | Path, temp_dir: Path) -> Path:
    if isinstance(source, bytes):
        path = temp_dir / "presentation.pptx"
        path.write_bytes(source)
        return path
    return Path(source)


def _render_with_libreoffice(
    input_path: Path,
    temp_dir: Path,
    output_dir: Path,
    prefix: str,
    max_slides: int | None,
) -> list[str]:
    soffice = _find_soffice()
    if not soffice:
        return []
    try:
        profile_uri = (temp_dir / "libreoffice_profile").resolve().as_uri()
        subprocess.run(
            [
                soffice,
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temp_dir),
                str(input_path),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        pdf_path = temp_dir / f"{input_path.stem}.pdf"
        if not pdf_path.exists():
            return []
        try:
            import fitz
        except ImportError:
            logger.warning("visual_renderer.pymupdf_unavailable")
            return []

        prior_errors = fitz.TOOLS.mupdf_display_errors()
        prior_warnings = fitz.TOOLS.mupdf_display_warnings()
        fitz.TOOLS.mupdf_display_errors(False)
        fitz.TOOLS.mupdf_display_warnings(False)
        document = None
        try:
            document = fitz.open(str(pdf_path))
            limit = len(document) if max_slides is None else min(len(document), max_slides)
            images = []
            for page_index in range(limit):
                output_path = (
                    output_dir
                    / f"{prefix}_slide_{page_index + 1}_{uuid.uuid4().hex[:6]}.png"
                )
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pixmap.save(str(output_path))
                images.append(str(output_path))
            return images
        finally:
            if document is not None:
                document.close()
            fitz.TOOLS.mupdf_display_errors(prior_errors)
            fitz.TOOLS.mupdf_display_warnings(prior_warnings)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("visual_renderer.libreoffice_failed", error=str(exc)[:200])
        return []


def _find_soffice() -> str | None:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable:
        return executable
    for candidate in (
        Path("C:/Program Files/LibreOffice/program/soffice.com"),
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _render_ooxml_preview(
    input_path: Path,
    output_dir: Path,
    prefix: str,
    max_slides: int | None,
) -> list[str]:
    from PIL import Image, ImageDraw
    from pptx import Presentation

    presentation = Presentation(str(input_path))
    slide_width = presentation.slide_width or 9_144_000
    slide_height = presentation.slide_height or 5_143_500
    scale_x = 960 / slide_width
    scale_y = 540 / slide_height
    slides = list(presentation.slides)
    if max_slides is not None:
        slides = slides[:max_slides]
    outputs = []

    for index, slide in enumerate(slides, 1):
        image = Image.new("RGB", (960, 540), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        for shape in slide.shapes:
            x = round((shape.left or 0) * scale_x)
            y = round((shape.top or 0) * scale_y)
            w = round((shape.width or 0) * scale_x)
            h = round((shape.height or 0) * scale_y)
            fill = _shape_fill(shape)
            if getattr(shape, "shape_type", None) == 13:
                try:
                    picture = Image.open(io.BytesIO(shape.image.blob)).convert("RGB")
                    picture.thumbnail((max(w, 1), max(h, 1)))
                    image.paste(picture, (x, y))
                    continue
                except Exception:
                    pass
            if fill:
                draw.rectangle((x, y, x + w, y + h), fill=fill)
            if getattr(shape, "has_text_frame", False) and shape.text:
                draw.text((x + 3, y + 3), shape.text[:180], fill=(30, 30, 30))
        output_path = output_dir / f"{prefix}_slide_{index}_{uuid.uuid4().hex[:6]}.png"
        image.save(output_path, format="PNG")
        outputs.append(str(output_path))
    return outputs


def _shape_fill(shape) -> tuple[int, int, int] | None:
    try:
        rgb = shape.fill.fore_color.rgb
        if rgb:
            return (rgb[0], rgb[1], rgb[2])
    except (AttributeError, TypeError):
        return None
    return None
