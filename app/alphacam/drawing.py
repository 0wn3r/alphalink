"""Drawing operations on a project's geometry (pure Python; the canvas tools call these through the undo stack).

Coordinates are X diameter and Z. Geometry maths is done in (radius, Z). TOUCH.md 5.1: rough placement is snapped
(end points, the axis, a grid) and direction-locked (Z, X, 45 degrees); exact values come from typed entry."""

from __future__ import annotations

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
        raise ValueError("a line needs two different points")
    gid = next_id(project)
    project.geometry.append(Line(a, b, id=gid))
    return gid


def delete_element(project, gid: str) -> None:
    before = len(project.geometry)
    project.geometry[:] = [g for g in project.geometry if g.id != gid]
    if len(project.geometry) == before:
        raise KeyError(f"no element {gid}")
    for op in project.operations:  # drop it from contours that used it
        if hasattr(op, "contour"):
            op.contour = [c for c in op.contour if c != gid]


def _corner(project, p: Point):
    lines = [g for g in project.geometry if isinstance(g, Line) and p in (g.a, g.b)]
    if len(lines) != 2:
        raise ValueError(f"X{p.x:g} Z{p.z:g} is not a corner between two lines")
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
        raise ValueError("radius must be > 0")
    l1, l2 = _corner(project, p)
    pr, pz = p.x / 2, p.z
    u1 = _unit(_away(l1, p), p)
    u2 = _unit(_away(l2, p), p)
    cos_t = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
    theta = math.acos(cos_t)
    if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
        raise ValueError("the lines are parallel at this point")
    d = radius / math.tan(theta / 2)
    if d > min(_len(l1), _len(l2)) + 1e-9:
        raise ValueError(f"radius {radius:g} is too large for these lines")
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
        raise ValueError("chamfer size must be > 0")
    if not 0 < angle < 90:
        raise ValueError("chamfer angle must be between 0 and 90 degrees")
    l1, l2 = _corner(project, p)
    pr, pz = p.x / 2, p.z
    u1, u2 = _unit(_away(l1, p), p), _unit(_away(l2, p), p)
    # reference line: the one closer to Z-parallel (|dz| component larger); the other gets the computed leg
    ref_first = abs(u1[1]) >= abs(u2[1])
    ua, ub = (u1, u2) if ref_first else (u2, u1)
    corner = math.acos(max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1])))
    theta = math.radians(angle)
    if theta + corner >= math.pi - 1e-9:
        raise ValueError(f"a {angle:g} degree chamfer does not fit this corner")
    other = size * math.sin(theta) / math.sin(theta + corner)
    s1, s2 = (size, other) if ref_first else (other, size)
    for line, s in ((l1, s1), (l2, s2)):
        a, b = line.a, line.b
        if s > math.hypot((a.x - b.x) / 2, a.z - b.z) + 1e-9:
            raise ValueError(f"chamfer {size:g} x {angle:g} deg is too large for these lines")
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
                raise ValueError(f"{remaining[0].id} does not connect to the picked contour")
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
        raise ValueError("the three points are on a line")
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
        raise ValueError("pick two different lines")
    (a1r, a1z), (b1r, b1z) = (l1.a.x / 2, l1.a.z), (l1.b.x / 2, l1.b.z)
    (a2r, a2z), (b2r, b2z) = (l2.a.x / 2, l2.a.z), (l2.b.x / 2, l2.b.z)
    d1r, d1z, d2r, d2z = b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z
    den = d1r * d2z - d1z * d2r
    if abs(den) < 1e-12:
        raise ValueError("the lines are parallel")
    t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den
    p = _pt((a1r + t * d1r, a1z + t * d1z))
    for line in (l1, l2):
        if line.a.distance(p) < line.b.distance(p):
            line.a = p
        else:
            line.b = p
        if line.a == line.b:
            raise ValueError("a line would shrink to a point")
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
        raise ValueError("no text")
    project.texts.append(Text(p, text, size))


def nearest_text(project, p: Point) -> int | None:
    """Index of the text whose start point is nearest p (ALPHA-T U erases it)."""
    if not project.texts:
        return None
    return min(range(len(project.texts)), key=lambda i: p.distance(project.texts[i].p))
