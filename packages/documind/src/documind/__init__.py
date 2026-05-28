"""DocuMind SDK public API.

Use after installing from PyPI:

    from documind import init, generate_document
"""

from __future__ import annotations

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
