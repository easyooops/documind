"""SDK PPTX format engine."""

from __future__ import annotations

from pathlib import Path

from src.formats.base import FormatEngine
from src.formats.pptx.orchestrator import compile_pptx_pipeline


class PPTXFormatEngine(FormatEngine):
    def __init__(self):
        self._pipeline = None

    @property
    def format_id(self) -> str:
        return "pptx"

    @property
    def renderer(self):
        return None

    def _get_pipeline(self):
        if self._pipeline is None:
            self._pipeline = compile_pptx_pipeline()
        return self._pipeline

    async def generate(
        self,
        user_query: str,
        *,
        template_bytes: bytes | None = None,
        template_filename: str = "template.pptx",
        session_id: str = "",
        needs_research: bool = False,
    ) -> dict:
        initial_state = {
            "user_query": user_query,
            "session_id": session_id,
            "title": "",
            "_template_bytes": template_bytes,
            "_template_filename": template_filename,
            "needs_research": needs_research,
            "current_phase": "pending",
        }
        return await self._get_pipeline().ainvoke(initial_state)

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        raise NotImplementedError(
            "PPTXFormatEngine.render() is deprecated. Use generate() for the SDK pipeline."
        )

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """SDK builds do not run screenshot/VLM validation."""
        return 1.0
