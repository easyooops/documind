"""Markdown template-led publication pipeline."""

from src.formats.md.renderer import MarkdownRenderer
from src.formats.md.rulesets import MARKDOWN_RULESET
from src.formats.rich_document.orchestrator import compile_native_document_pipeline


def compile_md_pipeline():
    return compile_native_document_pipeline("md", MarkdownRenderer, MARKDOWN_RULESET)

