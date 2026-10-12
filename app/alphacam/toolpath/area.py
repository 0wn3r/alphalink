"""Area clear (#127=6), external, towards the chuck: axial passes at constant X (AREA1 sample).

Rules learnt from AREA1 (contour X0..100, depth of cut 2, nose radius 0.4, retract 1):
  * pass k (1..n) at X = max(Xmax - 2*k*depth, Xmin - 2r); n = passes until that floor is reached;
  * each pass: rapid to (X, start Z), feed along -Z to the contour, feed a 45-degree retract (+retract in X radius
    and Z), rapid back to start Z;
  * the cut ends on the first contour element met that spans X, with spans half-open towards the larger
    diameter (a pass exactly on a shoulder continues to the next face down);
  * after the last pass, rapid to the tool start."""

from __future__ import annotations

import math

from ..model.geometry import Point
from ..model.operations import AreaClear
from .contour import mm
from .toolpath import Move, MoveKind, Pass, Toolpath

RETRACT_MM = 1.0  # RETRACT DIST default


def _end_z(points: list[Point], x: float) -> float:
    xq = max(x, min(p.x for p in points))
    hits = []
    for a, b in zip(points, points[1:]):
        if a.x == b.x:
            continue
        lo, hi = sorted((a, b), key=lambda p: p.x)
        if lo.x <= xq < hi.x:
            hits.append(lo.z + (hi.z - lo.z) * (xq - lo.x) / (hi.x - lo.x))
    if not hits:
        raise ValueError(f"area clear: no contour below X{x:g}")
    return max(hits)


def area_clear(op: AreaClear, points: list[Point], nose_radius: float, units: str, decimals: int) -> Toolpath:
    if op.offset_x or op.offset_z:
        raise NotImplementedError("area clear offsets are not supported yet")
    if op.pre_finish or not op.towards:
        raise NotImplementedError("area clear: only towards the chuck without pre-finish is supported yet")
    if op.depth_of_cut <= 0:
        raise ValueError("depth of cut must be > 0")
    xmax = max(p.x for p in points)
    floor = min(p.x for p in points) - 2 * nose_radius
    step = 2 * op.depth_of_cut
    n = math.ceil(round((xmax - floor) / step, 9))
    retract = round(mm(RETRACT_MM, units), decimals)
    zs = op.start.z
    passes = []
    for k in range(1, n + 1):
        x = round(max(xmax - k * step, floor), 9)
        z = _end_z(points, x)
        passes.append(Pass([
            Move(MoveKind.RAPID, x, zs),
            Move(MoveKind.FEED, x, z, op.feed),
            Move(MoveKind.FEED, x + 2 * retract, z + retract, op.feed),
            Move(MoveKind.RAPID, x + 2 * retract, zs),
        ]))
    return Toolpath(passes, retract=[Move(MoveKind.RAPID, op.start.x, op.start.z)])
