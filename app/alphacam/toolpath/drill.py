"""Drilling (#127=18) on the axis, pecks written out as G1 moves (DRILL_DWELL sample).

From the start Z, each peck goes `peck` deeper than the last one (not than the retract), then backs off 2 mm;
the final peck stops at the bottom, dwells, backs off 2 mm, and the tool rapids back to the start."""

from __future__ import annotations

from ..model.operations import Drill
from .contour import mm
from .toolpath import Move, MoveKind, Pass, Toolpath

PECK_RETRACT_MM = 2.0


def drill(op: Drill, units: str, decimals: int) -> Toolpath:
    x, z, bottom = op.start.x, op.start.z, op.depth
    if bottom >= z:
        raise ValueError("drill depth must be below the start Z")
    back = round(mm(PECK_RETRACT_MM, units), decimals)
    moves = []
    while True:
        z = round(max(z - op.peck, bottom), 9) if op.peck > 0 else bottom
        moves.append(Move(MoveKind.FEED, x, z, op.feed))
        if z == bottom:
            break
        moves.append(Move(MoveKind.FEED, x, z + back, op.feed))
    if op.dwell_revs:
        moves.append(Move(MoveKind.DWELL, x, z, dwell_revs=op.dwell_revs))
    moves.append(Move(MoveKind.FEED, x, bottom + back, op.feed))
    return Toolpath([Pass(moves)], retract=[Move(MoveKind.RAPID, x, op.start.z)])
