"""Import a turned-part profile from DXF (ALPHA-T's ATDXF, reimplemented after tools/dxf2sdl.py).

DXF X is the Z axis (spindle axis), DXF Y the distance from the axis: a radius (usual for lathe drawings) or a
diameter, as chosen. Entities: LINE, ARC, CIRCLE (two half arcs, as the app draws circles), POLYLINE/VERTEX (bulges),
LWPOLYLINE (bulges); TEXT, INSERT and the rest are skipped, as ADXF does. A drawing from the office lands in place:
it can be turned (a part drawn upright), put with its front face on the right, its axis on Y0 (as drawn, the middle
of a full section, or the lowest point of a half drawing) and its front face on Z0. By default only the half at or
above the axis is kept (a full-section drawing mirrors the part below it)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..model.geometry import Arc, Line, Point
from ..model.project import Billet, Project


@dataclass
class DxfResult:
    project: Project
    skipped: dict[str, int] = field(default_factory=dict)


def _groups(text: str):
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        try:
            yield int(lines[i].strip()), lines[i + 1].strip()
        except ValueError:
            continue


def _entities(text: str):
    sec, cur = None, None
    for c, v in _groups(text):
        if c == 0:
            if cur is not None and sec == "ENTITIES":
                yield cur
            cur = None
            if v == "SECTION":
                sec = "pending"
                continue
            if v == "ENDSEC":
                sec = None
                continue
            cur = {"type": v, "g": []}
        elif c == 2 and sec == "pending":
            sec = v
        elif cur is not None:
            cur["g"].append((c, v))
    if cur is not None and sec == "ENTITIES":
        yield cur


def read_dxf(text: str, y_is: str = "radius", units: str = "mm", upper_half: bool = True, rotate: int = 0,
             front: str = "right", axis: str = "as drawn", datum: bool = False) -> DxfResult:
    """y_is: "radius" or "diameter"; units: unit of the DXF and of the new part ("mm" or "inch").
    rotate: 0, 90, 180 or 270 degrees anticlockwise, first; front: "right" (as the lathe is seen) or "left" (the
    drawing is mirrored so the front face comes to the right); axis: "as drawn" (DXF Y0), "middle" (a full section:
    half way between the lowest and the highest point) or "lowest" (a half drawing standing on its axis);
    datum: put the front face (the largest Z) on Z0."""
    k = 2.0 if y_is == "radius" else 1.0
    prims: list = []  # ("line", a, b) | ("arc", a, b, c, ccw) in DXF (x, y), before turning and moving
    elements: list = []
    skipped: dict[str, int] = {}

    def add_line(a, b):
        if a != b:
            prims.append(("line", a, b))

    def add_arc(a, b, c, ccw):
        # DXF counter-clockwise in (x, y) = (Z, radius) is counter-clockwise with Z right and X up = G3
        prims.append(("arc", a, b, c, ccw))

    def bulge(a, b, bg):
        th = 4 * math.atan(bg)
        d = math.dist(a, b)
        if d == 0:
            return
        r = d / (2 * math.sin(abs(th) / 2))
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        h = math.sqrt(max(r * r - (d / 2) ** 2, 0)) * (1 if abs(th) < math.pi else -1) * (1 if bg > 0 else -1)
        ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
        add_arc(a, b, (mx - uy * h, my + ux * h), bg > 0)

    def chain(vertices, closed):
        v = vertices + ([vertices[0]] if closed and vertices else [])
        for (a, bg), (b, _b2) in zip(v, v[1:]):
            bulge(a, b, bg) if bg else add_line(a, b)

    poly = None
    for e in _entities(text):
        g: dict[int, str] = {}
        for c, v in e["g"]:
            g.setdefault(c, v)
        f = lambda c, d=0.0: float(g.get(c, d))  # noqa: E731
        t = e["type"]
        if t == "LINE":
            add_line((f(10), f(20)), (f(11), f(21)))
        elif t == "CIRCLE":  # two half arcs, split at the 3 and 9 o'clock points
            cx, cy, r = f(10), f(20), f(40)
            if r > 0:
                add_arc((cx + r, cy), (cx - r, cy), (cx, cy), True)
                add_arc((cx - r, cy), (cx + r, cy), (cx, cy), True)
        elif t == "ARC":
            cx, cy, r = f(10), f(20), f(40)
            a0, a1 = math.radians(f(50)), math.radians(f(51))
            add_arc((cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1)),
                    (cx, cy), True)
        elif t == "POLYLINE":
            poly = {"closed": int(f(70)) & 1, "v": []}
        elif t == "VERTEX" and poly is not None:
            poly["v"].append(((f(10), f(20)), f(42)))
        elif t == "SEQEND" and poly is not None:
            chain(poly["v"], poly["closed"])
            poly = None
        elif t == "LWPOLYLINE":
            verts, x = [], None
            for c, v in e["g"]:
                if c == 10:
                    x = float(v)
                elif c == 20 and x is not None:
                    verts.append([(x, float(v)), 0.0])
                elif c == 42 and verts:
                    verts[-1][1] = float(v)
            chain([(tuple(p), b) for p, b in verts], int(f(70)) & 1)
        else:
            skipped[t] = skipped.get(t, 0) + 1
    # turn, mirror, then move the axis and the front face into place (DXF units)
    def turned(p):
        x, y = p
        for _ in range((rotate // 90) % 4):
            x, y = -y, x
        return (-x if front == "left" else x, y)

    mirrored = front == "left"
    pts = [turned(q) for pr in prims for q in pr[1:4] if isinstance(q, tuple)]
    dy = 0.0
    if pts and axis == "middle":
        dy = -(min(y for _, y in pts) + max(y for _, y in pts)) / 2
    elif pts and axis == "lowest":
        dy = -min(y for _, y in pts)
    dx = -max(x for x, _ in pts) if pts and datum else 0.0

    def pt(p) -> Point:
        x, y = turned(p)
        return Point(round(k * (y + dy), 6), round(x + dx, 6))  # (X diameter, Z)

    for pr in prims:
        if pr[0] == "line":
            elements.append(Line(pt(pr[1]), pt(pr[2])))
        else:
            elements.append(Arc(pt(pr[1]), pt(pr[2]), pt(pr[3]), ccw=pr[4] != mirrored))
    if upper_half:
        elements = [g for g in elements if min(g.a.x, g.b.x) >= -1e-9 and
                    not (isinstance(g, Arc) and g.centre.x < -1e-9)]
    for n, g in enumerate(elements):
        g.id = f"g{n}"
    xs = [p.x for g in elements for p in (g.a, g.b)] or [50.0]
    zs = [p.z for g in elements for p in (g.a, g.b)] or [-80.0]
    step = 0.1 if units == "inch" else 2.0
    billet = Billet(diameter=math.ceil(max(xs) / step) * step + step,
                    length=math.ceil(-min(min(zs), 0) / step) * step + step)
    tc = Point(round(100 / 25.4, 4), round(50 / 25.4, 4)) if units == "inch" else Point(100, 50)
    return DxfResult(Project(units=units, billet=billet, tool_change=tc, geometry=elements), skipped)


def load_dxf(path: str | Path, **kw) -> DxfResult:
    return read_dxf(Path(path).read_text(errors="replace"), **kw)
