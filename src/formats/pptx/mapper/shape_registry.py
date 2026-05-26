"""Shape Registry — maps data-pptx-shape values to python-pptx MSO_SHAPE constants."""

from __future__ import annotations


def get_shape_type(shape_name: str):
    """Map a data-pptx-shape string to MSO_SHAPE enum value.

    Returns None if the shape should be rendered as a freeform/textbox.
    """
    from pptx.enum.shapes import MSO_SHAPE

    SHAPE_MAP = {
        "rect": MSO_SHAPE.RECTANGLE,
        "rounded_rect": MSO_SHAPE.ROUNDED_RECTANGLE,
        "oval": MSO_SHAPE.OVAL,
        "triangle": MSO_SHAPE.ISOSCELES_TRIANGLE,
        "diamond": MSO_SHAPE.DIAMOND,
        "pentagon": MSO_SHAPE.PENTAGON,
        "hexagon": MSO_SHAPE.HEXAGON,
        "octagon": MSO_SHAPE.OCTAGON,
        "parallelogram": MSO_SHAPE.PARALLELOGRAM,
        "trapezoid": MSO_SHAPE.TRAPEZOID,
        "chevron": MSO_SHAPE.CHEVRON,
        "right_arrow": MSO_SHAPE.RIGHT_ARROW,
        "left_arrow": MSO_SHAPE.LEFT_ARROW,
        "up_arrow": MSO_SHAPE.UP_ARROW,
        "down_arrow": MSO_SHAPE.DOWN_ARROW,
        "bent_arrow": MSO_SHAPE.BENT_ARROW,
        "circular_arrow": MSO_SHAPE.CIRCULAR_ARROW,
        "u_turn_arrow": MSO_SHAPE.U_TURN_ARROW,
        "striped_right_arrow": MSO_SHAPE.STRIPED_RIGHT_ARROW,
        "notched_right_arrow": MSO_SHAPE.NOTCHED_RIGHT_ARROW,
        "cloud": MSO_SHAPE.CLOUD,
        "thought_bubble": MSO_SHAPE.CLOUD_CALLOUT,
        "rounded_callout": MSO_SHAPE.ROUNDED_RECTANGULAR_CALLOUT,
        "wedge_rect_callout": MSO_SHAPE.RECTANGULAR_CALLOUT,
        "wedge_ellipse_callout": MSO_SHAPE.OVAL_CALLOUT,
        "star_4": MSO_SHAPE.STAR_4_POINT,
        "star_5": MSO_SHAPE.STAR_5_POINT,
        "star_6": MSO_SHAPE.STAR_6_POINT,
        "star_8": MSO_SHAPE.STAR_8_POINT,
        "ribbon": MSO_SHAPE.DOWN_RIBBON,
        "ribbon_2": MSO_SHAPE.UP_RIBBON,
        "explosion_1": MSO_SHAPE.EXPLOSION1,
        "explosion_2": MSO_SHAPE.EXPLOSION2,
        "flowchart_process": MSO_SHAPE.FLOWCHART_PROCESS,
        "flowchart_decision": MSO_SHAPE.FLOWCHART_DECISION,
        "flowchart_terminator": MSO_SHAPE.FLOWCHART_TERMINATOR,
        "flowchart_data": MSO_SHAPE.FLOWCHART_DATA,
        "flowchart_document": MSO_SHAPE.FLOWCHART_DOCUMENT,
        "flowchart_connector": MSO_SHAPE.FLOWCHART_CONNECTOR,
        "flowchart_merge": MSO_SHAPE.FLOWCHART_MERGE,
        "heart": MSO_SHAPE.HEART,
        "lightning_bolt": MSO_SHAPE.LIGHTNING_BOLT,
        "sun": MSO_SHAPE.SUN,
        "moon": MSO_SHAPE.MOON,
        "block_arc": MSO_SHAPE.BLOCK_ARC,
        "donut": MSO_SHAPE.DONUT,
        "frame": MSO_SHAPE.FRAME,
        "bevel": MSO_SHAPE.BEVEL,
        "fold_corner": MSO_SHAPE.FOLDED_CORNER,
        "cross": MSO_SHAPE.CROSS,
        "plus": MSO_SHAPE.MATH_PLUS,
    }

    return SHAPE_MAP.get(shape_name.lower().strip())


def get_connector_type(connector_name: str):
    """Map connector type string to MSO_CONNECTOR_TYPE."""
    from pptx.enum.shapes import MSO_CONNECTOR_TYPE

    CONNECTOR_MAP = {
        "straight": MSO_CONNECTOR_TYPE.STRAIGHT,
        "elbow": MSO_CONNECTOR_TYPE.ELBOW,
        "curved": MSO_CONNECTOR_TYPE.CURVE,
    }

    return CONNECTOR_MAP.get(connector_name.lower().strip(), MSO_CONNECTOR_TYPE.STRAIGHT)
