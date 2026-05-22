"""PPTX compensation handlers for Category B CSS properties.

Category B properties cannot be directly mapped to DrawingML
but can be approximated using multiple shapes or effects.
"""

from __future__ import annotations

from src.formats.base import CompensationHandler


class BackdropFilterHandler(CompensationHandler):
    """Approximate backdrop-filter (blur, brightness) with overlapping shapes."""

    @property
    def css_property(self) -> str:
        return "backdropFilter"

    @property
    def needs_context_decision(self) -> bool:
        return True

    async def execute(self, element: dict, **kwargs) -> list[dict]:
        """Generate semi-transparent overlay shapes to approximate backdrop blur."""
        # Strategy: create a semi-transparent rectangle with a solid color
        # that approximates the blur effect visually
        return [
            {
                "type": "rectangle",
                "x": element.get("x", 0),
                "y": element.get("y", 0),
                "width": element.get("width", 100),
                "height": element.get("height", 50),
                "fill": {"type": "solid", "color": "FFFFFF", "alpha": 70000},
                "role": "decorative",
            }
        ]


class ClipPathHandler(CompensationHandler):
    """Approximate clip-path with custom shapes or cropping."""

    @property
    def css_property(self) -> str:
        return "clipPath"

    async def execute(self, element: dict, **kwargs) -> list[dict]:
        """Generate shape approximation for clip-path."""
        return [element]
