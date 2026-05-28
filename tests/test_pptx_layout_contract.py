from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from src.formats.pptx.agents.nodes.html_generator import _inject_fixed_template
from src.formats.pptx.agents.nodes.unified_planner import _normalize_blueprints
from src.formats.pptx.mapper.engine import CSStoOOXMLEngine
from src.formats.pptx.mapper.html_parser import ParsedElement, parse_slide_html
from src.formats.pptx.rulesets import get_ruleset
from src.utils.iconify import get_fallback_icon_path


def test_standard_layout_catalog_exposes_master_zones_and_body_patterns() -> None:
    ruleset = get_ruleset()

    assert len(ruleset.layout_zones["header_zones"]) >= 20
    assert len(ruleset.layout_zones["footer_zones"]) >= 20
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


def test_icon_is_layered_above_card_without_moving_card(tmp_path: Path, monkeypatch) -> None:
    icon_path = tmp_path / "icon.png"
    Image.new("RGBA", (32, 32), (12, 34, 56, 255)).save(icon_path)
    monkeypatch.setattr("src.utils.iconify.get_icon_path", lambda *args, **kwargs: icon_path)

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
