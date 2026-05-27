"""XLSX analytical workbook pipeline."""

from src.formats.rich_document.orchestrator import compile_native_document_pipeline
from src.formats.xlsx.renderer import XLSXRenderer
from src.formats.xlsx.rulesets import XLSX_RULESET


def compile_xlsx_pipeline():
    return compile_native_document_pipeline("xlsx", XLSXRenderer, XLSX_RULESET)

