from io import BytesIO
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu

from src.api.v1.documents import _embed_pptx_preview_assets
from src.formats.pptx.agents.nodes.html_generator import (
    _inject_fixed_template,
    _inject_visual_asset_images,
    _materialize_html_icon_node,
)
from src.formats.pptx.agents.nodes.render_convert import (
    _embed_cached_icons,
    _embed_local_images,
    _normalize_slide_html,
    _normalize_legacy_icon_nodes,
)
from src.formats.pptx.agents.nodes.unified_planner import _normalize_blueprints
from src.formats.pptx.agents.nodes.unified_planner import _extract_slide_revision_instructions
from src.formats.pptx.agents.nodes.unified_planner import _normalize_revision_scope
from src.formats.pptx.agents.nodes.visual_asset_planner import (
    METHOD_DIAGRAMS,
    _diagrams_node_candidates,
    _diagrams_topology,
    _fallback_plan,
    _layout_nodes,
    _negative_visual_asset_signal,
    _normalize_plan,
    _parse_mermaid,
    _quick_visual_signal,
    _render_asset,
    _safe_diagrams_topology,
)
from src.formats.pptx.mapper.engine import CSStoOOXMLEngine
from src.formats.pptx.mapper.html_parser import ParsedElement, parse_slide_html
from src.formats.pptx.rulesets import get_ruleset
from src.utils.iconify import get_fallback_icon_path


def test_standard_layout_catalog_exposes_master_zones_and_body_patterns() -> None:
    ruleset = get_ruleset()

    assert len(ruleset.layout_zones["header_zones"]) >= 20
    assert len(ruleset.layout_zones["footer_zones"]) >= 20
    assert len(ruleset.icon_layouts["placements"]) >= 20
    assert "data-pptx-type=\"icon\"" in ruleset.get_icon_layout_rules()
    assert (
        ruleset.layout_patterns["total_patterns"] >= 100
        and ruleset.layout_patterns["total_patterns"] <= 300
    )
    assert sum(
        len(category["patterns"])
        for category in ruleset.layout_patterns["categories"].values()
    ) == ruleset.layout_patterns["total_patterns"]
    assert ruleset.get_body_layout("compare_vs__evidence")["variant_of"] == "compare_vs"
    rules = ruleset.get_planner_layout_rules()
    assert "OOXML Planning Boundary" in rules
    assert "header_claim_bar" in rules
    assert "dashboard_chart_sidebar" in rules


def test_revision_request_extracts_per_slide_instructions() -> None:
    instructions = _extract_slide_revision_instructions(
        "슬라이드 3: LangChain RAG Pipeline 아키텍처 다이어그램으로 변경해줘. "
        "슬라이드 5: 하단 카드 내 3개 영역으로 명확하게 구분해줘."
    )

    assert instructions == {
        3: "LangChain RAG Pipeline 아키텍처 다이어그램으로 변경해줘.",
        5: "하단 카드 내 3개 영역으로 명확하게 구분해줘.",
    }


def test_revision_request_expands_multi_slide_korean_targets() -> None:
    query = "슬라이드 4, 5 장 아키텍처 말고 일반 내용으로 변경해줘"

    assert _extract_slide_revision_instructions(query) == {
        4: "아키텍처 말고 일반 내용으로 변경해줘",
        5: "아키텍처 말고 일반 내용으로 변경해줘",
    }
    assert _normalize_revision_scope(None, query) == "slide_rewrite"


def test_negative_architecture_revision_suppresses_visual_asset_signal() -> None:
    query = "슬라이드 4, 5 장 아키텍처 말고 일반 내용으로 변경해줘"

    assert _negative_visual_asset_signal(query) is True
    assert _quick_visual_signal(
        query,
        [{"index": 4, "title": "Native Agent Architectures"}],
    ) is False


def test_blueprints_are_normalized_to_known_body_layouts() -> None:
    ruleset = get_ruleset()
    blueprints = _normalize_blueprints(
        [
            {"slide_type": "cover", "title": "Opening"},
            {
                "slide_type": "data",
                "title": "Evidence",
                "layout_plan": {
                    "body_layout_id": "not-a-layout",
                    "sub_layout_ids": ["grid_2x2", "not-a-layout"],
                },
            },
        ],
        ruleset,
    )

    assert blueprints[0]["layout_plan"]["master_role"] == "cover"
    assert blueprints[1]["layout_plan"]["body_layout_id"] == "dashboard_chart_sidebar"
    assert blueprints[1]["layout_plan"]["sub_layout_ids"] == ["grid_2x2"]


def test_fixed_template_uses_selected_master_zone_positions() -> None:
    master_layout = get_ruleset().resolve_master_layout(
        {"header_zone_id": "header_side_rule", "footer_zone_id": "footer_compact"}
    )
    html = (
        '<div data-slide="2" style="position:absolute;left:0;top:0;width:960px;height:540px">'
        '<div data-pptx-type="textbox" style="position:absolute;left:40px;top:100px;'
        'width:200px;height:40px">Body</div></div>'
    )
    output = _inject_fixed_template(
        html,
        2,
        {"title": "A decision claim", "section_label": "EVIDENCE"},
        {"master_layout": master_layout},
    )

    assert "left:52px;top:16px;width:250px;height:14px" in output
    assert "left:40px;top:16px;width:4px;height:54px" in output
    assert "left:40px;top:526px;width:720px;height:11px" in output
    assert ">Body</div>" in output


def test_header_title_size_is_consistent_for_long_and_short_titles() -> None:
    short = _inject_fixed_template(
        '<div data-slide="1"><div data-pptx-type="textbox" '
        'style="position:absolute;left:40px;top:100px;width:200px;height:40px">Body</div></div>',
        1,
        {"title": "Short title", "section_label": "A"},
        {},
    )
    long = _inject_fixed_template(
        '<div data-slide="2"><div data-pptx-type="textbox" '
        'style="position:absolute;left:40px;top:100px;width:200px;height:40px">Body</div></div>',
        2,
        {
            "title": "AI 관측 서비스 구축을 위한 전략적 실행 로드맵과 핵심 과제",
            "section_label": "A",
        },
        {},
    )

    assert "font-size:22px" in short
    assert "font-size:22px" in long
    assert "line-height:1.08;padding:0;vertical-align:middle" in long


def test_template_slide_size_scales_html_coordinates(tmp_path: Path) -> None:
    template = Presentation()
    template.slide_width = Emu(12_192_000)
    template.slide_height = Emu(6_858_000)
    template.slides.add_slide(template.slide_layouts[6])
    buffer = BytesIO()
    template.save(buffer)

    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="textbox" style="position:absolute;left:480px;top:270px;'
        'width:96px;height:54px;font-size:20px;color:#111827">Scaled</div></div>'
    )

    output = CSStoOOXMLEngine().build_presentation(
        [{"index": 1, "html": html, "metadata": {"slide_type": "content"}}],
        tmp_path,
        template_bytes=buffer.getvalue(),
    )
    prs = Presentation(output)
    shape = prs.slides[0].shapes[0]

    assert prs.slide_width == 12_192_000
    assert prs.slide_height == 6_858_000
    assert abs(shape.left - 6_096_000) < 5
    assert abs(shape.top - 3_429_000) < 5
    assert abs(shape.width - 1_219_200) < 5


def test_icon_is_layered_above_card_without_moving_card(tmp_path: Path, monkeypatch) -> None:
    icon_path = tmp_path / "icon.png"
    Image.new("RGBA", (32, 32), (12, 34, 56, 255)).save(icon_path)
    monkeypatch.setattr("src.utils.iconify.get_icon_asset_path", lambda *args, **kwargs: icon_path)

    element = ParsedElement(
        pptx_type="textbox",
        pptx_shape=None,
        position={"left": 40, "top": 100, "width": 220, "height": 120},
        styles={
            "background-color": "#F1F5F9",
            "color": "#112233",
            "padding": "12px",
        },
        text_content="Data pipeline",
        children=[],
        attributes={"data-pptx-icon": "database"},
    )
    original_position = dict(element.position)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    CSStoOOXMLEngine()._add_element(slide, element)

    assert element.position == original_position
    assert element.styles["padding-top"] == "50px"
    assert slide.shapes[0].shape_type != MSO_SHAPE_TYPE.PICTURE
    assert slide.shapes[1].shape_type == MSO_SHAPE_TYPE.PICTURE


def test_explicit_icon_slot_renders_on_compact_heading(tmp_path: Path, monkeypatch) -> None:
    icon_path = tmp_path / "icon.png"
    Image.new("RGBA", (32, 32), (12, 34, 56, 255)).save(icon_path)
    monkeypatch.setattr("src.utils.iconify.get_icon_asset_path", lambda *args, **kwargs: icon_path)

    element = ParsedElement(
        pptx_type="textbox",
        pptx_shape=None,
        position={"left": 40, "top": 100, "width": 220, "height": 24},
        styles={"color": "#112233", "font-size": "14px"},
        text_content="Compact heading",
        children=[],
        attributes={
            "data-pptx-icon": "database",
            "data-pptx-icon-layout": "inline-left",
            "data-pptx-icon-size": "20",
        },
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    CSStoOOXMLEngine()._add_element(slide, element)

    assert element.styles["padding-left"] == "34px"
    assert slide.shapes[0].shape_type != MSO_SHAPE_TYPE.PICTURE
    assert slide.shapes[1].shape_type == MSO_SHAPE_TYPE.PICTURE


def test_html_preview_embeds_icons_for_explicit_compact_slots(monkeypatch, tmp_path: Path) -> None:
    icon_path = tmp_path / "icon.svg"
    icon_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        '<path fill="#112233" d="M4 4h24v24H4z"/></svg>',
        encoding="utf-8",
    )
    monkeypatch.setattr("src.utils.iconify.get_icon_asset_path", lambda *args, **kwargs: icon_path)

    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="textbox" data-pptx-icon="database" '
        'data-pptx-icon-layout="inline-left" data-pptx-icon-size="20" '
        'style="position:absolute;left:40px;top:100px;width:220px;height:24px;'
        'color:#112233;font-size:14px">Compact heading</div></div>'
    )

    output = _embed_cached_icons(html)

    assert "data:image/svg+xml;base64" in output
    assert "width:20px;height:20px" in output
    assert "padding-left:34px" in output


def test_independent_icon_element_maps_to_html_and_pptx(tmp_path: Path, monkeypatch) -> None:
    png_path = tmp_path / "icon.png"
    svg_path = tmp_path / "icon.svg"
    Image.new("RGBA", (32, 32), (12, 34, 56, 255)).save(png_path)
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        '<path fill="#112233" d="M4 4h24v24H4z"/></svg>',
        encoding="utf-8",
    )

    def fake_asset(*args, **kwargs):
        return svg_path if kwargs.get("target") == "html" else png_path

    monkeypatch.setattr("src.utils.iconify.get_icon_asset_path", fake_asset)
    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="icon" data-pptx-icon="brain" '
        'data-pptx-icon-placement="diagram_node_top" '
        'style="position:absolute;left:40px;top:100px;width:32px;height:32px;'
        'color:#112233"></div>'
        '<div data-pptx-type="textbox" style="position:absolute;left:84px;top:96px;'
        'width:220px;height:44px;color:#112233">AI Engine</div></div>'
    )

    preview = _embed_cached_icons(html)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    for element in parse_slide_html(html):
        CSStoOOXMLEngine()._add_element(slide, element)

    assert "data:image/svg+xml;base64" in preview
    assert "left:40px;top:100px;width:32px;height:32px" in preview
    assert slide.shapes[0].shape_type == MSO_SHAPE_TYPE.PICTURE
    assert slide.shapes[1].shape_type != MSO_SHAPE_TYPE.PICTURE


def test_independent_icon_is_centered_in_declared_box(tmp_path: Path, monkeypatch) -> None:
    icon_path = tmp_path / "wide_icon.png"
    Image.new("RGBA", (64, 32), (12, 34, 56, 255)).save(icon_path)
    monkeypatch.setattr("src.utils.iconify.get_icon_asset_path", lambda *args, **kwargs: icon_path)

    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="icon" data-pptx-icon="database" '
        'style="position:absolute;left:40px;top:100px;width:40px;height:40px;'
        'color:#112233"></div></div>'
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    for element in parse_slide_html(html):
        CSStoOOXMLEngine()._add_element(slide, element)

    picture = slide.shapes[0]
    assert picture.shape_type == MSO_SHAPE_TYPE.PICTURE
    assert abs(picture.left - Emu(40 * 9525)) < 5
    assert abs(picture.top - Emu(110 * 9525)) < 5
    assert abs(picture.width - Emu(40 * 9525)) < 5
    assert abs(picture.height - Emu(20 * 9525)) < 5


def test_generated_html_icon_node_is_materialized_with_data_image(tmp_path: Path) -> None:
    from bs4 import BeautifulSoup

    icon_path = tmp_path / "icon.svg"
    icon_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        '<path fill="#112233" d="M4 4h24v24H4z"/></svg>',
        encoding="utf-8",
    )
    soup = BeautifulSoup(
        '<div data-pptx-type="icon" data-pptx-icon="brain" '
        'style="position:absolute;left:40px;top:100px;width:32px;height:32px;'
        'background-color:#A855F7;color:#112233"></div>',
        "html.parser",
    )

    _materialize_html_icon_node(soup.div, icon_path)

    style = soup.div["style"]
    assert "background-image:url(data:image/svg+xml;base64," in style
    assert "background-color:transparent" in style
    assert "background-color:#A855F7" not in style


def test_legacy_textbox_icon_is_normalized_to_separate_icon_area() -> None:
    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="textbox" data-pptx-icon="brain" '
        'style="position:absolute;left:40px;top:100px;width:220px;height:32px;'
        'color:#112233;font-size:14px">AI Engine</div></div>'
    )

    output = _normalize_legacy_icon_nodes(html)

    assert 'data-pptx-type="icon"' in output
    assert 'data-pptx-icon-placement="card_lead_left"' in output
    assert output.count('data-pptx-icon="brain"') == 1
    assert "left:74px" in output


def test_parser_recovers_flattened_bullet_line_breaks() -> None:
    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="textbox" style="position:absolute;left:0;top:0;'
        'width:240px;height:120px">• 클라우드 MSP 전문 기업 • 토스뱅크 주주 참여 '
        '• 2024년 매출 성장</div></div>'
    )

    [element] = parse_slide_html(html)

    assert element.text_content.splitlines() == [
        "• 클라우드 MSP 전문 기업",
        "• 토스뱅크 주주 참여",
        "• 2024년 매출 성장",
    ]


def test_html_lists_are_preserved_as_bullet_lines() -> None:
    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="textbox" style="position:absolute;left:40px;top:100px;'
        'width:240px;height:120px;font-size:14px">'
        "<ul><li>Data collection</li><li>Model training</li><li>Deployment</li></ul>"
        "</div></div>"
    )

    normalized = _normalize_slide_html(html)
    [element] = parse_slide_html(normalized)

    assert 'data-pptx-list="bullet"' in normalized
    assert element.text_content.splitlines() == [
        "• Data collection",
        "• Model training",
        "• Deployment",
    ]


def test_parser_inherits_root_and_inline_font_details() -> None:
    html = (
        '<div data-slide="1">'
        '<div data-pptx-type="textbox" style="position:absolute;left:40px;top:100px;'
        "width:300px;height:80px;font-family:'Inter';font-size:18px;"
        'font-weight:600;color:#112233">'
        'Root <span style="font-size:12px;color:#CC0000;font-style:italic;'
        'text-decoration:underline">inline</span></div></div>'
    )

    [element] = parse_slide_html(html)

    assert element.text_runs[0]["font_family"] == "'Inter'"
    assert element.text_runs[0]["size"] == "18px"
    assert element.text_runs[0]["bold"] is True
    inline = element.text_runs[1]
    assert inline["text"] == "inline"
    assert inline["size"] == "12px"
    assert inline["color"] == "#CC0000"
    assert inline["italic"] is True
    assert inline["underline"] is True


def test_normalized_html_forces_text_contrast_on_backing_shape() -> None:
    html = (
        '<div data-slide="1" style="position:absolute;left:0;top:0;width:960px;height:540px">'
        '<div data-pptx-type="shape" data-pptx-shape="rounded_rect" '
        'style="position:absolute;left:40px;top:100px;width:320px;height:120px;'
        'background-color:#111827"></div>'
        '<div data-pptx-type="textbox" style="position:absolute;left:60px;top:120px;'
        'width:280px;height:40px;font-size:14px;font-weight:400;color:#111827">'
        'Readable text</div></div>'
    )

    normalized = _normalize_slide_html(html)

    assert "color:#FFFFFF" in normalized


def test_pptx_text_color_is_corrected_against_own_dark_fill() -> None:
    element = ParsedElement(
        pptx_type="textbox",
        pptx_shape=None,
        position={"left": 40, "top": 100, "width": 220, "height": 80},
        styles={
            "background-color": "#111827",
            "font-size": "14px",
            "color": "#111827",
        },
        text_content="Readable",
        children=[],
        attributes={},
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    CSStoOOXMLEngine()._add_element(slide, element)
    run = slide.shapes[0].text_frame.paragraphs[0].runs[0]

    assert str(run.font.color.rgb) == "FFFFFF"


def test_pptx_textbox_uses_precise_padding_shorthand() -> None:
    element = ParsedElement(
        pptx_type="textbox",
        pptx_shape=None,
        position={"left": 40, "top": 100, "width": 220, "height": 80},
        styles={
            "padding": "6px 18px 10px 24px",
            "font-size": "14px",
            "color": "#112233",
        },
        text_content="Precise padding",
        children=[],
        attributes={},
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    CSStoOOXMLEngine()._add_element(slide, element)
    frame = slide.shapes[0].text_frame

    assert abs(frame.margin_left - Emu(24 * 9525)) < 5
    assert abs(frame.margin_right - Emu(18 * 9525)) < 5
    assert abs(frame.margin_top - Emu(6 * 9525)) < 5
    assert abs(frame.margin_bottom - Emu(10 * 9525)) < 5


def test_pptx_textbox_uses_explicit_text_alignment_attrs() -> None:
    element = ParsedElement(
        pptx_type="textbox",
        pptx_shape=None,
        position={"left": 40, "top": 100, "width": 220, "height": 56},
        styles={
            "font-size": "24px",
            "font-weight": "700",
            "color": "#112233",
        },
        text_content="69.5%",
        children=[],
        attributes={
            "data-pptx-text-role": "kpi_value",
            "data-pptx-text-align": "center",
            "data-pptx-text-valign": "middle",
            "data-pptx-text-padding": '{"top":0,"right":4,"bottom":0,"left":4}',
        },
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    CSStoOOXMLEngine()._add_element(slide, element)
    frame = slide.shapes[0].text_frame

    assert frame.vertical_anchor == MSO_ANCHOR.MIDDLE
    assert frame.paragraphs[0].alignment == PP_ALIGN.CENTER
    assert abs(frame.margin_left - Emu(4 * 9525)) < 5
    assert abs(frame.margin_top - Emu(0)) < 5


def test_normalized_html_adds_precise_text_attrs_for_metric() -> None:
    html = """<div data-slide="1" style="position:absolute;left:0;top:0;width:960px;height:540px">
    <div data-pptx-type="textbox"
      style="position:absolute;left:80px;top:120px;width:180px;height:56px;font-size:32px;font-weight:700;color:#6366F1">69.5%</div>
    </div>"""

    normalized = _normalize_slide_html(html)

    assert 'data-pptx-text-role="kpi_value"' in normalized
    assert 'data-pptx-text-align="center"' in normalized
    assert 'data-pptx-text-valign="middle"' in normalized
    assert 'data-pptx-text-padding="0px 4px 0px 4px"' in normalized


def test_text_is_hard_wrapped_to_card_width() -> None:
    engine = CSStoOOXMLEngine()
    size, lines = engine._fit_text_lines_to_box(
        text="• 클라우드 MSP 전문 기업 토스뱅크 주주 참여 금융 진출",
        font_size_px=14,
        line_height=1.35,
        container_w=190,
        container_h=96,
        pad_left=12,
        pad_right=12,
        pad_top=10,
        pad_bottom=10,
    )

    assert size <= 14
    assert len(lines) >= 2
    assert all(len(line) <= 18 for line in lines)


def test_chart_table_shape_options_do_not_break_native_conversion(tmp_path: Path) -> None:
    html = """<div data-slide="1" style="position:absolute;left:0;top:0;width:960px;height:540px">
    <div data-pptx-type="chart" data-pptx-chart-type="bar"
      data-pptx-chart-data='[{"label":"2022","value":"2,600"},{"label":"2023","value":"3,350"}]'
      data-pptx-chart-options='{"show_legend":false,"data_label_position":"outside_end","gap_width":40,"colors":["#3B82F6"]}'
      style="position:absolute;left:40px;top:80px;width:360px;height:220px"></div>
    <div data-pptx-type="table"
      data-pptx-table-data='{"headers":["항목","값"],"rows":[["매출","4,058"],["성장률","21%"]]}'
      data-pptx-table-options='{"numeric_align":"right","vertical_align":"middle","body_font_size":9}'
      style="position:absolute;left:430px;top:80px;width:300px;height:120px"></div>
    <div data-pptx-type="shape" data-pptx-shape="diamond"
      data-pptx-shape-options='{"line_color":"#FF0000","line_width":2,"transparency":0.2}'
      style="position:absolute;left:760px;top:100px;width:40px;height:40px;background-color:#10B981"></div>
    </div>"""

    output = CSStoOOXMLEngine().build_presentation(
        [{"index": 1, "html": html, "metadata": {"slide_type": "content"}}],
        tmp_path,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_fallback_icon_is_created_when_iconify_cache_is_missing() -> None:
    path = get_fallback_icon_path("database", color="112233", size=32)

    assert path is not None
    assert path.exists()
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0


def test_architecture_request_routes_to_diagrams_visual_asset() -> None:
    plan = _fallback_plan(
        "AWS 아키텍처 3 티어 구조 설계가 포함되어야되",
        [{"index": 1, "slide_type": "cover"}, {"index": 2, "slide_type": "content"}],
    )

    assert plan["enabled"] is True
    assert plan["assets"][0]["slide_index"] == 2
    assert plan["assets"][0]["method"] == METHOD_DIAGRAMS
    assert plan["assets"][0]["diagrams_provider"] == "aws"
    assert plan["assets"][0]["diagrams_nodes"]
    assert "CloudFront" in plan["assets"][0]["mermaid"]


def test_diagrams_topology_fields_are_preserved() -> None:
    plan = _normalize_plan(
        {
            "enabled": True,
            "assets": [
                {
                    "slide_index": 2,
                    "method": METHOD_DIAGRAMS,
                    "description": "Azure application architecture",
                    "diagrams_provider": "azure",
                    "diagrams_direction": "TB",
                    "diagrams_clusters": [
                        {"id": "region", "label": "Azure Region"},
                        {"id": "vnet", "label": "Virtual Network", "parent": "region"},
                    ],
                    "diagrams_nodes": [
                        {
                            "id": "APP",
                            "library": "azure",
                            "service": "app_services",
                            "label": "App Service",
                            "cluster": "vnet",
                        },
                        {
                            "id": "DB",
                            "provider": "azure",
                            "service": "sql_database",
                            "label": "SQL Database",
                            "cluster": "vnet",
                        },
                    ],
                    "diagrams_edges": [
                        {
                            "from": "APP",
                            "to": "DB",
                            "label": "SQL",
                            "style": "dashed",
                        }
                    ],
                    "mermaid": "graph LR\n  U[Users] --> APP[App Service]\n  APP --> DB[Cloud SQL]",
                }
            ],
        },
        "Azure 기반으로 구성해줘",
        [{"index": 2, "slide_type": "content"}],
    )

    asset = plan["assets"][0]
    topology = _diagrams_topology(asset)
    assert asset["diagrams_provider"] == "azure"
    assert topology["direction"] == "TB"
    assert topology["clusters"][1] == {
        "id": "vnet",
        "label": "Virtual Network",
        "parent": "region",
    }
    assert topology["nodes"][0]["provider"] == "azure"
    assert topology["nodes"][0]["service"] == "appservice"
    assert topology["nodes"][1]["service"] == "sqldatabase"
    assert topology["edges"][0]["style"] == "dashed"


def test_diagrams_node_candidates_fall_back_to_specific_provider_icons() -> None:
    candidates = _diagrams_node_candidates(
        {"provider": "generic", "service": "appservice", "label": "Azure App Service"}
    )

    assert ("diagrams.azure.compute", "AppServices") in candidates
    assert candidates[-1] == ("diagrams.generic.blank", "Blank")


def test_generic_diagrams_prefers_flowchart_icons_over_blank() -> None:
    agent_candidates = _diagrams_node_candidates(
        {"provider": "generic", "service": "agent", "label": "LangChain Agent"}
    )
    docs_candidates = _diagrams_node_candidates(
        {"provider": "generic", "service": "document", "label": "Retrieved Docs"}
    )
    search_candidates = _diagrams_node_candidates(
        {"provider": "generic", "service": "search", "label": "Retrieval & Search"}
    )

    assert ("diagrams.programming.flowchart", "PredefinedProcess") in agent_candidates
    assert ("diagrams.programming.flowchart", "Document") in docs_candidates
    assert ("diagrams.programming.flowchart", "Inspection") in search_candidates
    assert agent_candidates.index(("diagrams.programming.flowchart", "PredefinedProcess")) < agent_candidates.index(("diagrams.generic.blank", "Blank"))


def test_visual_asset_plan_honors_targeted_slide_instruction() -> None:
    plan = _normalize_plan(
        {
            "enabled": True,
            "assets": [
                {
                    "slide_index": 5,
                    "method": METHOD_DIAGRAMS,
                    "title": "LangChain RAG Pipeline Architecture",
                    "description": "LangChain RAG Pipeline architecture diagram",
                }
            ],
        },
        "슬라이드 3: LangChain RAG Pipeline 아키텍처 다이어그램으로 변경해줘. "
        "슬라이드 5: 하단 카드 영역 구분.",
        [{"index": 3, "slide_type": "content"}, {"index": 5, "slide_type": "content"}],
        {
            3: "LangChain RAG Pipeline 아키텍처 다이어그램으로 변경해줘.",
            5: "하단 카드 영역 구분.",
        },
    )

    assert plan["assets"][0]["slide_index"] == 3


def test_safe_diagrams_topology_breaks_cluster_cycles_and_bad_refs() -> None:
    topology = _safe_diagrams_topology(
        {
            "method": METHOD_DIAGRAMS,
            "title": "Cyclic Architecture",
            "diagrams_clusters": [
                {"id": "a", "label": "A", "parent": "b"},
                {"id": "b", "label": "B", "parent": "a"},
                {"id": "c", "label": "C", "parent": "missing"},
            ],
            "diagrams_nodes": [
                {"id": "app", "label": "App", "provider": "aws", "service": "ec2", "cluster": "a"},
                {"id": "db", "label": "DB", "provider": "aws", "service": "rds", "cluster": "missing"},
            ],
            "diagrams_edges": [
                {"from": "app", "to": "db", "label": "SQL"},
                {"from": "db", "to": "db", "label": "self"},
                {"from": "app", "to": "missing", "label": "bad"},
            ],
        }
    )

    parent_by_id = {cluster["id"]: cluster["parent"] for cluster in topology["clusters"]}
    for cluster_id in parent_by_id:
        seen = {cluster_id}
        parent = parent_by_id[cluster_id]
        while parent:
            assert parent not in seen
            seen.add(parent)
            parent = parent_by_id.get(parent, "")

    assert {edge["label"] for edge in topology["edges"]} == {"SQL"}
    assert next(node for node in topology["nodes"] if node["id"] == "db")["cluster"] == ""


async def test_diagrams_asset_falls_back_when_native_renderer_recurses(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def boom(asset, output_path):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(
        "src.formats.pptx.agents.nodes.visual_asset_planner._render_diagrams_asset",
        boom,
    )
    asset = {
        "id": "recursing",
        "slide_index": 2,
        "method": METHOD_DIAGRAMS,
        "title": "Safe fallback",
        "description": "AWS architecture",
        "diagrams_nodes": [
            {"id": "app", "label": "App", "provider": "aws", "service": "ec2"},
            {"id": "db", "label": "DB", "provider": "aws", "service": "rds"},
        ],
        "diagrams_edges": [{"from": "app", "to": "db", "label": "SQL"}],
    }

    rendered = await _render_asset(asset, tmp_path)

    assert rendered is not None
    assert rendered["renderer"] == "diagrams_fallback"
    assert Path(rendered["path"]).exists()
    with Image.open(rendered["path"]) as image:
        assert image.size[0] > 0 and image.size[1] > 0


def test_db_backed_pptx_preview_embeds_local_visual_asset(tmp_path: Path) -> None:
    image_path = tmp_path / "diagram.png"
    Image.new("RGB", (64, 48), (12, 34, 56)).save(image_path)
    html = (
        '<div data-slide="1">'
        f'<div data-pptx-type="image" data-pptx-image-path="{image_path}" '
        'style="position:absolute;left:40px;top:100px;width:300px;height:180px"></div>'
        '</div>'
    )

    preview = _embed_pptx_preview_assets(html)

    assert "data:image/png;base64" in preview
    assert str(image_path) not in preview


def test_auto_visual_asset_removes_overlapping_body_content(tmp_path: Path) -> None:
    image_path = tmp_path / "diagram.png"
    Image.new("RGB", (64, 48), (12, 34, 56)).save(image_path)
    html = (
        '<div data-slide="2">'
        '<div data-pptx-region="header" data-pptx-type="textbox" '
        'style="position:absolute;left:40px;top:20px;width:500px;height:30px">Title</div>'
        '<div data-pptx-type="textbox" style="position:absolute;left:120px;top:130px;'
        'width:240px;height:80px">Overlapping content</div>'
        '<div data-pptx-type="textbox" style="position:absolute;left:40px;top:440px;'
        'width:200px;height:40px">Safe note</div>'
        "</div>"
    )
    slides = [{"index": 2, "html": html, "elements_used": []}]
    assets = [
        {
            "id": "arch",
            "slide_index": 2,
            "path": str(image_path),
            "placement": {"x": 100, "y": 110, "w": 320, "h": 180},
        }
    ]

    [slide] = _inject_visual_asset_images(slides, assets)

    assert 'data-pptx-image-id="arch"' in slide["html"]
    assert "Overlapping content" not in slide["html"]
    assert "Safe note" in slide["html"]
    assert "Title" in slide["html"]


def test_pptx_image_preserves_source_aspect_inside_html_box(tmp_path: Path) -> None:
    image_path = tmp_path / "wide_diagram.png"
    Image.new("RGB", (400, 100), (12, 34, 56)).save(image_path)
    html = (
        '<div data-slide="1">'
        f'<div data-pptx-type="image" data-pptx-image-path="{image_path}" '
        'style="position:absolute;left:40px;top:100px;width:300px;height:300px"></div>'
        '</div>'
    )

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    for element in parse_slide_html(html):
        CSStoOOXMLEngine()._add_element(slide, element)

    picture = slide.shapes[0]
    assert picture.shape_type == MSO_SHAPE_TYPE.PICTURE
    assert abs(picture.left - Emu(40 * 9525)) < 5
    assert abs(picture.top - Emu(212.5 * 9525)) < 5
    assert abs(picture.width - Emu(300 * 9525)) < 5
    assert abs(picture.height - Emu(75 * 9525)) < 5


def test_slide_html_normalizer_shrinks_overflowing_text_and_resolves_overlap() -> None:
    html = (
        '<div data-slide="1" style="position:absolute;left:0;top:0;width:960px;height:540px">'
        '<div data-pptx-type="textbox" style="position:absolute;left:40px;top:90px;'
        'width:360px;height:80px;font-size:54px;line-height:1.1;color:#111827">'
        'AWS 3-Tier 인프라 아키텍처</div>'
        '<div data-pptx-type="textbox" style="position:absolute;left:60px;top:110px;'
        'width:280px;height:60px;font-size:18px;color:#111827">중첩되는 설명 텍스트</div>'
        '</div>'
    )

    output = _normalize_slide_html(html)

    assert "font-size:54px" not in output
    assert "overflow:hidden" in output
    assert "top:178px" in output


def test_fallback_layout_keeps_many_unconnected_nodes_from_overlapping() -> None:
    graph = _parse_mermaid(
        "graph LR\n"
        "  A[Client]\n  B[CDN]\n  C[Load Balancer]\n  D[Web]\n"
        "  E[API]\n  F[Cache]\n  G[DB Primary]\n  H[DB Standby]\n"
        "  I[Object Storage]\n  J[Monitoring]"
    )
    positions = _layout_nodes(graph["nodes"], graph["edges"], width=1024, height=640)
    coords = list(positions.values())

    for index, (x1, y1) in enumerate(coords):
        for x2, y2 in coords[index + 1:]:
            assert abs(x1 - x2) >= 80 or abs(y1 - y2) >= 80


def test_mermaid_chain_edges_are_preserved_for_fallback_layout() -> None:
    graph = _parse_mermaid(
        "graph LR\n"
        "  U[Users] --> CDN[CDN] --> ALB[Load Balancer] --> APP[App] --> DB[(DB)]"
    )

    assert [edge["from"] for edge in graph["edges"]] == ["U", "CDN", "ALB", "APP"]
    assert [edge["to"] for edge in graph["edges"]] == ["CDN", "ALB", "APP", "DB"]


async def test_diagrams_visual_asset_renders_and_maps_to_pptx(tmp_path: Path) -> None:
    asset = {
        "id": "aws_arch",
        "slide_index": 2,
        "method": METHOD_DIAGRAMS,
        "title": "AWS 3-Tier",
        "description": "AWS 3-tier architecture",
        "diagrams_provider": "aws",
        "diagrams_direction": "LR",
        "diagrams_clusters": [
            {"id": "region", "label": "AWS Region", "parent": ""},
            {"id": "vpc", "label": "VPC", "parent": "region"},
            {"id": "private_data", "label": "Private Data", "parent": "vpc"},
        ],
        "diagrams_nodes": [
            {"id": "U", "label": "Users", "provider": "generic", "service": "users"},
            {"id": "ALB", "label": "ALB", "provider": "aws", "service": "alb", "cluster": "vpc"},
            {
                "id": "APP",
                "label": "App Tier",
                "provider": "aws",
                "service": "ec2",
                "cluster": "vpc",
            },
            {
                "id": "DB",
                "label": "RDS",
                "provider": "aws",
                "service": "rds",
                "cluster": "private_data",
            },
        ],
        "diagrams_edges": [
            {"from": "U", "to": "ALB", "label": "HTTPS"},
            {"from": "ALB", "to": "APP"},
            {"from": "APP", "to": "DB", "label": "SQL"},
        ],
        "mermaid": (
            "graph LR\n"
            "  U[Users] --> ALB[ALB]\n"
            "  ALB --> APP[App Tier]\n"
            "  APP --> DB[(RDS)]"
        ),
        "placement": {"x": 80, "y": 100, "w": 520, "h": 320},
    }

    rendered = await _render_asset(asset, tmp_path)
    assert rendered is not None
    assert Path(rendered["path"]).exists()
    assert Path(rendered["diagrams_topology_path"]).exists()
    topology = Path(rendered["diagrams_topology_path"]).read_text(encoding="utf-8")
    assert '"provider": "aws"' in topology
    assert '"service": "rds"' in topology
    assert '"from": "APP"' in topology

    html = (
        '<div data-slide="1">'
        f'<div data-pptx-type="image" data-pptx-image-path="{rendered["path"]}" '
        'style="position:absolute;left:40px;top:100px;width:300px;height:180px"></div>'
        '</div>'
    )
    preview = _embed_local_images(html)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    for element in parse_slide_html(html):
        CSStoOOXMLEngine()._add_element(slide, element)

    assert "data:image/png;base64" in preview
    assert slide.shapes[0].shape_type == MSO_SHAPE_TYPE.PICTURE
