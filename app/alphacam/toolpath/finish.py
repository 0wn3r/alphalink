"""Finish pass (#127=14, internal 15): one pass along the imaginary tool-tip path (contour.tip_path)."""

from __future__ import annotations

from ..model.operations import FinishPass
from .contour import Seg, leg45, mm, tip_path, translate
from .toolpath import Move, MoveKind, Pass, Toolpath

LEAD_IN_MM = 2.0  # 45-degree approach to the first point, from +Z and outside in X (-X internal)
LEAD_OUT_MM = 0.2  # 45-degree pull-off from the last point, same directions (ALPHA-T; V1.81 uses 2)


def finish_pass(op: FinishPass, path: list[Seg], nose_radius: float, units: str, decimals: int,
                lead_out: float | None = None) -> Toolpath:
    """lead_out (project units) overrides the 0.2 mm pull-off: the area pre-finish (#127=10) pulls off by the area
    retract distance as entered (1 mm; inch 0.0394, which gives 0.0279 at 45 degrees, not 0.0278)."""
    side = 1 if op.external else -1
    tip = translate(tip_path(path, nose_radius, op.external), op.offset_x, op.offset_z, op.external)
    lead_in = leg45(mm(LEAD_IN_MM, units), decimals)
    lead_out = leg45(mm(LEAD_OUT_MM, units) if lead_out is None else lead_out, decimals)
    first, last = tip[0].a, tip[-1].b
    moves = [Move(MoveKind.RAPID, first.x + side * 2 * lead_in, first.z + lead_in),
             Move(MoveKind.FEED, first.x, first.z, op.feed)]
    for s in tip:
        if s.is_arc:
            kind = MoveKind.ARC_CCW if s.ccw else MoveKind.ARC_CW
            moves.append(Move(kind, s.b.x, s.b.z, op.feed, centre_x=s.centre.x, centre_z=s.centre.z))
        else:
            moves.append(Move(MoveKind.FEED, s.b.x, s.b.z, op.feed))
    moves.append(Move(MoveKind.FEED, last.x + side * 2 * lead_out, last.z + lead_out, op.feed))
    return Toolpath([Pass(moves)], retract=[Move(MoveKind.RAPID, op.start.x, op.start.z)])
