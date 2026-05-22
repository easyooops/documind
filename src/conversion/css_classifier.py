"""CSS Classifier — DEPRECATED.

This module is no longer used in the OOXML-DSL architecture.
Previously classified CSS properties into Category A/B/C for PPTX conversion.
With DSL, there is no CSS — all styling is expressed as typed DSL fields that
map directly to DrawingML attributes.
"""

from __future__ import annotations


class CSSClassifier:
    """DEPRECATED: CSS property classifier for PPTX conversion."""

    def classify(self, styles: dict) -> dict:
        raise NotImplementedError(
            "CSSClassifier is deprecated. OOXML-DSL architecture has no CSS."
        )
