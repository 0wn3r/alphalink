"""Drawing operations on a project's geometry (pure Python; the canvas tools call these through the undo stack).

Coordinates are X diameter and Z. Geometry maths is done in (radius, Z). TOUCH.md 5.1: rough placement is snapped
(end points, the axis, a grid) and direction-locked (Z, X, 45 degrees); exact values come from typed entry."""

from __future__ import annotations

import math

from .model.geometry import Arc, Line, Point

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


def chamfer(project, p: Point, size: float) -> str:
    """Cut the corner at p back by `size` along both lines and join with a line."""
    if size <= 0:
        raise ValueError("chamfer size must be > 0")
    l1, l2 = _corner(project, p)
    if size > min(_len(l1), _len(l2)) + 1e-9:
        raise ValueError(f"chamfer {size:g} is too large for these lines")
    pr, pz = p.x / 2, p.z
    u1, u2 = _unit(_away(l1, p), p), _unit(_away(l2, p), p)
    T1 = _pt((pr + u1[0] * size, pz + u1[1] * size))
    T2 = _pt((pr + u2[0] * size, pz + u2[1] * size))
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
