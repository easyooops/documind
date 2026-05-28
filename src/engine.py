"""Package-level public API for API-free document generation.

Install from PyPI and call this module directly through the ``documind``
package. The FastAPI server, web UI, and database layer are not required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal

DocumentType = Literal["pptx", "docx", "pdf", "md", "xlsx", "hwp"]


_MIME_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "md": "text/markdown",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "hwp": "application/hwp+zip",
}

_GLOBAL_CONFIG: dict[str, Any] = {}


@dataclass
class TemplateInput:
    """Uploaded template supplied to package generation."""

    path: str | Path | None = None
    content: bytes | None = None
    filename: str | None = None


@dataclass
class ImageAttachment:
    """Image evidence supplied by the caller."""

    path: str | Path | None = None
    content: bytes | None = None
    filename: str | None = None
    mime_type: str | None = None
    role: str = "content_reference"
    description: str | None = None


@dataclass
class GenerationRequest:
    """Document generation input shared by SDK and service adapters."""

    query: str
    document_type: DocumentType = "pptx"
    template: TemplateInput | str | Path | bytes | dict[str, Any] | None = None
    images: list[ImageAttachment | str | Path | bytes | dict[str, Any]] = field(
        default_factory=list
    )
    session_id: str | None = None
    locale: str = "ko"
    needs_research: bool | None = None
    stream: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    """Result of a document generation run."""

    output_path: str | None = None
    document_type: str = "pptx"
    mime_type: str = _MIME_TYPES["pptx"]
    fidelity_scores: list[float] = field(default_factory=list)
    slide_count: int = 0
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.output_path is not None and len(self.errors) == 0

    @property
    def output_bytes(self) -> bytes | None:
        if not self.output_path:
            return None
        path = Path(self.output_path)
        return path.read_bytes() if path.exists() else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_path": self.output_path,
            "document_type": self.document_type,
            "mime_type": self.mime_type,
            "fidelity_scores": self.fidelity_scores,
            "slide_count": self.slide_count,
            "errors": self.errors,
            "metadata": self.metadata,
        }


@dataclass
class GenerationEvent:
    """Streaming event that can be adapted to SSE by API services."""

    event: str
    data: dict[str, Any] = field(default_factory=dict)
    phase: str | None = None
    agent: str | None = None

    def to_sse(self) -> str:
        import json

        payload = {
            "event": self.event,
            "phase": self.phase,
            "agent": self.agent,
            "data": self.data,
        }
        return f"event: {self.event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def init(**config: Any) -> None:
    """Set process-wide defaults used by convenience calls.

    Example:
        ``documind.init(llm_provider="openai", openai_api_key="...", storage_local_path="./out")``

    ``preload_icons=True`` is accepted as an SDK option. Icons are warmed when
    generation starts, not at import time.
    """

    _GLOBAL_CONFIG.update({key: value for key, value in config.items() if value is not None})


configure = init


def _get_format_pipeline(format_id: str):
    """Get the compiled pipeline for the given document format.

    Routes to the format-specific orchestrator (e.g. formats/pptx/orchestrator.py).
    Every exposed format owns a native, format-specific pipeline.
    """
    if format_id == "pptx":
        from src.formats.pptx.orchestrator import compile_pptx_pipeline
        return compile_pptx_pipeline()
    elif format_id == "docx":
        from src.formats.docx.orchestrator import compile_docx_pipeline
        return compile_docx_pipeline()
    elif format_id == "pdf":
        from src.formats.pdf.orchestrator import compile_pdf_pipeline
        return compile_pdf_pipeline()
    elif format_id == "md":
        from src.formats.md.orchestrator import compile_md_pipeline
        return compile_md_pipeline()
    elif format_id == "xlsx":
        from src.formats.xlsx.orchestrator import compile_xlsx_pipeline
        return compile_xlsx_pipeline()
    elif format_id == "hwp":
        from src.formats.hwp.orchestrator import compile_hwp_pipeline
        return compile_hwp_pipeline()
    raise ValueError(f"Unsupported document format: {format_id}")


class DocuMind:
    """Configurable document generation engine.

    All settings can be passed programmatically — no .env file required.
    Settings passed here override any .env values.

    Args:
        llm_provider: Backend provider
            (openai|anthropic|azure|bedrock|gcp_vertex|gemini|ollama|vllm|custom)
        use_default_models: If True, all agents use the default model for their type.
        default_llm_model: Default model for text generation agents.
        default_vlm_model: Default model for vision-language agents.
        default_image_model: Default model for image generation agents.
        **kwargs: Any Settings field (openai_api_key, aws_profile, aws_region, etc.)
    """

    def __init__(
        self,
        llm_provider: str | None = None,
        use_default_models: bool | None = None,
        default_llm_model: str | None = None,
        default_vlm_model: str | None = None,
        default_image_model: str | None = None,
        **kwargs: Any,
    ):
        defaults = {
            "llm_provider": "openai",
            "use_default_models": True,
            "default_llm_model": "gpt-4o",
            "default_vlm_model": "gpt-4o",
            "default_image_model": "dall-e-3",
        }
        explicit = {
            key: value
            for key, value in {
                "llm_provider": llm_provider,
                "use_default_models": use_default_models,
                "default_llm_model": default_llm_model,
                "default_vlm_model": default_vlm_model,
                "default_image_model": default_image_model,
            }.items()
            if value is not None
        }
        self._config = {
            **defaults,
            **_GLOBAL_CONFIG,
            **explicit,
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

        Path(settings.storage_local_path).mkdir(parents=True, exist_ok=True)
        if settings.log_file:
            Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)

        # Clear any cached agent configs to pick up new model settings
        from src.agents.loader import reload_config
        reload_config()

        self._initialized = True

    async def prepare_assets(
        self,
        *,
        preload_icons: bool | None = None,
        icon_limit: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Prepare local runtime assets.

        Icon download is intentionally opt-in for SDK usage. Generation can
        still create icons lazily and falls back to local placeholders when the
        network is unavailable.
        """

        self._apply_config()
        should_preload = (
            bool(self._config.get("preload_icons"))
            if preload_icons is None
            else preload_icons
        )
        if not should_preload:
            from src.utils.iconify import ensure_icon_registry

            ensure_icon_registry()
            return {"icons": {"preloaded": False}}

        from src.utils.iconify import preload_recommended_icons

        stats = await preload_recommended_icons(
            limit=icon_limit if icon_limit is not None else self._config.get("icon_preload_limit"),
            force=force,
        )
        return {"icons": {"preloaded": True, **stats}}

    async def generate(
        self,
        query: str,
        format: DocumentType = "pptx",
        template_id: str | None = None,
        document_type: DocumentType | None = None,
        template: TemplateInput | str | Path | bytes | dict[str, Any] | None = None,
        images: list[ImageAttachment | str | Path | bytes | dict[str, Any]] | None = None,
        session_id: str | None = None,
        locale: str = "ko",
        needs_research: bool | None = None,
        **options: Any,
    ) -> GenerationResult:
        """Generate a document from a natural language query.

        Args:
            query: Natural language description of the document to create.
            format: Output format (pptx|docx|pdf|md|xlsx|hwp).
            document_type: Alias for format.
            template: Path, bytes, or TemplateInput for a native/template file.
            images: Optional image evidence for planning.
            template_id: Optional service-side template ID for compatibility.
            needs_research: Whether to run web research first. If None, infer from query intent.
            **options: Additional pipeline options.

        Returns:
            GenerationResult with output_path, scores, and metadata.
        """
        self._apply_config()
        await self.prepare_assets(preload_icons=options.pop("preload_icons", None))

        request = GenerationRequest(
            query=query,
            document_type=document_type or format,
            template=template,
            images=images or [],
            session_id=session_id,
            locale=locale,
            needs_research=needs_research,
            options={**options, "template_id": template_id},
        )
        initial_state = await self._build_initial_state(request)

        pipeline = _get_format_pipeline(request.document_type)
        result = await pipeline.ainvoke(initial_state, config={"recursion_limit": 80})

        return self._result_from_state(result, request.document_type)

    async def generate_from_request(self, request: GenerationRequest) -> GenerationResult:
        """Generate from a structured request object."""

        return await self.generate(
            query=request.query,
            document_type=request.document_type,
            template=request.template,
            images=request.images,
            session_id=request.session_id,
            locale=request.locale,
            needs_research=request.needs_research,
            **request.options,
        )

    async def generate_stream(
        self,
        request: GenerationRequest | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[GenerationEvent]:
        """Generate a document and yield progress events.

        API servers can forward ``event.to_sse()`` for Server-Sent Events. SDK
        callers can consume the async iterator directly.
        """

        self._apply_config()
        await self.prepare_assets(preload_icons=kwargs.pop("preload_icons", None))
        if request is None:
            if "format" in kwargs and "document_type" not in kwargs:
                kwargs["document_type"] = kwargs.pop("format")
            request = GenerationRequest(**kwargs)
        state = await self._build_initial_state(request)
        pipeline = _get_format_pipeline(request.document_type)
        accumulated: dict[str, Any] = dict(state)

        yield GenerationEvent(
            event="started",
            phase="planning",
            data={"document_type": request.document_type, "query": request.query},
        )
        try:
            async for update in pipeline.astream(
                state,
                config={"recursion_limit": 80},
                stream_mode="updates",
            ):
                for node_name, node_output in update.items():
                    if isinstance(node_output, dict):
                        accumulated.update(node_output)
                        phase = node_output.get("current_phase") or accumulated.get("current_phase")
                        yield GenerationEvent(
                            event="progress",
                            agent=str(node_name),
                            phase=phase,
                            data=_public_update(node_output),
                        )
            result = self._result_from_state(accumulated, request.document_type)
            yield GenerationEvent(
                event="completed" if result.success else "failed",
                phase="done" if result.success else "error",
                data={"result": result.to_dict()},
            )
        except Exception as exc:
            yield GenerationEvent(
                event="failed",
                phase="error",
                data={"error": str(exc), "error_type": type(exc).__name__},
            )

    async def _build_initial_state(self, request: GenerationRequest) -> dict[str, Any]:
        from src.agents.research_intent import analyze_research_intent
        from src.schemas.agents import DocuMindState
        from src.utils.language import detect_output_language

        template = _coerce_template(request.template)
        image_payloads = [_coerce_image(image, index) for index, image in enumerate(request.images)]
        template_analysis = {}
        if template.content:
            template_analysis = _analyze_template(
                template.content,
                template.filename or f"template.{request.document_type}",
            )
        inferred_needs_research = (
            (await analyze_research_intent(request.query)).needs_research
            if request.needs_research is None
            else request.needs_research
        )
        template_id = request.options.get("template_id")
        visual_intent = {
            "attachments": [
                {
                    "id": image["id"],
                    "filename": image["filename"],
                    "mime_type": image["mime_type"],
                    "role": image["role"],
                    "description": image.get("description"),
                }
                for image in image_payloads
            ]
        }
        initial_state: DocuMindState = {
            "user_query": request.query,
            "session_id": request.session_id or f"engine-{id(self)}",
            "template_id": template_id,
            "conversation_history": [],
            "document_format": request.document_type,
            "locale": request.locale,
            "output_language": detect_output_language(request.query),
            "needs_research": inferred_needs_research,
            "template_provided": bool(template.content or template_id),
            "current_phase": "planning",
            "errors": [],
            "retry_count": 0,
            "qa_iterations": 0,
            "_template_bytes": template.content,
            "_template_filename": template.filename or "",
            "_template_analysis": template_analysis,
            "_image_attachments": image_payloads,
            "image_attachment_ids": [image["id"] for image in image_payloads],
            "visual_intent": visual_intent,
        }
        return initial_state

    def _result_from_state(self, result: dict[str, Any], document_type: str) -> GenerationResult:
        return GenerationResult(
            output_path=result.get("output_path"),
            document_type=document_type,
            mime_type=_MIME_TYPES.get(document_type, "application/octet-stream"),
            fidelity_scores=result.get("fidelity_scores", []),
            slide_count=len(result.get("slides_html", []) or result.get("section_blueprints", [])),
            errors=result.get("errors", []),
            metadata={
                "format": document_type,
                "qa_iterations": result.get("qa_iterations", 0),
                "validation_passed": result.get("validation_result", {}).get("passed", False),
                "html_preview_path": result.get("html_preview_path"),
                "pptx_screenshots": result.get("pptx_screenshots", []),
            },
        )


async def generate_document(
    query: str,
    format: DocumentType = "pptx",
    template_id: str | None = None,
    document_type: DocumentType | None = None,
    template: TemplateInput | str | Path | bytes | dict[str, Any] | None = None,
    images: list[ImageAttachment | str | Path | bytes | dict[str, Any]] | None = None,
    session_id: str | None = None,
    locale: str = "ko",
    needs_research: bool | None = None,
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
            format: Output format (pptx|docx|pdf|md|xlsx|hwp).
        template_id: Optional template for design reference.
        needs_research: Whether to gather external data first. If None, infer from query intent.
        **config: Any DocuMind/Settings config overrides.

    Returns:
        GenerationResult with output_path, fidelity_scores, etc.
    """
    engine = DocuMind(**config)
    return await engine.generate(
        query=query,
        format=format,
        document_type=document_type,
        template_id=template_id,
        template=template,
        images=images,
        session_id=session_id,
        locale=locale,
        needs_research=needs_research,
    )


async def stream_document(
    request: GenerationRequest | None = None,
    **kwargs: Any,
) -> AsyncIterator[GenerationEvent]:
    """Convenience streaming helper."""

    engine_config = kwargs.pop("config", {})
    engine = DocuMind(**engine_config)
    async for event in engine.generate_stream(request, **kwargs):
        yield event


def _coerce_template(
    value: TemplateInput | str | Path | bytes | dict[str, Any] | None,
) -> TemplateInput:
    if value is None:
        return TemplateInput()
    if isinstance(value, TemplateInput):
        if value.content is None and value.path:
            path = Path(value.path)
            return TemplateInput(
                path=path,
                content=path.read_bytes(),
                filename=value.filename or path.name,
            )
        return value
    if isinstance(value, bytes):
        return TemplateInput(content=value, filename="template")
    if isinstance(value, (str, Path)):
        path = Path(value)
        return TemplateInput(path=path, content=path.read_bytes(), filename=path.name)
    path_value = value.get("path")
    content = value.get("content") or value.get("bytes")
    filename = value.get("filename")
    if content is None and path_value:
        path = Path(path_value)
        content = path.read_bytes()
        filename = filename or path.name
    return TemplateInput(path=path_value, content=content, filename=filename)


def _coerce_image(
    value: ImageAttachment | str | Path | bytes | dict[str, Any],
    index: int,
) -> dict[str, Any]:
    if isinstance(value, ImageAttachment):
        path = Path(value.path) if value.path else None
        content = (
            value.content
            if value.content is not None
            else (path.read_bytes() if path else None)
        )
        filename = value.filename or (path.name if path else f"image_{index}")
        return {
            "id": f"image-{index}",
            "content": content,
            "filename": filename,
            "mime_type": value.mime_type or _guess_image_mime(filename),
            "role": value.role,
            "description": value.description,
        }
    if isinstance(value, bytes):
        return {
            "id": f"image-{index}",
            "content": value,
            "filename": f"image_{index}",
            "mime_type": "application/octet-stream",
            "role": "content_reference",
        }
    if isinstance(value, (str, Path)):
        path = Path(value)
        return {
            "id": f"image-{index}",
            "content": path.read_bytes(),
            "filename": path.name,
            "mime_type": _guess_image_mime(path.name),
            "role": "content_reference",
        }
    path_value = value.get("path")
    content = value.get("content") or value.get("bytes")
    filename = value.get("filename")
    if content is None and path_value:
        path = Path(path_value)
        content = path.read_bytes()
        filename = filename or path.name
    filename = filename or f"image_{index}"
    return {
        "id": str(value.get("id") or f"image-{index}"),
        "content": content,
        "filename": filename,
        "mime_type": value.get("mime_type") or _guess_image_mime(filename),
        "role": value.get("role", "content_reference"),
        "description": value.get("description"),
    }


def _guess_image_mime(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")


def _analyze_template(content: bytes, filename: str) -> dict[str, Any]:
    try:
        from src.formats.rich_document.template_analysis import analyze_template

        return analyze_template(content, filename)
    except Exception:
        return {"filename": filename}


def _public_update(update: dict[str, Any]) -> dict[str, Any]:
    hidden = {"_template_bytes", "_image_attachments"}
    allowed_scalar = (str, int, float, bool, type(None))
    public: dict[str, Any] = {}
    for key, value in update.items():
        if key in hidden:
            continue
        if isinstance(value, allowed_scalar):
            public[key] = value
        elif key in {
            "errors",
            "fidelity_scores",
            "qa_feedback",
            "validation_result",
            "output_path",
            "current_phase",
        }:
            public[key] = value
    return public
