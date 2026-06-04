"""Deterministic CSS→OOXML Mapping Engine — the core converter.

Converts ParsedElements from HTML into python-pptx shapes. No LLM calls.
All conversions are pure arithmetic (px → EMU, degrees → 60000ths).
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

from src.core.logging import get_logger
from src.formats.pptx.color_utils import (
    choose_legible_text_color,
    contrast_ratio,
    relative_luminance,
)
from src.formats.pptx.mapper.effects import (
    apply_border,
    apply_corner_radius,
    apply_gradient_fill,
    apply_shadow,
    parse_border,
)
from src.formats.pptx.mapper.html_parser import ParsedElement, parse_slide_html
from src.formats.pptx.mapper.shape_registry import get_shape_type

logger = get_logger(__name__)

PX_TO_EMU = 9525

ICON_UNICODE_MAP = {
    "chart_trending_up": "\U0001F4C8",
    "lightbulb": "\U0001F4A1",
    "people": "\U0001F465",
    "globe": "\U0001F310",
    "shield": "\U0001F6E1",
    "rocket": "\U0001F680",
    "target": "\U0001F3AF",
    "gear": "\u2699\uFE0F",
    "money": "\U0001F4B0",
    "calendar": "\U0001F4C5",
    "clock": "\U0001F551",
    "document": "\U0001F4C4",
    "building": "\U0001F3E2",
    "graph": "\U0001F4CA",
    "checkmark": "\u2705",
    "warning": "\u26A0\uFE0F",
    "star": "\u2B50",
    "heart": "\u2764\uFE0F",
    "cloud_upload": "\u2601\uFE0F",
    "database": "\U0001F5C4",
    "lock": "\U0001F512",
    "link": "\U0001F517",
    "mail": "\u2709\uFE0F",
    "phone": "\U0001F4DE",
}
FONT_PX_TO_HUNDREDTHS_PT = 75
DEGREES_TO_60K = 60000


class CSStoOOXMLEngine:
    """Deterministic engine that converts parsed HTML elements to PPTX shapes."""

    def __init__(self) -> None:
        self._px_to_emu_x = PX_TO_EMU
        self._px_to_emu_y = PX_TO_EMU
        self._font_scale = 1.0
        self._slide_width_emu = 960 * PX_TO_EMU
        self._slide_height_emu = 540 * PX_TO_EMU

    def build_presentation(
        self,
        slides_html: list[dict],
        output_dir: Path,
        title: str = "",
        template_bytes: bytes | None = None,
    ) -> Path:
        """Convert all slides HTML into a .pptx file."""
        from pptx import Presentation
        from pptx.util import Emu

        source_layouts = []
        using_template = bool(template_bytes)
        if template_bytes:
            prs = Presentation(io.BytesIO(template_bytes))
            source_layouts = [slide.slide_layout for slide in prs.slides]
            self._remove_template_slides(prs)
        else:
            prs = Presentation()
            prs.slide_width = Emu(960 * PX_TO_EMU)
            prs.slide_height = Emu(540 * PX_TO_EMU)
        self._configure_canvas_scale(prs, using_template=using_template)

        for position, slide_data in enumerate(
            sorted(slides_html, key=lambda s: s.get("index", 0))
        ):
            html = slide_data.get("html", "")
            if not html:
                continue

            layout = self._select_layout(prs, slide_data, source_layouts, position)
            slide = prs.slides.add_slide(layout)
            elements = parse_slide_html(html)
            slide_type = slide_data.get("metadata", {}).get("slide_type", "content")
            template_background_is_dark = None
            if using_template:
                # Generated HTML already includes its own title. Populating a template
                # title placeholder duplicates that title and can inherit oversized fonts.
                self._remove_slide_placeholders(slide)
                template_background_is_dark = self._template_background_is_dark(slide)

            for element in elements:
                try:
                    if using_template and self._is_background_element(element):
                        # Keep original master/layout background inherited from the uploaded OOXML.
                        continue
                    if using_template and self._is_generated_footer(element):
                        # Template footers belong to the native master/layout,
                        # rather than generated overlay HTML.
                        continue
                    if self._is_background_element(element):
                        self._apply_slide_background(slide, element)
                        continue
                    if using_template:
                        self._prepare_element_for_template(
                            element, slide_type, template_background_is_dark
                        )
                    self._add_element(slide, element)
                except Exception as e:
                    logger.warning(
                        "mapper.element_error",
                        error=str(e)[:200],
                        pptx_type=getattr(element, 'pptx_type', 'unknown'),
                        position=getattr(element, 'position', {}),
                    )

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"presentation_{uuid.uuid4().hex[:8]}.pptx"
        prs.save(str(output_path))

        logger.info("mapper.saved", path=str(output_path), slides=len(prs.slides))
        return output_path

    def _configure_canvas_scale(self, prs, *, using_template: bool) -> None:
        """Map the 960x540 HTML coordinate system to the actual PPTX canvas."""
        self._slide_width_emu = int(prs.slide_width)
        self._slide_height_emu = int(prs.slide_height)
        self._px_to_emu_x = self._slide_width_emu / 960
        self._px_to_emu_y = self._slide_height_emu / 540
        self._font_scale = min(self._px_to_emu_x / PX_TO_EMU, self._px_to_emu_y / PX_TO_EMU)
        if using_template:
            logger.info(
                "mapper.template_canvas_scaled",
                slide_width=self._slide_width_emu,
                slide_height=self._slide_height_emu,
                scale_x=round(self._px_to_emu_x / PX_TO_EMU, 4),
                scale_y=round(self._px_to_emu_y / PX_TO_EMU, 4),
            )

    def _x(self, px: float) -> int:
        return int(px * self._px_to_emu_x)

    def _y(self, px: float) -> int:
        return int(px * self._px_to_emu_y)

    def _w(self, px: float) -> int:
        return int(px * self._px_to_emu_x)

    def _h(self, px: float) -> int:
        return int(px * self._px_to_emu_y)

    def _s(self, px: float) -> int:
        return int(px * min(self._px_to_emu_x, self._px_to_emu_y))

    def _pt(self, px: float) -> float:
        return px * 0.75 * self._font_scale

    def _select_layout(self, prs, slide_data: dict, source_layouts: list, position: int):
        """Select an uploaded template layout while preserving the original master tree."""
        slide_type = slide_data.get("metadata", {}).get("slide_type", "content")
        if source_layouts:
            if slide_type in {"cover", "section"}:
                return source_layouts[0]
            if len(source_layouts) > 1:
                return source_layouts[1]
            return source_layouts[0]
        fallback_index = 6 if len(prs.slide_layouts) > 6 else 0
        return prs.slide_layouts[fallback_index]

    def _remove_template_slides(self, presentation) -> None:
        """Remove sample slides while leaving template masters, themes, and layouts intact."""
        slide_ids = presentation.slides._sldIdLst
        for slide_id in list(slide_ids):
            presentation.part.drop_rel(slide_id.rId)
            slide_ids.remove(slide_id)

    def _remove_slide_placeholders(self, slide) -> None:
        """Remove empty cloned placeholders before generated content is overlaid."""
        for placeholder in list(slide.placeholders):
            element = placeholder._element
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)

    def _template_background_is_dark(self, slide) -> bool:
        """Infer template backdrop contrast, defaulting to PowerPoint's white canvas."""
        slide_width = self._slide_width_emu
        slide_height = self._slide_height_emu
        shape_sources = (
            list(getattr(slide.slide_layout, "shapes", []))
            + list(getattr(slide.slide_layout.slide_master, "shapes", []))
        )
        for shape in reversed(shape_sources):
            if (
                getattr(shape, "width", 0) < slide_width * 0.9
                or getattr(shape, "height", 0) < slide_height * 0.9
            ):
                continue
            color = self._shape_fill_color(shape)
            if color:
                return self._is_dark_color(color)
        try:
            color = str(slide.background.fill.fore_color.rgb)
            if color:
                return self._is_dark_color(color)
        except (AttributeError, TypeError, ValueError):
            pass
        return False

    def _shape_fill_color(self, shape) -> str | None:
        try:
            rgb = shape.fill.fore_color.rgb
            return str(rgb).lower() if rgb else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _prepare_element_for_template(
        self, element: ParsedElement, slide_type: str, background_is_dark: bool | None
    ) -> None:
        """Make generated overlays legible against an inherited template backdrop."""
        if background_is_dark is None:
            return

        is_exposed_heading = slide_type in {"cover", "section"} or self._is_generated_header(
            element
        )
        if is_exposed_heading and element.pptx_type == "textbox":
            if not self._has_background(element.styles):
                color = self._extract_color(element.styles.get("color", ""))
                if color and self._is_dark_color(color) == background_is_dark:
                    element.styles["color"] = "#ffffff" if background_is_dark else "#1e293b"

        if background_is_dark or self._is_background_element(element):
            return
        if element.pptx_type not in {"shape", "textbox"}:
            return
        if "border" in element.styles:
            return
        pos = element.position
        if pos.get("width", 0) < 80 or pos.get("height", 0) < 32:
            return
        fill_color = self._extract_color(
            element.styles.get("background-color", "")
            or element.styles.get("background", "")
        )
        if fill_color and not self._is_dark_color(fill_color):
            element.styles["border"] = "1px solid #e2e8f0"

    def _apply_native_title(self, slide, elements: list[ParsedElement]) -> bool:
        """Fill a template title placeholder using generated heading text."""
        title_shape = slide.shapes.title
        if title_shape is None:
            return False

        title_elements = [
            element
            for element in elements
            if self._is_generated_header(element)
            and element.pptx_type == "textbox"
            and element.text_content.strip()
        ]
        if not title_elements:
            return False

        title_element = max(
            title_elements,
            key=lambda element: self._extract_px_value(
                element.styles.get("font-size", "0")
            ),
        )
        title_shape.text = title_element.text_content.strip()
        return True

    def _is_generated_header(self, element: ParsedElement) -> bool:
        return element.attributes.get("data-pptx-region") == "header"

    def _is_generated_footer(self, element: ParsedElement) -> bool:
        return element.attributes.get("data-pptx-region") == "footer"

    def _add_element(self, slide, element: ParsedElement) -> None:
        """Route element to appropriate builder based on pptx_type."""
        pptx_type = element.pptx_type

        if pptx_type == "icon":
            self._add_icon(slide, element)
            return

        icon_name = element.attributes.get("data-pptx-icon", "")
        if icon_name:
            self._reserve_icon_space_for_element(element)

        if pptx_type == "table":
            self._add_table(slide, element)
        elif pptx_type == "chart":
            self._add_chart(slide, element)
        elif pptx_type == "connector":
            self._add_connector(slide, element)
        elif pptx_type == "textbox":
            self._add_textbox(slide, element)
        elif pptx_type == "image":
            self._add_placeholder_image(slide, element)
        else:
            self._add_shape(slide, element)

        # Keep icon artwork above the containing card/background shape.
        if icon_name:
            self._insert_icon_for_element(slide, element, icon_name)

    def _add_shape(self, slide, element: ParsedElement) -> None:
        """Add a shape (rect, oval, arrow, etc.) to the slide."""
        from pptx.util import Emu
        from pptx.enum.shapes import MSO_SHAPE

        self._normalize_arrow_shape_geometry(element)
        pos = element.position
        x = self._x(pos["left"])
        y = self._y(pos["top"])
        w = self._w(pos["width"])
        h = self._h(pos["height"])

        shape_type = None
        if element.pptx_shape:
            shape_type = get_shape_type(element.pptx_shape)

        has_text = bool(element.text_content)
        has_bg = self._has_background(element.styles)
        has_radius = self._get_border_radius(element.styles) > 0

        if has_text and not has_bg and not has_radius and not shape_type:
            pptx_shape = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
        else:
            if not shape_type:
                shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if has_radius else MSO_SHAPE.RECTANGLE
            pptx_shape = slide.shapes.add_shape(shape_type, Emu(x), Emu(y), Emu(w), Emu(h))
            try:
                pptx_shape.line.fill.background()
            except (AttributeError, TypeError):
                pass

            if has_radius:
                radius = self._get_border_radius(element.styles)
                apply_corner_radius(pptx_shape, radius, w, h)

        self._apply_fill(pptx_shape, element.styles)
        self._apply_arrow_shape_defaults(pptx_shape, element)
        self._apply_effects(pptx_shape, element)
        self._apply_shape_options(pptx_shape, element)

        if has_text:
            self._apply_text(pptx_shape, element)

        if element.rotation:
            pptx_shape.rotation = element.rotation

    def _add_textbox(self, slide, element: ParsedElement) -> None:
        """Add a text box element."""
        from pptx.util import Emu

        pos = element.position
        x = self._x(pos["left"])
        y = self._y(pos["top"])
        w = self._w(pos["width"])
        h = self._h(pos["height"])

        has_bg = self._has_background(element.styles)
        has_radius = self._get_border_radius(element.styles) > 0

        if has_bg or has_radius:
            from pptx.enum.shapes import MSO_SHAPE
            shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if has_radius else MSO_SHAPE.RECTANGLE
            pptx_shape = slide.shapes.add_shape(shape_type, Emu(x), Emu(y), Emu(w), Emu(h))
            try:
                pptx_shape.line.fill.background()
            except (AttributeError, TypeError):
                pass
            if has_radius:
                apply_corner_radius(pptx_shape, self._get_border_radius(element.styles), w, h)
            self._apply_fill(pptx_shape, element.styles)
        else:
            pptx_shape = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))

        self._apply_effects(pptx_shape, element)
        self._apply_text(pptx_shape, element)

    def _add_table(self, slide, element: ParsedElement) -> None:
        """Add a native PowerPoint table from data-pptx-table-data."""
        from pptx.dml.color import RGBColor
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Emu, Pt

        table_data = element.table_data
        if not table_data:
            self._add_shape(slide, element)
            return

        headers = table_data.get("headers", [])
        rows_data = table_data.get("rows", [])

        # If headers is empty but rows exist, treat first row as header
        if not headers and rows_data:
            headers = rows_data[0]
            rows_data = rows_data[1:]

        all_rows = ([headers] if headers else []) + rows_data
        if not all_rows:
            return

        pos = element.position
        row_count = len(all_rows)
        col_count = max(len(row) for row in all_rows)

        table_shape = slide.shapes.add_table(
            row_count, col_count,
            Emu(self._x(pos["left"])),
            Emu(self._y(pos["top"])),
            Emu(self._w(pos["width"])),
            Emu(self._h(pos["height"])),
        )
        table = table_shape.table

        header_fill = table_data.get("header_fill", "")
        row_fill = table_data.get("row_fill", "")

        # Extract colors from element styles if not in table_data
        if not header_fill:
            bg = self._extract_color(element.styles.get("background-color", ""))
            header_fill = bg or "1e293b"
        if not row_fill:
            row_fill = "ffffff"

        options = {**element.table_options, **table_data.get("options", {})}
        alt_row_fill = table_data.get("alt_row_fill", "f9fafb")
        header_font_size = float(options.get("header_font_size", table_data.get("header_font_size", 11)))
        body_font_size = float(options.get("body_font_size", table_data.get("body_font_size", 10)))
        header_align = str(options.get("header_align", "center"))
        body_align = str(options.get("body_align", "left"))
        numeric_align = str(options.get("numeric_align", "right"))
        vertical_align = str(options.get("vertical_align", "middle"))
        align_map = {
            "left": PP_ALIGN.LEFT,
            "center": PP_ALIGN.CENTER,
            "right": PP_ALIGN.RIGHT,
            "justify": PP_ALIGN.JUSTIFY,
        }
        anchor_map = {
            "top": MSO_ANCHOR.TOP,
            "middle": MSO_ANCHOR.MIDDLE,
            "bottom": MSO_ANCHOR.BOTTOM,
        }
        padding = options.get("cell_padding", {})
        pad_left = float(padding.get("left", options.get("padding_left", 8)))
        pad_right = float(padding.get("right", options.get("padding_right", 8)))
        pad_top = float(padding.get("top", options.get("padding_top", 4)))
        pad_bottom = float(padding.get("bottom", options.get("padding_bottom", 4)))
        col_widths = options.get("column_widths", [])
        row_heights = options.get("row_heights", [])

        if isinstance(col_widths, list):
            for c_idx, width_px in enumerate(col_widths[:col_count]):
                try:
                    table.columns[c_idx].width = Emu(self._w(float(width_px)))
                except (TypeError, ValueError, IndexError):
                    pass
        if isinstance(row_heights, list):
            for r_idx, height_px in enumerate(row_heights[:row_count]):
                try:
                    table.rows[r_idx].height = Emu(self._h(float(height_px)))
                except (TypeError, ValueError, IndexError):
                    pass

        for r_idx, row in enumerate(all_rows):
            if r_idx >= row_count:
                break
            for c_idx in range(col_count):
                try:
                    cell = table.cell(r_idx, c_idx)
                except (IndexError, ValueError):
                    continue
                text = str(row[c_idx]) if c_idx < len(row) else ""
                is_header = headers and r_idx == 0

                if is_header:
                    fill_color = header_fill
                elif r_idx % 2 == 0:
                    fill_color = row_fill
                else:
                    fill_color = alt_row_fill
                try:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = RGBColor.from_string(fill_color.lstrip("#")[:6])
                except (ValueError, TypeError):
                    pass

                cell.margin_left = Emu(self._w(pad_left))
                cell.margin_right = Emu(self._w(pad_right))
                cell.margin_top = Emu(self._h(pad_top))
                cell.margin_bottom = Emu(self._h(pad_bottom))
                cell.vertical_anchor = anchor_map.get(vertical_align, MSO_ANCHOR.MIDDLE)

                tf = cell.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = ""
                p.alignment = align_map.get(
                    header_align if is_header else (
                        numeric_align if self._looks_numeric(text) else body_align
                    ),
                    PP_ALIGN.LEFT,
                )
                run = p.add_run()
                run.text = text
                run.font.size = Pt(self._pt(header_font_size if is_header else body_font_size))
                run.font.bold = is_header
                run.font.name = str(options.get("font_family", "Pretendard"))
                text_color = "ffffff" if is_header else "1e293b"
                run.font.color.rgb = RGBColor.from_string(text_color)

        try:
            self._apply_thin_table_borders(table, col_count, row_count, options)
        except (IndexError, ValueError, TypeError):
            pass

    def _add_chart(self, slide, element: ParsedElement) -> None:
        """Add a native PowerPoint chart from data-pptx-chart-data."""
        from pptx.chart.data import ChartData
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.util import Emu

        chart_type_str = element.chart_type or "bar"
        chart_data_raw = element.chart_data
        if not chart_data_raw:
            self._add_shape(slide, element)
            return

        chart_type_map = {
            "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE,
            "pie": XL_CHART_TYPE.PIE,
            "doughnut": XL_CHART_TYPE.DOUGHNUT,
            "area": XL_CHART_TYPE.AREA,
        }

        pptx_chart_data = ChartData()
        chart_options = element.chart_options

        if isinstance(chart_data_raw, list):
            labels = []
            for item in chart_data_raw:
                label = str(item.get("label", ""))
                if label not in labels:
                    labels.append(label)
            grouped: dict[str, list[float]] = {}
            for item in chart_data_raw:
                series_name = str(item.get("series") or chart_options.get("series_name") or "Series")
                grouped.setdefault(series_name, [0.0 for _ in labels])
                try:
                    label_index = labels.index(str(item.get("label", "")))
                except ValueError:
                    continue
                grouped[series_name][label_index] = self._coerce_number(item.get("value", 0))
            pptx_chart_data.categories = labels
            for series_name, values in grouped.items():
                pptx_chart_data.add_series(series_name, values)
            chart_options.setdefault("colors", [item.get("color") for item in chart_data_raw if item.get("color")])
        else:
            self._add_shape(slide, element)
            return

        pos = element.position
        chart_shape = slide.shapes.add_chart(
            chart_type_map.get(chart_type_str, XL_CHART_TYPE.BAR_CLUSTERED),
            Emu(self._x(pos["left"])),
            Emu(self._y(pos["top"])),
            Emu(self._w(pos["width"])),
            Emu(self._h(pos["height"])),
            pptx_chart_data,
        )

        chart = chart_shape.chart
        self._apply_chart_options(chart, chart_options, chart_type_str)

    def _apply_chart_options(self, chart, options: dict, chart_type: str) -> None:
        """Apply supported native OOXML chart formatting options."""
        from pptx.dml.color import RGBColor
        from pptx.enum.chart import XL_DATA_LABEL_POSITION, XL_LEGEND_POSITION
        from pptx.util import Pt

        legend_position = str(options.get("legend_position", "none")).lower()
        chart.has_legend = bool(options.get("show_legend", legend_position != "none"))
        if chart.has_legend:
            positions = {
                "bottom": XL_LEGEND_POSITION.BOTTOM,
                "right": XL_LEGEND_POSITION.RIGHT,
                "left": XL_LEGEND_POSITION.LEFT,
                "top": XL_LEGEND_POSITION.TOP,
            }
            chart.legend.position = positions.get(legend_position, XL_LEGEND_POSITION.RIGHT)
            chart.legend.include_in_layout = False
        try:
            plot = chart.plots[0]
            if chart_type in {"bar", "column"} and "gap_width" in options:
                plot.gap_width = int(options["gap_width"])
            plot.has_data_labels = bool(options.get("show_data_labels", True))
            plot.data_labels.show_value = True
            plot.data_labels.show_category_name = bool(options.get("show_category_name", False))
            positions = {
                "outside_end": XL_DATA_LABEL_POSITION.OUTSIDE_END,
                "inside_end": XL_DATA_LABEL_POSITION.INSIDE_END,
                "center": XL_DATA_LABEL_POSITION.CENTER,
                "best_fit": XL_DATA_LABEL_POSITION.BEST_FIT,
            }
            label_position = str(options.get("data_label_position", "outside_end"))
            plot.data_labels.position = positions.get(
                label_position, XL_DATA_LABEL_POSITION.OUTSIDE_END
            )
            plot.data_labels.font.size = Pt(self._pt(float(options.get("label_font_size", 9))))
        except (IndexError, AttributeError):
            pass
        for axis_name in ("category_axis", "value_axis"):
            try:
                axis = getattr(chart, axis_name)
                axis.visible = bool(options.get(f"{axis_name}_visible", True))
                axis.tick_labels.font.size = Pt(self._pt(float(options.get("axis_font_size", 9))))
            except (AttributeError, ValueError):
                pass
        try:
            if options.get("grid_lines", "major") == "none":
                chart.value_axis.has_major_gridlines = False
            elif options.get("gridline_color"):
                chart.value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string(
                    str(options["gridline_color"]).lstrip("#")[:6]
                )
        except (AttributeError, ValueError):
            pass
        colors = [color for color in options.get("colors", []) if color]
        if colors:
            for idx, series in enumerate(chart.series):
                try:
                    color = str(colors[idx % len(colors)]).lstrip("#")[:6]
                    series.format.fill.solid()
                    series.format.fill.fore_color.rgb = RGBColor.from_string(color)
                    series.format.line.color.rgb = RGBColor.from_string(color)
                except (AttributeError, ValueError):
                    pass

    def _add_connector(self, slide, element: ParsedElement) -> None:
        """Add a connector line with width and style."""
        from pptx.util import Emu, Pt

        pos = element.position
        x1 = self._x(pos["left"])
        y1 = self._y(pos["top"])
        x2 = x1 + self._w(pos["width"])
        y2 = y1 + self._h(pos["height"])

        connector_type = 1  # straight
        connector_name = element.attributes.get("data-pptx-connector-type", "straight")
        if connector_name == "elbow":
            connector_type = 2
        elif connector_name == "curved":
            connector_type = 3

        connector = slide.shapes.add_connector(
            connector_type, Emu(x1), Emu(y1), Emu(x2), Emu(y2)
        )

        color = self._extract_color(
            element.styles.get("background-color", "")
            or element.styles.get("border-color", "")
            or element.styles.get("color", "")
        )
        if color:
            from pptx.dml.color import RGBColor
            connector.line.color.rgb = RGBColor.from_string(color)

        height_px = pos.get("height", 2)
        border_width = self._extract_px_value(element.styles.get("border-width", ""))
        line_width = border_width if border_width > 0 else min(height_px, 4)
        if line_width <= 2:
            line_width = 0.5
        connector.line.width = Pt(self._pt(line_width / 0.75))

    def _add_placeholder_image(self, slide, element: ParsedElement) -> None:
        """Add an image element. Uses generated image from cache if available, else placeholder."""
        from pptx.util import Emu

        image_path = element.attributes.get("data-pptx-image-path", "")
        if image_path:
            try:
                path = Path(image_path)
                if path.exists() and path.is_file():
                    x, y, w, h = self._image_geometry_for_element(path, element)
                    slide.shapes.add_picture(str(path), Emu(x), Emu(y), Emu(w), Emu(h))
                    return
            except Exception:
                pass

        image_prompt = element.attributes.get("data-pptx-image-gen", "")
        if image_prompt:
            try:
                from src.utils.image_gen import IMAGE_CACHE_DIR
                import hashlib
                cache_key = hashlib.md5(f"{image_prompt}_512_512_professional".encode()).hexdigest()
                cache_path = IMAGE_CACHE_DIR / f"{cache_key}.png"
                if cache_path.exists():
                    x, y, w, h = self._image_geometry_for_element(cache_path, element)
                    slide.shapes.add_picture(str(cache_path), Emu(x), Emu(y), Emu(w), Emu(h))
                    return
            except Exception:
                pass

        self._add_shape(slide, element)

    def _image_geometry_for_element(self, path: Path, element: ParsedElement) -> tuple[int, int, int, int]:
        """Fit image pixels into the HTML image box without distorting aspect ratio."""
        pos = element.position
        x = self._x(pos["left"])
        y = self._y(pos["top"])
        w = max(1, self._w(pos["width"]))
        h = max(1, self._h(pos["height"]))
        fit = str(element.attributes.get("data-pptx-image-fit", "contain")).lower()
        if fit == "stretch":
            return x, y, w, h

        source_w, source_h = self._image_pixel_size(path)
        if source_w <= 0 or source_h <= 0:
            return x, y, w, h

        box_ratio = w / h
        source_ratio = source_w / source_h
        if fit == "cover":
            if source_ratio > box_ratio:
                target_h = h
                target_w = int(h * source_ratio)
            else:
                target_w = w
                target_h = int(w / source_ratio)
        else:
            if source_ratio > box_ratio:
                target_w = w
                target_h = int(w / source_ratio)
            else:
                target_h = h
                target_w = int(h * source_ratio)

        return (
            x + int((w - target_w) / 2),
            y + int((h - target_h) / 2),
            max(1, target_w),
            max(1, target_h),
        )

    def _image_pixel_size(self, path: Path) -> tuple[int, int]:
        try:
            from PIL import Image

            with Image.open(path) as image:
                return image.size
        except Exception:
            return 0, 0

    def _apply_fill(self, pptx_shape, styles: dict) -> None:
        """Apply background fill from CSS styles."""
        from pptx.dml.color import RGBColor

        bg = styles.get("background", "")
        bg_color = styles.get("background-color", "")

        skip_values = ("transparent", "inherit", "initial", "none", "")

        if "linear-gradient" in bg:
            apply_gradient_fill(pptx_shape, bg)
        elif bg_color and bg_color.strip().lower() not in skip_values:
            color = self._extract_color(bg_color)
            if color:
                pptx_shape.fill.solid()
                pptx_shape.fill.fore_color.rgb = RGBColor.from_string(color)
            else:
                pptx_shape.fill.background()
        elif bg and "linear-gradient" not in bg and bg.strip().lower() not in skip_values:
            color = self._extract_color(bg)
            if color:
                pptx_shape.fill.solid()
                pptx_shape.fill.fore_color.rgb = RGBColor.from_string(color)
            else:
                pptx_shape.fill.background()
        else:
            pptx_shape.fill.background()

    def _apply_effects(self, pptx_shape, element: ParsedElement) -> None:
        """Apply shadow, border from styles. Skip shadows on line elements."""
        shadow_css = element.styles.get("box-shadow", "")
        if shadow_css:
            if not self._is_line_like_element(element) and not self._is_arrow_shape(element):
                apply_shadow(pptx_shape, shadow_css)

        border_css = element.styles.get("border", "")
        if border_css:
            parsed = parse_border(border_css)
            if parsed:
                width = min(parsed["width"], 1.0)
                apply_border(pptx_shape, width, parsed["color"], parsed["style"])

    def _is_line_like_element(self, element: ParsedElement) -> bool:
        """Separator lines and connectors should stay flat, without shadows."""
        if element.pptx_type == "connector":
            return True
        pos = element.position
        width = pos.get("width", 0)
        height = pos.get("height", 0)
        shape_name = str(element.pptx_shape or "").lower()
        if shape_name in {"line", "straight_line"}:
            return True
        if width <= 0 or height <= 0:
            return False
        return min(width, height) <= 8 and max(width, height) >= min(width, height) * 4

    def _apply_shape_options(self, pptx_shape, element: ParsedElement) -> None:
        """Apply OOXML-style options for symbol/shape objects."""
        from pptx.dml.color import RGBColor
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        from pptx.util import Pt

        options = element.shape_options
        if not options:
            return
        if options.get("fill") == "none":
            pptx_shape.fill.background()
        if options.get("line_color"):
            try:
                pptx_shape.line.color.rgb = RGBColor.from_string(
                    str(options["line_color"]).lstrip("#")[:6]
                )
            except ValueError:
                pass
        if options.get("line_width") is not None:
            try:
                pptx_shape.line.width = Pt(self._pt(float(options["line_width"]) / 0.75))
            except (TypeError, ValueError):
                pass
        dash_map = {
            "dash": MSO_LINE_DASH_STYLE.DASH,
            "dot": MSO_LINE_DASH_STYLE.ROUND_DOT,
            "dash_dot": MSO_LINE_DASH_STYLE.DASH_DOT,
            "solid": MSO_LINE_DASH_STYLE.SOLID,
        }
        dash = str(options.get("line_dash", "")).lower()
        if dash in dash_map:
            pptx_shape.line.dash_style = dash_map[dash]
        if options.get("transparency") is not None:
            self._apply_shape_transparency(pptx_shape, float(options["transparency"]))

    def _is_arrow_shape(self, element: ParsedElement) -> bool:
        return str(element.pptx_shape or "").lower().strip() in {
            "right_arrow",
            "left_arrow",
            "up_arrow",
            "down_arrow",
            "bent_arrow",
            "circular_arrow",
            "u_turn_arrow",
            "striped_right_arrow",
            "notched_right_arrow",
            "chevron",
        }

    def _normalize_arrow_shape_geometry(self, element: ParsedElement) -> None:
        """Give PPTX arrow shapes enough geometry to survive conversion."""
        if not self._is_arrow_shape(element):
            return
        shape_name = str(element.pptx_shape or "").lower().strip()
        horizontal = shape_name not in {"up_arrow", "down_arrow"}
        min_w, min_h = (18.0, 10.0) if horizontal else (10.0, 18.0)
        try:
            element.position["width"] = max(float(element.position.get("width", 0)), min_w)
            element.position["height"] = max(float(element.position.get("height", 0)), min_h)
        except (TypeError, ValueError):
            element.position["width"] = min_w
            element.position["height"] = min_h
        bg = str(
            element.styles.get("background-color")
            or element.styles.get("background")
            or ""
        ).strip().lower()
        if bg in {"", "transparent", "none", "inherit", "initial"}:
            fallback = self._extract_color(element.styles.get("color", "")) or "2563EB"
            element.styles["background-color"] = f"#{fallback}"

    def _apply_arrow_shape_defaults(self, pptx_shape, element: ParsedElement) -> None:
        """Default arrows to solid fills and no outline unless explicitly styled."""
        if not self._is_arrow_shape(element):
            return
        try:
            pptx_shape.line.fill.background()
        except (AttributeError, TypeError):
            pass

    def _apply_text(self, pptx_shape, element: ParsedElement) -> None:
        """Apply text content with styling to shape's text frame."""
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
        from pptx.util import Emu, Pt

        text = self._protect_label_colon_breaks(element.text_content)
        if not text:
            return

        tf = pptx_shape.text_frame
        tf.word_wrap = not self._is_generated_header(element)

        container_h = element.position.get("height", 0)
        container_w = element.position.get("width", 0)
        if container_h > 0 and container_h < 60 and len(text) > 30:
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        else:
            tf.auto_size = MSO_AUTO_SIZE.NONE

        pad_top, pad_right, pad_bottom, pad_left = self._text_padding_box(element)
        tf.margin_left = Emu(self._w(pad_left))
        tf.margin_right = Emu(self._w(pad_right))
        tf.margin_top = Emu(self._h(pad_top))
        tf.margin_bottom = Emu(self._h(pad_bottom))

        v_align = self._text_vertical_align(element, text)
        anchor_map = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
        tf.vertical_anchor = anchor_map.get(v_align, MSO_ANCHOR.TOP)

        align_css = self._text_horizontal_align(element, text)
        align_map = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}

        font_size_px = self._extract_px_value(element.styles.get("font-size", "16px"))
        font_weight = element.styles.get("font-weight", "400")
        font_family = element.styles.get("font-family", "Pretendard")
        font_color = self._extract_color(element.styles.get("color", ""))
        line_height_str = element.attributes.get("data-pptx-line-height") or element.styles.get("line-height", "1.4")

        if not font_color:
            bg_color = self._extract_color(
                element.styles.get("background-color", "")
                or element.styles.get("background", "")
            )
            if bg_color and self._is_dark_color(bg_color):
                font_color = "ffffff"
            else:
                font_color = "1e293b"

        is_bold = font_weight in ("bold", "600", "700", "800", "900") or (font_weight.isdigit() and int(font_weight) >= 600)
        bg_color = self._extract_color(
            element.styles.get("background-color", "")
            or element.styles.get("background", "")
        )
        if bg_color and font_color:
            font_color = self._ensure_contrast_color(
                font_color,
                bg_color,
                font_size_px=font_size_px,
                bold=is_bold,
            )

        line_height = self._parse_line_height(line_height_str)
        letter_spacing_px = self._extract_px_value(element.styles.get("letter-spacing", "0"))

        container_h = element.position.get("height", 0)
        container_w = element.position.get("width", 0)

        list_kind = self._list_kind(element, text)
        if list_kind:
            text = self._normalize_list_text(text, ordered=list_kind == "numbered")

        explicit_newline = "\n" in text or "\\n" in text
        actual_paras = [p for p in text.replace("\\n", "\n").split("\n") if p.strip()]
        preserve_natural_lines = not list_kind and not explicit_newline and len(actual_paras) == 1
        if container_w > 0 and container_h > 0 and text and not self._is_generated_header(element):
            font_size_px, line_height, actual_paras = self._fit_text_lines_to_box(
                text=text,
                font_size_px=font_size_px,
                line_height=line_height,
                container_w=container_w,
                container_h=container_h,
                pad_left=pad_left,
                pad_right=pad_right,
                pad_top=pad_top,
                pad_bottom=pad_bottom,
                preserve_natural_lines=preserve_natural_lines,
            )
            if letter_spacing_px >= 0 and font_size_px <= 9:
                letter_spacing_px = -0.2

        for i, para_text in enumerate(actual_paras):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()

            p.alignment = align_map.get(align_css, PP_ALIGN.LEFT)
            p.line_spacing = line_height
            if len(actual_paras) > 1:
                p.space_before = Pt(0)
                p.space_after = Pt(0)
            if list_kind:
                p.level = 0

            para_runs = [
                r for r in element.text_runs
                if r.get("text", "").strip() and para_text.find(r["text"].strip()) >= 0
            ]

            if para_runs and not list_kind:
                for run_data in para_runs:
                    run = p.add_run()
                    run.text = run_data["text"]
                    run_bold = run_data.get("bold", False) or is_bold
                    run.font.bold = run_bold
                    run.font.italic = run_data.get("italic", False)
                    run.font.underline = bool(run_data.get("underline", False))
                    if hasattr(run.font, "strike"):
                        run.font.strike = bool(run_data.get("strike", False))
                    run_size = self._extract_px_value(run_data.get("size", "")) or font_size_px
                    run.font.size = Pt(self._pt(run_size))
                    run_color = self._extract_color(run_data.get("color", "")) or font_color
                    if bg_color and run_color:
                        run_color = self._ensure_contrast_color(
                            run_color,
                            bg_color,
                            font_size_px=run_size,
                            bold=run_bold,
                        )
                    if run_color:
                        run.font.color.rgb = RGBColor.from_string(run_color)
                    run_family = run_data.get("font_family", "") or font_family
                    font_name = run_family.split(",")[0].strip().strip("'\"")
                    run.font.name = font_name
                    if letter_spacing_px != 0:
                        self._apply_letter_spacing(run, letter_spacing_px)
            else:
                run = p.add_run()
                run.text = para_text.strip()
                run.font.size = Pt(self._pt(font_size_px))
                run.font.bold = is_bold
                run.font.italic = str(element.styles.get("font-style", "")).lower() == "italic"
                decoration = str(element.styles.get("text-decoration", "")).lower()
                run.font.underline = "underline" in decoration
                if hasattr(run.font, "strike"):
                    run.font.strike = "line-through" in decoration
                if font_color:
                    run.font.color.rgb = RGBColor.from_string(font_color)
                font_name = font_family.split(",")[0].strip().strip("'\"")
                run.font.name = font_name
                if letter_spacing_px != 0:
                    self._apply_letter_spacing(run, letter_spacing_px)

    def _fit_text_lines_to_box(
        self,
        text: str,
        font_size_px: float,
        line_height: float,
        container_w: float,
        container_h: float,
        pad_left: float,
        pad_right: float,
        pad_top: float,
        pad_bottom: float,
        preserve_natural_lines: bool = False,
    ) -> tuple[float, float, list[str]]:
        """Estimate wrapping and shrink text so PPTX export stays inside its card."""
        min_font_px = 8.0
        usable_h = max(1.0, container_h - pad_top - pad_bottom)
        natural_lines = [p for p in text.replace("\\n", "\n").split("\n") if p.strip()]
        current_size = font_size_px
        while current_size >= min_font_px:
            lines = self._wrap_text_for_box(text, current_size, container_w, pad_left, pad_right)
            for candidate_line_height in self._line_height_candidates(line_height, len(lines)):
                needed_h = len(lines) * current_size * candidate_line_height
                if needed_h <= usable_h:
                    return (
                        current_size,
                        candidate_line_height,
                        natural_lines if preserve_natural_lines and natural_lines else lines,
                    )
            current_size -= 1
        lines = self._wrap_text_for_box(text, min_font_px, container_w, pad_left, pad_right)
        fitted_line_height = self._line_height_candidates(line_height, len(lines))[-1]
        if preserve_natural_lines and natural_lines:
            return min_font_px, fitted_line_height, natural_lines
        max_lines = max(1, int(usable_h / (min_font_px * fitted_line_height)))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._ellipsize(lines[-1], max(8, len(lines[-1]) - 1))
        return min_font_px, fitted_line_height, lines

    def _line_height_candidates(self, line_height: float, line_count: int) -> list[float]:
        """Compensate for PowerPoint's taller paragraph metrics in fitted boxes."""
        if line_count <= 1:
            return [line_height]
        compact = max(1.02, min(line_height, line_height - 0.08))
        tight = max(1.0, min(compact, line_height - 0.16))
        candidates = [line_height, compact, tight]
        deduped = []
        for value in candidates:
            if value not in deduped:
                deduped.append(value)
        return deduped

    def _wrap_text_for_box(
        self,
        text: str,
        font_size_px: float,
        container_w: float,
        pad_left: float,
        pad_right: float,
    ) -> list[str]:
        usable_w = max(1.0, container_w - pad_left - pad_right)
        char_ratio = self._text_width_ratio(text)
        chars_per_line = max(4, int(usable_w / max(1.0, font_size_px * char_ratio)))
        lines: list[str] = []
        for raw in text.replace("\\n", "\n").split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            lines.extend(self._wrap_one_line(raw, chars_per_line))
        return lines or [text.strip()]

    def _text_width_ratio(self, text: str) -> float:
        """Approximate mixed Korean/Latin line width for PPTX fitting."""
        chars = [char for char in str(text or "") if char not in "\r\n"]
        if not chars:
            return 0.75
        total = 0.0
        for char in chars:
            code = ord(char)
            if "\uac00" <= char <= "\ud7a3" or 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF:
                total += 0.88
            elif char.isspace():
                total += 0.32
            elif char.isupper():
                total += 0.68
            elif char.islower():
                total += 0.54
            elif char.isdigit():
                total += 0.58
            elif char in ".,;:!?/\\|-_()[]{}":
                total += 0.40
            else:
                total += 0.70
        return max(0.46, min(0.92, total / len(chars)))

    def _wrap_one_line(self, line: str, chars_per_line: int) -> list[str]:
        import re

        unicode_marker_match = re.match(
            r"^([\u2022\u2023\u25E6\u2043\u2219\-*+]|\d+[.)])\s*(.*)",
            line,
        )
        if unicode_marker_match:
            marker = unicode_marker_match.group(1) + " "
            body = unicode_marker_match.group(2).strip()
            chars_per_line = max(4, chars_per_line - len(marker))
            if len(marker + body) <= chars_per_line + len(marker):
                return [marker + body]
            chunks = []
            remaining = body
            while remaining:
                if len(remaining) <= chars_per_line:
                    chunks.append(remaining)
                    break
                protected = self._protected_colon_prefix_length(remaining)
                break_at = remaining.rfind(" ", 0, chars_per_line + 1)
                if protected and break_at < protected:
                    break_at = protected
                if break_at < max(4, chars_per_line // 2):
                    break_at = chars_per_line
                chunks.append(remaining[:break_at].strip())
                remaining = remaining[break_at:].strip()
            return [(marker if idx == 0 else "  ") + chunk for idx, chunk in enumerate(chunks)]

        marker_match = re.match(r"^([•▸▶→▪◦◆◇✓]|\d+[.)])\s*(.*)", line)
        marker = ""
        body = line
        if marker_match:
            marker = marker_match.group(1) + " "
            body = marker_match.group(2).strip()
            chars_per_line = max(4, chars_per_line - len(marker))
        if len(marker + body) <= chars_per_line + len(marker):
            return [marker + body]
        chunks = []
        remaining = body
        while remaining:
            if len(remaining) <= chars_per_line:
                chunks.append(remaining)
                break
            protected = self._protected_colon_prefix_length(remaining)
            break_at = remaining.rfind(" ", 0, chars_per_line + 1)
            if protected and break_at < protected:
                break_at = protected
            if break_at < max(4, chars_per_line // 2):
                break_at = chars_per_line
            chunks.append(remaining[:break_at].strip())
            remaining = remaining[break_at:].strip()
        return [(marker if idx == 0 else "  ") + chunk for idx, chunk in enumerate(chunks)]

    def _protected_colon_prefix_length(self, text: str) -> int:
        import re

        match = re.match(r"^[^\s:：]{1,16}[:：]\u00a0\S+", str(text or ""))
        return len(match.group(0)) if match else 0

    def _protect_label_colon_breaks(self, text: str) -> str:
        """Keep short label prefixes like '핵심:' attached to the following word."""
        import re

        return re.sub(
            r"(?<!\S)([^\s:：]{1,16}[:：])\s+(?=\S)",
            lambda match: match.group(1) + "\u00a0",
            str(text or ""),
        )

    def _ellipsize(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max(1, max_chars - 1)].rstrip() + "…"

    def _text_padding_box(self, element: ParsedElement) -> tuple[float, float, float, float]:
        raw = element.attributes.get("data-pptx-text-padding")
        if raw:
            parsed = self._parse_text_padding_attr(raw)
            if parsed:
                return parsed

        has_css_padding = any(
            element.styles.get(name)
            for name in ("padding", "padding-top", "padding-right", "padding-bottom", "padding-left")
        )
        if has_css_padding:
            return self._padding_box(element.styles)

        defaults = {
            "kpi_value": (0.0, 4.0, 0.0, 4.0),
            "kpi_label": (1.0, 4.0, 1.0, 4.0),
            "badge": (1.0, 8.0, 1.0, 8.0),
            "card_title": (2.0, 4.0, 2.0, 4.0),
            "caption": (1.0, 4.0, 1.0, 4.0),
        }
        return defaults.get(self._text_role(element, element.text_content), self._padding_box(element.styles))

    def _parse_text_padding_attr(self, raw: object) -> tuple[float, float, float, float] | None:
        if isinstance(raw, dict):
            return self._padding_from_dict(raw)

        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except (TypeError, ValueError):
                data = None
            if isinstance(data, dict):
                return self._padding_from_dict(data)

        return self._padding_values(text)

    def _padding_from_dict(self, data: dict) -> tuple[float, float, float, float] | None:
        if not data:
            return None

        def number(name: str, fallback: float = 0.0) -> float:
            try:
                return float(str(data.get(name, fallback)).replace("px", ""))
            except (TypeError, ValueError):
                return fallback

        if "x" in data or "y" in data:
            x = number("x", 0.0)
            y = number("y", 0.0)
            return y, x, y, x
        return (
            number("top", 0.0),
            number("right", 0.0),
            number("bottom", 0.0),
            number("left", 0.0),
        )

    def _text_horizontal_align(self, element: ParsedElement, text: str) -> str:
        raw = self._normalize_text_align(element.attributes.get("data-pptx-text-align"))
        if raw:
            return raw

        align_css = self._normalize_text_align(element.styles.get("text-align", ""))
        if align_css:
            return align_css

        justify = element.styles.get("justify-content", "")
        if justify in ("center", "middle"):
            return "center"
        if justify in ("flex-end", "end", "right"):
            return "right"

        role = self._text_role(element, text)
        if role in {"kpi_value", "kpi_label", "badge"}:
            return "center"
        return "left"

    def _text_vertical_align(self, element: ParsedElement, text: str) -> str:
        raw = self._normalize_text_valign(element.attributes.get("data-pptx-text-valign"))
        if raw:
            return raw

        v_align = self._normalize_text_valign(element.styles.get("vertical-align", ""))
        if v_align:
            return v_align

        align_items = element.styles.get("align-items", "")
        if align_items in ("center", "middle"):
            return "middle"
        if align_items in ("flex-end", "end", "bottom"):
            return "bottom"

        role = self._text_role(element, text)
        if role in {"kpi_value", "kpi_label", "badge", "card_title", "caption"}:
            return "middle"
        return "top"

    def _text_role(self, element: ParsedElement, text: str) -> str:
        raw = str(element.attributes.get("data-pptx-text-role", "") or "").strip().lower()
        role = raw.replace("-", "_")
        known = {
            "badge", "body", "callout", "caption", "card_body", "card_title",
            "kpi_label", "kpi_value", "list", "title",
        }
        if role in known:
            return role

        if element.attributes.get("data-pptx-list"):
            return "list"
        font_size = self._extract_px_value(element.styles.get("font-size", "16px"))
        font_weight = str(element.styles.get("font-weight", "400"))
        height = float(element.position.get("height", 0) or 0)
        if font_size >= 24 or self._looks_like_kpi_text(text):
            return "kpi_value"
        if height and height <= 30 and font_size <= 12:
            return "caption"
        is_bold = font_weight in ("bold", "600", "700", "800", "900") or (
            font_weight.isdigit() and int(font_weight) >= 600
        )
        if is_bold and height and height <= 48:
            return "card_title"
        return "body"

    def _looks_like_kpi_text(self, text: str) -> bool:
        import re

        value = str(text or "").strip()
        if len(value) > 24:
            return False
        return bool(re.search(r"(?:[$€₩¥]\s?\d|\d+(?:\.\d+)?\s?%|\d+\s?배|\d+\s?x)", value, re.I))

    def _normalize_text_align(self, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"left", "center", "right", "justify"} else ""

    def _normalize_text_valign(self, value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"center", "middle"}:
            return "middle"
        return text if text in {"top", "bottom"} else ""

    def _padding_box(self, styles: dict) -> tuple[float, float, float, float]:
        values = self._padding_values(styles.get("padding", ""))
        top, right, bottom, left = values or (4.0, 8.0, 4.0, 8.0)
        top = self._extract_px_value(styles.get("padding-top", "")) or top
        right = self._extract_px_value(styles.get("padding-right", "")) or right
        bottom = self._extract_px_value(styles.get("padding-bottom", "")) or bottom
        left = self._extract_px_value(styles.get("padding-left", "")) or left
        return top, right, bottom, left

    def _padding_values(self, value: str) -> tuple[float, float, float, float] | None:
        import re

        nums = [
            float(match.group(1))
            for match in re.finditer(r"(-?\d+(?:\.\d+)?)\s*px?", str(value or ""))
        ]
        if not nums:
            return None
        if len(nums) == 1:
            return nums[0], nums[0], nums[0], nums[0]
        if len(nums) == 2:
            return nums[0], nums[1], nums[0], nums[1]
        if len(nums) == 3:
            return nums[0], nums[1], nums[2], nums[1]
        return nums[0], nums[1], nums[2], nums[3]

    def _list_kind(self, element: ParsedElement, text: str) -> str:
        import re

        raw = str(element.attributes.get("data-pptx-list", "")).lower()
        if raw in {"number", "numbered", "ordered"}:
            return "numbered"
        if raw in {"bullet", "bullets", "unordered"}:
            return "bullet"
        lines = [line.strip() for line in text.replace("\\n", "\n").split("\n") if line.strip()]
        if len(lines) < 2:
            return ""
        marker_count = sum(1 for line in lines if self._list_marker_match(line))
        if marker_count >= max(2, len(lines) - 1):
            if all(re.match(r"^\s*\d+[.)]\s+", line) for line in lines[:marker_count]):
                return "numbered"
            return "bullet"
        short_lines = sum(1 for line in lines if len(self._strip_list_marker(line)) <= 42)
        return "bullet" if len(lines) >= 3 and short_lines == len(lines) else ""

    def _normalize_list_text(self, text: str, *, ordered: bool) -> str:
        lines = [line.strip() for line in text.replace("\\n", "\n").split("\n") if line.strip()]
        normalized_unicode = []
        for index, line in enumerate(lines, start=1):
            body = self._strip_list_marker(line)
            if not body:
                continue
            marker = f"{index}." if ordered else "\u2022"
            normalized_unicode.append(f"{marker} {body}")
        return "\n".join(normalized_unicode)

        normalized = []
        for index, line in enumerate(lines, start=1):
            body = self._strip_list_marker(line)
            if not body:
                continue
            marker = f"{index}." if ordered else "•"
            normalized.append(f"{marker} {body}")
        return "\n".join(normalized)

    def _strip_list_marker(self, line: str) -> str:
        import re

        text = str(line)
        previous = None
        while previous != text:
            previous = text
            text = re.sub(
                r"^\s*(?:[\u2022\u2023\u25E6\u2043\u2219\-*+]|[0-9]+[.)]|[A-Za-z][.)])\s*",
                "",
                text,
            )
        if text != str(line):
            return text.strip()

        return re.sub(
            r"^\s*(?:[•▸▶→▪◦◆◇✓\-*+]|[0-9]+[.)]|[A-Za-z][.)])\s*",
            "",
            str(line),
        ).strip()

    def _list_marker_match(self, line: str):
        import re

        unicode_match = re.match(
            r"^\s*(?:[\u2022\u2023\u25E6\u2043\u2219\-*+]|[0-9]+[.)]|[A-Za-z][.)])\s+",
            str(line),
        )
        if unicode_match:
            return unicode_match

        return re.match(
            r"^\s*(?:[•▸▶→▪◦◆◇✓\-*+]|[0-9]+[.)]|[A-Za-z][.)])\s+",
            str(line),
        )

    def _apply_thin_table_borders(
        self, table, col_count: int, row_count: int, options: dict | None = None
    ) -> None:
        """Apply thin (0.5pt) borders to table cells for a clean look."""
        from pptx.oxml.ns import qn
        from lxml import etree

        options = options or {}
        border = options.get("border", {}) if isinstance(options.get("border"), dict) else {}
        border_color = str(border.get("color", options.get("border_color", "E2E8F0"))).lstrip("#")[:6]
        width_pt = float(border.get("width_pt", options.get("border_width", 0.5)))
        border_width = str(int(width_pt * 12700))

        for r_idx in range(row_count):
            for c_idx in range(col_count):
                try:
                    cell = table.cell(r_idx, c_idx)
                except (IndexError, ValueError):
                    continue
                tc = cell._tc
                tcPr = tc.find(qn("a:tcPr"))
                if tcPr is None:
                    tcPr = etree.SubElement(tc, qn("a:tcPr"))

                for border_tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
                    existing = tcPr.find(qn(border_tag))
                    if existing is not None:
                        tcPr.remove(existing)
                    ln = etree.SubElement(tcPr, qn(border_tag))
                    ln.set("w", border_width)
                    ln.set("cap", "flat")
                    ln.set("cmpd", "sng")
                    solidFill = etree.SubElement(ln, qn("a:solidFill"))
                    srgbClr = etree.SubElement(solidFill, qn("a:srgbClr"))
                    srgbClr.set("val", border_color)

    def _looks_numeric(self, value: str) -> bool:
        import re

        return bool(re.fullmatch(r"\s*[-+]?[\d,.]+%?\s*", value or ""))

    def _coerce_number(self, value: object) -> float:
        import re

        if isinstance(value, int | float):
            return float(value)
        text = str(value)
        text = text.replace(",", "")
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else 0.0

    def _apply_shape_transparency(self, pptx_shape, transparency: float) -> None:
        """Apply fill transparency via OOXML alpha."""
        try:
            from pptx.oxml.ns import qn
            from lxml import etree

            alpha_val = str(int(max(0.0, min(1.0, 1 - transparency)) * 100000))
            sp_pr = pptx_shape._element.spPr
            solid_fill = sp_pr.find(qn("a:solidFill"))
            if solid_fill is None:
                return
            srgb = solid_fill.find(qn("a:srgbClr"))
            if srgb is None:
                return
            for alpha in srgb.findall(qn("a:alpha")):
                srgb.remove(alpha)
            alpha = etree.SubElement(srgb, qn("a:alpha"))
            alpha.set("val", alpha_val)
        except (AttributeError, TypeError, ValueError):
            pass

    def _has_background(self, styles: dict) -> bool:
        bg = styles.get("background", "")
        bg_color = styles.get("background-color", "")
        return bool(bg) or bool(bg_color)

    def _is_background_element(self, element: ParsedElement) -> bool:
        """Check if an element covers entire slide and acts as background."""
        pos = element.position
        w = pos.get("width", 0)
        h = pos.get("height", 0)
        left = pos.get("left", 0)
        top = pos.get("top", 0)
        if w >= 950 and h >= 530 and left <= 5 and top <= 5:
            if not element.text_content and self._has_background(element.styles):
                return True
        return False

    def _apply_slide_background(self, slide, element: ParsedElement) -> None:
        """Apply the element's background as the slide fill (solid or gradient)."""
        from pptx.dml.color import RGBColor

        bg = element.styles.get("background", "")
        bg_color = element.styles.get("background-color", "")

        if "linear-gradient" in bg:
            try:
                from src.formats.pptx.mapper.effects import parse_gradient
                gradient = parse_gradient(bg)
                if gradient and gradient.get("stops"):
                    fill = slide.background.fill
                    fill.gradient()
                    for i, stop in enumerate(gradient["stops"][:2]):
                        color = stop.get("color", "")
                        if color and i < len(fill.gradient_stops):
                            from pptx.dml.color import RGBColor as RGB
                            fill.gradient_stops[i].color.rgb = RGB.from_string(color)
                    return
            except Exception:
                pass

        color_css = bg_color or bg
        color = self._extract_color(color_css)
        if color:
            fill = slide.background.fill
            fill.solid()
            fill.fore_color.rgb = RGBColor.from_string(color)

    def _get_border_radius(self, styles: dict) -> float:
        val = styles.get("border-radius", "0")
        return self._extract_px_value(val)

    def _parse_line_height(self, value: str) -> float:
        """Parse CSS line-height supporting px, em, %, and unitless values."""
        value = value.strip()
        if not value:
            return 1.5
        if value.endswith("px"):
            return 1.5  # Absolute px — fallback to default ratio
        if value.endswith("%"):
            try:
                return float(value.replace("%", "")) / 100.0
            except ValueError:
                return 1.5
        if value.endswith("em"):
            value = value.replace("em", "")
        try:
            return float(value)
        except (ValueError, TypeError):
            return 1.5

    def _extract_px_value(self, value: str) -> float:
        """Extract numeric px value from CSS size string. Handles px, pt, rem, em."""
        import re
        if not value:
            return 0.0
        value = value.strip()
        match = re.match(r"([-\d.]+)\s*(px|pt|rem|em|)?", value)
        if not match:
            return 0.0
        num = float(match.group(1))
        unit = match.group(2) or "px"
        if unit == "pt":
            return num * 1.333
        elif unit in ("rem", "em"):
            return num * 16
        return num

    def _extract_color(self, value: str) -> str | None:
        """Extract 6-char hex color from CSS value."""
        import re
        if not value:
            return None
        value = value.strip()
        if value.lower() in ("transparent", "inherit", "initial", "none", "currentcolor"):
            return None
        if value.startswith("#"):
            hex_val = value[1:]
            if len(hex_val) == 3:
                return "".join(c * 2 for c in hex_val).lower()
            if len(hex_val) >= 6:
                return hex_val[:6].lower()
        embedded_hex = re.search(r"#([0-9a-fA-F]{3,8})", value)
        if embedded_hex:
            hex_val = embedded_hex.group(1)
            if len(hex_val) == 3:
                return "".join(c * 2 for c in hex_val).lower()
            if len(hex_val) >= 6:
                return hex_val[:6].lower()
        rgba = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value)
        if rgba:
            r, g, b = int(rgba.group(1)), int(rgba.group(2)), int(rgba.group(3))
            return f"{r:02x}{g:02x}{b:02x}"
        return None

    def _ensure_contrast_color(
        self,
        foreground: str,
        background: str,
        *,
        font_size_px: float,
        bold: bool,
    ) -> str:
        """Return a readable text color against its fill."""
        return choose_legible_text_color(
            foreground,
            background,
            font_size_px=font_size_px,
            bold=bold,
        ).lower()

    def _contrast_ratio(self, color_a: str, color_b: str) -> float:
        return contrast_ratio(color_a, color_b)

    def _relative_luminance(self, hex_color: str) -> float:
        return relative_luminance(hex_color)

    def _is_dark_color(self, hex_color: str) -> bool:
        """Determine if a hex color is dark (for choosing text color contrast)."""
        try:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            return luminance < 0.5
        except (ValueError, IndexError):
            return False

    def _insert_icon_for_element(self, slide, element: ParsedElement, icon_name: str) -> None:
        """Insert icons for large cards or explicit icon slots."""
        if not self._should_render_icon_for_element(element):
            return

        icon_name_clean = icon_name.replace("mdi:", "").replace("_", "-")
        self._try_insert_icon_image(slide, element, icon_name_clean)

    def _reserve_icon_space_for_element(self, element: ParsedElement) -> None:
        """Reserve text space before creating a textbox with an icon."""
        if element.pptx_type not in {"textbox", "shape"} or not element.text_content:
            return
        if not self._should_render_icon_for_element(element):
            return
        icon_size = self._icon_size_for_element(element)
        icon_layout = self._icon_layout_for_element(element)
        self._reserve_icon_text_space(element, icon_size, 12, icon_layout)

    def _should_render_icon_for_element(self, element: ParsedElement) -> bool:
        pos = element.position
        width = pos.get("width", 0)
        height = pos.get("height", 0)
        has_explicit_slot = bool(
            element.attributes.get("data-pptx-icon-layout")
            or element.attributes.get("data-pptx-icon-size")
        )
        if has_explicit_slot:
            return width >= 24 and height >= 18
        return width >= 120 and height >= 80

    def _try_insert_icon_image(self, slide, element: ParsedElement, icon_name: str) -> bool:
        """Insert a large, prominent icon in the top-left area of a card."""
        from pptx.util import Emu

        try:
            from src.utils.iconify import (
                get_fallback_icon_path,
                get_icon_asset_path,
                normalize_icon_color,
            )

            icon_color = normalize_icon_color(
                self._extract_color(element.styles.get("color", "")) or "1E293B"
            )
            icon_path = get_icon_asset_path(icon_name, color=icon_color, size=32, target="pptx")
            if not icon_path or not icon_path.exists():
                icon_path = get_fallback_icon_path(icon_name, color=icon_color, size=32)
            if not icon_path or not icon_path.exists():
                logger.debug("icon_insert.not_available", icon=icon_name, color=icon_color)
                return False


            # Icon size proportional to card — large and prominent
            icon_size = self._icon_size_for_element(element)
            icon_emu = self._s(icon_size)

            # Position: top-left corner of the card with padding
            ix, iy = self._icon_position_for_element(element, icon_size, pad=12)

            if str(icon_path).endswith(".png"):
                slide.shapes.add_picture(
                    str(icon_path), Emu(ix), Emu(iy), Emu(icon_emu), Emu(icon_emu)
                )
            else:
                png_data = self._svg_to_png(icon_path)
                if png_data:
                    image_stream = io.BytesIO(png_data)
                    slide.shapes.add_picture(
                        image_stream, Emu(ix), Emu(iy), Emu(icon_emu), Emu(icon_emu)
                    )
                else:
                    return False

            logger.info("icon_insert.success", icon=icon_name, size=icon_size)
            return True
        except Exception as e:
            logger.warning("icon_insert.failed", icon=icon_name, error=str(e)[:100])
            return False

    def _icon_layout_for_element(self, element: ParsedElement) -> str:
        layout = str(element.attributes.get("data-pptx-icon-layout", "top-left")).strip()
        allowed = {"top-left", "inline-left", "badge-top-right", "metric-left"}
        layout = layout if layout in allowed else "top-left"
        if layout == "top-left" and element.position.get("height", 0) < 64:
            return "inline-left"
        return layout

    def _icon_size_for_element(self, element: ParsedElement) -> int:
        raw_size = element.attributes.get("data-pptx-icon-size")
        try:
            requested = int(str(raw_size)) if raw_size else 0
        except ValueError:
            requested = 0
        height = element.position.get("height", 0)
        if requested:
            return min(44, max(16, requested))
        if height < 64:
            return min(28, max(16, int(height * 0.75)))
        return min(44, max(24, int(height * 0.25)))

    def _icon_position_for_element(
        self,
        element: ParsedElement,
        icon_size: int,
        *,
        pad: int,
    ) -> tuple[int, int]:
        pos = element.position
        layout = self._icon_layout_for_element(element)
        left = pos["left"] + pad
        top = pos["top"] + pad
        if layout in {"inline-left", "metric-left"}:
            left = pos["left"] + min(8, max(2, pad // 2))
            top = pos["top"] + (pos.get("height", icon_size) - icon_size) / 2
        elif layout == "badge-top-right":
            left = pos["left"] + pos.get("width", icon_size) - pad - icon_size
        return self._x(left), self._y(top)

    def _reserve_icon_text_space(
        self,
        element: ParsedElement,
        icon_size: int,
        pad: int,
        layout: str,
    ) -> None:
        """Keep the card geometry stable while reserving room for its icon."""
        if layout in {"inline-left", "metric-left"}:
            current_padding = self._extract_px_value(
                element.styles.get("padding-left", "") or element.styles.get("padding", "")
            )
            required_padding = min(8, max(2, pad // 2)) + icon_size + 8
            if current_padding < required_padding:
                element.styles["padding-left"] = f"{required_padding}px"
            return
        if layout == "badge-top-right":
            return
        current_padding = self._extract_px_value(
            element.styles.get("padding-top", "") or element.styles.get("padding", "")
        )
        required_padding = pad + icon_size + 8
        if current_padding < required_padding:
            element.styles["padding-top"] = f"{required_padding}px"

    def _add_icon(self, slide, element: ParsedElement) -> bool:
        """Add an independent icon element using the HTML element box exactly."""
        from pptx.util import Emu

        icon_name = element.attributes.get("data-pptx-icon", "")
        if not icon_name:
            return False

        try:
            from src.utils.iconify import (
                get_fallback_icon_path,
                get_icon_asset_path,
                normalize_icon_color,
            )

            icon_color = normalize_icon_color(
                element.attributes.get("data-pptx-icon-color")
                or self._extract_color(element.styles.get("color", ""))
                or self._extract_color(element.styles.get("background-color", ""))
                or "1E293B"
            )
            icon_path = get_icon_asset_path(icon_name, color=icon_color, size=32, target="pptx")
            if not icon_path or not icon_path.exists():
                icon_path = get_fallback_icon_path(icon_name, color=icon_color, size=32)
            if not icon_path or not icon_path.exists():
                logger.debug("icon_element.not_available", icon=icon_name, color=icon_color)
                return False

            pos = element.position
            x, y, w, h = self._icon_geometry_for_element(icon_path, element)

            if str(icon_path).endswith(".png"):
                slide.shapes.add_picture(str(icon_path), Emu(x), Emu(y), Emu(w), Emu(h))
            else:
                png_data = self._svg_to_png(icon_path)
                if not png_data:
                    return False
                slide.shapes.add_picture(io.BytesIO(png_data), Emu(x), Emu(y), Emu(w), Emu(h))

            logger.info(
                "icon_element.success",
                icon=icon_name,
                x=pos["left"],
                y=pos["top"],
                w=pos.get("width", 32),
                h=pos.get("height", 32),
            )
            return True
        except Exception as e:
            logger.warning("icon_element.failed", icon=icon_name, error=str(e)[:100])
            return False

    def _icon_geometry_for_element(self, path: Path, element: ParsedElement) -> tuple[int, int, int, int]:
        """Center icon artwork inside its declared HTML box."""
        pos = element.position
        box_x = self._x(pos["left"])
        box_y = self._y(pos["top"])
        box_w = max(1, self._w(max(1, pos.get("width", 32))))
        box_h = max(1, self._h(max(1, pos.get("height", 32))))
        source_w, source_h = self._image_pixel_size(path)
        if source_w <= 0 or source_h <= 0:
            side = min(box_w, box_h)
            return box_x + int((box_w - side) / 2), box_y + int((box_h - side) / 2), side, side
        ratio = source_w / source_h
        if ratio > box_w / box_h:
            target_w = box_w
            target_h = int(box_w / ratio)
        else:
            target_h = box_h
            target_w = int(box_h * ratio)
        return (
            box_x + int((box_w - target_w) / 2),
            box_y + int((box_h - target_h) / 2),
            max(1, target_w),
            max(1, target_h),
        )

    def _svg_to_png(self, svg_path) -> bytes | None:
        """Convert SVG file to PNG bytes using svglib+reportlab."""
        try:
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPM

            drawing = svg2rlg(str(svg_path))
            if not drawing:
                return None
            render_size = 64
            sx = render_size / drawing.width if drawing.width else 1
            sy = render_size / drawing.height if drawing.height else 1
            drawing.width = render_size
            drawing.height = render_size
            drawing.scale(sx, sy)
            return renderPM.drawToString(drawing, fmt="PNG", dpi=72)
        except Exception:
            return None

    def _apply_letter_spacing(self, run, spacing_px: float) -> None:
        """Apply letter spacing (character spacing) to a run via OOXML."""
        try:
            from pptx.oxml.ns import qn
            spacing_hundredths_pt = int(spacing_px * 75)
            rPr = run._r.get_or_add_rPr()
            rPr.set(qn("a:spc"), str(spacing_hundredths_pt))
        except (AttributeError, TypeError):
            pass
