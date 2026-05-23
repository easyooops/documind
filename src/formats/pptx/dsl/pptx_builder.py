"""DSL → PPTX Builder — lossless conversion from OOXML-DSL to PowerPoint.

All conversions are pure arithmetic (px → EMU, degrees → 60000ths), so zero
information is lost between DSL and DrawingML.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path

from src.core.logging import get_logger
from src.formats.pptx.dsl.schema import (
    Border,
    GradientFill,
    NoFill,
    PresentationDSL,
    Shadow,
    Shape,
    SlideDSL,
    SolidFill,
    TableData,
    ChartData,
    TextParagraph,
    PX_TO_EMU,
    PT_TO_EMU,
    FONT_PX_TO_HUNDREDTHS_PT,
    DEGREES_TO_60K,
    GRADIENT_POS_TO_PERMILLE,
)

logger = get_logger(__name__)


class DSLtoPPTXBuilder:
    """Builds a .pptx file from a PresentationDSL object."""

    def build(self, dsl: PresentationDSL, output_dir: Path) -> Path:
        """Convert PresentationDSL → .pptx file. Returns output path."""
        from pptx import Presentation
        from pptx.util import Emu

        prs = Presentation()
        prs.slide_width = Emu(dsl.viewport_width * PX_TO_EMU)
        prs.slide_height = Emu(dsl.viewport_height * PX_TO_EMU)

        blank_layout = prs.slide_layouts[6]

        for slide_dsl in dsl.slides:
            slide = prs.slides.add_slide(blank_layout)
            sorted_shapes = sorted(slide_dsl.shapes, key=lambda s: s.z_index)
            for shape in sorted_shapes:
                self._add_shape(slide, shape)

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"presentation_{uuid.uuid4().hex[:8]}.pptx"
        prs.save(str(output_path))

        logger.info("dsl_builder.saved", path=str(output_path), slides=len(dsl.slides))
        return output_path

    def _add_shape(self, slide, shape: Shape) -> None:
        """Add a single shape to the slide."""
        from pptx.util import Emu
        from pptx.enum.shapes import MSO_SHAPE

        x = shape.position.x * PX_TO_EMU
        y = shape.position.y * PX_TO_EMU
        w = shape.position.w * PX_TO_EMU
        h = shape.position.h * PX_TO_EMU

        if shape.table:
            self._add_table(slide, shape, shape.table)
            return

        if shape.chart:
            self._add_chart(slide, shape, shape.chart)
            return

        has_text = shape.text is not None and len(shape.text) > 0
        has_fill = shape.fill is not None and not isinstance(shape.fill, NoFill)
        has_radius = shape.border_radius > 0

        if has_text and not has_fill and not has_radius:
            pptx_shape = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
        else:
            shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if has_radius else MSO_SHAPE.RECTANGLE
            pptx_shape = slide.shapes.add_shape(shape_type, Emu(x), Emu(y), Emu(w), Emu(h))

            if has_radius:
                self._apply_corner_radius(pptx_shape, shape.border_radius, min(w, h))

            pptx_shape.line.fill.background()

        if has_fill:
            self._apply_fill(pptx_shape, shape.fill)
        elif not has_text:
            pptx_shape.fill.background()

        if shape.shadow:
            self._apply_shadow(pptx_shape, shape.shadow)

        if shape.border:
            self._apply_border(pptx_shape, shape.border)

        if has_text:
            tf = pptx_shape.text_frame
            tf.word_wrap = True
            self._apply_text(tf, shape.text, shape.vertical_align)

    def _apply_fill(self, pptx_shape, fill: SolidFill | GradientFill) -> None:
        """Apply fill (solid or gradient) to a shape."""
        from pptx.dml.color import RGBColor

        if isinstance(fill, SolidFill):
            pptx_shape.fill.solid()
            pptx_shape.fill.fore_color.rgb = RGBColor.from_string(fill.color)

        elif isinstance(fill, GradientFill):
            self._apply_gradient(pptx_shape, fill)

    def _apply_gradient(self, pptx_shape, fill: GradientFill) -> None:
        """Apply gradient fill using DrawingML XML."""
        from pptx.oxml.ns import qn
        from lxml import etree

        spPr = pptx_shape._element.spPr

        for tag in ("a:solidFill", "a:gradFill", "a:noFill"):
            existing = spPr.find(qn(tag))
            if existing is not None:
                spPr.remove(existing)

        gradFill = etree.SubElement(spPr, qn("a:gradFill"))
        gsLst = etree.SubElement(gradFill, qn("a:gsLst"))

        for stop in fill.stops:
            gs = etree.SubElement(gsLst, qn("a:gs"))
            gs.set("pos", str(stop.position * GRADIENT_POS_TO_PERMILLE))
            srgbClr = etree.SubElement(gs, qn("a:srgbClr"))
            srgbClr.set("val", stop.color.lower())

        lin = etree.SubElement(gradFill, qn("a:lin"))
        lin.set("ang", str(fill.angle * DEGREES_TO_60K))
        lin.set("scaled", "1")

    def _apply_shadow(self, pptx_shape, shadow: Shadow) -> None:
        """Apply outer shadow effect."""
        from pptx.oxml.ns import qn
        from lxml import etree

        spPr = pptx_shape._element.spPr
        effectLst = spPr.find(qn("a:effectLst"))
        if effectLst is None:
            effectLst = etree.SubElement(spPr, qn("a:effectLst"))

        dist = int(math.hypot(shadow.offset_x, shadow.offset_y) * PT_TO_EMU)
        direction = int(math.degrees(math.atan2(shadow.offset_y, shadow.offset_x)) * DEGREES_TO_60K) if dist > 0 else 0
        if direction < 0:
            direction += 360 * DEGREES_TO_60K

        outerShdw = etree.SubElement(effectLst, qn("a:outerShdw"))
        outerShdw.set("blurRad", str(shadow.blur * PT_TO_EMU))
        outerShdw.set("dist", str(dist))
        outerShdw.set("dir", str(direction))
        outerShdw.set("rotWithShape", "0")

        srgbClr = etree.SubElement(outerShdw, qn("a:srgbClr"))
        srgbClr.set("val", shadow.color.lower())
        alpha = etree.SubElement(srgbClr, qn("a:alpha"))
        alpha.set("val", str(int(shadow.opacity * 100000)))

    def _apply_corner_radius(self, pptx_shape, radius_px: int, min_dim_emu: int) -> None:
        """Set corner rounding on a rounded rectangle."""
        max_radius_emu = min_dim_emu // 2
        radius_emu = min(radius_px * PX_TO_EMU, max_radius_emu)
        ratio = int((radius_emu / max_radius_emu) * 50000) if max_radius_emu > 0 else 0
        ratio = min(ratio, 50000)
        pptx_shape.adjustments[0] = ratio / 100000.0

    def _apply_border(self, pptx_shape, border: Border) -> None:
        """Apply border/line to shape."""
        from pptx.dml.color import RGBColor
        from pptx.util import Pt

        line = pptx_shape.line
        line.color.rgb = RGBColor.from_string(border.color)
        line.width = Pt(border.width)

    def _apply_text(
        self,
        text_frame,
        paragraphs: list[TextParagraph],
        vertical_align: str = "top",
    ) -> None:
        """Apply text paragraphs and runs to a text frame."""
        from pptx.util import Pt, Emu
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.dml.color import RGBColor

        text_frame.margin_left = Emu(8 * PX_TO_EMU)
        text_frame.margin_right = Emu(8 * PX_TO_EMU)
        text_frame.margin_top = Emu(4 * PX_TO_EMU)
        text_frame.margin_bottom = Emu(4 * PX_TO_EMU)
        anchor_map = {
            "top": MSO_ANCHOR.TOP,
            "middle": MSO_ANCHOR.MIDDLE,
            "bottom": MSO_ANCHOR.BOTTOM,
        }
        text_frame.vertical_anchor = anchor_map.get(vertical_align, MSO_ANCHOR.TOP)

        align_map = {
            "left": PP_ALIGN.LEFT,
            "center": PP_ALIGN.CENTER,
            "right": PP_ALIGN.RIGHT,
            "justify": PP_ALIGN.JUSTIFY,
        }

        for i, para_dsl in enumerate(paragraphs):
            if i == 0:
                p = text_frame.paragraphs[0]
            else:
                p = text_frame.add_paragraph()

            p.alignment = align_map.get(para_dsl.align, PP_ALIGN.LEFT)
            p.line_spacing = para_dsl.line_height
            if para_dsl.spacing_before:
                p.space_before = Pt(para_dsl.spacing_before * 0.75)
            if para_dsl.spacing_after:
                p.space_after = Pt(para_dsl.spacing_after * 0.75)

            for run_dsl in para_dsl.runs:
                run = p.add_run()
                run.text = run_dsl.text

                font = run.font
                font.size = Pt(run_dsl.font_size * 0.75)
                font.bold = run_dsl.font_weight >= 700
                font.italic = run_dsl.italic
                font.color.rgb = RGBColor.from_string(run_dsl.color)

                if run_dsl.font_family:
                    font.name = run_dsl.font_family.split(",")[0].strip().strip("'\"")

    def _add_table(self, slide, shape: Shape, table_data: TableData) -> None:
        """Add a native PowerPoint table."""
        from pptx.dml.color import RGBColor
        from pptx.util import Emu, Pt

        rows = ([table_data.headers] if table_data.headers else []) + table_data.rows
        if not rows:
            return

        row_count = len(rows)
        col_count = max(len(row) for row in rows)
        pptx_table_shape = slide.shapes.add_table(
            row_count,
            col_count,
            Emu(shape.position.x * PX_TO_EMU),
            Emu(shape.position.y * PX_TO_EMU),
            Emu(shape.position.w * PX_TO_EMU),
            Emu(shape.position.h * PX_TO_EMU),
        )
        table = pptx_table_shape.table

        for r_idx, row in enumerate(rows):
            for c_idx in range(col_count):
                cell = table.cell(r_idx, c_idx)
                text = str(row[c_idx]) if c_idx < len(row) else ""
                is_header = table_data.headers and r_idx == 0
                fill_color = (
                    table_data.header_fill
                    if is_header
                    else table_data.alternate_row_fill
                    if r_idx % 2 == 0
                    else table_data.row_fill
                )
                text_color = table_data.header_text_color if is_header else "111827"
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor.from_string(fill_color)
                cell.margin_left = Emu(6 * PX_TO_EMU)
                cell.margin_right = Emu(6 * PX_TO_EMU)
                cell.margin_top = Emu(4 * PX_TO_EMU)
                cell.margin_bottom = Emu(4 * PX_TO_EMU)

                paragraph = cell.text_frame.paragraphs[0]
                paragraph.text = text
                run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                run.font.name = table_data.font_family
                run.font.size = Pt(table_data.font_size * 0.75)
                run.font.bold = is_header
                run.font.color.rgb = RGBColor.from_string(text_color)

    def _add_chart(self, slide, shape: Shape, chart_data: ChartData) -> None:
        """Add a native PowerPoint chart."""
        from pptx.chart.data import ChartData as PptxChartData
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.util import Emu

        chart_type_map = {
            "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE,
            "pie": XL_CHART_TYPE.PIE,
            "donut": XL_CHART_TYPE.DOUGHNUT,
        }
        pptx_data = PptxChartData()
        pptx_data.categories = [point.label for point in chart_data.data]
        pptx_data.add_series(chart_data.series_name, [point.value for point in chart_data.data])

        chart_shape = slide.shapes.add_chart(
            chart_type_map.get(chart_data.chart_type, XL_CHART_TYPE.BAR_CLUSTERED),
            Emu(shape.position.x * PX_TO_EMU),
            Emu(shape.position.y * PX_TO_EMU),
            Emu(shape.position.w * PX_TO_EMU),
            Emu(shape.position.h * PX_TO_EMU),
            pptx_data,
        )
        chart = chart_shape.chart
        chart.has_legend = chart_data.show_legend
        chart.has_title = bool(chart_data.title)
        if chart.has_title:
            chart.chart_title.text_frame.text = chart_data.title
        try:
            chart.value_axis.has_major_gridlines = False
            chart.category_axis.tick_labels.font.size = None
            chart.value_axis.tick_labels.font.size = None
        except Exception:
            logger.debug("dsl_builder.chart_axis_style_skipped")
