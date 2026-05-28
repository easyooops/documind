"""Deterministic CSS→OOXML Mapping Engine — the core converter.

Converts ParsedElements from HTML into python-pptx shapes. No LLM calls.
All conversions are pure arithmetic (px → EMU, degrees → 60000ths).
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from src.core.logging import get_logger
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
        slide_width = 960 * PX_TO_EMU
        slide_height = 540 * PX_TO_EMU
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

        pos = element.position
        x = int(pos["left"] * PX_TO_EMU)
        y = int(pos["top"] * PX_TO_EMU)
        w = int(pos["width"] * PX_TO_EMU)
        h = int(pos["height"] * PX_TO_EMU)

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
        x = int(pos["left"] * PX_TO_EMU)
        y = int(pos["top"] * PX_TO_EMU)
        w = int(pos["width"] * PX_TO_EMU)
        h = int(pos["height"] * PX_TO_EMU)

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
            Emu(int(pos["left"] * PX_TO_EMU)),
            Emu(int(pos["top"] * PX_TO_EMU)),
            Emu(int(pos["width"] * PX_TO_EMU)),
            Emu(int(pos["height"] * PX_TO_EMU)),
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
                    table.columns[c_idx].width = Emu(int(float(width_px) * PX_TO_EMU))
                except (TypeError, ValueError, IndexError):
                    pass
        if isinstance(row_heights, list):
            for r_idx, height_px in enumerate(row_heights[:row_count]):
                try:
                    table.rows[r_idx].height = Emu(int(float(height_px) * PX_TO_EMU))
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

                cell.margin_left = Emu(int(pad_left * PX_TO_EMU))
                cell.margin_right = Emu(int(pad_right * PX_TO_EMU))
                cell.margin_top = Emu(int(pad_top * PX_TO_EMU))
                cell.margin_bottom = Emu(int(pad_bottom * PX_TO_EMU))
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
                run.font.size = Pt(header_font_size if is_header else body_font_size)
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
            Emu(int(pos["left"] * PX_TO_EMU)),
            Emu(int(pos["top"] * PX_TO_EMU)),
            Emu(int(pos["width"] * PX_TO_EMU)),
            Emu(int(pos["height"] * PX_TO_EMU)),
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
            plot.data_labels.font.size = Pt(float(options.get("label_font_size", 9)))
        except (IndexError, AttributeError):
            pass
        for axis_name in ("category_axis", "value_axis"):
            try:
                axis = getattr(chart, axis_name)
                axis.visible = bool(options.get(f"{axis_name}_visible", True))
                axis.tick_labels.font.size = Pt(float(options.get("axis_font_size", 9)))
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
        x1 = int(pos["left"] * PX_TO_EMU)
        y1 = int(pos["top"] * PX_TO_EMU)
        x2 = x1 + int(pos["width"] * PX_TO_EMU)
        y2 = y1 + int(pos["height"] * PX_TO_EMU)

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
        connector.line.width = Pt(line_width)

    def _add_placeholder_image(self, slide, element: ParsedElement) -> None:
        """Add an image element. Uses generated image from cache if available, else placeholder."""
        from pptx.util import Emu

        image_prompt = element.attributes.get("data-pptx-image-gen", "")
        if image_prompt:
            try:
                from src.utils.image_gen import IMAGE_CACHE_DIR
                import hashlib
                cache_key = hashlib.md5(f"{image_prompt}_512_512_professional".encode()).hexdigest()
                cache_path = IMAGE_CACHE_DIR / f"{cache_key}.png"
                if cache_path.exists():
                    pos = element.position
                    x = int(pos["left"] * PX_TO_EMU)
                    y = int(pos["top"] * PX_TO_EMU)
                    w = int(pos["width"] * PX_TO_EMU)
                    h = int(pos["height"] * PX_TO_EMU)
                    slide.shapes.add_picture(str(cache_path), Emu(x), Emu(y), Emu(w), Emu(h))
                    return
            except Exception:
                pass

        self._add_shape(slide, element)

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
            # Skip shadow for lines (thin shapes with height <= 4px or connectors)
            h = element.position.get("height", 0)
            is_line = h <= 4 or element.pptx_type == "connector"
            if not is_line:
                apply_shadow(pptx_shape, shadow_css)

        border_css = element.styles.get("border", "")
        if border_css:
            parsed = parse_border(border_css)
            if parsed:
                width = min(parsed["width"], 1.0)
                apply_border(pptx_shape, width, parsed["color"], parsed["style"])

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
                pptx_shape.line.width = Pt(float(options["line_width"]))
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

    def _apply_text(self, pptx_shape, element: ParsedElement) -> None:
        """Apply text content with styling to shape's text frame."""
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
        from pptx.util import Emu, Pt

        text = element.text_content
        if not text:
            return

        tf = pptx_shape.text_frame
        tf.word_wrap = True

        container_h = element.position.get("height", 0)
        container_w = element.position.get("width", 0)
        if container_h > 0 and container_h < 60 and len(text) > 30:
            tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        else:
            tf.auto_size = MSO_AUTO_SIZE.NONE

        padding_str = element.styles.get("padding", "")
        pad_px = self._extract_px_value(padding_str) if padding_str else 0
        pad_left = self._extract_px_value(element.styles.get("padding-left", "")) or pad_px or 8
        pad_right = self._extract_px_value(element.styles.get("padding-right", "")) or pad_px or 8
        pad_top = self._extract_px_value(element.styles.get("padding-top", "")) or pad_px or 4
        pad_bottom = self._extract_px_value(element.styles.get("padding-bottom", "")) or pad_px or 4
        tf.margin_left = Emu(int(pad_left * PX_TO_EMU))
        tf.margin_right = Emu(int(pad_right * PX_TO_EMU))
        tf.margin_top = Emu(int(pad_top * PX_TO_EMU))
        tf.margin_bottom = Emu(int(pad_bottom * PX_TO_EMU))

        v_align = element.styles.get("vertical-align", "")
        if not v_align:
            align_items = element.styles.get("align-items", "")
            if align_items in ("center", "middle"):
                v_align = "middle"
            elif align_items in ("flex-end", "end", "bottom"):
                v_align = "bottom"
            else:
                v_align = "top"
        anchor_map = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
        tf.vertical_anchor = anchor_map.get(v_align, MSO_ANCHOR.TOP)

        align_css = element.styles.get("text-align", "")
        if not align_css:
            justify = element.styles.get("justify-content", "")
            if justify in ("center", "middle"):
                align_css = "center"
            elif justify in ("flex-end", "end", "right"):
                align_css = "right"
            else:
                align_css = "left"
        align_map = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY}

        font_size_px = self._extract_px_value(element.styles.get("font-size", "16px"))
        font_weight = element.styles.get("font-weight", "400")
        font_family = element.styles.get("font-family", "Pretendard")
        font_color = self._extract_color(element.styles.get("color", ""))
        line_height_str = element.styles.get("line-height", "1.4")

        if not font_color:
            bg_color = self._extract_color(
                element.styles.get("background-color", "")
                or element.styles.get("background", "")
            )
            if bg_color and self._is_dark_color(bg_color):
                font_color = "ffffff"
            else:
                font_color = "1e293b"

        is_bold = font_weight in ("bold", "700", "800", "900") or (font_weight.isdigit() and int(font_weight) >= 700)

        line_height = self._parse_line_height(line_height_str)
        letter_spacing_px = self._extract_px_value(element.styles.get("letter-spacing", "0"))

        container_h = element.position.get("height", 0)
        container_w = element.position.get("width", 0)

        actual_paras = [p for p in text.split("\n") if p.strip()]
        if container_w > 0 and container_h > 0 and text:
            font_size_px, actual_paras = self._fit_text_lines_to_box(
                text=text,
                font_size_px=font_size_px,
                line_height=line_height,
                container_w=container_w,
                container_h=container_h,
                pad_left=pad_left,
                pad_right=pad_right,
                pad_top=pad_top,
                pad_bottom=pad_bottom,
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
                p.space_before = Pt(2)
                p.space_after = Pt(2)

            para_runs = [
                r for r in element.text_runs
                if r.get("text", "").strip() and para_text.find(r["text"].strip()) >= 0
            ]

            if para_runs:
                for run_data in para_runs:
                    run = p.add_run()
                    run.text = run_data["text"]
                    run_bold = run_data.get("bold", False) or is_bold
                    run.font.bold = run_bold
                    run.font.italic = run_data.get("italic", False)
                    run_size = self._extract_px_value(run_data.get("size", "")) or font_size_px
                    run.font.size = Pt(run_size * 0.75)
                    run_color = self._extract_color(run_data.get("color", "")) or font_color
                    if run_color:
                        run.font.color.rgb = RGBColor.from_string(run_color)
                    font_name = font_family.split(",")[0].strip().strip("'\"")
                    run.font.name = font_name
                    if letter_spacing_px != 0:
                        self._apply_letter_spacing(run, letter_spacing_px)
            else:
                run = p.add_run()
                run.text = para_text.strip()
                run.font.size = Pt(font_size_px * 0.75)
                run.font.bold = is_bold
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
    ) -> tuple[float, list[str]]:
        """Hard-wrap and shrink text so PPTX export stays inside its card."""
        min_font_px = 8.0
        usable_h = max(1.0, container_h - pad_top - pad_bottom)
        current_size = font_size_px
        while current_size >= min_font_px:
            lines = self._wrap_text_for_box(text, current_size, container_w, pad_left, pad_right)
            needed_h = len(lines) * current_size * line_height
            if needed_h <= usable_h:
                return current_size, lines
            current_size -= 1
        lines = self._wrap_text_for_box(text, min_font_px, container_w, pad_left, pad_right)
        max_lines = max(1, int(usable_h / (min_font_px * line_height)))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._ellipsize(lines[-1], max(8, len(lines[-1]) - 1))
        return min_font_px, lines

    def _wrap_text_for_box(
        self,
        text: str,
        font_size_px: float,
        container_w: float,
        pad_left: float,
        pad_right: float,
    ) -> list[str]:
        usable_w = max(1.0, container_w - pad_left - pad_right)
        char_ratio = 0.75
        if any("\uac00" <= c <= "\ud7a3" for c in text):
            char_ratio = 0.86
        chars_per_line = max(4, int(usable_w / max(1.0, font_size_px * char_ratio)))
        lines: list[str] = []
        for raw in text.replace("\\n", "\n").split("\n"):
            raw = raw.strip()
            if not raw:
                continue
            lines.extend(self._wrap_one_line(raw, chars_per_line))
        return lines or [text.strip()]

    def _wrap_one_line(self, line: str, chars_per_line: int) -> list[str]:
        import re

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
            break_at = remaining.rfind(" ", 0, chars_per_line + 1)
            if break_at < max(4, chars_per_line // 2):
                break_at = chars_per_line
            chunks.append(remaining[:break_at].strip())
            remaining = remaining[break_at:].strip()
        return [(marker if idx == 0 else "  ") + chunk for idx, chunk in enumerate(chunks)]

    def _ellipsize(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max(1, max_chars - 1)].rstrip() + "…"

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
        rgba = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value)
        if rgba:
            r, g, b = int(rgba.group(1)), int(rgba.group(2)), int(rgba.group(3))
            return f"{r:02x}{g:02x}{b:02x}"
        return None

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
        """Insert icon for large cards only. Skip small elements."""
        pos = element.position
        card_height = pos.get("height", 0)
        card_width = pos.get("width", 0)

        # Only insert icons on large cards (min 80px tall, 120px wide)
        if card_height < 80 or card_width < 120:
            return

        icon_name_clean = icon_name.replace("mdi:", "").replace("_", "-")
        self._try_insert_icon_image(slide, element, icon_name_clean)

    def _reserve_icon_space_for_element(self, element: ParsedElement) -> None:
        """Reserve card text space before creating a textbox with an icon."""
        if element.pptx_type not in {"textbox", "shape"} or not element.text_content:
            return
        pos = element.position
        card_h = pos.get("height", 0)
        card_w = pos.get("width", 0)
        if card_h < 80 or card_w < 120:
            return
        icon_size = min(36, max(24, int(card_h * 0.25)))
        self._reserve_icon_text_space(element, icon_size, 12)

    def _try_insert_icon_image(self, slide, element: ParsedElement, icon_name: str) -> bool:
        """Insert a large, prominent icon in the top-left area of a card."""
        from pptx.util import Emu

        try:
            from src.utils.iconify import (
                get_fallback_icon_path,
                get_icon_path,
                normalize_icon_color,
            )

            icon_color = normalize_icon_color(
                self._extract_color(element.styles.get("color", "")) or "1E293B"
            )
            icon_path = get_icon_path(icon_name, color=icon_color, size=32)
            if not icon_path or not icon_path.exists():
                icon_path = get_fallback_icon_path(icon_name, color=icon_color, size=32)
            if not icon_path or not icon_path.exists():
                logger.debug("icon_insert.not_available", icon=icon_name, color=icon_color)
                return False

            pos = element.position
            card_h = pos.get("height", 0)

            # Icon size proportional to card — large and prominent
            icon_size = min(36, max(24, int(card_h * 0.25)))
            icon_emu = int(icon_size * PX_TO_EMU)

            # Position: top-left corner of the card with padding
            pad = 12
            ix = int((pos["left"] + pad) * PX_TO_EMU)
            iy = int((pos["top"] + pad) * PX_TO_EMU)

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

    def _reserve_icon_text_space(self, element: ParsedElement, icon_size: int, pad: int) -> None:
        """Keep the card geometry stable while reserving room for its icon."""
        current_padding = self._extract_px_value(
            element.styles.get("padding-top", "") or element.styles.get("padding", "")
        )
        required_padding = pad + icon_size + 8
        if current_padding < required_padding:
            element.styles["padding-top"] = f"{required_padding}px"

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
