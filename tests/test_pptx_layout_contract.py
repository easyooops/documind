from pathlib import Path
from io import BytesIO

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

from src.formats.pptx.agents.nodes.html_generator import _inject_fixed_template
from src.formats.pptx.agents.nodes.html_generator import _materialize_html_icon_node
from src.formats.pptx.agents.nodes.render_convert import (
    _embed_cached_icons,
    _normalize_legacy_icon_nodes,
)
from src.formats.pptx.agents.nodes.unified_planner import _normalize_blueprints
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
        {"title": "AI 관측 서비스 구축을 위한 전략적 실행 로드맵과 핵심 과제", "section_label": "A"},
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
