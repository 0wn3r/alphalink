"""Contour geometry for the posts: chaining picked elements, the imaginary tool-tip path, offsets.

ALPHA-T writes the **imaginary tool tip** path, not the drawing (cloud samples ARC_*, SLOPE_area, INT_*): the
nose-centre path (what the SD1 lists, the contour offset by the nose radius r towards the tool) shifted by
(-r, -r) in (radius, Z) for an external tool and (+r, -r) for an internal one. Lines along the axes therefore stay
on the drawing, while arcs become radius R + r (convex) or R - r (concave) and sloped lines move. The first and last
points are the drawing's own end points (the lead-in/lead-out start there).

Internally everything is in (radius, Z); the API uses X as a diameter like the rest of the model."""

from __future__ import annotations

from ..i18n import tr
import math
from dataclasses import dataclass

from ..model.geometry import Arc, Element, Point

COS45 = math.cos(math.radians(45))
MM_PER_INCH = 25.4
EPS = 1e-7


@dataclass
class Seg:
    """An oriented path element. X is a diameter. For arcs `ccw` follows the SDG/G-code sense (True = G3)."""

    a: Point
    b: Point
    centre: Point | None = None
    ccw: bool = False

    @property
    def is_arc(self) -> bool:
        return self.centre is not None


def chain(elements: list[Element]) -> list[Seg]:
    """Orient picked elements into a connected path from the first element's free end."""
    if not elements:
        raise ValueError(tr("empty contour"))

    def seg(e: Element, reverse: bool) -> Seg:
        c = e.centre if isinstance(e, Arc) else None
        ccw = e.ccw if isinstance(e, Arc) else False
        return Seg(e.b, e.a, c, not ccw) if reverse else Seg(e.a, e.b, c, ccw)

    first = elements[0]
    reverse = len(elements) > 1 and _same(first.a, elements[1].a) or len(elements) > 1 and _same(first.a, elements[1].b)
    path = [seg(first, reverse)]
    for e in elements[1:]:
        end = path[-1].b
        if _same(e.a, end):
            path.append(seg(e, False))
        elif _same(e.b, end):
            path.append(seg(e, True))
        else:
            raise ValueError(tr("contour is not connected at X{x} Z{z} ({id})", x=f"{end.x:g}", z=f"{end.z:g}", id=e.id))
    return path


def front_first(path: list[Seg]) -> list[Seg]:
    """The path running from the front (the end with the larger Z) towards the chuck. Area and face clear are
    computed for that direction: ALPHA-T's own programs for a contour picked from the chuck end are not usable
    (AREA_towards_from_chuck_end only skims the front face; FACE_towards_from_chuck_end makes one cut), so the
    app cuts such a contour as if it had been picked from the front."""
    if path and path[-1].b.z > path[0].a.z + EPS:
        return [Seg(s.b, s.a, s.centre, not s.ccw if s.is_arc else False) for s in reversed(path)]
    return path


def points(path: list[Seg]) -> list[Point]:
    return [path[0].a] + [s.b for s in path]


def _same(p: Point, q: Point) -> bool:
    return abs(p.x - q.x) < 1e-6 and abs(p.z - q.z) < 1e-6


# --- (radius, z) vector helpers --------------------------------------------------------------------------

def _rz(p: Point) -> tuple[float, float]:
    return p.x / 2, p.z


def _pt(r: float, z: float) -> Point:
    return Point(round(2 * r, 9), round(z, 9))


def _tool_normal(seg: Seg, at_start: bool, side: int) -> tuple[float, float]:
    """Unit normal towards the tool at the start or end of a segment. side = +1 external, -1 internal."""
    (ar, az), (br, bz) = _rz(seg.a), _rz(seg.b)
    if seg.is_arc:
        cr, cz = _rz(seg.centre)
        pr, pz = (ar, az) if at_start else (br, bz)
        vr, vz = pr - cr, pz - cz
        # G3 (ccw=True) is clockwise in a right-handed (r, z) frame
        tr, tz = (vz, -vr) if seg.ccw else (-vz, vr)
    else:
        tr, tz = br - ar, bz - az
    n = math.hypot(tr, tz)
    return side * -tz / n, side * tr / n


def _offset(seg: Seg, d: float, side: int) -> Seg:
    if not seg.is_arc:
        nr, nz = _tool_normal(seg, True, side)
        (ar, az), (br, bz) = _rz(seg.a), _rz(seg.b)
        return Seg(_pt(ar + d * nr, az + d * nz), _pt(br + d * nr, bz + d * nz))
    (ar, az), (br, bz), (cr, cz) = _rz(seg.a), _rz(seg.b), _rz(seg.centre)
    radius = math.hypot(ar - cr, az - cz)
    nr, nz = _tool_normal(seg, True, side)
    grow = 1 if nr * (ar - cr) + nz * (az - cz) > 0 else -1
    k = (radius + grow * d) / radius
    return Seg(_pt(cr + k * (ar - cr), cz + k * (az - cz)), _pt(cr + k * (br - cr), cz + k * (bz - cz)),
               seg.centre, seg.ccw)


def _join(s1: Seg, s2: Seg, near: Point) -> Point:
    """Corner point between two consecutive offset segments: the end/start if they already meet (tangent
    continuity), else the intersection of the extended elements closest to `near`."""
    if _same(s1.b, s2.a):
        return s1.b
    if not s1.is_arc and not s2.is_arc:
        (a1r, a1z), (b1r, b1z) = _rz(s1.a), _rz(s1.b)
        (a2r, a2z), (b2r, b2z) = _rz(s2.a), _rz(s2.b)
        d1r, d1z, d2r, d2z = b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z
        den = d1r * d2z - d1z * d2r
        if abs(den) < EPS:
            return s2.a
        t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den
        return _pt(a1r + t * d1r, a1z + t * d1z)
    line, arc = (s1, s2) if not s1.is_arc else (s2, s1)
    if line.is_arc:
        raise NotImplementedError(tr("arc-to-arc corners are not supported yet"))
    (ar, az), (br, bz), (cr, cz) = _rz(line.a), _rz(line.b), _rz(arc.centre)
    radius = math.hypot(*(v - c for v, c in zip(_rz(arc.a), (cr, cz))))
    dr, dz = br - ar, bz - az
    fr, fz = ar - cr, az - cz
    qa, qb, qc = dr * dr + dz * dz, 2 * (fr * dr + fz * dz), fr * fr + fz * fz - radius * radius
    disc = max(qb * qb - 4 * qa * qc, 0.0)
    cands = [(-qb + sgn * math.sqrt(disc)) / (2 * qa) for sgn in (1, -1)]
    nr_, nz_ = _rz(near)
    best = min(cands, key=lambda t: math.hypot(ar + t * dr - nr_, az + t * dz - nz_))
    return _pt(ar + best * dr, az + best * dz)


def _offset_path(path: list[Seg], d: float, side: int) -> tuple[list[Seg], list[Seg], list[Point]]:
    """(path without zero-length elements, offset elements, corner points incl. both offset ends)."""
    # a zero-length element (e.g. a diameter fully eaten by the fillets on both ends) has no direction
    path = [s for s in path if not _same(s.a, s.b)] or path
    off = [_offset(s, d, side) for s in path]
    corners = [off[0].a]
    for i in range(1, len(off)):
        corners.append(_join(off[i - 1], off[i], path[i].a))
    corners.append(off[-1].b)
    return path, off, corners


def centre_path(path: list[Seg], nose_radius: float, external: bool = True, extend_end: bool = True) -> list[Seg]:
    """The nose-centre path as ALPHA-T lists it in the .SD1: the contour offset by r towards the tool, the first point
    offset along its element's normal, the last one too and then (extend_end; not for face clear) carried r further
    along the last element (MET2: (100,-50) -> X100.8 Z-49.6)."""
    side = 1 if external else -1
    path, off, corners = _offset_path(path, nose_radius, side)
    if extend_end and nose_radius:
        last = off[-1]
        if last.is_arc:
            nr, nz = _tool_normal(path[-1], False, side)
            tr, tz = (nz, -nr) if side > 0 else (-nz, nr)  # tangent: the normal turned back to the path direction
        else:
            (ar, az), (br, bz) = _rz(last.a), _rz(last.b)
            n = math.hypot(br - ar, bz - az)
            tr, tz = (br - ar) / n, (bz - az) / n
        er, ez = _rz(corners[-1])
        corners[-1] = _pt(er + nose_radius * tr, ez + nose_radius * tz)
    return [Seg(corners[i], corners[i + 1], s.centre, s.ccw) for i, s in enumerate(off)]


def tip_path(path: list[Seg], nose_radius: float, external: bool = True, end_on_drawing: bool = True) -> list[Seg]:
    """The imaginary tool-tip path that ALPHA-T writes for a drawn contour (see the module docstring).
    end_on_drawing=False ends on the shifted offset end instead of the drawn end point (face clear pre-finish,
    FACE_prefinish: last point X99.2 for the drawn X100 with r 0.4)."""
    if nose_radius == 0:
        return [Seg(s.a, s.b, s.centre, s.ccw) for s in path]
    side = 1 if external else -1
    path, off, corners = _offset_path(path, nose_radius, side)
    corners[0] = path[0].a
    if end_on_drawing:
        corners[-1] = path[-1].b
    shift_r, shift_z = -side * nose_radius, -nose_radius

    def shifted(p: Point) -> Point:
        r, z = _rz(p)
        return _pt(r + shift_r, z + shift_z)

    out = []
    for i, s in enumerate(off):
        a = corners[i] if i == 0 else shifted(corners[i])
        b = corners[i + 1] if i == len(off) - 1 and end_on_drawing else shifted(corners[i + 1])
        out.append(Seg(a, b, shifted(s.centre) if s.is_arc else None, s.ccw))
    return out


def translate(path: list[Seg], offset_x: float, offset_z: float, external: bool = True) -> list[Seg]:
    """OFFSET X (radius) / Z: move the path by (2*offset_x, offset_z), X outwards for external work and inwards
    (-X) for internal. The first point only moves across its element, not along it: an external face starting on
    the axis keeps X0 (OFFSET_finish), an internal bore starting at the front keeps Z0 (INT_finish_offset)."""
    if not (offset_x or offset_z):
        return path
    dx = (1 if external else -1) * 2 * offset_x

    def mv(p: Point, keep_x: bool = False, keep_z: bool = False) -> Point:
        return Point(p.x if keep_x else round(p.x + dx, 9), p.z if keep_z else round(p.z + offset_z, 9))

    out = [Seg(mv(s.a), mv(s.b), mv(s.centre) if s.is_arc else None, s.ccw) for s in path]
    first = path[0]
    if not first.is_arc:
        out[0].a = mv(first.a, keep_x=first.a.z == first.b.z, keep_z=first.a.x == first.b.x)
    return out


def z_at(seg: Seg, x: float) -> float:
    """Z of the segment at diameter x (x within the segment's X range)."""
    if not seg.is_arc:
        if seg.a.x == seg.b.x:
            return max(seg.a.z, seg.b.z)
        t = (x - seg.a.x) / (seg.b.x - seg.a.x)
        return seg.a.z + t * (seg.b.z - seg.a.z)
    (ar, az), (_, bz), (cr, cz) = _rz(seg.a), _rz(seg.b), _rz(seg.centre)
    radius = math.hypot(ar - cr, az - cz)
    dz = math.sqrt(max(radius * radius - (x / 2 - cr) ** 2, 0.0))
    # the arc's Z range picks the branch (fine for arcs up to a quarter circle, which is what corner radii are)
    lo, hi = min(az, bz), max(az, bz)
    cands = [z for z in (cz + dz, cz - dz) if lo - 1e-6 <= z <= hi + 1e-6]
    return max(cands) if cands else (cz + dz if abs(cz + dz - az) < abs(cz - dz - az) else cz - dz)


def x_at(seg: Seg, z: float) -> float | None:
    """Diameter of the segment at Z, or None when the segment does not reach Z (faces never do)."""
    lo, hi = sorted((seg.a.z, seg.b.z))
    if not lo - 1e-9 <= z <= hi + 1e-9 or lo == hi:
        return None
    if not seg.is_arc:
        return seg.a.x + (z - seg.a.z) / (seg.b.z - seg.a.z) * (seg.b.x - seg.a.x)
    (ar, az), (cr, cz) = _rz(seg.a), _rz(seg.centre)
    radius = math.hypot(ar - cr, az - cz)
    dr = math.sqrt(max(radius * radius - (z - cz) ** 2, 0.0))
    # the branch on the arc's own side of the centre (corner radii are at most a quarter circle)
    lo_r, hi_r = sorted((ar, seg.b.x / 2))
    cands = [r for r in (cr + dr, cr - dr) if lo_r - 1e-6 <= r <= hi_r + 1e-6]
    return 2 * (cands[0] if cands else cr + dr)


def mm(value_mm: float, units: str) -> float:
    """A length given in mm (tool data, ALPHA-T constants) in project units."""
    return value_mm / MM_PER_INCH if units == "inch" else value_mm


def leg45(length: float, decimals: int) -> float:
    """Radial (= Z) component of a 45-degree move of `length`, rounded to the output decimals first. ALPHA-T
    rounds this component before adding it (MET2: lead-out 0.2 -> 0.141, so X100.282 Z-49.859, not 100.283)."""
    return round(length * COS45, decimals)


def limits(pts: list[Point], nose_radius: float) -> tuple[float, float]:
    """#125/#126 (SD1 G115): drawn contour minimum X - 4r (diameter) and minimum Z - 2r. Same for internal
    (INT_*) and with offsets (OFFSET_*)."""
    return min(p.x for p in pts) - 4 * nose_radius, min(p.z for p in pts) - 2 * nose_radius
