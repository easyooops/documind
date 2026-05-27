"""HWPX native report pipeline."""

from src.formats.hwp.renderer import HWPXRenderer
from src.formats.hwp.rulesets import HWPX_RULESET
from src.formats.rich_document.orchestrator import compile_native_document_pipeline


def compile_hwp_pipeline():
    return compile_native_document_pipeline("hwp", HWPXRenderer, HWPX_RULESET)

