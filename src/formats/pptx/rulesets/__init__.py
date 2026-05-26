"""OOXML Rule-Sets Manager — loads, validates, and provides access to design rules.

The RuleSetManager is the single entry point for all rule-set operations.
It loads JSON Schema files from disk, resolves references, and provides
typed access to constraints for the planner, generator, and evaluator.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)

_SCHEMAS_DIR = Path(__file__).parent / "schemas"
_PRESETS_DIR = Path(__file__).parent / "presets"


class RuleSet:
    """Immutable container for a loaded set of design rules."""

    def __init__(self, schemas: dict[str, dict], presets: dict[str, dict]):
        self._schemas = schemas
        self._presets = presets
        self._version = schemas.get("_meta", {}).get("version", "unknown")

    @property
    def version(self) -> str:
        return self._version

    @property
    def canvas(self) -> dict:
        return self._schemas.get("canvas", {})

    @property
    def layout(self) -> dict:
        return self._schemas.get("layout", {})

    @property
    def typography(self) -> dict:
        return self._schemas.get("typography", {})

    @property
    def color(self) -> dict:
        return self._schemas.get("color", {})

    @property
    def shapes(self) -> dict:
        return self._schemas.get("shapes", {})

    @property
    def textbox(self) -> dict:
        return self._schemas.get("textbox", {})

    @property
    def table(self) -> dict:
        return self._schemas.get("table", {})

    @property
    def chart(self) -> dict:
        return self._schemas.get("chart", {})

    @property
    def effects(self) -> dict:
        return self._schemas.get("effects", {})

    @property
    def connector(self) -> dict:
        return self._schemas.get("connector", {})

    @property
    def image(self) -> dict:
        return self._schemas.get("image", {})

    @property
    def evaluation(self) -> dict:
        return self._schemas.get("evaluation", {})

    def get_slide_preset(self, slide_type: str) -> dict:
        """Get layout preset for a specific slide type."""
        return self._presets.get(f"slide_types/{slide_type}", {})

    def get_theme_preset(self, theme_name: str) -> dict:
        """Get a named theme preset."""
        return self._presets.get(f"themes/{theme_name}", {})

    def get_element_spec(self, element_type: str) -> dict:
        """Get the full specification for a specific element type."""
        if element_type in ("table",):
            return self.table
        if element_type in ("chart", "chart_bar", "chart_line", "chart_pie"):
            return self.chart
        if element_type in ("connector", "line"):
            return self.connector
        if element_type in ("textbox", "title", "subtitle", "body"):
            return self.textbox
        if element_type in ("image", "icon", "photo"):
            return self.image
        return self.shapes

    def get_constraints_for_slide(self, slide_type: str) -> dict:
        """Build a combined constraint dict for a given slide type.

        Used by both Planner and HTML Generator to understand what's valid.
        """
        preset = self.get_slide_preset(slide_type)
        return {
            "canvas": self.canvas.get("canvas", {}),
            "regions": self.canvas.get("regions", {}),
            "safe_area": self.canvas.get("safe_area", {}),
            "layout": preset.get("layout", self.layout),
            "constraints": preset.get("constraints", {}),
            "required_elements": preset.get("required_elements", []),
            "optional_elements": preset.get("optional_elements", []),
            "body_layouts": preset.get("body_layouts", {}),
            "typography": self.typography.get("scale", {}),
            "color_rules": self.color.get("usage_rules", {}),
        }

    def get_evaluation_rubric(self) -> dict:
        """Get the full evaluation rubric for the quality evaluator."""
        return self.evaluation

    def get_generator_prompt_rules(self) -> str:
        """Generate a condensed rules summary for LLM system prompts."""
        canvas = self.canvas
        typo = self.typography
        color = self.color
        layout = self.layout

        parts = [
            "## OOXML Design Rules (STRICT — violations will fail evaluation)\n",
            f"Canvas: {canvas.get('canvas', {}).get('width_px')}×{canvas.get('canvas', {}).get('height_px')}px",
            f"Safe area: top/bottom {canvas.get('safe_area', {}).get('top')}px, left/right {canvas.get('safe_area', {}).get('left')}px",
            f"Grid: {canvas.get('grid', {}).get('columns')}-column, gutter {canvas.get('grid', {}).get('gutter_px')}px",
            "",
            "### Regions",
            f"  Header: y {canvas.get('regions', {}).get('header', {}).get('y_min')}-{canvas.get('regions', {}).get('header', {}).get('y_max')}px",
            f"  Body: y {canvas.get('regions', {}).get('body', {}).get('y_min')}-{canvas.get('regions', {}).get('body', {}).get('y_max')}px",
            f"  Footer: y {canvas.get('regions', {}).get('footer', {}).get('y_min')}-{canvas.get('regions', {}).get('footer', {}).get('y_max')}px",
            "",
            "### Typography Scale (px)",
        ]

        scale = typo.get("scale", {})
        for role, spec in scale.items():
            parts.append(f"  {role}: {spec.get('min_px', spec.get('min', '?'))}-{spec.get('max_px', spec.get('max', '?'))}px, weight {spec.get('weight', 400)}")

        parts.extend([
            f"\nFonts: {', '.join(typo.get('fonts', {}).get('allowed', []))}",
            f"Max title chars: {typo.get('text_fitting', {}).get('max_chars_per_role', {}).get('title', 25)}",
            f"Max body chars/line: {typo.get('text_fitting', {}).get('max_chars_per_role', {}).get('body_per_line', 40)}",
            "",
            "### Color Rules",
            f"Max unique colors/slide: {color.get('usage_rules', {}).get('max_unique_colors_per_slide', 6)}",
            f"Accent max ratio: {color.get('usage_rules', {}).get('accent_max_usage_ratio', 0.15)}",
            f"Text contrast minimum: {color.get('contrast', {}).get('text_on_background_min', 4.5)}:1",
            "",
            "### Layout Rules",
            f"Min element gap: {layout.get('spacing', {}).get('min_element_gap_px', 8)}px",
            f"Whitespace target: {layout.get('balance', {}).get('whitespace_target_ratio', {}).get('min', 0.25)}-{layout.get('balance', {}).get('whitespace_target_ratio', {}).get('max', 0.45)}",
            f"Max elements/slide: {layout.get('element_count', {}).get('max_per_slide', 20)}",
            f"Content overlap: FORBIDDEN",
        ])

        return "\n".join(parts)


class RuleSetManager:
    """Singleton manager for loading and accessing rule-sets."""

    _instance: RuleSetManager | None = None
    _ruleset: RuleSet | None = None

    def __new__(cls) -> RuleSetManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, version: str = "latest") -> RuleSet:
        """Load all schemas and presets from disk."""
        if self._ruleset is not None:
            return self._ruleset

        schemas = self._load_schemas()
        presets = self._load_presets()

        self._ruleset = RuleSet(schemas, presets)
        logger.info(
            "rulesets.loaded",
            version=self._ruleset.version,
            schemas=len(schemas),
            presets=len(presets),
        )
        return self._ruleset

    def get_ruleset(self) -> RuleSet:
        """Get the currently loaded ruleset, loading if necessary."""
        if self._ruleset is None:
            return self.load()
        return self._ruleset

    def reload(self) -> RuleSet:
        """Force reload from disk."""
        self._ruleset = None
        return self.load()

    def _load_schemas(self) -> dict[str, dict]:
        """Load all .schema.json files from the schemas directory."""
        schemas: dict[str, dict] = {}
        if not _SCHEMAS_DIR.exists():
            logger.warning("rulesets.schemas_dir_missing", path=str(_SCHEMAS_DIR))
            return schemas

        for path in sorted(_SCHEMAS_DIR.glob("*.schema.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                schema_id = data.get("$id", path.stem.replace(".schema", ""))
                schemas[schema_id] = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("rulesets.schema_load_error", path=str(path), error=str(e))

        return schemas

    def _load_presets(self) -> dict[str, dict]:
        """Load all preset JSON files recursively."""
        presets: dict[str, dict] = {}
        if not _PRESETS_DIR.exists():
            return presets

        for path in _PRESETS_DIR.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                rel = path.relative_to(_PRESETS_DIR)
                key = str(rel.with_suffix("")).replace("\\", "/")
                presets[key] = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("rulesets.preset_load_error", path=str(path), error=str(e))

        return presets


@lru_cache(maxsize=1)
def get_ruleset_manager() -> RuleSetManager:
    """Get the global RuleSetManager instance."""
    return RuleSetManager()


def get_ruleset() -> RuleSet:
    """Convenience function to get the loaded ruleset."""
    return get_ruleset_manager().get_ruleset()
