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
    from (external: lo <= X < hi; internal: lo < X <= hi), so a pass exactly on a shoulder goes on to the next face;
  * pockets (external): where the profile dips below a shoulder and comes back up (an undercut), the main passes
    treat it as filled and do not restart their stepping at its floor; after them each pocket gets its own passes
    (DIVE1-3, RE_NOTES Progress 43). From the shoulder's tip-path corner the tool goes down a line no steeper than
    the tool's rear clearance angle (ALPHA-T's collision check; the profile itself when that is shallower), level by
    level (2 x depth of cut from the shoulder down to the floor), and along each level to the far wall."""

from __future__ import annotations

import math

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


def tip_angle_ok(front: float, rear: float, external: bool = True, towards: bool = True, face: bool = False) -> bool:
    """ALPHA-T's tip angle test (ALPHA-T.EXE overlay 10, the routine before message 242; RE_NOTES Progress 42): the
    tool's clearance angles (degrees from +Z, negative for internal tools) against the cut. Tools without angles
    (both 0: grooving, drills, ball-ends) pass, and ALPHA-T then skips its collision check. Face clear only looks at
    the size of the angles; area clear (FRONT or FACE) wants the rear edge on the side the tool moves away from, and
    the front edge steeper than the rear one."""
    if front == 0 and rear == 0:
        return True
    if face:
        if towards:
            return -90 <= rear <= 90 and -90 <= front <= 90
        return abs(rear) >= 90 and abs(front) >= 90
    if external:
        rear_ok = 0 < rear <= 90 if towards else 90 <= rear < 180
        front_ok = 0 < front < 180
    else:
        rear_ok = -90 < rear < 0 if towards else -180 < rear < -90
        front_ok = -180 < front < 0
    if not (rear_ok and front_ok) or front == rear:
        return False
    return abs(front) >= abs(rear) if towards else abs(front) <= abs(rear)


def collision_angle(tool, op) -> float | None:
    """The steepest plunge ALPHA-T's collision check allows this tool in this area clear (degrees from the Z axis),
    or None when it does not check (a tool without angles, or angles that fail the tip angle test: the operator
    then goes on without the check)."""
    if tool is None or (tool.front_angle == 0 and tool.rear_angle == 0):
        return None
    if not tip_angle_ok(tool.front_angle, tool.rear_angle, getattr(op, "external", True), op.towards):
        return None
    return abs(tool.rear_angle)


def _pockets(boundary: list[Seg]) -> list[tuple[int, int, int]]:
    """External pockets as (first, deepest, last) element indices: from the element that leaves the highest level
    so far downwards to the one that climbs back to it (or the end of the contour)."""
    out, top, k = [], -math.inf, 0
    while k < len(boundary):
        s = boundary[k]
        if s.b.x < s.a.x - 1e-9 and s.a.x >= top - 1e-9:
            first, level, deepest = k, s.a.x, k
            while k < len(boundary) and boundary[k].b.x < level - 1e-9:
                if boundary[k].b.x <= boundary[deepest].b.x + 1e-9:  # the last element at the floor
                    deepest = k
                k += 1
            out.append((first, deepest, min(k, len(boundary) - 1)))
            top = level
            k += 1
            continue
        top = max(top, s.a.x, s.b.x)
        k += 1
    return out


def _pocket_passes(boundary, pocket, step, angle, retract, feed, zs, decimals):
    """ALPHA-T's passes for one pocket (DIVE1-3): see the module docstring. Returns (passes, the last line Z)."""
    first, deepest, last = pocket
    corner = boundary[first].a
    floor = min(min(s.a.x, s.b.x) for s in boundary[first:deepest + 1])
    s0 = boundary[first]
    drop = (s0.a.x - s0.b.x) / 2
    run = abs(s0.a.z - s0.b.z)
    slope = math.degrees(math.atan2(drop, run)) if not s0.is_arc else 90.0
    line = slope if angle is None else min(slope, angle)
    tan = math.tan(math.radians(line)) if line < 90 else math.inf

    def on_line(x):
        return corner.z + ((x - corner.x) / 2 / tan if tan != math.inf else 0.0)

    def profile(x):  # where the way down meets level x: the profile, nearest the shoulder
        found = [z_at(s, x) for s in boundary[first:deepest + 1]
                 if min(s.a.x, s.b.x) - 1e-9 <= x <= max(s.a.x, s.b.x) + 1e-9 and s.a.x != s.b.x]
        return max(found) if found else on_line(x)

    def cut(x):
        return round(min(on_line(x), profile(x)) if angle is not None else profile(x), decimals)

    def far(x):  # the far wall at level x
        for s in boundary[deepest + 1:]:
            lo, hi = sorted((s.a.x, s.b.x))
            if lo != hi and lo <= x < hi:
                return round(z_at(s, x), decimals)
        return round(boundary[last].b.z, decimals)

    levels, x = [], corner.x
    while x > floor + 1e-9:
        x = round(max(x - step, floor), 9)
        levels.append(x)
        if len(levels) > MAX_PASSES:
            raise too_many(tr("area clear"))
    passes, prev = [], corner.x
    for n, x in enumerate(levels):
        z = cut(x)
        if n == 0:
            up = prev + 2 * retract
            zu = round(on_line(up), decimals)
            moves = [Move(MoveKind.RAPID, up, zs), Move(MoveKind.RAPID, up, zu), Move(MoveKind.FEED, up, zu, feed)]
        else:
            zp = cut(prev)
            moves = [Move(MoveKind.RAPID, prev + 2 * retract, zp), Move(MoveKind.FEED, prev, zp, feed)]
        ze = far(x)
        moves += [Move(MoveKind.FEED, x, z, feed), Move(MoveKind.FEED, x, ze, feed),
                  Move(MoveKind.FEED, x + 2 * retract, round(ze + retract, decimals), feed),
                  Move(MoveKind.RAPID, x + 2 * retract, z)]
        passes.append(Pass(moves))
        prev = x
    return passes, cut(levels[-1]) if levels else None


def area_clear(op: AreaClear, path: list[Seg], nose_radius: float, units: str, decimals: int,
               angle: float | None = None) -> Toolpath:
    """angle: the steepest plunge into a pocket (collision_angle), None = follow the profile."""
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
    pockets = _pockets(boundary) if op.external else []
    inside = {k for first, _, last in pockets for k in range(first, last + 1)}
    walls = sorted({s.a.x for k, s in enumerate(boundary) if not s.is_arc and s.a.x == s.b.x and k not in inside
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
    retract_moves = [Move(MoveKind.RAPID, op.start.x, op.start.z)]
    last_z = None
    for pocket in pockets:
        more, z = _pocket_passes(boundary, pocket, step, angle, retract, op.feed,
                                 round(op.start.z - nose_radius, decimals), decimals)
        passes += more
        last_z = z if z is not None else last_z
    if last_z is not None:  # out of the last pocket: up past the part, then back to the start
        retract_moves.insert(0, Move(MoveKind.RAPID, round(op.start.x - 2 * nose_radius, decimals), last_z))
    return Toolpath(passes, retract=retract_moves)
