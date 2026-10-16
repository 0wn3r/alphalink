"""Import a turned-part profile from DXF (ALPHA-T's ATDXF; the file is read with ezdxf).

DXF X is the Z axis (spindle axis), DXF Y the distance from the axis: a radius (usual for lathe drawings) or a
diameter, as chosen. Entities: LINE, ARC, CIRCLE (two half arcs, as the app draws circles), POLYLINE and LWPOLYLINE
(bulges); TEXT, INSERT (blocks: the title block too), SPLINE, ELLIPSE and the rest are skipped and counted, as ADXF
does. A drawing from the office lands in place: it can be turned (a part drawn upright), put with its front face on
the right, its axis on Y0 (as drawn, the middle of a full section, or the lowest point of a half drawing) and its
front face on Z0. By default only the half at or above the axis is kept (a full-section drawing mirrors the part
below it)."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import recover

from ..i18n import tr
from ..model.geometry import Arc, Line, Point
from ..model.project import Billet, Project


@dataclass
class DxfResult:
    project: Project
    skipped: dict[str, int] = field(default_factory=dict)


def _wcs_prims(entities, prims: list, skipped: dict[str, int]) -> None:
    """LINE, ARC, CIRCLE and 2D/3D polylines as ("line", a, b) | ("arc", a, b, c, ccw) in WCS (x, y). Entities drawn
    with a flipped extrusion (0, 0, -1), as some CAD programs write mirrored geometry, run the other way in WCS."""
    for e in entities:
        t = e.dxftype()
        if t == "LINE":
            _line(prims, e.dxf.start, e.dxf.end)
        elif t == "ARC":
            # DXF counter-clockwise in (x, y) = (Z, radius) is counter-clockwise with Z right and X up = G3
            a, b, c = e.start_point, e.end_point, e.ocs().to_wcs(e.dxf.center)
            prims.append(("arc", (a.x, a.y), (b.x, b.y), (c.x, c.y), e.dxf.extrusion.z > 0))
        elif t == "CIRCLE":  # two half arcs, split at the 3 and 9 o'clock points
            c, r = e.ocs().to_wcs(e.dxf.center), e.dxf.radius
            if r > 0:
                prims.append(("arc", (c.x + r, c.y), (c.x - r, c.y), (c.x, c.y), True))
                prims.append(("arc", (c.x - r, c.y), (c.x + r, c.y), (c.x, c.y), True))
        elif t == "LWPOLYLINE":
            _chain(prims, e.ocs(), [((x, y), b) for x, y, b in e.get_points("xyb")], e.closed)
        elif t == "POLYLINE" and e.is_2d_polyline:
            _chain(prims, e.ocs(), [((v.dxf.location.x, v.dxf.location.y), v.dxf.bulge) for v in e.vertices],
                   e.is_closed)
        elif t == "POLYLINE" and e.is_3d_polyline:  # WCS points, no bulges
            pts = [v.dxf.location for v in e.vertices]
            for a, b in zip(pts, pts[1:] + (pts[:1] if e.is_closed else [])):
                _line(prims, a, b)
        else:
            skipped[t] = skipped.get(t, 0) + 1


def _line(prims: list, a, b) -> None:
    if (a.x, a.y) != (b.x, b.y):
        prims.append(("line", (a.x, a.y), (b.x, b.y)))


def _chain(prims: list, ocs, vertices, closed) -> None:
    """Polyline vertices ((x, y) in OCS, bulge to the next): lines and arcs in the drawn order and direction."""
    v = vertices + ([vertices[0]] if closed and vertices else [])
    ccw_is_ccw = ocs.uz.z > 0
    for (a, bg), (b, _b2) in zip(v, v[1:]):
        wa, wb = ocs.to_wcs((*a, 0)), ocs.to_wcs((*b, 0))
        if not bg:
            _line(prims, wa, wb)
            continue
        d = math.dist(a, b)
        if d == 0:
            continue
        th = 4 * math.atan(bg)
        r = d / (2 * math.sin(abs(th) / 2))
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        h = math.sqrt(max(r * r - (d / 2) ** 2, 0)) * (1 if abs(th) < math.pi else -1) * (1 if bg > 0 else -1)
        ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
        c = ocs.to_wcs((mx - uy * h, my + ux * h, 0))
        prims.append(("arc", (wa.x, wa.y), (wb.x, wb.y), (c.x, c.y), (bg > 0) == ccw_is_ccw))


def read_doc(doc, y_is: str = "radius", units: str = "mm", upper_half: bool = True, rotate: int = 0,
             front: str = "right", axis: str = "as drawn", datum: bool = False) -> DxfResult:
    """The model space of an ezdxf document as a part. y_is: "radius" or "diameter"; units: unit of the DXF and of
    the new part ("mm" or "inch"). rotate: 0, 90, 180 or 270 degrees anticlockwise, first; front: "right" (as the
    lathe is seen) or "left" (the drawing is mirrored so the front face comes to the right); axis: "as drawn" (DXF
    Y0), "middle" (a full section: half way between the lowest and the highest point) or "lowest" (a half drawing
    standing on its axis); datum: put the front face (the largest Z) on Z0."""
    k = 2.0 if y_is == "radius" else 1.0
    prims: list = []  # ("line", a, b) | ("arc", a, b, c, ccw) in DXF (x, y), before turning and moving
    elements: list = []
    skipped: dict[str, int] = {}
    _wcs_prims(doc.modelspace(), prims, skipped)
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


def read_dxf(text: str, **kw) -> DxfResult:
    """A DXF given as text (see read_doc for the options)."""
    return _read(lambda: recover.read(io.BytesIO(text.encode("utf-8"))), **kw)


def load_dxf(path: str | Path, **kw) -> DxfResult:
    """A DXF file, ASCII (any version and code page) or binary (see read_doc for the options)."""
    with open(path, "rb") as f:
        binary = f.read(len(BINARY_SENTINEL)) == BINARY_SENTINEL
    # ezdxf's recover mode reads ASCII files other programs wrote loosely (missing sections, broken tags) and
    # repairs them; it has no binary reader, binary files are written by AutoCAD itself and load normally
    return _read((lambda: (ezdxf.readfile(str(path)), None)) if binary else (lambda: recover.readfile(str(path))),
                 **kw)


BINARY_SENTINEL = b"AutoCAD Binary DXF"


def _read(read, **kw) -> DxfResult:
    try:
        doc, _auditor = read()
    except (ezdxf.DXFError, ValueError) as e:
        raise ValueError(tr("not a readable DXF file ({error})", error=e)) from e
    if not len(doc.modelspace()):
        raise ValueError(tr("the file has no drawing in it"))
    return read_doc(doc, **kw)
