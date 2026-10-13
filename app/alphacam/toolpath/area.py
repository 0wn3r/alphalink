"""Area clear (#127=6, internal 7), towards the chuck: axial passes at constant X.

Rules from the samples (AREA1, ARC_area, SLOPE_area, OFFSET_area, INT_area, TOOLCHG):
  * the boundary is the imaginary tool-tip path (contour.tip_path), translated by the offsets;
  * external: passes step down by 2*depth from the boundary's Xmax to Xmin - 2r (Xmin from the drawing);
    internal: up from the boundary's Xmin to its Xmax. Each Z-parallel boundary diameter in between gets a pass
    and restarts the stepping (zones, seen in the inch samples);
  * each pass: rapid to (X, start Z), feed along -Z to the boundary, feed a 45-degree retract (RETRACT DIST, default
    1 mm, in X radius, outwards from the cut: -X internal, and in +Z), rapid back to start Z; after the last, rapid
    to start; FOLLOW (AREA_follow_contour) first feeds along the profile up to where the previous pass stopped;
  * pre-finish (op.pre_finish) is posted as a separate finish block, see post.alphat_prg;
  * the cut ends on the first boundary element met that spans X, with spans half-open on the side the passes come
    from (external: lo <= X < hi; internal: lo < X <= hi), so a pass exactly on a shoulder goes on to the next face."""

from __future__ import annotations

from ..i18n import tr

from ..model.operations import AreaClear
from ..model.geometry import Point
from .contour import Seg, mm, points, tip_path, translate, z_at
from .toolpath import MAX_PASSES, Move, MoveKind, Pass, Toolpath, too_many

RETRACT_MM = 1.0  # RETRACT DIST default


def retract_mm(op, units: str, default_mm: float = RETRACT_MM) -> float:
    """An operation's RETRACT DIST in mm (it is entered in drawing units; None = ALPHA-T's default)."""
    if getattr(op, "retract", None) is None:
        return default_mm
    return op.retract * 25.4 if units == "inch" else op.retract


def _end_hit(boundary: list[Seg], x: float, external: bool) -> tuple[int, float]:
    """(index of the boundary element the pass at X ends on, its Z)."""
    pts = points(boundary)
    xq = max(x, min(p.x for p in pts)) if external else min(x, max(p.x for p in pts))
    best = None
    for k, s in enumerate(boundary):
        lo, hi = sorted((s.a.x, s.b.x))
        if lo == hi:
            continue
        if (lo <= xq < hi) if external else (lo < xq <= hi):
            z = z_at(s, xq)
            if best is None or z > best[1]:
                best = (k, z)
    if best is None:
        raise ValueError(tr("area clear: no contour below X{x}", x=f"{x:g}"))
    return best


def _on(seg: Seg, p: Point) -> bool:
    """Is p on the element (between its ends)?"""
    if abs(p.x - seg.a.x) < 1e-6 and abs(p.z - seg.a.z) < 1e-6 or abs(p.x - seg.b.x) < 1e-6 and abs(p.z - seg.b.z) < 1e-6:
        return True
    if seg.is_arc:
        return False  # targets on arcs are their end points (pass ends land on the arc ends or a straight element)
    (ar, az), (br, bz), (pr, pz) = (seg.a.x / 2, seg.a.z), (seg.b.x / 2, seg.b.z), (p.x / 2, p.z)
    cross = (br - ar) * (pz - az) - (bz - az) * (pr - ar)
    dot = (pr - ar) * (br - ar) + (pz - az) * (bz - az)
    return abs(cross) < 1e-6 and 0 <= dot <= (br - ar) ** 2 + (bz - az) ** 2 + 1e-9


def _follow(boundary: list[Seg], k: int, end: Point, target: Point, feed: float, decimals: int) -> list[Move]:
    """FOLLOW (AREA_follow_contour): from where the pass stops, feed along the profile towards the chuck to where
    the previous pass stopped, cleaning the step it left (pass X56 ends at Z-25: up to X60 Z-25, along X60 to
    Z-50)."""
    moves, here = [], end
    for s in boundary[k:]:
        stop = target if _on(s, target) and not (abs(target.x - here.x) < 1e-6 and abs(target.z - here.z) < 1e-6) \
            else s.b
        q = Point(round(stop.x, decimals), round(stop.z, decimals))
        if s.is_arc:
            kind = MoveKind.ARC_CCW if s.ccw else MoveKind.ARC_CW
            moves.append(Move(kind, q.x, q.z, feed, centre_x=s.centre.x, centre_z=s.centre.z))
        elif (q.x, q.z) != (round(here.x, decimals), round(here.z, decimals)):
            moves.append(Move(MoveKind.FEED, q.x, q.z, feed))
        here = q
        if stop is target:
            break
    return moves


def area_clear(op: AreaClear, path: list[Seg], nose_radius: float, units: str, decimals: int) -> Toolpath:
    if not op.towards:
        # AWAY_area_tool5 and its rerun: ALPHA-T's passes end 1.6 mm past the dia-100 shoulder (its preview does not)
        raise NotImplementedError(tr("area clear AWAY: ALPHA-T itself cuts 1.6 mm past the shoulder for it, so the app "
                                  "does not offer it; use TOWARDS"))
    if op.depth_of_cut <= 0:
        raise ValueError(tr("depth of cut must be > 0"))
    boundary = translate(tip_path(path, nose_radius, op.external), op.offset_x, op.offset_z, op.external)
    bx = [p.x for p in points(boundary)]
    step = 2 * op.depth_of_cut
    if op.external:
        start, limit = max(bx), min(p.x for p in points(path)) - 2 * nose_radius
    else:
        start, limit = min(bx), max(bx)
    first_x = start
    # Zones: every diameter of a Z-parallel boundary line between start and limit is a pass of its own, and the
    # stepping restarts from it (inch samples: ... 2.426, 2.4, 2.2426 ...; in the metric samples the steps happened
    # to land on the shoulders, so this was invisible there).
    walls = sorted({s.a.x for s in boundary if not s.is_arc and s.a.x == s.b.x
                    and min(start, limit) < s.a.x < max(start, limit)}, reverse=op.external)
    xs = []
    for stop in walls + [limit]:
        stop = round(stop, 9)  # x is rounded below; an unrounded stop would never be reached
        x = start
        while True:
            x = round(max(x - step, stop) if op.external else min(x + step, stop), 9)
            xs.append(x)
            if len(xs) > MAX_PASSES:
                raise too_many(tr("area clear"))
            if x == stop:
                break
        start = stop
    side = 1 if op.external else -1
    retract = round(mm(retract_mm(op, units), units), decimals)
    zs = op.start.z
    passes = []
    prev = None  # where the previous pass stopped (FOLLOW goes back up to it)
    for x in xs:
        k, z = _end_hit(boundary, x, op.external)
        z = round(z, decimals)
        moves = [Move(MoveKind.RAPID, x, zs), Move(MoveKind.FEED, x, z, op.feed)]
        ex, ez = x, z
        if op.follow:
            if prev is None:  # the first pass goes back up to where the stepping started (the top of the profile)
                prev = max((p for p in points(boundary) if abs(p.x - first_x) < 1e-9), key=lambda p: p.z)
            path = _follow(boundary, k, Point(x, z), prev, op.feed, decimals)
            moves += path
            if path:
                ex, ez = path[-1].x, path[-1].z
            prev = Point(x, z)
        xr = ex + side * 2 * retract
        moves += [Move(MoveKind.FEED, xr, ez + retract, op.feed), Move(MoveKind.RAPID, xr, zs)]
        passes.append(Pass(moves))
    return Toolpath(passes, retract=[Move(MoveKind.RAPID, op.start.x, op.start.z)])
