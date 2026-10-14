"""Feature helpers on the drawing (workflow improvements, step 3): ISO metric threads with a DIN 76 relief groove,
and chamfers or radii on every outside corner of the profile at once. Pure geometry: the app applies them as one
undoable edit."""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import tr
from .model.geometry import Line, Point

# ISO 261 metric threads (mm): nominal diameter -> coarse pitch, then the usual fine pitches
METRIC = {
    6: (1.0, 0.75), 8: (1.25, 1.0), 10: (1.5, 1.25, 1.0), 12: (1.75, 1.5, 1.25), 14: (2.0, 1.5), 16: (2.0, 1.5),
    18: (2.5, 2.0, 1.5), 20: (2.5, 2.0, 1.5), 22: (2.5, 2.0, 1.5), 24: (3.0, 2.0, 1.5), 27: (3.0, 2.0, 1.5),
    30: (3.5, 2.0, 1.5), 33: (3.5, 2.0), 36: (4.0, 3.0, 2.0), 39: (4.0, 3.0), 42: (4.5, 3.0), 45: (4.5, 3.0),
    48: (5.0, 3.0), 52: (5.0, 4.0, 3.0), 56: (5.5, 4.0), 60: (5.5, 4.0), 64: (6.0, 4.0),
    40: (3.0, 2.0, 1.5), 50: (3.0, 2.0, 1.5),  # fine pitches only (no coarse M40 or M50 in ISO 261)
}
METRIC = dict(sorted(METRIC.items()))
FINE_ONLY = {40, 50}


def coarse(d: float, pitch: float) -> bool:
    return int(d) not in FINE_ONLY and METRIC.get(int(d), (None,))[0] == pitch


@dataclass
class ThreadSpec:
    d: float  # nominal (major) diameter
    pitch: float

    @property
    def minor(self) -> float:
        """External thread's minor diameter d3 (ISO 965-1): d - 1.22687 P."""
        return round(self.d - 1.22687 * self.pitch, 3)

    @property
    def depth(self) -> float:
        """Thread depth on the radius, h3 = 0.61343 P."""
        return round(0.61343 * self.pitch, 3)

    @property
    def relief_diameter(self) -> float:
        """DIN 76-1 external undercut diameter, about d - 1.5 P (rounded to 0.1)."""
        return round(self.d - 1.5 * self.pitch, 1)

    @property
    def relief_width(self) -> float:
        """DIN 76-1 undercut length, between g1 (about 2 P) and g2 (3.5 P): 3 P, rounded to 0.1."""
        return round(3.0 * self.pitch, 1)

    @property
    def name(self) -> str:
        return f"M{self.d:g}" if coarse(self.d, self.pitch) else f"M{self.d:g} × {self.pitch:g}"


def nearest_size(diameter: float) -> int:
    return min(METRIC, key=lambda d: abs(d - diameter))


def add_thread(project, gid: str, spec: ThreadSpec, length: float | None = None, relief: bool = True) -> str:
    """Thread the profile line gid (a diameter along Z): it is set to the nominal diameter if it is not, the
    thread's minor-diameter line is drawn under it (for the Threading operation), and with `relief` a DIN 76
    undercut is cut into the line at its chuck end. Returns the minor line's id."""
    from .drawing import add_line

    g = next((e for e in project.geometry if e.id == gid), None)
    if not isinstance(g, Line) or abs(g.a.x - g.b.x) > 1e-9 or abs(g.a.z - g.b.z) < 1e-9:
        raise ValueError(tr("choose a line along Z (a diameter) for the thread"))
    front, back = (g.a, g.b) if g.a.z > g.b.z else (g.b, g.a)
    span = front.z - back.z
    width = spec.relief_width if relief else 0.0
    if width >= span:
        raise ValueError(tr("the line is too short for M{d:g} and its relief groove", d=spec.d))
    d = spec.d
    # the line at the nominal diameter, shortened by the groove; the ends of the neighbours follow
    _move_point(project, front, Point(d, front.z))
    _move_point(project, back, Point(d, back.z))
    g = next(e for e in project.geometry if e.id == gid)
    if relief:
        start = Point(d, back.z + width)
        if g.a.z > g.b.z:
            g.b = start
        else:
            g.a = start
        dg = spec.relief_diameter
        add_line(project, start, Point(dg, start.z))
        add_line(project, Point(dg, start.z), Point(dg, back.z))
        add_line(project, Point(dg, back.z), Point(d, back.z))
    thread_end = back.z + width / 2 if relief else back.z
    end = front.z - length if length else thread_end
    end = max(end, back.z)
    return add_line(project, Point(spec.minor, front.z), Point(spec.minor, round(end, 3)))


def _move_point(project, old: Point, new: Point) -> None:
    if old == new:
        return
    for e in project.geometry:
        if e.a == old:
            e.a = new
        if e.b == old:
            e.b = new


def outside_corners(project) -> list[Point]:
    """The convex corners between two lines of the outer profile (where a chamfer or radius takes material off the
    outside of the part), front to back; corners on the axis are left out."""
    from .drawing import outer_profile

    by_id = {g.id: g for g in project.geometry}
    chain = [by_id[i] for i in outer_profile(project) if i in by_id]
    if not chain:
        return []
    # walk the chain as one path from its front end
    path = []
    pos = chain[0].a if chain[0].a.z >= chain[0].b.z else chain[0].b
    for e in chain:
        a, b = (e.a, e.b) if e.a == pos else (e.b, e.a)
        path.append((e, a, b))
        pos = b
    corners = []
    for (e1, a1, b1), (e2, a2, b2) in zip(path, path[1:]):
        if not (isinstance(e1, Line) and isinstance(e2, Line)) or abs(b1.x) < 1e-9:
            continue
        d1 = (b1.z - a1.z, (b1.x - a1.x) / 2)
        d2 = (b2.z - a2.z, (b2.x - a2.x) / 2)
        if d1[0] * d2[1] - d1[1] * d2[0] > 1e-9:  # turning away from the material below the profile
            corners.append(b1)
    return corners


def all_corners(project, kind: str, size: float, angle: float = 45.0) -> int:
    """Chamfer ("chamfer") or round ("fillet") every outside corner; returns how many were done (a corner whose
    lines are too short for the size is left as it is)."""
    from . import drawing

    done = 0
    for c in outside_corners(project):
        try:
            if kind == "chamfer":
                drawing.chamfer(project, c, size, angle)
            else:
                drawing.fillet(project, c, size)
            done += 1
        except ValueError:
            continue
    return done
