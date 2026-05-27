"""PDF template-led publication pipeline."""

from src.formats.pdf.renderer import PDFRenderer
from src.formats.pdf.rulesets import PDF_RULESET
from src.formats.rich_document.orchestrator import compile_native_document_pipeline


def compile_pdf_pipeline():
    return compile_native_document_pipeline("pdf", PDFRenderer, PDF_RULESET)

