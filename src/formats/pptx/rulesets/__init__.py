"""OOXML Rule-Sets Manager — loads, validates, and provides access to design rules.

The RuleSetManager is the single entry point for all rule-set operations.
It loads JSON Schema files from disk, resolves references, and provides
typed access to constraints for the planner, generator, and evaluator.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

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
        self._expanded_layout_patterns = self._build_expanded_layout_patterns()

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

    @property
    def design_strategy(self) -> dict:
        return self._schemas.get("design_strategy", {})

    @property
    def layout_patterns(self) -> dict:
        return self._expanded_layout_patterns

    @property
    def layout_zones(self) -> dict:
        return self._presets.get("layout_zones", {})

    @property
    def icon_layouts(self) -> dict:
        return self._presets.get("icon_layouts", {})

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

    def get_body_layout(self, layout_id: str) -> dict | None:
        """Find a standard body layout by identifier."""
        for category, definition in self.layout_patterns.get("categories", {}).items():
            for pattern in definition.get("patterns", []):
                if pattern.get("id") == layout_id:
                    return {**pattern, "category": category}
        return None

    def _build_expanded_layout_patterns(self) -> dict:
        """Expand curated base patterns into strategy-specific variants."""
        import copy

        base = copy.deepcopy(self._presets.get("layout_patterns", {}))
        categories = base.setdefault("categories", {})
        variant_specs = [
            ("claim", "claim-led", "Lead with claim title, one proof object, and a short implication rail."),
            ("evidence", "evidence-heavy", "Prioritize chart/table/proof object with direct labels and source note."),
            ("executive", "executive-summary", "Use larger whitespace, fewer objects, and decision callouts."),
        ]
        additions: dict[str, list[dict]] = {}
        for category, definition in list(categories.items()):
            patterns = definition.get("patterns", [])
            category_additions = []
            for pattern in patterns:
                for suffix, variant_name, guidance in variant_specs:
                    clone = copy.deepcopy(pattern)
                    clone["id"] = f"{pattern.get('id')}__{suffix}"
                    clone["name"] = f"{pattern.get('name')} ({variant_name})"
                    clone["variant_of"] = pattern.get("id")
                    clone["strategy_variant"] = suffix
                    clone["structure"] = f"{pattern.get('structure')} | {guidance}"
                    clone["selection_guidance"] = guidance
                    category_additions.append(clone)
            additions[category] = category_additions
        for category, category_additions in additions.items():
            categories[category].setdefault("patterns", []).extend(category_additions)
        base["total_patterns"] = sum(
            len(definition.get("patterns", [])) for definition in categories.values()
        )
        base["expansion_strategy"] = {
            "base_patterns": self._presets.get("layout_patterns", {}).get("total_patterns", 0),
            "variants_per_pattern": 4,
            "target_range": "100-300",
            "description": "Each base layout is exposed as base, claim-led, evidence-heavy, and executive-summary variants.",
        }
        return base

    def get_default_body_layout_id(self, slide_type: str) -> str:
        """Select a conservative body pattern when the planner omitted one."""
        defaults = {
            "data": "dashboard_chart_sidebar",
            "comparison": "compare_vs",
            "process": "process_4col",
            "summary": "numbered_list_card",
            "solution": "split_60_40",
            "problem": "split_60_40",
            "content": "split_60_40",
        }
        return defaults.get(slide_type, "split_60_40")

    def resolve_master_layout(self, requested: dict | None = None) -> dict:
        """Resolve deck-level cover/header/footer choices to known zone definitions."""
        request = requested if isinstance(requested, dict) else {}
        catalog = self.layout_zones
        defaults = catalog.get("defaults", {})

        def pick(group: str, key: str) -> dict:
            requested_id = request.get(key) or defaults.get(key)
            items = catalog.get(group, [])
            return next(
                (item for item in items if item.get("id") == requested_id),
                items[0] if items else {},
            )

        header = pick("header_zones", "header_zone_id")
        footer = pick("footer_zones", "footer_zone_id")
        cover = pick("cover_layouts", "cover_layout_id")
        body_y = header.get("region", {}).get("h", 78) + 4
        footer_y = footer.get("region", {}).get("y", 518)
        return {
            "cover_layout_id": cover.get("id", ""),
            "header_zone_id": header.get("id", ""),
            "footer_zone_id": footer.get("id", ""),
            "cover": cover,
            "header": header,
            "footer": footer,
            "body_region": {"x": 40, "y": body_y, "w": 880, "h": max(80, footer_y - body_y - 4)},
        }

    def get_planner_layout_rules(self) -> str:
        """Describe the curated layout choices the planning LLM is permitted to select."""
        zones = self.layout_zones
        body_ids = []
        for category, definition in self.layout_patterns.get("categories", {}).items():
            ids = [pattern.get("id", "") for pattern in definition.get("patterns", [])]
            body_ids.append(f"  {category}: {', '.join(ids)}")
        header_ids = ", ".join(item.get("id", "") for item in zones.get("header_zones", []))
        footer_ids = ", ".join(item.get("id", "") for item in zones.get("footer_zones", []))
        cover_ids = ", ".join(item.get("id", "") for item in zones.get("cover_layouts", []))
        philosophy = self.design_strategy.get("philosophy", {})
        return "\n".join([
            "## Slide Designer Principles (MANDATORY)",
            f"Core principle: {philosophy.get('core_principle', 'One claim and one proof object per slide.')}",
            "A slide exists to move a decision: express a conclusion, show its proof object, and remove decoration without meaning.",
            "A proof object is not automatically a diagram. Prefer tables, charts, comparison panels, KPI/detail cards, or compact native process cards unless a rendered diagram is explicitly requested or necessary for comprehension.",
            "Do not plan rendered diagram/image slots merely because the topic says workflow, process, flow, architecture, or pipeline.",
            "Use icons as semantic anchors for concepts or navigation, never as filler or approximated brand marks.",
            "Header and footer zones are master choices: select each once for the deck and keep them unchanged on all body slides.",
            "",
            "## Curated Layout Contract (MANDATORY JSON choices)",
            f"Cover layout IDs: {cover_ids}",
            f"Header zone IDs: {header_ids}",
            f"Footer zone IDs: {footer_ids}",
            "Body layout IDs by family (select one for each non-cover slide):",
            *body_ids,
            "A slide may include `sub_layout_ids` only when the selected body pattern needs nested composition.",
            "",
            "## OOXML Planning Boundary (MUST BE PRESERVED)",
            "Plan only elements convertible by the deterministic mapper: textbox, shape, table, chart, image, connector, and icon.",
            "Do not plan flex/grid CSS, animations, unsupported SVG ornaments, arbitrary HTML widgets, or unverified brand marks.",
            "Every planned proof object must be representable later through data-pptx-* attributes and absolute pixel geometry.",
            "Every planned top-level element must have non-overlapping x/y/w/h inside the body safe area; if nested composition is needed, allocate the parent slot first and then divide it into non-overlapping sub-slots.",
            "",
            self.get_icon_layout_rules(),
        ])

    def get_icon_layout_rules(self) -> str:
        """Describe the standard icon placement contract for planner/generator prompts."""
        catalog = self.icon_layouts
        placements = catalog.get("placements", [])
        placement_lines = [
            f"  {item.get('id')}: {item.get('description')}"
            for item in placements
        ]
        defaults = catalog.get("defaults", {})
        return "\n".join([
            "## Icon Layout Contract (MANDATORY)",
            "Icons are independent semantic elements. Prefer data-pptx-type=\"icon\" with its own absolute rectangle.",
            "Do not attach data-pptx-icon to textboxes unless preserving legacy HTML; create a separate icon element and a separate text element.",
            "The HTML icon rectangle is the source of truth for PPTX conversion: same left/top/width/height/color.",
            f"Recommended icons/slide: {defaults.get('recommended_icons_per_slide', {}).get('min', 3)}-{defaults.get('recommended_icons_per_slide', {}).get('max', 8)}; max {defaults.get('max_icons_per_slide', 12)}.",
            f"Minimum clear gap between icon and text regions: {defaults.get('min_gap_px', 8)}px.",
            "Standard icon placement IDs:",
            *placement_lines,
        ])

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
            "Content overlap: FORBIDDEN",
            "",
            self.get_icon_layout_rules(),
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
