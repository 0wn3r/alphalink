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
    # the part's outer profile in cutting order, without loose construction or thread lines (ALPHA-T: the whole
    # drawing, re-picked by hand); grooves and threads get their own elements preselected below
    from alphacam.drawing import groove_elements, outer_profile, thread_lines

    contour = outer_profile(project)
    for op in reversed(project.operations[:at]):  # reuse the start and feed of the operation before
        if hasattr(op, "start"):
            start = op.start
            break
    feed = 0.15
    try:
        feed = standard_library().get(tool).default_feed
    except KeyError:
        pass
    if inch:  # the library feeds are mm per rev; 0.15 in/rev would be 3.8 mm/rev
        feed = round(feed / 25.4, 4)
    lines = [g.id for g in project.geometry if not isinstance(g, Arc) and g.a.x == g.b.x]
    if kind == "area_clear":
        return AreaClear(tool, start, contour, depth_of_cut=0.0787 if inch else 2.0, feed=feed)
    if kind == "finish":
        return FinishPass(tool, start, contour, feed=feed)
    if kind == "face_clear":
        return FaceClear(tool, start, contour, depth_of_cut=0.0787 if inch else 2.0, feed=feed)
    if kind == "groove":
        return Groove(tool, start, groove_elements(project, contour) or contour, feed=feed, stepover=mm(2.0),
                      tip_width=mm(3.0))
    if kind == "thread":
        pair = thread_lines(project, contour)
        if pair:
            outer, inner = pair
        else:
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


# the tool types an operation needs (ALPHA-T's ATOOLIB naming, see alphacam/model/tools.py); the first is chosen
NEEDS = {"groove": ("groove",), "thread": ("thread",), "drill": ("drill",),
         "area_clear": ("turn", "bore"), "finish": ("turn", "bore"), "face_clear": ("turn",)}


def find_tool(project, number: int, library=None):
    for t in project.tools:
        if t.number == number:
            return t
    for lib in (library, standard_library()):
        if lib is None:
            continue
        try:
            return lib.get(number)
        except KeyError:
            pass
    return None


def _internal(tool) -> bool:
    low = tool.name.lower()
    return "internal" in low or "int." in low or tool.type == "bore" or tool.front_angle < 0


def tool_for(kind: str, project, at: int, library=None, external: bool = True) -> int | None:
    """Tool change on demand (workflow quick win): when the tool in use at `at` does not suit a new `kind` operation
    (a turning tool for a groove, say), the first suitable tool of the library, external or internal like the
    operation; None when the current one will do."""
    need = NEEDS.get(kind)
    if not need:
        return None
    fits = lambda t: t.type in need and (kind == "drill" or _internal(t) != external)  # noqa: E731
    cur = find_tool(project, current_tool(project, at), library)
    if cur is not None and fits(cur):
        return None
    lib = library if library is not None else standard_library()
    for ttype in need:
        for t in sorted(lib.tools, key=lambda t: t.number):
            if t.type == ttype and fits(t):
                return t.number
    return None
