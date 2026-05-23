"""DocuMind Engine - Package-level public API for external service integration.

This module provides the primary interface for using DocuMind as an imported
package (no API server required). Install from PyPI and call methods directly.

Usage:
    from src import DocuMind, generate_document

    # Method 1: Quick function call (uses .env settings)
    result = await generate_document(query="topic", format="pptx")

    # Method 2: Configurable engine instance (pass settings programmatically)
    engine = DocuMind(
        llm_provider="openai",
        use_default_models=True,
        default_llm_model="gpt-4o",
        default_vlm_model="gpt-4o",
        openai_api_key="sk-...",
    )
    result = await engine.generate(query="topic", format="pptx")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerationResult:
    """Result of a document generation run."""

    output_path: str | None = None
    fidelity_scores: list[float] = field(default_factory=list)
    slide_count: int = 0
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.output_path is not None and len(self.errors) == 0


def _get_format_pipeline(format_id: str):
    """Get the compiled pipeline for the given document format.

    Routes to the format-specific orchestrator (e.g. formats/pptx/orchestrator.py).
    Falls back to PPTX pipeline for unsupported formats.
    """
    if format_id == "pptx":
        from src.formats.pptx.orchestrator import compile_pptx_pipeline
        return compile_pptx_pipeline()
    elif format_id == "docx":
        try:
            from src.formats.docx.orchestrator import compile_docx_pipeline
            return compile_docx_pipeline()
        except (ImportError, AttributeError):
            from src.formats.pptx.orchestrator import compile_pptx_pipeline
            return compile_pptx_pipeline()
    elif format_id == "pdf":
        try:
            from src.formats.pdf.orchestrator import compile_pdf_pipeline
            return compile_pdf_pipeline()
        except (ImportError, AttributeError):
            from src.formats.pptx.orchestrator import compile_pptx_pipeline
            return compile_pptx_pipeline()
    elif format_id == "md":
        try:
            from src.formats.md.orchestrator import compile_md_pipeline
            return compile_md_pipeline()
        except (ImportError, AttributeError):
            from src.formats.pptx.orchestrator import compile_pptx_pipeline
            return compile_pptx_pipeline()
    elif format_id == "html":
        try:
            from src.formats.html.orchestrator import compile_html_pipeline
            return compile_html_pipeline()
        except (ImportError, AttributeError):
            from src.formats.pptx.orchestrator import compile_pptx_pipeline
            return compile_pptx_pipeline()
    else:
        from src.formats.pptx.orchestrator import compile_pptx_pipeline
        return compile_pptx_pipeline()


class DocuMind:
    """Configurable document generation engine.

    All settings can be passed programmatically — no .env file required.
    Settings passed here override any .env values.

    Args:
        llm_provider: Backend provider (openai|anthropic|azure|bedrock|gcp_vertex|gemini|ollama|vllm|custom)
        use_default_models: If True, all agents use the default model for their type.
        default_llm_model: Default model for text generation agents.
        default_vlm_model: Default model for vision-language agents.
        default_image_model: Default model for image generation agents.
        **kwargs: Any Settings field (openai_api_key, aws_profile, aws_region, etc.)
    """

    def __init__(
        self,
        llm_provider: str = "openai",
        use_default_models: bool = True,
        default_llm_model: str = "gpt-4o",
        default_vlm_model: str = "gpt-4o",
        default_image_model: str = "dall-e-3",
        **kwargs: Any,
    ):
        self._config = {
            "llm_provider": llm_provider,
            "use_default_models": use_default_models,
            "default_llm_model": default_llm_model,
            "default_vlm_model": default_vlm_model,
            "default_image_model": default_image_model,
            **kwargs,
        }
        self._initialized = False

    def _apply_config(self) -> None:
        """Apply programmatic config to the settings singleton."""
        if self._initialized:
            return

        from src.core.config import settings

        for key, value in self._config.items():
            if hasattr(settings, key) and value is not None:
                object.__setattr__(settings, key, value)

        # Clear any cached agent configs to pick up new model settings
        from src.agents.loader import reload_config
        reload_config()

        self._initialized = True

    async def generate(
        self,
        query: str,
        format: str = "pptx",
        template_id: str | None = None,
        needs_research: bool = True,
        **options: Any,
    ) -> GenerationResult:
        """Generate a document from a natural language query.

        Args:
            query: Natural language description of the document to create.
            format: Output format (pptx|docx|pdf|xlsx).
            template_id: Optional template ID for design reference.
            needs_research: Whether to run web research first.
            **options: Additional pipeline options.

        Returns:
            GenerationResult with output_path, scores, and metadata.
        """
        self._apply_config()

        from src.schemas.agents import DocuMindState
        from src.utils.language import detect_output_language

        initial_state: DocuMindState = {
            "user_query": query,
            "session_id": f"engine-{id(self)}",
            "template_id": template_id,
            "conversation_history": [],
            "document_format": format,
            "locale": options.get("locale", "ko"),
            "output_language": detect_output_language(query),
            "needs_research": needs_research,
            "template_provided": template_id is not None,
            "current_phase": "planning",
            "errors": [],
            "retry_count": 0,
            "qa_iterations": 0,
        }

        pipeline = _get_format_pipeline(format)
        result = await pipeline.ainvoke(initial_state)

        return GenerationResult(
            output_path=result.get("output_path"),
            fidelity_scores=result.get("fidelity_scores", []),
            slide_count=len(result.get("slides_html", [])),
            errors=result.get("errors", []),
            metadata={
                "format": format,
                "qa_iterations": result.get("qa_iterations", 0),
                "validation_passed": result.get("validation_result", {}).get("passed", False),
            },
        )


async def generate_document(
    query: str,
    format: str = "pptx",
    template_id: str | None = None,
    needs_research: bool = True,
    **config: Any,
) -> GenerationResult:
    """Convenience function for one-shot document generation.

    Uses .env settings by default. Pass config kwargs to override:

        result = await generate_document(
            query="AI trends presentation",
            format="pptx",
            llm_provider="bedrock",
            default_llm_model="anthropic.claude-v2",
            aws_region="us-east-1",
        )

    Args:
        query: Natural language description of the document.
        format: Output format (pptx|docx|pdf|xlsx).
        template_id: Optional template for design reference.
        needs_research: Whether to gather external data first.
        **config: Any DocuMind/Settings config overrides.

    Returns:
        GenerationResult with output_path, fidelity_scores, etc.
    """
    engine = DocuMind(**config)
    return await engine.generate(
        query=query,
        format=format,
        template_id=template_id,
        needs_research=needs_research,
    )
