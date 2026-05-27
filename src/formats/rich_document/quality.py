"""Native document quality evaluation based on format-specific rule sets."""

# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path

from .spec import has_substantive_content


def evaluate_document(spec: dict, design: dict, ruleset: dict, output_path: str | None) -> dict:
    """Evaluate content richness, template styling and final native artifact."""
    if design.get("native_template_mode") == "populate_existing_form":
        output_ready = bool(
            output_path
            and Path(output_path).exists()
            and Path(output_path).stat().st_size > 0
        )
        issues = [] if output_ready else ["The populated template file was not rendered."]
        return {
            "passed": output_ready,
            "score": 1.0 if output_ready else 0.0,
            "issues": issues,
            "fix_instructions": issues,
            "category_scores": {
                "template_preservation": 1.0 if output_ready else 0.0,
                "native_output": 1.0 if output_ready else 0.0,
            },
        }
    issues: list[str] = []
    section_count = len(spec.get("sections", []))
    blocks = [
        block.get("type")
        for section in spec.get("sections", [])
        for block in section.get("blocks", [])
    ]
    required = set(ruleset["quality"]["required_blocks"])
    present = set(blocks)

    if not has_substantive_content(spec):
        issues.append("The document has no substantive reader-facing content.")
    if section_count < ruleset["quality"]["min_sections"]:
        issues.append(f"At least {ruleset['quality']['min_sections']} designed sections are required.")
    for missing in sorted(required - present):
        issues.append(f"Missing required native design block: {missing}.")
    if not design.get("template_name") or not design.get("design_rationale"):
        issues.append("A template-derived design rationale is required.")
    if not output_path or not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
        issues.append("The native output file was not rendered.")

    total_checks = 4 + len(required)
    passed_checks = total_checks - len(issues)
    score = max(0.0, min(1.0, passed_checks / total_checks))
    threshold = float(ruleset["quality"].get("minimum_score", 0.78))
    return {
        "passed": score >= threshold,
        "score": score,
        "issues": issues,
        "fix_instructions": issues,
        "category_scores": {
            "content_structure": 1.0 if section_count >= ruleset["quality"]["min_sections"] else 0.4,
            "component_richness": (len(present & required) / len(required)) if required else 1.0,
            "template_design": 1.0 if design.get("template_name") else 0.0,
            "native_output": 1.0 if output_path and Path(output_path).exists() else 0.0,
        },
    }
