"""Rule-Based Design Quality Evaluator — replaces VLM QA with deterministic rule checks.

Validates generated HTML slides against OOXML Rule-Sets.
Produces structured scores, per-slide issues, and fix instructions.
"""

from __future__ import annotations

import re

from src.core.logging import get_logger
from src.formats.pptx.color_utils import (
    contrast_ratio,
    contrast_threshold,
    extract_colors,
    relative_luminance,
)
from src.formats.pptx.rulesets import RuleSet, get_ruleset

logger = get_logger(__name__)


class EvaluationResult:
    """Structured result from design quality evaluation."""

    def __init__(
        self,
        score: float,
        passed: bool,
        per_slide: list[dict],
        cross_slide: dict,
        fix_instructions: list[str],
        category_scores: dict[str, float],
    ):
        self.score = score
        self.passed = passed
        self.per_slide = per_slide
        self.cross_slide = cross_slide
        self.fix_instructions = fix_instructions
        self.category_scores = category_scores

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "passed": self.passed,
            "per_slide": self.per_slide,
            "cross_slide": self.cross_slide,
            "fix_instructions": self.fix_instructions,
            "category_scores": {k: round(v, 3) for k, v in self.category_scores.items()},
        }


class DesignQualityEvaluator:
    """Evaluates slides against the OOXML rule-sets."""

    def __init__(self, ruleset: RuleSet | None = None):
        self._ruleset = ruleset or get_ruleset()
        self._evaluation_config = self._ruleset.evaluation

    def evaluate(self, slides_html: list[dict], design_system: dict | None = None) -> EvaluationResult:
        """Run full evaluation across all slides."""
        threshold = self._evaluation_config.get("pass_threshold", 0.85)
        categories = self._evaluation_config.get("categories", {})

        per_slide_results = []
        all_elements: list[list[dict]] = []

        for slide_data in slides_html:
            html = slide_data.get("html", "")
            slide_idx = slide_data.get("index", 0)
            metadata = slide_data.get("metadata", {})
            slide_type = metadata.get("slide_type", "content")

            elements = self._parse_elements(html)
            all_elements.append(elements)

            slide_result = self._evaluate_slide(
                elements, slide_idx, slide_type, design_system or {}
            )
            per_slide_results.append(slide_result)

        cross_slide = self._evaluate_cross_slide(all_elements, design_system or {})

        category_scores = self._aggregate_category_scores(per_slide_results, categories)
        total_score = sum(
            category_scores.get(cat, 1.0) * spec.get("weight", 0)
            for cat, spec in categories.items()
        )

        cross_weight = 0.1
        total_score = total_score * (1 - cross_weight) + cross_slide.get("score", 1.0) * cross_weight

        passed = total_score >= threshold
        fix_instructions = self._generate_fix_instructions(per_slide_results, cross_slide)

        return EvaluationResult(
            score=total_score,
            passed=passed,
            per_slide=per_slide_results,
            cross_slide=cross_slide,
            fix_instructions=fix_instructions,
            category_scores=category_scores,
        )

    def _evaluate_slide(
        self, elements: list[dict], slide_idx: int, slide_type: str, design_system: dict
    ) -> dict:
        """Evaluate a single slide against rules."""
        issues: list[dict] = []
        category_scores: dict[str, float] = {}

        layout_score = self._check_layout(elements, slide_type, issues, slide_idx)
        category_scores["layout_compliance"] = layout_score

        typo_score = self._check_typography(elements, issues, slide_idx)
        category_scores["typography_compliance"] = typo_score

        color_score = self._check_colors(elements, design_system, issues, slide_idx)
        category_scores["color_compliance"] = color_score

        element_score = self._check_elements(elements, slide_type, issues, slide_idx)
        category_scores["element_completeness"] = element_score

        balance_score = self._check_balance(elements, issues, slide_idx)
        category_scores["visual_balance"] = balance_score

        ooxml_score = self._check_ooxml_validity(elements, issues, slide_idx)
        category_scores["ooxml_validity"] = ooxml_score

        categories = self._evaluation_config.get("categories", {})
        slide_score = sum(
            category_scores.get(cat, 1.0) * spec.get("weight", 0)
            for cat, spec in categories.items()
        )

        return {
            "index": slide_idx,
            "score": round(slide_score, 3),
            "issues": issues,
            "category_scores": category_scores,
        }

    def _check_layout(self, elements: list[dict], slide_type: str, issues: list, idx: int) -> float:
        """Check layout compliance: regions, grid, safe area, overlap."""
        score = 1.0
        canvas = self._ruleset.canvas
        safe_area = canvas.get("safe_area", {})
        canvas_w = canvas.get("canvas", {}).get("width_px", 960)
        canvas_h = canvas.get("canvas", {}).get("height_px", 540)

        safe_left = safe_area.get("left", 40)
        safe_right = canvas_w - safe_area.get("right", 40)
        safe_top = safe_area.get("top", 24)
        safe_bottom = canvas_h - safe_area.get("bottom", 14)

        preset = self._ruleset.get_slide_preset(slide_type)
        is_full_bleed = preset.get("layout", {}).get("full_bleed", False)

        for el in elements:
            pos = el.get("position", {})
            left = pos.get("left", 0)
            top = pos.get("top", 0)
            width = pos.get("width", 0)
            height = pos.get("height", 0)
            right = left + width
            bottom = top + height
            pptx_type = el.get("pptx_type", "")

            # Skip background elements
            if pptx_type == "shape" and width >= canvas_w * 0.9 and height >= canvas_h * 0.9:
                continue

            # Skip template header/footer elements (auto-injected, not content)
            if top < 75 or top >= 510:
                continue

            if not is_full_bleed and pptx_type in ("textbox", "table", "chart"):
                if left < safe_left - 4 or right > safe_right + 4:
                    score -= 0.05
                    issues.append({
                        "slide": idx, "category": "layout", "severity": "minor",
                        "message": f"Element outside safe area (x:{left}-{right}, safe:{safe_left}-{safe_right})"
                    })
                if top < safe_top - 4 or bottom > safe_bottom + 4:
                    score -= 0.05
                    issues.append({
                        "slide": idx, "category": "layout", "severity": "minor",
                        "message": f"Element outside safe area (y:{top}-{bottom}, safe:{safe_top}-{safe_bottom})"
                    })

        content_elements = [
            e for e in elements
            if e.get("pptx_type") in ("textbox", "table", "chart")
        ]
        for i, el_a in enumerate(content_elements):
            for el_b in content_elements[i + 1:]:
                if self._is_nested_content(el_a, el_b, elements):
                    continue
                overlap = self._calculate_overlap(el_a, el_b)
                if overlap > 0.20:
                    score -= 0.10
                    issues.append({
                        "slide": idx, "category": "layout", "severity": "critical",
                        "message": f"Content element overlap detected ({overlap:.0%}) — elements must not overlap"
                    })
                elif overlap > 0.05:
                    score -= 0.03
                    issues.append({
                        "slide": idx, "category": "layout", "severity": "minor",
                        "message": f"Minor element overlap ({overlap:.0%}) — increase spacing"
                    })

        for el in content_elements:
            pos = el.get("position", {})
            text = el.get("text_content", "")
            if text and pos.get("height", 0) > 0:
                styles = el.get("styles", {})
                font_size = self._parse_px(styles.get("font-size", "16"))
                line_height = 1.5
                lh_str = styles.get("line-height", "")
                if lh_str:
                    try:
                        line_height = float(lh_str.replace("em", "").replace("%", ""))
                        if line_height > 10:
                            line_height = line_height / 100.0
                    except (ValueError, TypeError):
                        pass
                lines = text.count("\n") + 1
                needed_height = lines * font_size * line_height + 16
                available_height = pos.get("height", 0)
                if needed_height > available_height * 1.5:
                    score -= 0.06
                    issues.append({
                        "slide": idx, "category": "layout", "severity": "major",
                        "message": f"Text clipping risk: needs {needed_height:.0f}px but container is {available_height:.0f}px"
                    })

        return max(0.0, min(1.0, score))

    def _check_typography(self, elements: list[dict], issues: list, idx: int) -> float:
        """Check typography: fonts, sizes, overflow, consistency."""
        score = 1.0
        typo_rules = self._ruleset.typography
        fonts_allowed = typo_rules.get("fonts", {}).get("allowed", [])
        text_fitting = typo_rules.get("text_fitting", {})
        max_chars = text_fitting.get("max_chars_per_role", {})

        # Allowed font sizes from font_spec (tolerance ±1px)
        allowed_sizes = {9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 28, 32, 36, 42, 48}
        used_sizes: set[int] = set()
        used_weights: set[str] = set()
        expressive_treatments = 0
        text_element_count = 0

        for el in elements:
            if el.get("pptx_type") not in ("textbox", "shape"):
                continue
            styles = el.get("styles", {})
            text = el.get("text_content", "")
            if not text:
                continue
            text_element_count += 1

            font_family = styles.get("font-family", "")
            if font_family and fonts_allowed:
                found_valid = any(f.lower() in font_family.lower() for f in fonts_allowed)
                if not found_valid and font_family not in ("", "inherit"):
                    score -= 0.04
                    issues.append({
                        "slide": idx, "category": "typography", "severity": "minor",
                        "message": f"Unapproved font used: {font_family}"
                    })

            font_size_str = styles.get("font-size", "")
            font_size = self._parse_px(font_size_str)
            if font_size > 0:
                used_sizes.add(round(font_size))
                # Check if size is from allowed set (with ±1px tolerance)
                size_valid = any(abs(font_size - s) <= 1 for s in allowed_sizes)
                if not size_valid:
                    score -= 0.05
                    issues.append({
                        "slide": idx, "category": "typography", "severity": "major",
                        "message": f"Non-standard font size ({font_size}px) — use approved sizes: 10-14, 16, 22, 32, 42px"
                    })

                if font_size >= 36:
                    max_title_chars = max_chars.get("title", 25)
                    if len(text) > max_title_chars:
                        score -= 0.08
                        issues.append({
                            "slide": idx, "category": "typography", "severity": "major",
                            "message": f"Title text overflow: {len(text)} chars > {max_title_chars} char limit"
                        })
                elif font_size >= 13:
                    width = el.get("position", {}).get("width", 800)
                    char_width = font_size * 0.85
                    chars_per_line = int(width / char_width) if char_width > 0 else 40
                    lines = text.split("\n")
                    for line in lines:
                        if len(line) > chars_per_line * 1.1:
                            score -= 0.08
                            issues.append({
                                "slide": idx, "category": "typography", "severity": "major",
                                "message": f"Text clipping risk: {len(line)} chars in {width}px container (max ~{chars_per_line})"
                            })
                            break

            line_height_str = styles.get("line-height", "")
            if line_height_str:
                try:
                    lh = float(line_height_str.replace("em", "").replace("px", "").replace("%", ""))
                    if lh > 10:
                        lh = lh / 100.0
                    if lh < 1.2:
                        score -= 0.06
                        issues.append({
                            "slide": idx, "category": "typography", "severity": "major",
                            "message": f"Line-height too tight ({lh}) — minimum recommended: 1.3"
                        })
                except (ValueError, TypeError):
                    pass

            font_weight = styles.get("font-weight", "")
            if font_weight:
                used_weights.add(str(font_weight).lower())
            if font_weight and font_size and font_size <= 14:
                if font_weight in ("700", "800", "900", "bold"):
                    score -= 0.03
                    issues.append({
                        "slide": idx, "category": "typography", "severity": "minor",
                        "message": f"Body text ({font_size}px) should not be bold (weight:{font_weight})"
                    })

            # Font size consistency: detect sizes outside expected ranges
            if font_size > 0:
                if font_size < 10:
                    score -= 0.06
                    issues.append({
                        "slide": idx, "category": "typography", "severity": "major",
                        "message": f"Font too small ({font_size}px) — minimum 10px for readability"
                    })
                elif font_size > 48:
                    score -= 0.04
                    issues.append({
                        "slide": idx, "category": "typography", "severity": "minor",
                        "message": f"Font too large ({font_size}px) — maximum 48px recommended"
                    })

            if str(styles.get("font-style", "")).lower() == "italic":
                expressive_treatments += 1
            decoration = str(styles.get("text-decoration", "")).lower()
            if any(token in decoration for token in ("underline", "line-through")):
                expressive_treatments += 1
            if styles.get("text-shadow") or styles.get("letter-spacing"):
                expressive_treatments += 1

        if text_element_count >= 4:
            normalized_weights = {
                "700" if weight == "bold" else "400" if weight == "normal" else weight
                for weight in used_weights
            }
            if len(used_sizes) < 3 or len(normalized_weights) < 2:
                score -= 0.08
                issues.append({
                    "slide": idx,
                    "category": "typography",
                    "severity": "major",
                    "message": (
                        "Typography lacks visual hierarchy: use at least 3 font sizes "
                        "and 2 font weights across title, labels, body, and emphasis text"
                    ),
                })
            if expressive_treatments == 0:
                score -= 0.04
                issues.append({
                    "slide": idx,
                    "category": "typography",
                    "severity": "minor",
                    "message": (
                        "Typography is too plain: add one restrained text treatment such as "
                        "italic label, underline accent, letter spacing, or text shadow"
                    ),
                })

        return max(0.0, min(1.0, score))

    def _check_colors(self, elements: list[dict], design_system: dict, issues: list, idx: int) -> float:
        """Check color compliance: palette usage, contrast."""
        score = 1.0
        if not design_system:
            score -= self._check_text_contrast(elements, issues, idx)
            return max(0.0, min(1.0, score))

        palette_colors = set()
        for key in ("primary", "secondary", "accent", "background", "surface", "text_primary", "text_secondary", "tint"):
            val = design_system.get(key, "")
            if val:
                palette_colors.add(val.lower().lstrip("#"))

        slide_colors: set[str] = set()
        for el in elements:
            styles = el.get("styles", {})
            for prop in ("color", "background-color"):
                color_val = styles.get(prop, "")
                hex_color = self._extract_hex(color_val)
                if hex_color:
                    slide_colors.add(hex_color.lower())

        max_colors = self._ruleset.color.get("usage_rules", {}).get("max_unique_colors_per_slide", 6)
        if len(slide_colors) > max_colors:
            score -= 0.05
            issues.append({
                "slide": idx, "category": "color", "severity": "minor",
                "message": f"Color count exceeded: {len(slide_colors)} > {max_colors} limit"
            })

        off_palette = slide_colors - palette_colors
        if off_palette and len(off_palette) > 2:
            score -= 0.04
            issues.append({
                "slide": idx, "category": "color", "severity": "minor",
                "message": f"Off-palette colors: {len(off_palette)} used"
            })

        white_count = 0
        colored_bg_count = 0
        for el in elements:
            styles = el.get("styles", {})
            bg = styles.get("background-color", "") or styles.get("background", "")
            hex_bg = self._extract_hex(bg)
            if hex_bg:
                if hex_bg.lower() in ("ffffff", "fff"):
                    white_count += 1
                else:
                    colored_bg_count += 1

        total_bg = white_count + colored_bg_count
        if total_bg > 0:
            white_ratio = white_count / total_bg
            if white_ratio > 0.7:
                score -= 0.08
                issues.append({
                    "slide": idx, "category": "color", "severity": "major",
                    "message": f"Excessive white backgrounds: {white_count}/{total_bg} ({white_ratio:.0%}) — use colored tints"
                })

        score -= self._check_text_contrast(elements, issues, idx)

        return max(0.0, min(1.0, score))

    def _check_text_contrast(self, elements: list[dict], issues: list, idx: int) -> float:
        """Check each text element against its own or directly backing fill."""
        penalty = 0.0
        for element_index, el in enumerate(elements):
            if el.get("pptx_type") not in ("textbox", "shape"):
                continue
            if not el.get("text_content", "").strip():
                continue
            styles = el.get("styles", {})
            bg_colors = self._element_background_colors(styles)
            if not bg_colors:
                bg_colors = self._backing_fill_colors(el, elements[:element_index])
            if not bg_colors:
                continue
            fg = self._extract_hex(styles.get("color", "")) or "111827"
            font_size = self._parse_px(styles.get("font-size", "16"))
            font_weight = str(styles.get("font-weight", "400"))
            bold = font_weight in {"bold", "600", "700", "800", "900"} or (
                font_weight.isdigit() and int(font_weight) >= 600
            )
            threshold = contrast_threshold(font_size, bold)
            min_ratio = min(contrast_ratio(fg, bg) for bg in bg_colors)
            if min_ratio >= threshold:
                continue
            penalty += 0.08
            bg_label = ", ".join(f"#{bg.upper()}" for bg in bg_colors[:2])
            issues.append({
                "slide": idx,
                "category": "color",
                "severity": "major",
                "message": (
                    f"Text contrast failed ({min_ratio:.1f}:1 < {threshold:.1f}:1) "
                    f"against background {bg_label}; use dark same-family text on light "
                    "fills and white/near-white text on dark fills"
                ),
            })
        return min(0.24, penalty)

    def _element_background_colors(self, styles: dict) -> list[str]:
        colors: list[str] = []
        for prop in ("background-color", "background"):
            colors.extend(extract_colors(styles.get(prop, "")))
        return colors

    def _backing_fill_colors(self, text_element: dict, prior_elements: list[dict]) -> list[str]:
        text_box = text_element.get("position", {})
        candidates: list[tuple[float, list[str]]] = []
        for prior in prior_elements:
            if prior.get("pptx_type") not in ("shape", "textbox"):
                continue
            colors = self._element_background_colors(prior.get("styles", {}))
            if not colors:
                continue
            box = prior.get("position", {})
            overlap = self._calculate_overlap(text_element, prior)
            contains_center = self._box_contains_center(box, text_box)
            is_full_background = (
                box.get("width", 0) >= 900
                and box.get("height", 0) >= 480
                and box.get("left", 0) <= 10
                and box.get("top", 0) <= 10
            )
            dark_partial_backing = overlap >= 0.15 and any(
                relative_luminance(color) < 0.25 for color in colors
            )
            if overlap >= 0.45 or contains_center or is_full_background or dark_partial_backing:
                score = (
                    overlap
                    + (0.25 if contains_center else 0)
                    + (0.1 if is_full_background else 0)
                    + (0.15 if dark_partial_backing else 0)
                )
                candidates.append((score, colors))
        if not candidates:
            return []
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _box_contains_center(self, container: dict, inner: dict) -> bool:
        cx = inner.get("left", 0) + inner.get("width", 0) / 2
        cy = inner.get("top", 0) + inner.get("height", 0) / 2
        return (
            container.get("left", 0) <= cx <= container.get("left", 0) + container.get("width", 0)
            and container.get("top", 0) <= cy <= container.get("top", 0) + container.get("height", 0)
        )

    def _check_elements(self, elements: list[dict], slide_type: str, issues: list, idx: int) -> float:
        """Check element completeness and diversity."""
        score = 1.0
        preset = self._ruleset.get_slide_preset(slide_type)
        layout_rules = self._ruleset.layout

        min_elements = layout_rules.get("element_count", {}).get("min_per_slide", 3)
        if len(elements) < min_elements:
            score -= 0.10
            issues.append({
                "slide": idx, "category": "elements", "severity": "major",
                "message": f"Insufficient element count: {len(elements)} < minimum {min_elements}"
            })

        element_types = set(e.get("pptx_type", "") for e in elements)
        min_types = layout_rules.get("element_count", {}).get("min_unique_types_per_slide", 2)
        if len(element_types) < min_types:
            score -= 0.05
            issues.append({
                "slide": idx, "category": "elements", "severity": "minor",
                "message": f"Insufficient element diversity: {len(element_types)} types < minimum {min_types} types"
            })

        required = preset.get("required_elements", [])
        has_title = any(
            e.get("pptx_type") == "textbox" and self._parse_px(e.get("styles", {}).get("font-size", "0")) >= 24
            for e in elements
        )
        if "title" in required and not has_title:
            score -= 0.08
            issues.append({
                "slide": idx, "category": "elements", "severity": "major",
                "message": "Required element missing: title (no text >= 24px found)"
            })

        empty_containers = [
            el for el in elements
            if self._is_empty_large_container(el, elements)
        ]
        if empty_containers:
            score -= min(0.24, 0.12 * len(empty_containers))
            for el in empty_containers[:3]:
                pos = el.get("position", {})
                issues.append({
                    "slide": idx,
                    "category": "elements",
                    "severity": "major",
                    "message": (
                        "Empty visible container detected: fill this card/box/image slot "
                        "with text, table/chart, icon, or rendered image, or remove it "
                        f"(x:{pos.get('left', 0):.0f}, y:{pos.get('top', 0):.0f}, "
                        f"w:{pos.get('width', 0):.0f}, h:{pos.get('height', 0):.0f})"
                    ),
                })

        return max(0.0, min(1.0, score))

    def _is_empty_large_container(self, element: dict, elements: list[dict]) -> bool:
        pptx_type = element.get("pptx_type", "")
        if pptx_type not in {"shape", "textbox", "image"}:
            return False
        attrs = element.get("attributes", {})
        if attrs.get("data-pptx-region") in {"background", "header", "footer"}:
            return False
        pos = element.get("position", {})
        width = pos.get("width", 0)
        height = pos.get("height", 0)
        if width < 150 or height < 80 or width * height < 18000:
            return False
        if width >= 900 and height >= 480 and pos.get("left", 0) <= 10 and pos.get("top", 0) <= 10:
            return False
        if self._element_has_payload(element):
            return False

        styles = element.get("styles", {})
        has_visible_box = (
            bool(self._element_background_colors(styles))
            or any(prop.startswith("border") and str(value).strip() for prop, value in styles.items())
            or bool(styles.get("box-shadow"))
            or pptx_type == "image"
        )
        if not has_visible_box:
            return False

        for other in elements:
            if other is element:
                continue
            if not self._element_has_payload(other):
                continue
            overlap = self._calculate_overlap(other, element)
            if overlap >= 0.55 or self._box_contains_center(pos, other.get("position", {})):
                return False
        return True

    def _element_has_payload(self, element: dict) -> bool:
        if element.get("text_content", "").strip():
            return True
        pptx_type = element.get("pptx_type", "")
        attrs = element.get("attributes", {})
        if pptx_type in {"table", "chart", "icon", "connector"}:
            return True
        if pptx_type == "image":
            return bool(attrs.get("data-pptx-image-path") or attrs.get("data-pptx-image-src"))
        return False

    def _check_balance(self, elements: list[dict], issues: list, idx: int) -> float:
        """Check visual balance: whitespace, distribution."""
        score = 1.0
        canvas_w = self._ruleset.canvas.get("canvas", {}).get("width_px", 960)
        canvas_h = self._ruleset.canvas.get("canvas", {}).get("height_px", 540)
        total_area = canvas_w * canvas_h

        if not elements:
            return 0.5

        content_area = 0
        for el in elements:
            pos = el.get("position", {})
            w = pos.get("width", 0)
            h = pos.get("height", 0)
            content_area += w * h

        coverage = min(content_area / total_area, 1.0) if total_area > 0 else 0
        balance_rules = self._ruleset.layout.get("balance", {})
        min_coverage = balance_rules.get("min_content_coverage_ratio", 0.35)
        max_coverage = balance_rules.get("max_content_coverage_ratio", 0.85)

        if coverage < min_coverage:
            score -= 0.06
            issues.append({
                "slide": idx, "category": "balance", "severity": "minor",
                "message": f"Insufficient content: area ratio {coverage:.0%} < minimum {min_coverage:.0%}"
            })
        elif coverage > max_coverage:
            score -= 0.06
            issues.append({
                "slide": idx, "category": "balance", "severity": "minor",
                "message": f"Insufficient whitespace: area ratio {coverage:.0%} > maximum {max_coverage:.0%}"
            })

        return max(0.0, min(1.0, score))

    def _check_ooxml_validity(self, elements: list[dict], issues: list, idx: int) -> float:
        """Check OOXML validity: shape types, EMU bounds, data attributes."""
        score = 1.0
        valid_shapes = set()
        shapes_schema = self._ruleset.shapes
        for cat_data in shapes_schema.get("categories", {}).values():
            valid_shapes.update(cat_data.get("types", []))

        canvas = self._ruleset.canvas.get("canvas", {})
        max_w = canvas.get("width_px", 960)
        max_h = canvas.get("height_px", 540)

        for el in elements:
            pptx_shape = el.get("pptx_shape", "")
            if pptx_shape and pptx_shape not in valid_shapes:
                score -= 0.10
                issues.append({
                    "slide": idx, "category": "ooxml", "severity": "critical",
                    "message": f"Invalid shape type: '{pptx_shape}'"
                })

            pos = el.get("position", {})
            w = pos.get("width", 0)
            h = pos.get("height", 0)
            if w > max_w * 1.1 or h > max_h * 1.1:
                score -= 0.05
                issues.append({
                    "slide": idx, "category": "ooxml", "severity": "major",
                    "message": f"Element size exceeded: {w}×{h}px (canvas: {max_w}×{max_h})"
                })

        return max(0.0, min(1.0, score))

    def _evaluate_cross_slide(self, all_elements: list[list[dict]], design_system: dict) -> dict:
        """Evaluate consistency across all slides."""
        issues = []
        score = 1.0

        all_fonts: list[set] = []
        all_title_sizes: list[float] = []
        header_y_positions: list[list[float]] = []
        footer_y_positions: list[list[float]] = []

        for slide_idx, slide_elements in enumerate(all_elements):
            slide_fonts: set[str] = set()
            slide_header_ys = []
            slide_footer_ys = []
            for el in slide_elements:
                styles = el.get("styles", {})
                pos = el.get("position", {})
                font = styles.get("font-family", "")
                if font:
                    slide_fonts.add(font.split(",")[0].strip().strip("'\""))
                fs = self._parse_px(styles.get("font-size", "0"))
                if fs >= 24:
                    all_title_sizes.append(fs)

                top = pos.get("top", 0)
                if 20 <= top <= 100:
                    slide_header_ys.append(top)
                elif top >= 478:
                    slide_footer_ys.append(top)

            all_fonts.append(slide_fonts)
            header_y_positions.append(slide_header_ys)
            footer_y_positions.append(slide_footer_ys)

        if len(all_fonts) > 1:
            font_union = set()
            for sf in all_fonts:
                font_union.update(sf)
            if len(font_union) > 3:
                score -= 0.08
                issues.append({
                    "category": "consistency",
                    "message": f"Font inconsistency: {len(font_union)} fonts used across slides"
                })

        if all_title_sizes and len(set(all_title_sizes)) > 2:
            score -= 0.05
            issues.append({
                "category": "consistency",
                "message": f"Title size inconsistency: {sorted(set(all_title_sizes))}"
            })

        content_slides = len(all_elements)
        slides_with_header = sum(1 for ys in header_y_positions if ys)
        slides_with_footer = sum(1 for ys in footer_y_positions if ys)

        if content_slides > 2:
            header_ratio = slides_with_header / content_slides
            footer_ratio = slides_with_footer / content_slides
            if header_ratio < 0.7:
                score -= 0.12
                issues.append({
                    "category": "consistency",
                    "message": f"Header inconsistency: only {slides_with_header}/{content_slides} slides have header elements"
                })
            if footer_ratio < 0.5:
                score -= 0.08
                issues.append({
                    "category": "consistency",
                    "message": f"Footer inconsistency: only {slides_with_footer}/{content_slides} slides have footer elements"
                })

        if design_system:
            ds_accent = (design_system.get("accent", "") or "").lower().lstrip("#")
            ds_primary = (design_system.get("primary", "") or "").lower().lstrip("#")
            slides_using_accent = 0
            slides_using_primary = 0
            for slide_elements in all_elements:
                slide_colors_set = set()
                for el in slide_elements:
                    styles = el.get("styles", {})
                    for prop in ("color", "background-color", "background"):
                        hx = self._extract_hex(styles.get(prop, ""))
                        if hx:
                            slide_colors_set.add(hx.lower())
                if ds_accent and ds_accent in slide_colors_set:
                    slides_using_accent += 1
                if ds_primary and ds_primary in slide_colors_set:
                    slides_using_primary += 1

            if content_slides > 3:
                if ds_accent and slides_using_accent / content_slides < 0.4:
                    score -= 0.05
                    issues.append({
                        "category": "consistency",
                        "message": f"Design system accent color under-used: only {slides_using_accent}/{content_slides} slides"
                    })

        return {"score": max(0.0, score), "issues": issues}

    def _generate_fix_instructions(self, per_slide: list[dict], cross_slide: dict) -> list[str]:
        """Generate actionable fix instructions from evaluation issues."""
        instructions = []
        for slide_result in per_slide:
            idx = slide_result["index"]
            for issue in slide_result.get("issues", []):
                if issue.get("severity") in ("critical", "major"):
                    instructions.append(f"Slide {idx}: {issue['message']}")

        for issue in cross_slide.get("issues", []):
            instructions.append(f"All slides: {issue['message']}")

        return instructions[:10]

    def _aggregate_category_scores(self, per_slide: list[dict], categories: dict) -> dict[str, float]:
        """Average category scores across all slides."""
        if not per_slide:
            return {cat: 1.0 for cat in categories}

        aggregated: dict[str, float] = {}
        for cat in categories:
            scores = [
                s.get("category_scores", {}).get(cat, 1.0)
                for s in per_slide
            ]
            aggregated[cat] = sum(scores) / len(scores) if scores else 1.0

        return aggregated

    def _parse_elements(self, html: str) -> list[dict]:
        """Parse HTML into element dicts for evaluation."""
        from bs4 import BeautifulSoup

        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        elements = []

        for node in soup.find_all(attrs={"data-pptx-type": True}):
            style_str = node.get("style", "")
            styles = self._parse_inline_styles(style_str)
            position = {
                "left": self._parse_px(styles.get("left", "0")),
                "top": self._parse_px(styles.get("top", "0")),
                "width": self._parse_px(styles.get("width", "100")),
                "height": self._parse_px(styles.get("height", "50")),
            }

            text_parts = []
            for child in node.children:
                if hasattr(child, "attrs") and child.get("data-pptx-type"):
                    continue
                if hasattr(child, "get_text"):
                    t = child.get_text(strip=True)
                    if t:
                        text_parts.append(t)
                elif isinstance(child, str) and child.strip():
                    text_parts.append(child.strip())

            elements.append({
                "pptx_type": node.get("data-pptx-type", "shape"),
                "pptx_shape": node.get("data-pptx-shape", ""),
                "position": position,
                "styles": styles,
                "text_content": "\n".join(text_parts),
                "attributes": {
                    name: (" ".join(value) if isinstance(value, list) else value)
                    for name, value in node.attrs.items()
                    if str(name).startswith("data-pptx-")
                },
            })

        return elements

    def _parse_inline_styles(self, style_str: str) -> dict:
        styles = {}
        if not style_str:
            return styles
        for decl in style_str.split(";"):
            decl = decl.strip()
            if ":" not in decl:
                continue
            prop, _, value = decl.partition(":")
            styles[prop.strip().lower()] = value.strip()
        return styles

    def _parse_px(self, value: str) -> float:
        if not value:
            return 0.0
        match = re.match(r"([-\d.]+)", value.replace("px", "").strip())
        return float(match.group(1)) if match else 0.0

    def _extract_hex(self, color_str: str) -> str:
        if not color_str:
            return ""
        match = re.search(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", color_str)
        if match:
            hex_val = match.group(1)
            if len(hex_val) == 3:
                hex_val = "".join(c * 2 for c in hex_val)
            return hex_val
        return ""

    def _calculate_overlap(self, el_a: dict, el_b: dict) -> float:
        pos_a = el_a.get("position", {})
        pos_b = el_b.get("position", {})

        ax1, ay1 = pos_a.get("left", 0), pos_a.get("top", 0)
        ax2, ay2 = ax1 + pos_a.get("width", 0), ay1 + pos_a.get("height", 0)
        bx1, by1 = pos_b.get("left", 0), pos_b.get("top", 0)
        bx2, by2 = bx1 + pos_b.get("width", 0), by1 + pos_b.get("height", 0)

        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)

        if ix1 >= ix2 or iy1 >= iy2:
            return 0.0

        intersection = (ix2 - ix1) * (iy2 - iy1)
        area_a = pos_a.get("width", 1) * pos_a.get("height", 1)
        area_b = pos_b.get("width", 1) * pos_b.get("height", 1)
        smaller_area = min(area_a, area_b)

        return intersection / smaller_area if smaller_area > 0 else 0.0

    def _is_nested_content(self, el_a: dict, el_b: dict, all_elements: list[dict]) -> bool:
        """Check if one element is nested inside a shape container (intentional overlap)."""
        pos_a = el_a.get("position", {})
        pos_b = el_b.get("position", {})

        for el in all_elements:
            if el.get("pptx_type") == "shape":
                shape_pos = el.get("position", {})
                sl, st = shape_pos.get("left", 0), shape_pos.get("top", 0)
                sr = sl + shape_pos.get("width", 0)
                sb = st + shape_pos.get("height", 0)
                canvas = self._ruleset.canvas.get("canvas", {})
                canvas_w = canvas.get("width_px", 960)
                canvas_h = canvas.get("height_px", 540)
                if (
                    shape_pos.get("width", 0) >= canvas_w * 0.9
                    and shape_pos.get("height", 0) >= canvas_h * 0.9
                ):
                    # The injected slide background contains every object and must
                    # not turn real table/card collisions into intentional nesting.
                    continue

                a_in_shape = (pos_a.get("left", 0) >= sl - 2 and pos_a.get("top", 0) >= st - 2
                              and pos_a.get("left", 0) + pos_a.get("width", 0) <= sr + 2
                              and pos_a.get("top", 0) + pos_a.get("height", 0) <= sb + 2)
                b_in_shape = (pos_b.get("left", 0) >= sl - 2 and pos_b.get("top", 0) >= st - 2
                              and pos_b.get("left", 0) + pos_b.get("width", 0) <= sr + 2
                              and pos_b.get("top", 0) + pos_b.get("height", 0) <= sb + 2)
                if a_in_shape and b_in_shape:
                    return True

        return False
