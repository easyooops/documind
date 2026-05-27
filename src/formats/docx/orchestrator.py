"""DOCX template-led native document pipeline."""

from src.formats.docx.renderer import DOCXRenderer
from src.formats.docx.rulesets import DOCX_RULESET
from src.formats.rich_document.orchestrator import compile_native_document_pipeline


def compile_docx_pipeline():
    return compile_native_document_pipeline("docx", DOCXRenderer, DOCX_RULESET)

