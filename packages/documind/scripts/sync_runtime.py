"""Sync the minimal DocuMind runtime into the SDK package source tree."""

from __future__ import annotations

import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
TARGET_ROOT = PACKAGE_ROOT / "src" / "src"
OVERLAY_ROOT = PACKAGE_ROOT / "overlays" / "src"

EXCLUDED_DIRS = {
    "__pycache__",
    "api",
    "pdf",
}

EXCLUDED_FILES = {
    SOURCE_ROOT / "__main__.py",
    SOURCE_ROOT / "cli.py",
    SOURCE_ROOT / "main.py",
    SOURCE_ROOT / "schemas" / "api.py",
    SOURCE_ROOT / "infrastructure" / "database.py",
    SOURCE_ROOT / "infrastructure" / "models.py",
    SOURCE_ROOT / "infrastructure" / "storage.py",
    SOURCE_ROOT / "formats" / "docx" / "preview.py",
    SOURCE_ROOT / "formats" / "pptx" / "agents" / "configs" / "design_evaluator.json",
    SOURCE_ROOT / "formats" / "pptx" / "agents" / "configs" / "vlm_qa.json",
    SOURCE_ROOT / "formats" / "pptx" / "agents" / "nodes" / "design_evaluator.py",
    SOURCE_ROOT / "formats" / "pptx" / "agents" / "nodes" / "vlm_qa.py",
    SOURCE_ROOT / "formats" / "pptx" / "template_visual.py",
    SOURCE_ROOT / "formats" / "pptx" / "visual_renderer.py",
}


def _ignore(directory: str, names: list[str]) -> set[str]:
    root = Path(directory)
    ignored = {name for name in names if name in EXCLUDED_DIRS}
    for name in names:
        path = root / name
        if path in EXCLUDED_FILES:
            ignored.add(name)
    return ignored


def _copy_overlay() -> None:
    if not OVERLAY_ROOT.exists():
        return
    for source in OVERLAY_ROOT.rglob("*"):
        if source.is_dir():
            continue
        relative = source.relative_to(OVERLAY_ROOT)
        target = TARGET_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _patch_engine() -> None:
    path = TARGET_ROOT / "engine.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'DocumentType = Literal["pptx", "docx", "pdf", "md", "xlsx", "hwp"]',
        'DocumentType = Literal["pptx", "docx", "md", "xlsx", "hwp"]',
    )
    text = text.replace('    "pdf": "application/pdf",\n', "")
    text = text.replace(
        '    elif format_id == "pdf":\n'
        '        from src.formats.pdf.orchestrator import compile_pdf_pipeline\n'
        '        return compile_pdf_pipeline()\n',
        "",
    )
    text = text.replace("pptx|docx|pdf|md|xlsx|hwp", "pptx|docx|md|xlsx|hwp")
    path.write_text(text, encoding="utf-8")


def _patch_native_document_pipeline() -> None:
    path = TARGET_ROOT / "formats" / "rich_document" / "orchestrator.py"
    text = path.read_text(encoding="utf-8")
    old = (
        '    graph.add_node("quality_evaluate", quality_evaluate)\n'
        '    graph.add_node("export_document", export_document)\n'
        '    graph.add_node("reject_document", reject_document)\n'
        '    graph.set_entry_point("init_document_context")\n'
        '    graph.add_edge("init_document_context", "interpret_request")\n'
        '    graph.add_edge("interpret_request", "template_design")\n'
        '    graph.add_conditional_edges("template_design", route_research, {"research": "research", "plan": "document_plan"})\n'
        '    graph.add_edge("research", "document_plan")\n'
        '    graph.add_edge("document_plan", "native_render")\n'
        '    graph.add_edge("native_render", "quality_evaluate")\n'
        '    graph.add_conditional_edges(\n'
        '        "quality_evaluate",\n'
        '        route_quality,\n'
        '        {"plan": "document_plan", "export": "export_document", "fail": "reject_document"},\n'
        '    )\n'
        '    graph.add_edge("export_document", END)\n'
    )
    new = (
        '    graph.add_node("export_document", export_document)\n'
        '    graph.set_entry_point("init_document_context")\n'
        '    graph.add_edge("init_document_context", "interpret_request")\n'
        '    graph.add_edge("interpret_request", "template_design")\n'
        '    graph.add_conditional_edges("template_design", route_research, {"research": "research", "plan": "document_plan"})\n'
        '    graph.add_edge("research", "document_plan")\n'
        '    graph.add_edge("document_plan", "native_render")\n'
        '    graph.add_edge("native_render", "export_document")\n'
        '    graph.add_edge("export_document", END)\n'
    )
    if old not in text:
        raise RuntimeError("Could not patch native document pipeline for SDK build.")
    text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def _patch_template_analysis() -> None:
    path = TARGET_ROOT / "formats" / "rich_document" / "template_analysis.py"
    text = path.read_text(encoding="utf-8")
    old = (
        '    elif extension == ".pdf":\n'
        '        profile["reference_mode"] = "visual_layout_reference"\n'
        '        try:\n'
        '            import fitz\n'
        '\n'
        '            document = fitz.open(stream=content, filetype="pdf")\n'
        '            fields = []\n'
        '            for page in document:\n'
        '                fields.extend(\n'
        '                    str(widget.field_name)\n'
        '                    for widget in (page.widgets() or [])\n'
        '                    if widget.field_name\n'
        '                )\n'
        '            document.close()\n'
        '            profile["form_fields"] = fields[:80]\n'
        '            if fields:\n'
        '                profile["template_mode"] = "populate_uploaded_form"\n'
        '        except (ImportError, RuntimeError, ValueError):\n'
        '            pass\n'
    )
    new = (
        '    elif extension == ".pdf":\n'
        '        profile["warning"] = "PDF templates are not supported by the lightweight SDK."\n'
    )
    if old not in text:
        raise RuntimeError("Could not patch PDF template analysis for SDK build.")
    text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def _patch_pptx_mapper() -> None:
    path = TARGET_ROOT / "formats" / "pptx" / "mapper" / "engine.py"
    text = path.read_text(encoding="utf-8")
    old = (
        '        self._add_shape(slide, element)\n'
        '\n'
        '    def _image_geometry_for_element'
    )
    new = (
        '        logger.info(\n'
        '            "image_element.sdk_missing_asset_skipped",\n'
        '            prompt=bool(image_prompt),\n'
        '            path=bool(image_path),\n'
        '        )\n'
        '        return\n'
        '\n'
        '    def _image_geometry_for_element'
    )
    if old not in text:
        raise RuntimeError("Could not patch PPTX mapper image fallback for SDK build.")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def _apply_sdk_patches() -> None:
    _copy_overlay()
    _patch_engine()
    _patch_native_document_pipeline()
    _patch_template_analysis()
    _patch_pptx_mapper()


def main() -> None:
    if TARGET_ROOT.exists():
        shutil.rmtree(TARGET_ROOT)
    shutil.copytree(SOURCE_ROOT, TARGET_ROOT, ignore=_ignore)
    _apply_sdk_patches()


if __name__ == "__main__":
    main()
