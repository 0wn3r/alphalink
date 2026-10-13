"""Converting a part between mm and inch, for programs written in other units than the drawing (ALPHA-T asks
"Select Units To Store Output In" when it saves; the program is then written in those units, M71 / M70).

Every length and point of the drawing and the operations is converted, and the feeds and pitches too (mm per rev
to inch per rev; ALPHA-T keeps everything in mm inside, so this is presumably what it does too - no sample yet).
Revolutions, angles, counts and tool numbers stay. Values are rounded to 6 decimals; the post rounds them to the
output decimals (mm 3, inch 4)."""

from __future__ import annotations

import copy
import dataclasses

from .geometry import Arc, Point, Text

MM_PER_INCH = 25.4
# operation fields that are lengths (or feeds / pitches, per revolution) in drawing units
LENGTHS = {"depth_of_cut", "offset_x", "offset_z", "feed", "finish_offset_x", "finish_offset_z", "peck_depth",
           "stepover", "tip_width", "z_shift", "depth", "peck", "pitch", "cut_depth", "lead_in", "lead_out",
           "retract"}


def factor(src: str, dst: str) -> float:
    if src == dst:
        return 1.0
    return 1 / MM_PER_INCH if dst == "inch" else MM_PER_INCH


def convert_units(project, units: str):
    """A copy of the project in `units` (the drawing and every operation); its output units are its units."""
    out = copy.deepcopy(project)
    out.output_units = None
    k = factor(project.units, units)
    out.units = units
    if k == 1.0:
        return out

    def v(x: float) -> float:
        return round(x * k, 6)

    def p(q: Point) -> Point:
        return Point(v(q.x), v(q.z))

    for g in out.geometry:
        g.a, g.b = p(g.a), p(g.b)
        if isinstance(g, Arc):
            g.centre = p(g.centre)
    for t in out.texts:
        if isinstance(t, Text):
            t.p, t.size = p(t.p), v(t.size)
    b = out.billet
    b.diameter, b.length, b.bore, b.face_allowance = v(b.diameter), v(b.length), v(b.bore), v(b.face_allowance)
    out.tool_change = p(out.tool_change)
    for op in out.operations:
        for f in dataclasses.fields(op):
            val = getattr(op, f.name)
            if isinstance(val, Point):
                setattr(op, f.name, p(val))
            elif f.name in LENGTHS and isinstance(val, (int, float)) and not isinstance(val, bool):
                setattr(op, f.name, v(val))
    return out

