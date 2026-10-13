"""Sensible new operations for the Add menu: tool, start point and contour taken from the part."""

from __future__ import annotations

from alphacam.model.geometry import Arc, Point
from alphacam.model.operations import (
    AreaClear,
    Drill,
    Dwell,
    FaceClear,
    FinishPass,
    Groove,
    MoveTo,
    Thread,
    ToolChange,
)
from alphacam.model.tools import standard_library
from .i18n import _

OP_MENU = [
    (_("Area Clear"), "area_clear"), (_("Finish Pass"), "finish"), (_("Face Clear"), "face_clear"),
    (_("Grooving"), "groove"), (_("Threading"), "thread"), (_("Drilling"), "drill"), (_("Tool Change"), "tool_change"),
    (_("Dwell"), "dwell"), (_("Move / Rapid"), "move"),
]


def current_tool(project, at: int) -> int:
    tool = project.first_tool
    for op in project.operations[:at]:
        if isinstance(op, ToolChange):
            tool = op.tool
    return tool


def default_op(kind: str, project, at: int):
    inch = project.units == "inch"
    tool = current_tool(project, at)
    def mm(v: float) -> float:  # an ALPHA-T default given in mm, in the part's units
        return round(v / 25.4, 4) if inch else v

    # ALPHA-T's default tool start: billet diameter + 20, Z5 (the billet is already in the part's units)
    start = Point(round(project.billet.diameter + mm(20), 4), mm(5) if not inch else 0.2)
    contour = [g.id for g in project.geometry]
    try:  # the whole drawing as one chain, in cutting order (fillets are appended last in the element list)
        from alphacam.drawing import pick_order

        contour = pick_order(project, contour)
    except (ValueError, KeyError):
        pass
    for op in reversed(project.operations[:at]):  # reuse the start and feed of the operation before
        if hasattr(op, "start"):
            start = op.start
            break
    feed = 0.15
    try:
        feed = standard_library().get(tool).default_feed
    except KeyError:
        pass
    lines = [g.id for g in project.geometry if not isinstance(g, Arc) and g.a.x == g.b.x]
    if kind == "area_clear":
        return AreaClear(tool, start, contour, depth_of_cut=0.0787 if inch else 2.0, feed=feed)
    if kind == "finish":
        return FinishPass(tool, start, contour, feed=feed)
    if kind == "face_clear":
        return FaceClear(tool, start, contour, depth_of_cut=0.0787 if inch else 2.0, feed=feed)
    if kind == "groove":
        return Groove(tool, start, contour, feed=feed, stepover=mm(2.0), tip_width=mm(3.0))
    if kind == "thread":
        outer = max(lines, key=lambda i: _x(project, i)) if lines else ""
        inner = min(lines, key=lambda i: _x(project, i)) if lines else ""
        return Thread(tool, start, outer, inner, pitch=0.0625 if inch else 1.5, cut_depth=mm(1.0), lead_in=mm(5.0))
    if kind == "drill":
        return Drill(tool, Point(0, start.z), depth=-0.8 if inch else -20, peck=0.4 if inch else 10, feed=feed)
    if kind == "tool_change":
        return ToolChange(project.first_tool if at == 0 else tool, project.tool_change, feed)
    if kind == "dwell":
        return Dwell(1)
    if kind == "move":
        return MoveTo(start, rapid=True, feed=feed)
    raise ValueError(f"unknown operation {kind}")


def _x(project, gid):
    return next(g.a.x for g in project.geometry if g.id == gid)
