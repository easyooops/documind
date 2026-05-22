"""PPTX Conversion node — builds PPTX directly from OOXML-DSL (no Playwright)."""

from __future__ import annotations

import uuid
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger
from src.formats.pptx.dsl.html_renderer import DSLtoHTMLRenderer
from src.formats.pptx.dsl.pptx_builder import DSLtoPPTXBuilder
from src.formats.pptx.dsl.schema import PresentationDSL, SlideDSL
from src.schemas.agents import DocuMindState

logger = get_logger(__name__)

_builder = DSLtoPPTXBuilder()
_html_renderer = DSLtoHTMLRenderer()


async def conversion_node(state: DocuMindState) -> dict:
    """Convert validated DSL slides to PPTX format (lossless, no Playwright)."""
    logger.info("conversion.start", format="pptx", iteration=state.get("qa_iterations", 0))

    slides_dsl = state.get("slides_dsl", [])
    if not slides_dsl:
        logger.error("conversion.no_dsl_data")
        return {"current_phase": "error"}

    previous_output = state.get("output_path")
    previous_preview = state.get("html_preview_path")
    _cleanup_previous_files(previous_output, previous_preview)

    title = state.get("title", "DocuMind Presentation")
    presentation_dsl = PresentationDSL(
        title=title,
        slides=[SlideDSL.model_validate(s) for s in slides_dsl],
    )

    output_dir = Path(settings.storage_local_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = _builder.build(presentation_dsl, output_dir)

    html_preview_path = _save_html_preview(presentation_dsl)

    logger.info("conversion.complete", output_path=str(output_path), slides=len(slides_dsl))
    return {
        "output_path": str(output_path),
        "html_preview_path": html_preview_path,
        "current_phase": "converting",
    }


def _cleanup_previous_files(*paths: str | None) -> None:
    """Remove intermediate files from previous iterations."""
    for path in paths:
        if not path:
            continue
        try:
            file = Path(path)
            if file.exists():
                file.unlink()
                logger.info("conversion.cleanup", deleted=path)
        except OSError as e:
            logger.warning("conversion.cleanup_failed", path=path, error=str(e))


def _save_html_preview(dsl: PresentationDSL) -> str:
    """Generate and save HTML preview from DSL."""
    output_dir = Path(settings.storage_local_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex[:8]
    preview_path = output_dir / f"preview_{file_id}.html"

    html_content = _html_renderer.render(dsl)
    preview_path.write_text(html_content, encoding="utf-8")

    return str(preview_path)
