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
  * pockets (external; mirrored in X inside a bore, IDIVE1/2): where the profile dips below a shoulder and comes back up (an undercut), the main passes
    treat it as filled and do not restart their stepping at its floor; after them each pocket gets its own passes
    (DIVE1-3, RE_NOTES Progress 43). From the shoulder's tip-path corner the tool goes down a line no steeper than
    the tool's rear clearance angle (ALPHA-T's collision check; the profile itself when that is shallower), level by
    level (the stepping grid inside the pocket, then its floor), and along each level to the far wall;
  * FOLLOW (FOL3, FOLG): no zones, the follow stops where a pocket starts, and each pocket pass climbs the far wall
    back to where the previous pass on it stopped."""

from __future__ import annotations

import math

from ..i18n import tr

from ..model.operations import AreaClear
from ..model.geometry import Point
from .contour import Seg, limit_dives, mm, points, tip_path, translate, z_at
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


def _on(seg: Seg, p: Point, tol: float = 1e-6) -> bool:
    """Is p on the element (between its ends)? tol: p may be rounded (a pass end on a slope, FOL3)."""
    if abs(p.x - seg.a.x) < 1e-6 and abs(p.z - seg.a.z) < 1e-6 or abs(p.x - seg.b.x) < 1e-6 and abs(p.z - seg.b.z) < 1e-6:
        return True
    if seg.is_arc:
        return False  # targets on arcs are their end points (pass ends land on the arc ends or a straight element)
    (ar, az), (br, bz), (pr, pz) = (seg.a.x / 2, seg.a.z), (seg.b.x / 2, seg.b.z), (p.x / 2, p.z)
    cross = (br - ar) * (pz - az) - (bz - az) * (pr - ar)
    dot = (pr - ar) * (br - ar) + (pz - az) * (bz - az)
    length = math.hypot(br - ar, bz - az)
    return abs(cross) < max(1e-6, tol * length) and -tol * length <= dot <= length * length + tol * length


def _follow(boundary: list[Seg], k: int, end: Point, target: Point, feed: float, decimals: int,
            stops: frozenset = frozenset()) -> list[Move]:
    """FOLLOW (AREA_follow_contour): from where the pass stops, feed along the profile towards the chuck to where
    the previous pass stopped, cleaning the step it left (pass X56 ends at Z-25: up to X60 Z-25, along X60 to
    Z-50). It does not go down into a pocket: it stops where one starts (stops: the pockets' first elements;
    FOL3 X56: along X60 to the corner at Z-21.066)."""
    moves, here = [], end
    tol = 10 ** -decimals
    for j in range(k, len(boundary)):
        s = boundary[j]
        if j in stops and j != k:
            break
        stop = target if _on(s, target, tol) and not (abs(target.x - here.x) < 1e-6 and abs(target.z - here.z) < 1e-6) \
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
    if not tip_angle_ok(tool.front_angle, tool.rear_angle, getattr(op, "external", True),
                        getattr(op, "towards", True)):
        return None
    return abs(tool.rear_angle)


def _pockets(boundary: list[Seg], side: int = 1) -> list[tuple[int, int, int]]:
    """Pockets as (first, deepest, last) element indices: from the element that leaves the highest level so far
    (external; the lowest for internal work, side -1) away from the tool to the one that comes back to it (or the
    end of the contour)."""
    out, top, k = [], -math.inf, 0
    while k < len(boundary):
        s = boundary[k]
        a, b = side * s.a.x, side * s.b.x
        if b < a - 1e-9 and a >= top - 1e-9:
            first, level, deepest = k, a, k
            while k < len(boundary) and side * boundary[k].b.x < level - 1e-9:
                if side * boundary[k].b.x <= side * boundary[deepest].b.x + 1e-9:  # the last element at the floor
                    deepest = k
                k += 1
            out.append((first, deepest, min(k, len(boundary) - 1)))
            top = level
            k += 1
            continue
        top = max(top, a, b)
        k += 1
    return out


def _floor(boundary, pocket, side) -> float:
    xs = [x for s in boundary[pocket[0]:pocket[1] + 1] for x in (s.a.x, s.b.x)]
    return min(xs) if side > 0 else max(xs)


def _pocket_levels(boundary, pocket, side, grid, follow=False) -> list[float]:
    """A pocket's levels: the stepping grid's diameters inside it (non-FOLLOW: restarted at the pocket's shoulder,
    DIVE1 36, 32; FOLLOW: the main passes' grid, FOLG 28 in a groove from 30), then its floor (DIVE1 30; RADG: the
    point where the rear-angle line meets the far radius, an empty pass); with FOLLOW only when that is a Z-parallel
    line (not where the line meets a far wall, FOLG). None inside: no passes (RG2)."""
    first, deepest, _ = pocket
    top, floor = boundary[first].a.x, _floor(boundary, pocket, side)
    levels = [x for x in grid if side * floor < side * x < side * top - 1e-9]
    flat = any(not s.is_arc and abs(s.a.x - s.b.x) < 1e-9 and abs(s.a.x - floor) < 1e-9
               for s in boundary[first:deepest + 2])
    if levels and (flat or not follow) and abs(levels[-1] - floor) > 1e-9:
        levels.append(floor)
    return levels


def _pocket_passes(boundary, pocket, levels, angle, retract, feed, z0, decimals, side=1, here_x=None,
                   follow_from=None):
    """ALPHA-T's passes for one pocket (DIVE1-3, IDIVE1/2, FOL3, FOLG): see the module docstring. side -1 =
    internal (mirrored in X). here_x: where the tool is (X) before the first pass, z0 the Z of the first rapid.
    follow_from: FOLLOW, where the last pass on the far wall stopped (each pass climbs the far wall back to it).
    Returns (passes, the last line Z)."""
    first, deepest, last = pocket
    corner = boundary[first].a
    s0 = boundary[first]
    drop = side * (s0.a.x - s0.b.x) / 2
    run = abs(s0.a.z - s0.b.z)
    slope = math.degrees(math.atan2(drop, run)) if not s0.is_arc else 90.0
    line = slope if angle is None else min(slope, angle)
    tan = math.tan(math.radians(line)) if line < 90 else math.inf

    def on_line(x):
        return corner.z + (side * (x - corner.x) / 2 / tan if tan != math.inf else 0.0)

    def profile(x):  # where the way down meets level x: the profile, nearest the shoulder
        found = [z_at(s, x) for s in boundary[first:deepest + 1]
                 if min(s.a.x, s.b.x) - 1e-9 <= x <= max(s.a.x, s.b.x) + 1e-9 and s.a.x != s.b.x]
        return max(found) if found else on_line(x)

    def cut(x):
        return round(min(on_line(x), profile(x)) if angle is not None else profile(x), decimals)

    def far(x):  # the far wall at level x
        for s in boundary[deepest + 1:]:
            lo, hi = sorted((s.a.x, s.b.x))
            if lo != hi and ((lo <= x < hi) if side > 0 else (lo < x <= hi)):
                return round(z_at(s, x), decimals)
        return round(boundary[last].b.z, decimals)

    passes, prev, back, last_z = [], corner.x, None, None
    target = follow_from
    for n, x in enumerate(levels):
        z = cut(x)
        if n and far(x) >= z - 10 ** -decimals:  # nothing left at this level (RADG's floor): an empty pass
            passes.append(Pass([]))
            continue
        if n == 0:
            up = prev + side * 2 * retract
            moves = [] if here_x is not None and abs(here_x - up) < 1e-9 else [Move(MoveKind.RAPID, up, z0)]
            if follow_from is not None:  # FOL3: in level with the corner, then onto it
                zc = round(corner.z, decimals)
                moves += [Move(MoveKind.RAPID, up, zc), Move(MoveKind.FEED, prev, zc, feed),
                          Move(MoveKind.FEED, prev, zc, feed)]
            else:
                zu = round(on_line(up), decimals)
                moves += [Move(MoveKind.RAPID, up, zu), Move(MoveKind.FEED, up, zu, feed)]
        else:
            zp = cut(prev)
            moves = [Move(MoveKind.RAPID, back, zp), Move(MoveKind.FEED, prev, zp, feed)]
        ze = far(x)
        moves += [Move(MoveKind.FEED, x, z, feed), Move(MoveKind.FEED, x, ze, feed)]
        ex, ez = x, ze
        if target is not None and (abs(target.x - x) > 1e-9 or abs(target.z - ze) > 1e-9):
            moves.append(Move(MoveKind.FEED, target.x, target.z, feed))  # up the far wall
            ex, ez = target.x, target.z
        if follow_from is not None:
            target = Point(x, ze)
        back = ex + side * 2 * retract
        moves += [Move(MoveKind.FEED, back, round(ez + retract, decimals), feed), Move(MoveKind.RAPID, back, z)]
        passes.append(Pass(moves))
        prev, last_z = x, z
    return passes, last_z


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
    # ALPHA-T's collision check first: the passes see walls too steep for the tool at the rear angle (RG1, IDIVE2)
    boundary = limit_dives(boundary, angle, 1 if op.external else -1)
    bx = [p.x for p in points(boundary)]
    step = 2 * op.depth_of_cut
    side = 1 if op.external else -1
    pockets = _pockets(boundary, side)
    inside = {k for first, _, last in pockets for k in range(first, last + 1)}
    if op.external:
        start, limit = max(bx), min(p.x for p in points(path)) - 2 * nose_radius
    else:  # up to the widest diameter outside the pockets (IDIVE1: 50, not the undercut's 60)
        start = min(bx)
        limit = max(x for k, s in enumerate(boundary) if k not in inside for x in (s.a.x, s.b.x))
    first_x = start
    # Zones: every diameter of a Z-parallel boundary line between start and limit is a pass of its own, and the
    # stepping restarts from it (inch samples: ... 2.426, 2.4, 2.2426 ...; in the metric samples the steps happened
    # to land on the shoulders, so this was invisible there).
    # (not with FOLLOW: each pass cleans the step above it anyway, FOLG has no pass at the X50 shoulder)
    walls = [] if op.follow else sorted({s.a.x for k, s in enumerate(boundary) if not s.is_arc and s.a.x == s.b.x
                                         and k not in inside and min(start, limit) < s.a.x < max(start, limit)},
                                        reverse=op.external)
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
    stops = frozenset(first for first, _, _ in pockets)
    hits = []  # (element, X, Z) where each main pass met the profile
    for x in xs:
        k, z = _end_hit(boundary, x, op.external)
        z = round(z, decimals)
        hits.append((k, x, z))
        moves = [Move(MoveKind.RAPID, x, zs), Move(MoveKind.FEED, x, z, op.feed)]
        ex, ez = x, z
        if op.follow:
            if prev is None:  # the first pass goes back up to where the stepping started (the top of the profile)
                prev = max((p for p in points(boundary) if abs(p.x - first_x) < 1e-9), key=lambda p: p.z)
            path = _follow(boundary, k, Point(x, z), prev, op.feed, decimals, stops)
            moves += path
            if path:
                ex, ez = path[-1].x, path[-1].z
            prev = Point(x, z)
        xr = ex + side * 2 * retract
        moves += [Move(MoveKind.FEED, xr, ez + retract, op.feed), Move(MoveKind.RAPID, xr, zs)]
        passes.append(Pass(moves))
    retract_moves = [Move(MoveKind.RAPID, op.start.x, op.start.z)]
    last_z = None
    z0 = round(op.start.z - nose_radius, decimals)  # the first way in starts at the start Z less r (DIVE1: Z4.6)
    for pocket in pockets:
        if op.follow:  # the main passes' grid
            grid, x = [], first_x
            while side * x > side * _floor(boundary, pocket, side) and len(grid) <= MAX_PASSES:
                x = round(x - side * step, 9)
                grid.append(x)
        else:  # restarted at the pocket's shoulder
            grid, x = [], boundary[pocket[0]].a.x
            while side * x > side * _floor(boundary, pocket, side) and len(grid) <= MAX_PASSES:
                x = round(x - side * step, 9)
                grid.append(x)
        levels = _pocket_levels(boundary, pocket, side, grid, op.follow)
        if not levels:
            continue
        follow_from = None
        if op.follow:
            on_far = [Point(x, z) for k, x, z in hits if pocket[1] < k <= pocket[2]]
            follow_from = on_far[-1] if on_far else None
        moved = [q for q in passes if q.moves]
        here = moved[-1].moves[-1].x if moved else op.start.x
        more, z = _pocket_passes(boundary, pocket, levels, angle, retract, op.feed, z0, decimals, side, here,
                                 follow_from)
        passes += more
        moved = [q for q in more if q.moves]
        if moved:  # the next pocket's first rapid starts where this one left the tool
            z0 = moved[-1].moves[-1].z
        last_z = z if z is not None else last_z
    if last_z is not None:  # out of the last pocket: up past the part, then back to the start
        retract_moves.insert(0, Move(MoveKind.RAPID, round(op.start.x - side * 2 * nose_radius, decimals), last_z))
    return Toolpath(passes, retract=retract_moves)
