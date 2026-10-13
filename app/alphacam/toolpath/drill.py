"""Drilling (#127=18) on the axis, pecks written out as G1 moves (DRILL_DWELL sample).

From the start Z, each peck goes `peck` deeper than the last one (not than the retract), then backs off by RETRACT
DIST (default 2; DRILL_retract1: 1); the final peck stops at the bottom, dwells, backs off the same, and the tool
rapids back to the start.

FULL RET (DRILL_fullret, RETRACT DIST left at 2): after each peck it backs off half the retract distance (1), rapids
out to the start Z, then rapids back in to a quarter of it (0.5) above the last depth before the next peck. Only
the default distance is sampled with FULL RET, so the half / quarter split is inferred from that one sample."""

from __future__ import annotations

from ..i18n import tr
from ..model.operations import Drill
from .contour import mm
from .toolpath import Move, MoveKind, Pass, Toolpath

PECK_RETRACT_MM = 2.0  # RETRACT DIST default


def drill(op: Drill, units: str, decimals: int) -> Toolpath:
    x, z, bottom = op.start.x, op.start.z, op.depth
    if bottom >= z:
        raise ValueError(tr("drill depth must be below the start Z"))
    retract = mm(PECK_RETRACT_MM, units) if op.retract is None else op.retract
    if retract <= 0:
        raise ValueError(tr("retract distance must be > 0"))
    back = round(retract / 2 if op.full_retract else retract, decimals)
    clear = round(retract / 4, decimals)
    moves = []
    while True:
        z = round(max(z - op.peck, bottom), 9) if op.peck > 0 else bottom
        moves.append(Move(MoveKind.FEED, x, z, op.feed))
        if z == bottom:
            break
        moves.append(Move(MoveKind.FEED, x, z + back, op.feed))
        if op.full_retract:
            moves += [Move(MoveKind.RAPID, x, op.start.z), Move(MoveKind.RAPID, x, round(z + clear, 9))]
    if op.dwell_revs:
        moves.append(Move(MoveKind.DWELL, x, z, dwell_revs=op.dwell_revs))
    moves.append(Move(MoveKind.FEED, x, bottom + back, op.feed))
    return Toolpath([Pass(moves)], retract=[Move(MoveKind.RAPID, x, op.start.z)])
