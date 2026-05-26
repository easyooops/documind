"""PPTX Format Engine — v2 Hybrid Constraint-Creative Architecture.

Wraps the LangGraph pipeline orchestrator for end-to-end PPTX generation.
"""

from __future__ import annotations

from pathlib import Path

from src.formats.base import FormatEngine
from src.formats.pptx.orchestrator import compile_pptx_pipeline


class PPTXFormatEngine(FormatEngine):
    """PPTX generation engine using Hybrid Constraint-Creative pipeline."""

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
        """Run the full 4-phase pipeline and return result state."""
        initial_state = {
            "user_query": user_query,
            "session_id": session_id,
            "title": "",
            "_template_bytes": template_bytes,
            "_template_filename": template_filename,
            "needs_research": needs_research,
            "current_phase": "pending",
        }

        pipeline = self._get_pipeline()
        result = await pipeline.ainvoke(initial_state)
        return result

    async def render(self, classified_elements: list[dict], output_dir: Path) -> Path:
        """Legacy interface — use generate() instead."""
        raise NotImplementedError(
            "PPTXFormatEngine.render() is deprecated. Use generate() for the v2 pipeline."
        )

    async def validate(self, output_path: Path, reference_html: list[dict]) -> float:
        """Validate PPTX output quality via VLM QA."""
        from src.formats.pptx.agents.nodes.vlm_qa import vlm_quality_gate

        state = {
            "output_path": str(output_path),
            "html_screenshots": [],
            "qa_iterations": 0,
            "fidelity_scores": [],
        }
        result = await vlm_quality_gate(state)
        return result.get("fidelity_score", 0.0)
