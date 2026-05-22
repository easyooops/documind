"""Conversion engine - shared pipeline for HTML to document format conversion.

Format-agnostic components:
- pipeline.py: Orchestration (layout extract -> classify -> delegate)
- layout_resolver.py: Playwright-based element position/style extraction
- css_classifier.py: CSS property A/B/C categorization

Format-specific implementations live in src.formats.{format_id}/.
"""
