"""Drawing operations on a project's geometry (pure Python; the canvas tools call these through the undo stack).

Coordinates are X diameter and Z. Geometry maths is done in (radius, Z). TOUCH.md 5.1: rough placement is snapped
(end points, the axis, a grid) and direction-locked (Z, X, 45 degrees); exact values come from typed entry."""

from __future__ import annotations

from .i18n import tr
import math

from .model.geometry import Arc, Line, Point, Text

SNAP_LOCK_DEG = 8.0


def next_id(project) -> str:
    used = {g.id for g in project.geometry}
    n = len(project.geometry)
    while f"g{n}" in used:
        n += 1
    return f"g{n}"


def endpoints(project) -> list[Point]:
    pts = []
    for g in project.geometry:
        for p in (g.a, g.b):
            if p not in pts:
                pts.append(p)
    return pts


def snap(project, p: Point, tol: float, grid: float = 0.0) -> tuple[Point, str]:
    """Snap a rough point (tolerance in mm, from the input's screen radius): end points, then the axis, then grid."""
    best, best_d = None, tol
    for q in endpoints(project):
        d = p.distance(q)
        if d <= best_d:
            best, best_d = q, d
    if best is not None:
        return best, "end"
    if abs(p.x) / 2 <= tol:
        p = Point(0.0, p.z)
        kind = "axis"
    else:
        kind = "free"
    if grid > 0:
        gx = 0.0 if kind == "axis" else round(p.x / 2 / grid) * grid * 2
        p, kind = Point(round(gx, 6), round(round(p.z / grid) * grid, 6)), kind if kind == "axis" else "grid"
    return p, kind


def lock_direction(prev: Point, p: Point, deg: float = SNAP_LOCK_DEG) -> Point:
    """Lock the segment prev -> p to Z, X or 45 degrees when it is within `deg` of them (radius/Z space)."""
    dr, dz = (p.x - prev.x) / 2, p.z - prev.z
    length = math.hypot(dr, dz)
    if length == 0:
        return p
    ang = math.degrees(math.atan2(dr, dz))
    for target in (0, 45, 90, 135, 180, -45, -90, -135, -180):
        if abs(ang - target) <= deg:
            a = math.radians(target)
            if target in (0, 180, -180):  # along Z: keep the Z length, X fixed
                return Point(prev.x, p.z)
            if target in (90, -90):
                return Point(p.x, prev.z)
            along = (dr * math.sin(a) + dz * math.cos(a))
            return Point(round(prev.x + 2 * along * math.sin(a), 6), round(prev.z + along * math.cos(a), 6))
    return p


def add_line(project, a: Point, b: Point) -> str:
    if a == b:
        raise ValueError(tr("a line needs two different points"))
    gid = next_id(project)
    project.geometry.append(Line(a, b, id=gid))
    return gid


def delete_element(project, gid: str) -> None:
    before = len(project.geometry)
    project.geometry[:] = [g for g in project.geometry if g.id != gid]
    if len(project.geometry) == before:
        raise KeyError(tr("no element {gid}", gid=gid))
    for op in project.operations:  # drop it from contours that used it
        if hasattr(op, "contour"):
            op.contour = [c for c in op.contour if c != gid]


def _refit_arc(arc: Arc, a: Point, b: Point, radius: float) -> Point:
    """Centre for the arc a -> b with this radius, on the same side of the chord as the arc's centre was (so the
    direction and the short/long sweep stay as drawn)."""
    (ar, az), (br, bz) = _rz(a), _rz(b)
    dr, dz = br - ar, bz - az
    d = math.hypot(dr, dz)
    if d < 1e-9 or d > 2 * radius + 1e-9:
        raise ValueError(tr("an arc of radius {r} cannot join X{ax} Z{az} and X{bx} Z{bz}", r=f"{radius:g}",
                            ax=f"{a.x:g}", az=f"{a.z:g}", bx=f"{b.x:g}", bz=f"{b.z:g}"))
    h = math.sqrt(max(radius * radius - d * d / 4, 0.0))
    mr, mz = (ar + br) / 2, (az + bz) / 2
    nr, nz = -dz / d, dr / d  # unit normal to the chord
    (oar, oaz), (obr, obz), (ocr, ocz) = _rz(arc.a), _rz(arc.b), _rz(arc.centre)
    side = (obr - oar) * (ocz - oaz) - (obz - oaz) * (ocr - oar)
    c1, c2 = (mr + nr * h, mz + nz * h), (mr - nr * h, mz - nz * h)
    s1 = dr * (c1[1] - az) - dz * (c1[0] - ar)
    return _pt(c1 if (s1 >= 0) == (side >= 0) else c2)


def edit_element(project, gid: str, a: Point, b: Point, radius: float | None = None, follow: bool = True) -> None:
    """Give element gid new end points (and, for an arc, a new radius). With follow, the ends of other elements
    that met the moved ends move with them, so the profile stays joined; arcs among them keep their radius."""
    g = next((e for e in project.geometry if e.id == gid), None)
    if g is None:
        raise KeyError(tr("no element {gid}", gid=gid))
    moves = {g.a: a, g.b: b} if g.a != g.b else {g.a: a}
    new_ends = {}
    for e in project.geometry:
        if isinstance(e, Text):
            continue
        if e is g:
            new_ends[e.id] = (a, b)
        elif follow and (e.a in moves or e.b in moves):
            new_ends[e.id] = (moves.get(e.a, e.a), moves.get(e.b, e.b))
    for e in project.geometry:
        if e.id not in new_ends:
            continue
        na, nb = new_ends[e.id]
        if isinstance(e, Line):
            if na.distance(nb) < 1e-9:
                raise ValueError(tr("{gid} would have no length", gid=e.id))
        else:
            r = radius if (e is g and radius) else e.radius()
            e.centre = _refit_arc(e, na, nb, r)
        e.a, e.b = na, nb


def _corner(project, p: Point):
    lines = [g for g in project.geometry if isinstance(g, Line) and p in (g.a, g.b)]
    if len(lines) != 2:
        raise ValueError(tr("X{x} Z{z} is not a corner between two lines", x=f"{p.x:g}", z=f"{p.z:g}"))
    return lines


def _away(line: Line, p: Point) -> Point:
    return line.b if line.a == p else line.a


def _set_end(line: Line, p: Point, new: Point) -> None:
    if line.a == p:
        line.a = new
    else:
        line.b = new


def fillet(project, p: Point, radius: float) -> str:
    """Round the corner at p between two lines with an arc tangent to both (ALPHA-T 'radius/chamfer F')."""
    if radius <= 0:
        raise ValueError(tr("radius must be > 0"))
    l1, l2 = _corner(project, p)
    pr, pz = p.x / 2, p.z
    u1 = _unit(_away(l1, p), p)
    u2 = _unit(_away(l2, p), p)
    cos_t = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
    theta = math.acos(cos_t)
    if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
        raise ValueError(tr("the lines are parallel at this point"))
    d = radius / math.tan(theta / 2)
    if d > min(_len(l1), _len(l2)) + 1e-9:
        raise ValueError(tr("radius {radius} is too large for these lines", radius=f"{radius:g}"))
    t1 = (pr + u1[0] * d, pz + u1[1] * d)
    t2 = (pr + u2[0] * d, pz + u2[1] * d)
    bis = _norm((u1[0] + u2[0], u1[1] + u2[1]))
    h = radius / math.sin(theta / 2)
    c = (pr + bis[0] * h, pz + bis[1] * h)
    # orientation: walking from t1 to t2 around c; G3 (ccw=True) is counter-clockwise with Z right and X up,
    # i.e. in the (z, r) plane
    cross = (t1[1] - c[1]) * (t2[0] - c[0]) - (t1[0] - c[0]) * (t2[1] - c[1])  # (z, r) cross product
    ccw = cross > 0
    T1, T2, C = _pt(t1), _pt(t2), _pt(c)
    first, second = (l1, l2) if _is_before(project, l1, l2, p) else (l2, l1)
    start, end = (T1, T2) if first is l1 else (T2, T1)
    if first is not l1:
        ccw = not ccw
    _set_end(l1, p, T1)
    _set_end(l2, p, T2)
    gid = next_id(project)
    project.geometry.append(Arc(start, end, C, ccw=ccw, id=gid))
    return gid


def chamfer(project, p: Point, size: float, angle: float = 45.0) -> str:
    """Cut the corner at p with a straight chamfer: `size` along the more axial (Z-parallel) of the two lines and
    `angle` degrees between the chamfer and that line (lathe drawing style "2 x 30 deg", angle from the axis). The leg
    on the other line follows from the corner angle (law of sines; 45 deg on a square corner = size on both)."""
    if size <= 0:
        raise ValueError(tr("chamfer size must be > 0"))
    if not 0 < angle < 90:
        raise ValueError(tr("chamfer angle must be between 0 and 90 degrees"))
    l1, l2 = _corner(project, p)
    pr, pz = p.x / 2, p.z
    u1, u2 = _unit(_away(l1, p), p), _unit(_away(l2, p), p)
    # reference line: the one closer to Z-parallel (|dz| component larger); the other gets the computed leg
    ref_first = abs(u1[1]) >= abs(u2[1])
    ua, ub = (u1, u2) if ref_first else (u2, u1)
    corner = math.acos(max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1])))
    theta = math.radians(angle)
    if theta + corner >= math.pi - 1e-9:
        raise ValueError(tr("a {angle} degree chamfer does not fit this corner", angle=f"{angle:g}"))
    other = size * math.sin(theta) / math.sin(theta + corner)
    s1, s2 = (size, other) if ref_first else (other, size)
    for line, s in ((l1, s1), (l2, s2)):
        a, b = line.a, line.b
        if s > math.hypot((a.x - b.x) / 2, a.z - b.z) + 1e-9:
            raise ValueError(tr("chamfer {size} x {angle} deg is too large for these lines", size=f"{size:g}", angle=f"{angle:g}"))
    T1 = _pt((pr + u1[0] * s1, pz + u1[1] * s1))
    T2 = _pt((pr + u2[0] * s2, pz + u2[1] * s2))
    first = l1 if _is_before(project, l1, l2, p) else l2
    _set_end(l1, p, T1)
    _set_end(l2, p, T2)
    a, b = (T1, T2) if first is l1 else (T2, T1)
    gid = next_id(project)
    project.geometry.append(Line(a, b, id=gid))
    return gid


def _is_before(project, l1, l2, p) -> bool:
    """True if l1 ends at p and l2 starts there (drawing order), else decided by element order."""
    if l1.b == p and l2.a == p:
        return True
    if l2.b == p and l1.a == p:
        return False
    return project.geometry.index(l1) < project.geometry.index(l2)


def _unit(q: Point, p: Point) -> tuple[float, float]:
    return _norm((q.x / 2 - p.x / 2, q.z - p.z))


def _norm(v):
    n = math.hypot(*v)
    return v[0] / n, v[1] / n


def _len(line: Line) -> float:
    return line.a.distance(line.b)


def _pt(rz) -> Point:
    return Point(round(2 * rz[0], 6), round(rz[1], 6))


def pick_order(project, picked: list[str]) -> list[str]:
    """Contour picking (TOUCH.md 7.1): keep the tapped elements, ordered as a connected chain from the first."""
    from .toolpath.contour import chain

    by_id = {g.id: g for g in project.geometry}
    elems = [by_id[i] for i in picked if i in by_id]
    if not elems:
        return []
    remaining, order = elems[1:], [elems[0]]
    path = chain([elems[0]])
    while remaining:
        end = path[-1].b
        start = path[0].a
        nxt = next((e for e in remaining if end in (e.a, e.b)), None)
        if nxt is not None:
            order.append(nxt)
        else:
            prv = next((e for e in remaining if start in (e.a, e.b)), None)
            if prv is None:
                raise ValueError(tr("{id} does not connect to the picked contour", id=remaining[0].id))
            order.insert(0, prv)
            nxt = prv
        remaining.remove(nxt)
        path = chain(order)
    return [e.id for e in order]


def add_arc_3p(project, a: Point, m: Point, b: Point) -> str:
    """Arc from a through m to b (radius / Z space). The direction follows the three points: counter-clockwise
    with Z right and X up is G3 (ccw=True)."""
    (az, ar), (mz, mr), (bz, br) = (a.z, a.x / 2), (m.z, m.x / 2), (b.z, b.x / 2)
    d = 2 * (az * (mr - br) + mz * (br - ar) + bz * (ar - mr))
    if abs(d) < 1e-9:
        raise ValueError(tr("the three points are on a line"))
    s_a, s_m, s_b = az * az + ar * ar, mz * mz + mr * mr, bz * bz + br * br
    cz = (s_a * (mr - br) + s_m * (br - ar) + s_b * (ar - mr)) / d
    cr = (s_a * (bz - mz) + s_m * (az - bz) + s_b * (mz - az)) / d
    ccw = d > 0  # orientation of a -> m -> b in the (z, r) plane
    gid = next_id(project)
    project.geometry.append(Arc(a, b, _pt((cr, cz)), ccw=ccw, id=gid))
    return gid


def corner(project, id1: str, id2: str) -> Point:
    """Trim or extend two lines so that they meet: the end of each line nearer the intersection moves onto it."""
    by_id = {g.id: g for g in project.geometry}
    l1, l2 = by_id.get(id1), by_id.get(id2)
    if not isinstance(l1, Line) or not isinstance(l2, Line) or l1 is l2:
        raise ValueError(tr("pick two different lines"))
    (a1r, a1z), (b1r, b1z) = (l1.a.x / 2, l1.a.z), (l1.b.x / 2, l1.b.z)
    (a2r, a2z), (b2r, b2z) = (l2.a.x / 2, l2.a.z), (l2.b.x / 2, l2.b.z)
    d1r, d1z, d2r, d2z = b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z
    den = d1r * d2z - d1z * d2r
    if abs(den) < 1e-12:
        raise ValueError(tr("the lines are parallel"))
    t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den
    p = _pt((a1r + t * d1r, a1z + t * d1z))
    for line in (l1, l2):
        if line.a.distance(p) < line.b.distance(p):
            line.a = p
        else:
            line.b = p
        if line.a == line.b:
            raise ValueError(tr("a line would shrink to a point"))
    return p


# --- ALPHA-T cursor commands (hit codes D, Y, M, N, P, I, L, A, T, U, Q) ---------------------------------------


def polar(origin: Point, length: float, angle: float) -> Point:
    """The point `length` away from origin at `angle` degrees, as ALPHA-T's D (draw line D,A) and Y (L,A move):
    0 degrees is 9 o'clock (towards -Z), 90 is +X; the length is true (X is a diameter, so X moves 2 x radial)."""
    a = math.radians(angle)
    return Point(round(origin.x + 2 * length * math.sin(a), 6), round(origin.z - length * math.cos(a), 6))


def measure_xz(a: Point, b: Point) -> tuple[float, float]:
    """ALPHA-T M: X (diameter) and Z distance from a to b."""
    return round(b.x - a.x, 6), round(b.z - a.z, 6)


def measure_la(a: Point, b: Point) -> tuple[float, float]:
    """ALPHA-T N: length and angle from a to b, in the convention of polar()."""
    dr, dz = (b.x - a.x) / 2, b.z - a.z
    return round(math.hypot(dr, dz), 6), round(math.degrees(math.atan2(dr, -dz)) % 360, 6)


def key_points(project) -> list[Point]:
    """Points ALPHA-T's Find Point (P) locks onto: line and arc ends and arc centres."""
    pts = endpoints(project)
    for g in project.geometry:
        if isinstance(g, Arc) and g.centre not in pts:
            pts.append(g.centre)
    return pts


def _nearest(points, p: Point) -> Point | None:
    return min(points, key=p.distance, default=None)


def nearest_point(project, p: Point) -> Point | None:
    return _nearest(key_points(project), p)


def _rz(p: Point) -> tuple[float, float]:
    return p.x / 2, p.z


def _on_arc(arc: Arc, q: Point) -> bool:
    """Is q (on the arc's circle) within the arc's sweep?"""
    cr, cz = _rz(arc.centre)

    def ang(pt):
        r, z = _rz(pt)
        return math.atan2(r - cr, z - cz)

    ta, tb, tq = ang(arc.a), ang(arc.b), ang(q)
    if not arc.ccw:
        ta, tb, tq = -ta, -tb, -tq
    sweep = (tb - ta) % (2 * math.pi) or 2 * math.pi
    return (tq - ta) % (2 * math.pi) <= sweep + 1e-9


def distance_to(g, p: Point) -> float:
    """Shortest distance from p to a line or arc element (radius / Z space)."""
    pr, pz = _rz(p)
    if isinstance(g, Arc):
        cr, cz = _rz(g.centre)
        d = math.hypot(pr - cr, pz - cz)
        if d > 1e-12:
            q = _pt((cr + (pr - cr) / d * g.radius(), cz + (pz - cz) / d * g.radius()))
            if _on_arc(g, q):
                return abs(d - g.radius())
        return min(p.distance(g.a), p.distance(g.b))
    (ar, az), (br, bz) = _rz(g.a), _rz(g.b)
    dr, dz = br - ar, bz - az
    L2 = dr * dr + dz * dz
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((pr - ar) * dr + (pz - az) * dz) / L2))
    return math.hypot(pr - (ar + t * dr), pz - (az + t * dz))


def nearest_element(project, p: Point, kind: type | None = None) -> str | None:
    """Id of the line (kind=Line) or arc (kind=Arc) nearest p: ALPHA-T's Find Nearest Line / Arc (L, A)."""
    cands = [g for g in project.geometry if kind is None or isinstance(g, kind)]
    best = min(cands, key=lambda g: distance_to(g, p), default=None)
    return best.id if best is not None else None


def _circle(g):
    cr, cz = _rz(g.centre)
    return cr, cz, g.radius()


def _line_line(l1: Line, l2: Line) -> list[tuple[float, float]]:
    (a1r, a1z), (b1r, b1z) = _rz(l1.a), _rz(l1.b)
    (a2r, a2z), (b2r, b2z) = _rz(l2.a), _rz(l2.b)
    d1r, d1z, d2r, d2z = b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z
    den = d1r * d2z - d1z * d2r
    if abs(den) < 1e-12:
        return []
    t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den
    u = ((a2r - a1r) * d1z - (a2z - a1z) * d1r) / den
    eps = 1e-9
    return [(a1r + t * d1r, a1z + t * d1z)] if -eps <= t <= 1 + eps and -eps <= u <= 1 + eps else []


def _line_circle(line: Line, cr, cz, rad) -> list[tuple[float, float]]:
    (ar, az), (br, bz) = _rz(line.a), _rz(line.b)
    dr, dz = br - ar, bz - az
    fr, fz = ar - cr, az - cz
    a = dr * dr + dz * dz
    b = 2 * (fr * dr + fz * dz)
    c = fr * fr + fz * fz - rad * rad
    disc = b * b - 4 * a * c
    if a == 0 or disc < 0:
        return []
    out = []
    for t in {(-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a)}:
        if -1e-9 <= t <= 1 + 1e-9:
            out.append((ar + t * dr, az + t * dz))
    return out


def _circle_circle(c1, c2) -> list[tuple[float, float]]:
    (r1, z1, a), (r2, z2, b) = c1, c2
    d = math.hypot(r2 - r1, z2 - z1)
    if d < 1e-12 or d > a + b + 1e-9 or d < abs(a - b) - 1e-9:
        return []
    m = (a * a - b * b + d * d) / (2 * d)
    h = math.sqrt(max(a * a - m * m, 0.0))
    pr, pz = r1 + m * (r2 - r1) / d, z1 + m * (z2 - z1) / d
    return list({(pr + h * (z2 - z1) / d, pz - h * (r2 - r1) / d), (pr - h * (z2 - z1) / d, pz + h * (r2 - r1) / d)})


def intersections(project) -> list[Point]:
    """Every point where two drawing elements cross or meet (ALPHA-T's Find Nearest Intersection, I)."""
    geo = project.geometry
    out: list[Point] = []
    for i, g in enumerate(geo):
        for h in geo[i + 1:]:
            if isinstance(g, Line) and isinstance(h, Line):
                cand = _line_line(g, h)
            elif isinstance(g, Line) or isinstance(h, Line):
                line, arc = (g, h) if isinstance(g, Line) else (h, g)
                cand = [q for q in _line_circle(line, *_circle(arc)) if _on_arc(arc, _pt(q))]
            else:
                cand = [q for q in _circle_circle(_circle(g), _circle(h)) if _on_arc(g, _pt(q)) and _on_arc(h, _pt(q))]
            for q in cand:
                pt = _pt(q)
                if pt not in out:
                    out.append(pt)
    return out


def nearest_intersection(project, p: Point) -> Point | None:
    return _nearest(intersections(project), p)


def erase_drawing(project) -> None:
    """ALPHA-T Q: erase all the drawing (lines, arcs and text); contours that used it become empty."""
    project.geometry.clear()
    project.texts.clear()
    for op in project.operations:
        if hasattr(op, "contour"):
            op.contour = []


def add_text(project, p: Point, text: str, size: float = 5.0) -> None:
    if not text.strip():
        raise ValueError(tr("no text"))
    project.texts.append(Text(p, text, size))


def nearest_text(project, p: Point) -> int | None:
    """Index of the text whose start point is nearest p (ALPHA-T U erases it)."""
    if not project.texts:
        return None
    return min(range(len(project.texts)), key=lambda i: p.distance(project.texts[i].p))


# --- construction commands from the ALPHA-T manual (German manual pp. 20-34) ----------------------------------
# All work in (radius, Z) and return Points with X as a diameter. Where ALPHA-T offers several solutions and cycles
# them with the space bar, these return all of them and the app lets the user tap the one wanted.


def add_circle(project, centre: Point, radius: float) -> list[str]:
    """ALPHA-T KREIS: a full circle, as two half arcs split at 3 and 9 o'clock (ALPHA-T draws circles from the
    3-o'clock point, so that point is already a split point)."""
    if radius <= 0:
        raise ValueError(tr("radius must be > 0"))
    cr, cz = _rz(centre)
    p3, p9 = _pt((cr, cz + radius)), _pt((cr, cz - radius))
    return [add_arc_3p(project, p3, _pt((cr + radius, cz)), p9), add_arc_3p(project, p9, _pt((cr - radius, cz)), p3)]


def _circle_of(project, gid: str):
    g = next((e for e in project.geometry if e.id == gid), None)
    if not isinstance(g, Arc):
        raise ValueError(tr("pick an arc"))
    return _circle(g)


def _line_of(project, gid: str) -> Line:
    g = next((e for e in project.geometry if e.id == gid), None)
    if not isinstance(g, Line):
        raise ValueError(tr("pick a line"))
    return g


def tangents_point_arc(project, p: Point, arc_id: str) -> list[tuple[Point, Point]]:
    """ALPHA-T TANGENTE 1: the (up to 2) lines from p touching the arc's circle."""
    cr, cz, rad = _circle_of(project, arc_id)
    pr, pz = _rz(p)
    d = math.hypot(pr - cr, pz - cz)
    if d <= rad + 1e-9:
        raise ValueError(tr("the point is inside the arc"))
    base = math.atan2(pr - cr, pz - cz)
    off = math.acos(rad / d)
    return [(p, _pt((cr + rad * math.sin(base + s * off), cz + rad * math.cos(base + s * off)))) for s in (1, -1)]


def tangents_arc_arc(project, id1: str, id2: str) -> list[tuple[Point, Point]]:
    """ALPHA-T TANGENTE 2: the (up to 4) lines touching both arcs' circles: 2 outer, 2 crossing."""
    (r1, z1, a), (r2, z2, b) = _circle_of(project, id1), _circle_of(project, id2)
    dz, dr = z2 - z1, r2 - r1
    d = math.hypot(dz, dr)
    if d < 1e-9:
        raise ValueError(tr("the arcs have the same centre"))
    out = []
    for sign in (1, -1):  # 1: outer tangents (same side), -1: inner (crossing)
        c = (a - sign * b) / d
        if abs(c) > 1:
            continue
        h = math.sqrt(max(1 - c * c, 0.0))
        for k in (1, -1):
            # unit normal n with n . (centre2 - centre1)/d = c
            nz = (dz * c - k * h * dr) / d
            nr = (dr * c + k * h * dz) / d
            t1 = _pt((r1 + a * nr, z1 + a * nz))
            t2 = _pt((r2 + sign * b * nr, z2 + sign * b * nz))
            if t1 != t2:
                out.append((t1, t2))
    return out


def tangents_at_angle(project, arc_id: str, angle: float) -> list[tuple[Point, Point]]:
    """ALPHA-T TANGENTE ZUM KREISBOGEN: a line at `angle` (as polar: 0 = towards -Z, 90 = +X) touching the arc's
    circle, on either side; 1.5 x the diameter long, centred on the touching point (ALPHA-T draws it 1.5 x long)."""
    cr, cz, rad = _circle_of(project, arc_id)
    a = math.radians(angle)
    ur, uz = math.sin(a), -math.cos(a)  # direction in (r, z)
    half = 1.5 * rad
    out = []
    for s in (1, -1):
        tr_, tz_ = cr + s * rad * uz, cz - s * rad * ur  # normal to the direction
        out.append((_pt((tr_ - half * ur, tz_ - half * uz)), _pt((tr_ + half * ur, tz_ + half * uz))))
    return out


def parallels(project, line_id: str, distance: float) -> list[tuple[Point, Point]]:
    """ALPHA-T PARALLEL-LINIE: the two lines `distance` away from the line, 1.5 x its length, centred on it."""
    if distance <= 0:
        raise ValueError(tr("distance must be > 0"))
    g = _line_of(project, line_id)
    (ar, az), (br, bz) = _rz(g.a), _rz(g.b)
    length = math.hypot(br - ar, bz - az)
    ur, uz = (br - ar) / length, (bz - az) / length
    mr, mz = (ar + br) / 2, (az + bz) / 2
    half = 0.75 * length
    out = []
    for s in (1, -1):
        nr, nz = -uz * s * distance, ur * s * distance
        out.append((_pt((mr + nr - half * ur, mz + nz - half * uz)), _pt((mr + nr + half * ur, mz + nz + half * uz))))
    return out


def split_arc(project, arc_id: str, p: Point) -> list[str]:
    """ALPHA-T TEILE KREISBOGEN: split the arc at the point of it nearest p; returns the two new ids."""
    g = next((e for e in project.geometry if e.id == arc_id), None)
    if not isinstance(g, Arc):
        raise ValueError(tr("pick an arc"))
    cr, cz, rad = _circle(g)
    pr, pz = _rz(p)
    ang = math.atan2(pr - cr, pz - cz)
    q = _pt((cr + rad * math.sin(ang), cz + rad * math.cos(ang)))
    if not _on_arc(g, q) or q in (g.a, g.b):
        raise ValueError(tr("the point is not inside the arc"))
    i = project.geometry.index(g)
    first = Arc(g.a, q, g.centre, g.ccw, id=g.id)
    second = Arc(q, g.b, g.centre, g.ccw, id=next_id(project))
    project.geometry[i:i + 1] = [first, second]
    return [first.id, second.id]


def mirror_z(project, z0: float) -> list[str]:
    """ALPHA-T SPIEGELN: add the mirror image of every line and arc about the plane Z = z0 (a symmetric part drawn
    once). Returns the new ids."""
    def m(p: Point) -> Point:
        return Point(p.x, round(2 * z0 - p.z, 6))

    new = []
    for g in list(project.geometry):
        gid = next_id(project)
        if isinstance(g, Arc):
            project.geometry.append(Arc(m(g.a), m(g.b), m(g.centre), not g.ccw, id=gid))
        else:
            project.geometry.append(Line(m(g.a), m(g.b), id=gid))
        new.append(gid)
    return new


def _ref_loci(project, ref, radius: float):
    """Where the centre of a blend arc of `radius` can be for one reference: ("point", P) -> circle radius R about
    P; ("line", id) -> two parallel lines; ("arc", id, convex) -> concentric circle r + R (convex) or |r - R|."""
    kind = ref[0]
    if kind == "point":
        pr, pz = _rz(ref[1])
        return [("c", pr, pz, radius)]
    if kind == "line":
        g = _line_of(project, ref[1])
        (ar, az), (br, bz) = _rz(g.a), _rz(g.b)
        length = math.hypot(br - ar, bz - az)
        nr, nz = -(bz - az) / length, (br - ar) / length
        return [("l", ar + s * radius * nr, az + s * radius * nz, br - ar, bz - az) for s in (1, -1)]
    cr, cz, rad = _circle_of(project, ref[1])
    return [("c", cr, cz, rad + radius if ref[2] else abs(rad - radius))]


def _contact(project, ref, cr: float, cz: float, radius: float) -> Point:
    """The point where a blend arc centred (cr, cz) touches the reference."""
    kind = ref[0]
    if kind == "point":
        return ref[1]
    if kind == "line":
        g = _line_of(project, ref[1])
        (ar, az), (br, bz) = _rz(g.a), _rz(g.b)
        dr, dz = br - ar, bz - az
        t = ((cr - ar) * dr + (cz - az) * dz) / (dr * dr + dz * dz)
        return _pt((ar + t * dr, az + t * dz))
    r0, z0, rad = _circle_of(project, ref[1])
    d = math.hypot(cr - r0, cz - z0)
    return _pt((r0 + rad * (cr - r0) / d, z0 + rad * (cz - z0) / d))


def blends(project, ref1, ref2, radius: float) -> list[tuple[Point, Point, Point]]:
    """ALPHA-T VERSCHMELZUNG: arcs of `radius` touching two references (a point, a line, or an arc given as convex or
    concave). Returns (start, centre, end) for every solution; the user picks one by its centre."""
    if radius <= 0:
        raise ValueError(tr("radius must be > 0"))
    centres = []
    for l1 in _ref_loci(project, ref1, radius):
        for l2 in _ref_loci(project, ref2, radius):
            if l1[0] == "c" and l2[0] == "c":
                pts = _circle_circle(l1[1:], l2[1:])
            elif l1[0] == "l" and l2[0] == "l":
                a1r, a1z, d1r, d1z = l1[1:]
                a2r, a2z, d2r, d2z = l2[1:]
                den = d1r * d2z - d1z * d2r
                if abs(den) < 1e-12:
                    continue
                t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den
                pts = [(a1r + t * d1r, a1z + t * d1z)]
            else:
                ln, ci = (l1, l2) if l1[0] == "l" else (l2, l1)
                ar, az, dr, dz = ln[1:]
                fake = Line(_pt((ar, az)), _pt((ar + dr, az + dz)))
                pts = _line_circle(fake, *ci[1:])
            centres += pts
    out = []
    for cr, cz in centres:
        a = _contact(project, ref1, cr, cz, radius)
        b = _contact(project, ref2, cr, cz, radius)
        if a != b:
            out.append((a, _pt((cr, cz)), b))
    return out


def add_blend(project, start: Point, centre: Point, end: Point) -> str:
    """The shorter arc about `centre` from start to end."""
    cr, cz = _rz(centre)
    (ar, az), (br, bz) = _rz(start), _rz(end)
    rad = math.hypot(ar - cr, az - cz)
    ta, tb = math.atan2(ar - cr, az - cz), math.atan2(br - cr, bz - cz)
    d = (tb - ta + math.pi) % (2 * math.pi) - math.pi  # signed shortest sweep
    tm = ta + d / 2
    return add_arc_3p(project, start, _pt((cr + rad * math.sin(tm), cz + rad * math.cos(tm))), end)
