"""True DOCX preview rendering from the generated Word artifact."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger(__name__)


async def render_docx_pdf(source_path: Path, output_dir: Path) -> Path | None:
    """Convert the final DOCX artifact to PDF for a faithful in-app preview."""
    return await asyncio.to_thread(_render_docx_pdf_sync, source_path, output_dir)


async def render_docx_images(source_path: Path, output_dir: Path) -> list[Path]:
    """Render the actual DOCX into page images safe for sandboxed browser previews."""
    pdf_path = await render_docx_pdf(source_path, output_dir)
    if not pdf_path:
        return []
    return await render_pdf_images(pdf_path, output_dir)


async def render_pdf_images(source_path: Path, output_dir: Path) -> list[Path]:
    """Render PDF pages as PNGs instead of relying on browser PDF plugins."""
    return await asyncio.to_thread(_render_pdf_images_sync, source_path, output_dir)


def _render_docx_pdf_sync(source_path: Path, output_dir: Path) -> Path | None:
    source_path = source_path.resolve()
    if not source_path.exists():
        return None
    soffice = _find_soffice()
    if not soffice:
        logger.warning("docx_preview.libreoffice_not_found")
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / f"{source_path.stem}_preview.pdf"
    if preview_path.exists() and preview_path.stat().st_mtime >= source_path.stat().st_mtime:
        return preview_path
    try:
        with tempfile.TemporaryDirectory(
            prefix="documind_docx_preview_",
            dir=str(output_dir),
        ) as temp_name:
            temp_dir = Path(temp_name)
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
                    str(source_path),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            converted_path = temp_dir / f"{source_path.stem}.pdf"
            if not converted_path.exists():
                return None
            shutil.copyfile(converted_path, preview_path)
            return preview_path
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("docx_preview.libreoffice_failed", error=str(exc)[:200])
        return None


def _render_pdf_images_sync(source_path: Path, output_dir: Path) -> list[Path]:
    source_path = source_path.resolve()
    if not source_path.exists():
        return []
    try:
        import fitz
    except ImportError:
        logger.warning("docx_preview.pymupdf_not_found")
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    document = None
    try:
        document = fitz.open(str(source_path))
        pages = []
        for page_index in range(len(document)):
            page_path = output_dir / f"{source_path.stem}_page_{page_index + 1}.png"
            if not (
                page_path.exists()
                and page_path.stat().st_mtime >= source_path.stat().st_mtime
            ):
                pixmap = document.load_page(page_index).get_pixmap(
                    matrix=fitz.Matrix(1.5, 1.5),
                    alpha=False,
                )
                pixmap.save(str(page_path))
            pages.append(page_path)
        return pages
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("docx_preview.raster_failed", error=str(exc)[:200])
        return []
    finally:
        if document is not None:
            document.close()


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
