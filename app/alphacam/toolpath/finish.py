"""Finish pass (#127=14): one pass along the programmed contour (C5: not nose-compensated)."""

from __future__ import annotations

from ..model.geometry import Point
from ..model.operations import FinishPass
from .contour import leg45, mm
from .toolpath import Move, MoveKind, Pass, Toolpath

LEAD_IN_MM = 2.0  # 45-degree approach, +X +Z of the first point (MET2; also V1.81)
LEAD_OUT_MM = 0.2  # 45-degree pull-off, +X +Z of the last point (ALPHA-T MET2; V1.81 uses 2)


def finish_pass(op: FinishPass, points: list[Point], units: str, decimals: int) -> Toolpath:
    if op.offset:
        raise NotImplementedError("finish offset is not supported yet")
    lead_in = leg45(mm(LEAD_IN_MM, units), decimals)
    lead_out = leg45(mm(LEAD_OUT_MM, units), decimals)
    first, last = points[0], points[-1]
    moves = [Move(MoveKind.RAPID, first.x + 2 * lead_in, first.z + lead_in)]
    moves += [Move(MoveKind.FEED, p.x, p.z, op.feed) for p in points]
    moves.append(Move(MoveKind.FEED, last.x + 2 * lead_out, last.z + lead_out, op.feed))
    return Toolpath([Pass(moves)], retract=[Move(MoveKind.RAPID, op.start.x, op.start.z)])
