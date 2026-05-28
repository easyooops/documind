"""DocuMind - Agentic AI Document Generation Platform.

Public API for package usage:

    from src import DocuMind, generate_document

    # Quick one-shot generation
    result = await generate_document(
        query="Q3 earnings presentation",
        format="pptx",
    )

    # Advanced: configure and reuse
    engine = DocuMind(
        llm_provider="bedrock",
        use_default_models=True,
        default_llm_model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        default_vlm_model="anthropic.claude-3-5-sonnet-20241022-v2:0",
        default_image_model="stability.stable-diffusion-xl-v1",
        aws_region="us-west-2",
        aws_profile="my-profile",
    )
    result = await engine.generate(query="...", format="pptx")
"""

from src.engine import (
    DocuMind,
    GenerationEvent,
    GenerationRequest,
    GenerationResult,
    ImageAttachment,
    TemplateInput,
    configure,
    generate_document,
    init,
    stream_document,
)

__all__ = [
    "DocuMind",
    "GenerationEvent",
    "GenerationRequest",
    "GenerationResult",
    "ImageAttachment",
    "TemplateInput",
    "configure",
    "generate_document",
    "init",
    "stream_document",
]
