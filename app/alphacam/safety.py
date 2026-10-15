"""Safety check before export (workflow improvements): the preview already has every move; this flags, per
operation, what could hurt the part or the machine. Warnings only: the program is still written, so a deliberate
program is never blocked.

* gouge: a feed move inside the finished part (the operation's own contour, outside for external operations,
  inside for internal ones), or a rapid through it;
* chuck jaws: any move behind the billet length (the jaws hold the billet there);
* tailstock: any move into the live centre at the front face, when the part shows one;
* tip angle: ALPHA-T's own test before an area clear or face clear (RE_NOTES Progress 42): the tool's front and rear
  clearance angles must suit the cut's side and direction, or it says "Invalid tip angle" and drops its collision
  check;
* uncut: a wall steeper than the tool's rear clearance in an external finish pass. ALPHA-T's collision
  check (contour.limit_dives) goes down it at the rear angle instead, so material stays on the wall;
* tool angle: a feed move diving steeper than the tool's clearance allows, when ALPHA-T's collision check is off
  (a tool without angles, or angles that fail the tip angle test): the surface it leaves behind would run into the
  tool's trailing edge.

Moves are the PRG's: X is a diameter, the tool's imaginary tip; distances below are on the radius."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .i18n import tr
from .io.prg_parse import MoveKind
from .model.geometry import Arc
from .moves import segments
from .toolpath.area import collision_angle, tip_angle_ok  # ALPHA-T's rules live with the area clear they guard
from .toolpath.contour import chain

TOL = 0.02  # mm: a move this close to the surface is on it
DIVE_SLACK = 0.5  # degrees


@dataclass
class Finding:
    op_index: int | None
    kind: str  # gouge | rapid | chuck | tailstock | tip | uncut | angle
    message: str
    x: float  # where (X diameter, Z)
    z: float


def _profile(project, contour: list[str]) -> list[tuple[float, float, float, float]]:
    """The contour as (z0, r0, z1, r1) pieces (arcs as short chords), radius = X / 2."""
    by_id = {g.id: g for g in project.geometry}
    pieces = []
    for gid in contour:
        g = by_id.get(gid)
        if g is None:
            continue
        if isinstance(g, Arc):
            from .moves import Segment

            pts = Segment(MoveKind.ARC_CCW if g.ccw else MoveKind.ARC_CW, (g.a.x, g.a.z), (g.b.x, g.b.z), None, 0,
                          (g.centre.x, g.centre.z)).polyline(24)
        else:
            pts = [(g.a.x, g.a.z), (g.b.x, g.b.z)]
        for (xa, za), (xb, zb) in zip(pts, pts[1:]):
            pieces.append((za, xa / 2, zb, xb / 2))
    return pieces


def _surface(pieces, z: float, outer: bool) -> float | None:
    """The part's radius at z: the largest (external part) or smallest (bore) radius of the pieces spanning z."""
    rs = []
    for z0, r0, z1, r1 in pieces:
        lo, hi = min(z0, z1), max(z0, z1)
        if hi - lo < 1e-9 or not (lo <= z <= hi):
            continue
        t = (z - z0) / (z1 - z0)
        rs.append(r0 + t * (r1 - r0))
    if not rs:
        return None
    return max(rs) if outer else min(rs)


def _inside(pieces, x: float, z: float, outer: bool, tol: float = TOL, depth: float = TOL) -> bool:
    """Inside the finished part, on both sides of z: a point on a step's face (the surface jumps there) is on the
    part's surface, not in it."""
    if not pieces:
        return False
    zs = [p[0] for p in pieces] + [p[2] for p in pieces]
    if not (min(zs) + 2 * tol < z < max(zs) - 2 * tol):
        return False
    r = x / 2
    for zz in (z - tol, z + tol):
        s = _surface(pieces, zz, outer)
        if s is None or not (r < s - depth if outer else r > s + depth):
            return False
    return True


def _distance(pieces, x: float, z: float) -> float:
    """From (X diameter, Z) to the nearest point of the contour, on the radius."""
    r, best = x / 2, math.inf
    for z0, r0, z1, r1 in pieces:
        dz, dr = z1 - z0, r1 - r0
        L2 = dz * dz + dr * dr
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((z - z0) * dz + (r - r0) * dr) / L2))
        best = min(best, math.hypot(z - (z0 + t * dz), r - (r0 + t * dr)))
    return best


def _samples(seg, step: float = 0.5):
    pts = seg.polyline(16)
    for (xa, za), (xb, zb) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot((xb - xa) / 2, zb - za) / step))
        for k in range(1, n + 1):
            t = k / n
            yield xa + t * (xb - xa), za + t * (zb - za)


def _wedge(tool) -> tuple[float, float] | None:
    """The tool's body as an angle range from +Z towards +X (degrees, 0..360), from its clearance angles."""
    if tool is None or tool.type in ("groove", "drill", "thread") or tool.front_angle == tool.rear_angle:
        return None
    a, b = (tool.front_angle % 360, tool.rear_angle % 360)
    return (min(a, b), max(a, b))


def check(project, preview, library=None) -> list[Finding]:
    from .checks import _tool  # the part's tools, the library, ALPHA-T's standard tools

    if preview is None or preview.program is None:
        return []
    found: list[Finding] = []
    seen: set[tuple] = set()

    def add(op_index, kind, message, x, z):
        if (op_index, kind) not in seen:  # the first place per operation and kind is enough to look at
            seen.add((op_index, kind))
            found.append(Finding(op_index, kind, message, x, z))

    ops = project.operations
    k = 1 / 25.4 if project.units == "inch" else 1.0
    billet = project.billet
    tol = TOL * k
    profiles = {}
    depth = {}  # how far the imaginary tip may lie inside the drawn contour: on tapers and arcs the tip of a
    # radius-compensated path is up to the nose radius inside, while the nose itself only touches
    for i, op in enumerate(ops):
        t = _tool(project, getattr(op, "tool", 0), library) if getattr(op, "tool", None) else None
        depth[i] = tol + (t.nose_radius * k if t else 0.0)
        # the face clear's contour is the face it stops at, not a part outline: no gouge test for it
        if getattr(op, "contour", None) and type(op).__name__ in ("AreaClear", "FinishPass"):
            profiles[i] = (_profile(project, op.contour), getattr(op, "external", True))
    for i, op in enumerate(ops):
        name = type(op).__name__
        if name not in ("AreaClear", "FaceClear"):
            continue
        t = _tool(project, op.tool, library)
        if t is None or t.type in ("groove", "drill", "thread"):
            continue
        external = getattr(op, "external", True)
        if not tip_angle_ok(t.front_angle, t.rear_angle, external, op.towards, name == "FaceClear"):
            side = tr("face clear") if name == "FaceClear" else tr("external") if external else tr("internal")
            way = tr("towards the chuck") if op.towards else tr("away from the chuck")
            add(i, "tip", tr("T{n}'s angles (front {f:g}°, rear {r:g}°) don't suit this cut ({side}, {way}): "
                             "ALPHA-T refuses it with Invalid tip angle", n=op.tool, f=t.front_angle,
                             r=t.rear_angle, side=side, way=way), op.start.x, op.start.z)
    for i, op in enumerate(ops):  # walls ALPHA-T's collision check will not follow
        # the finish pass makes the final shape (the area clear leaves the wall to it anyway)
        if type(op).__name__ != "FinishPass" or not op.external or op.face:
            continue
        t = _tool(project, op.tool, library)
        angle = collision_angle(t, op)
        if angle is None:
            continue
        tan = math.tan(math.radians(angle))
        try:
            drawn = chain(project.contour(op.contour))
        except (ValueError, KeyError):
            continue
        for el in drawn:
            drop = (el.a.x - el.b.x) / 2
            if not el.is_arc and drop > 1e-6 and drop > tan * abs(el.a.z - el.b.z) + 1e-6:
                add(i, "uncut", tr("T{n} cannot follow the wall down to X{x:g} Z{z:g}: it is steeper than the "
                                   "tool's {a:g}° rear clearance, so material stays there (as in ALPHA-T)",
                                   n=op.tool, x=round(el.b.x, 3), z=round(el.b.z, 3), a=angle), el.b.x, el.b.z)
                break
    tools = {}
    for seg in segments(preview):
        i = seg.op_index
        if seg.kind is MoveKind.DWELL:
            continue
        for x, z in _samples(seg):
            if z < -billet.length - tol:
                add(i, "chuck", tr("moves into the chuck jaws (Z{z:g}, behind the billet length {l:g})",
                                   z=round(z, 3), l=billet.length), x, z)
                break
            if getattr(billet, "tailstock", False) and z > -tol:
                # the live centre: its point sits in a centre hole (about 3 mm at the face), then the 60° cone and
                # the quill; facing down to X0 is not possible with it
                cone = max(3 * k, 6 * k * min(z / (6 * 1.732 * k), 1.0)) if z < 6 * 1.732 * k else 12 * k
                if abs(x) / 2 < cone - tol and z < (6 * 1.732 + 30) * k:
                    add(i, "tailstock", tr("moves into the tailstock centre (X{x:g} Z{z:g})", x=round(x, 3),
                                           z=round(z, 3)), x, z)
                    break
            if i in profiles:
                pieces, outer = profiles[i]
                if _inside(pieces, x, z, outer, tol, depth.get(i, tol)):
                    if seg.kind is MoveKind.RAPID:
                        add(i, "rapid", tr("a rapid move passes through the part at X{x:g} Z{z:g}", x=round(x, 3),
                                           z=round(z, 3)), x, z)
                    else:
                        add(i, "gouge", tr("cuts into the part at X{x:g} Z{z:g}", x=round(x, 3), z=round(z, 3)),
                            x, z)
                    break
        # tool angle: the surface a feed move leaves behind must stay out of the tool's body. Only moves along the
        # part's contour leave a surface: lead-ins and pull-offs run in air
        if seg.kind in (MoveKind.FEED, MoveKind.ARC_CW, MoveKind.ARC_CCW) and i in profiles:
            number = getattr(ops[i], "tool", None)
            if number is None:
                continue
            if number not in tools:
                tools[number] = _wedge(_tool(project, number, library))
            wedge = tools[number]
            if wedge is None:
                continue
            pieces = profiles[i][0]
            t = _tool(project, number, library)
            near = max(getattr(ops[i], "offset_x", 0.0), getattr(ops[i], "offset_z", 0.0), 0.0) \
                + (t.nose_radius * k if t else 0.0) + 0.05 * k
            pts = seg.polyline(16)
            for (xa, za), (xb, zb) in zip(pts, pts[1:]):
                dr, dz = (xb - xa) / 2, zb - za
                if math.hypot(dr, dz) < 0.05:
                    continue
                if any(_distance(pieces, xx, zz) > near for xx, zz in ((xa, za), (xb, zb), ((xa + xb) / 2, (za + zb) / 2))):
                    continue  # not along the contour
                behind = math.degrees(math.atan2(-dr, -dz)) % 360
                lo, hi = wedge
                if lo + DIVE_SLACK < behind < hi - DIVE_SLACK:
                    dive = math.degrees(math.atan2(abs(dr), abs(dz)))
                    add(i, "angle", tr("dives at {a:.0f}°, steeper than T{n} clears",
                                       a=dive, n=number), xb, zb)
                    break
    order = {"chuck": 0, "tailstock": 1, "gouge": 2, "rapid": 3, "tip": 4, "uncut": 5, "angle": 6}  # the worst first: the banner shows it
    found.sort(key=lambda f: order.get(f.kind, 9))
    return found
