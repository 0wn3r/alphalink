"""Area clear (#127=6, internal 7), towards the chuck: axial passes at constant X.

Rules from the samples (AREA1, ARC_area, SLOPE_area, OFFSET_area, INT_area, TOOLCHG):
  * the boundary is the imaginary tool-tip path (contour.tip_path), translated by the offsets;
  * external: passes step down by 2*depth from the boundary's Xmax to Xmin - 2r (Xmin from the drawing);
    internal: up from the boundary's Xmin to its Xmax. Each Z-parallel boundary diameter in between gets a pass
    and restarts the stepping (zones, seen in the inch samples);
  * each pass: rapid to (X, start Z), feed along -Z to the boundary, feed a 45-degree retract (retract distance in
    X radius, outwards from the cut: -X internal, and in +Z), rapid back to start Z; after the last, rapid to start;
  * pre-finish (op.pre_finish) is posted as a separate finish block, see post.alphat_prg;
  * the cut ends on the first boundary element met that spans X, with spans half-open on the side the passes come
    from (external: lo <= X < hi; internal: lo < X <= hi), so a pass exactly on a shoulder goes on to the next face."""

from __future__ import annotations

import math

from ..model.operations import AreaClear
from .contour import Seg, mm, points, tip_path, translate, z_at
from .toolpath import Move, MoveKind, Pass, Toolpath

RETRACT_MM = 1.0  # RETRACT DIST default


def _end_z(boundary: list[Seg], x: float, external: bool) -> float:
    pts = points(boundary)
    xq = max(x, min(p.x for p in pts)) if external else min(x, max(p.x for p in pts))
    hits = []
    for s in boundary:
        lo, hi = sorted((s.a.x, s.b.x))
        if lo == hi:
            continue
        if (lo <= xq < hi) if external else (lo < xq <= hi):
            hits.append(z_at(s, xq))
    if not hits:
        raise ValueError(f"area clear: no contour below X{x:g}")
    return max(hits)


def area_clear(op: AreaClear, path: list[Seg], nose_radius: float, units: str, decimals: int) -> Toolpath:
    if not op.towards:
        # AWAY_area_tool5 and its rerun: ALPHA-T's passes end 1.6 mm past the dia-100 shoulder (its preview does not)
        raise NotImplementedError("area clear AWAY: ALPHA-T itself cuts 1.6 mm past the shoulder for it, so the app "
                                  "does not offer it; use TOWARDS")
    if op.depth_of_cut <= 0:
        raise ValueError("depth of cut must be > 0")
    boundary = translate(tip_path(path, nose_radius, op.external), op.offset_x, op.offset_z, op.external)
    bx = [p.x for p in points(boundary)]
    step = 2 * op.depth_of_cut
    if op.external:
        start, limit = max(bx), min(p.x for p in points(path)) - 2 * nose_radius
    else:
        start, limit = min(bx), max(bx)
    # Zones: every diameter of a Z-parallel boundary line between start and limit is a pass of its own, and the
    # stepping restarts from it (inch samples: ... 2.426, 2.4, 2.2426 ...; in the metric samples the steps happened
    # to land on the shoulders, so this was invisible there).
    walls = sorted({s.a.x for s in boundary if not s.is_arc and s.a.x == s.b.x
                    and min(start, limit) < s.a.x < max(start, limit)}, reverse=op.external)
    xs = []
    for stop in walls + [limit]:
        x = start
        while True:
            x = round(max(x - step, stop) if op.external else min(x + step, stop), 9)
            xs.append(x)
            if x == stop:
                break
        start = stop
    side = 1 if op.external else -1
    retract = round(mm(RETRACT_MM, units), decimals)
    zs = op.start.z
    passes = []
    for x in xs:
        z = round(_end_z(boundary, x, op.external), decimals)
        xr = x + side * 2 * retract
        passes.append(Pass([
            Move(MoveKind.RAPID, x, zs),
            Move(MoveKind.FEED, x, z, op.feed),
            Move(MoveKind.FEED, xr, z + retract, op.feed),
            Move(MoveKind.RAPID, xr, zs),
        ]))
    return Toolpath(passes, retract=[Move(MoveKind.RAPID, op.start.x, op.start.z)])
