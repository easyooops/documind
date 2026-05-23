"""Language detection and output policy helpers."""

from __future__ import annotations


def detect_output_language(text: str) -> str:
    """Return `en` for English-dominant prompts, otherwise `ko_mixed` for Korean prompts."""
    korean_chars = sum(1 for ch in text if "\uac00" <= ch <= "\ud7a3")
    latin_chars = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if latin_chars > 0 and korean_chars == 0:
        return "en"
    if latin_chars >= korean_chars * 3 and korean_chars < 8:
        return "en"
    return "ko_mixed"


def output_language_instruction(output_language: str) -> str:
    if output_language == "en":
        return (
            "Output language policy: English-only document. "
            "All slide titles, subtitles, labels, tables, chart labels, and speaker notes "
            "must be written in English unless the user explicitly supplied a proper noun in another language."
        )
    return (
        "Output language policy: Korean-dominant business document with English business terms allowed. "
        "Use Korean for slide titles and narrative copy, and keep common technical/business terms in English "
        "where natural (e.g., AI, Cloud, KPI, ROI, TCO)."
    )
