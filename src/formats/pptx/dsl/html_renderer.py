"""DSL → HTML Preview Renderer.

Converts OOXML-DSL to HTML+inline-CSS for browser preview.
Since PPTX is the primary output and HTML is derived from the same DSL,
consistency between preview and final document is guaranteed by design.
"""

from __future__ import annotations

import html as html_lib

from src.formats.pptx.dsl.schema import (
    GradientFill,
    NoFill,
    PresentationDSL,
    Shape,
    SlideDSL,
    SolidFill,
    TextParagraph,
    TextRun,
)


class DSLtoHTMLRenderer:
    """Renders PresentationDSL as an HTML string suitable for iframe preview."""

    def render(self, dsl: PresentationDSL) -> str:
        """Full presentation → complete HTML document with all slides."""
        vw = dsl.viewport_width
        vh = dsl.viewport_height
        slides_html = "\n".join(
            self.render_slide(s, vw, vh) for s in dsl.slides
        )

        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;gap:24px;padding:24px;font-family:'Pretendard',system-ui,sans-serif}}
[data-slide]{{border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.3)}}
</style>
</head>
<body>
{slides_html}
</body>
</html>"""

    def render_slide(self, slide: SlideDSL, vw: int = 960, vh: int = 540) -> str:
        """Single slide → HTML snippet (self-contained with inline styles)."""
        shapes_html = "\n".join(
            self._render_shape(shape) for shape in sorted(slide.shapes, key=lambda s: s.z_index)
        )
        return (
            f'<div data-slide="{slide.index}" data-type="{slide.slide_type}" '
            f'style="position:relative;width:{vw}px;height:{vh}px;overflow:hidden;font-family:\'Pretendard\',system-ui,sans-serif">'
            f'\n{shapes_html}\n</div>'
        )

    def _render_shape(self, shape: Shape) -> str:
        """Shape → positioned div with inline styles."""
        styles: list[str] = [
            "position:absolute",
            f"left:{shape.position.x}px",
            f"top:{shape.position.y}px",
            f"width:{shape.position.w}px",
            f"height:{shape.position.h}px",
            f"z-index:{shape.z_index}",
            "overflow:hidden",
        ]

        if shape.fill and not isinstance(shape.fill, NoFill):
            styles.append(self._fill_css(shape.fill))

        if shape.border_radius > 0:
            styles.append(f"border-radius:{shape.border_radius}px")

        if shape.opacity < 1.0:
            styles.append(f"opacity:{shape.opacity}")

        if shape.shadow:
            ox, oy, blur = shape.shadow.offset_x, shape.shadow.offset_y, shape.shadow.blur
            color = self._hex_to_rgba(shape.shadow.color, shape.shadow.opacity)
            styles.append(f"box-shadow:{ox}px {oy}px {blur}px {color}")

        if shape.border:
            styles.append(f"border:{shape.border.width}px {shape.border.style} #{shape.border.color}")

        if shape.text:
            justify = {
                "top": "flex-start",
                "middle": "center",
                "bottom": "flex-end",
            }.get(shape.vertical_align, "flex-start")
            styles.append(f"display:flex;flex-direction:column;justify-content:{justify};padding:8px")

        style_str = ";".join(styles)
        if shape.table:
            content = self._render_table(shape.table)
        elif shape.chart:
            content = self._render_chart(shape.chart)
        else:
            content = self._render_text(shape.text) if shape.text else ""

        return f'<div data-id="{shape.id}" data-role="{shape.role}" style="{style_str}">{content}</div>'

    def _render_table(self, table) -> str:
        rows = ([table.headers] if table.headers else []) + table.rows
        html_rows = []
        for i, row in enumerate(rows):
            cells = []
            for value in row:
                tag = "th" if i == 0 and table.headers else "td"
                cells.append(f"<{tag}>{html_lib.escape(str(value))}</{tag}>")
            html_rows.append(f"<tr>{''.join(cells)}</tr>")
        return (
            "<table style='width:100%;height:100%;border-collapse:collapse;font-size:"
            f"{table.font_size}px;font-family:{table.font_family},sans-serif'>"
            + "".join(html_rows)
            + "</table>"
        )

    def _render_chart(self, chart) -> str:
        max_value = max((point.value for point in chart.data), default=1)
        bars = []
        for point in chart.data:
            width = max(4, int((point.value / max_value) * 100)) if max_value else 0
            bars.append(
                "<div style='display:flex;align-items:center;gap:8px;margin:6px 0'>"
                f"<span style='width:80px;font-size:11px'>{html_lib.escape(point.label)}</span>"
                f"<div style='height:14px;width:{width}%;background:#{chart.color}'></div>"
                f"<span style='font-size:11px'>{point.value:g}</span></div>"
            )
        title = f"<strong>{html_lib.escape(chart.title)}</strong>" if chart.title else ""
        return f"<div style='padding:8px'>{title}{''.join(bars)}</div>"

    def _fill_css(self, fill: SolidFill | GradientFill) -> str:
        """Convert fill model to CSS background property."""
        if isinstance(fill, SolidFill):
            return f"background:#{fill.color}"
        elif isinstance(fill, GradientFill):
            stops = ",".join(f"#{s.color} {s.position}%" for s in fill.stops)
            return f"background:linear-gradient({fill.angle}deg,{stops})"
        return ""

    def _render_text(self, paragraphs: list[TextParagraph]) -> str:
        """Render text paragraphs to HTML."""
        parts: list[str] = []
        for para in paragraphs:
            p_style = f"text-align:{para.align};line-height:{para.line_height}"
            if para.spacing_before > 0:
                p_style += f";margin-top:{para.spacing_before}px"
            if para.spacing_after > 0:
                p_style += f";margin-bottom:{para.spacing_after}px"
            spans = "".join(self._render_run(r) for r in para.runs)
            parts.append(f'<p style="{p_style}">{spans}</p>')
        return "\n".join(parts)

    def _render_run(self, run: TextRun) -> str:
        """Single text run → styled span."""
        styles: list[str] = [
            f"font-size:{run.font_size}px",
            f"font-weight:{run.font_weight}",
            f"font-family:'{run.font_family}',sans-serif",
            f"color:#{run.color}",
        ]
        if run.italic:
            styles.append("font-style:italic")
        if run.letter_spacing:
            styles.append(f"letter-spacing:{run.letter_spacing}px")

        text = html_lib.escape(run.text)
        return f'<span style="{";".join(styles)}">{text}</span>'

    @staticmethod
    def _hex_to_rgba(hex_color: str, opacity: float) -> str:
        """Convert 6-char hex + opacity to rgba string."""
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"rgba({r},{g},{b},{opacity:.2f})"
