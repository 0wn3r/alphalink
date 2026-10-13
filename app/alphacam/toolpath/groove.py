"""Grooving (#127=9, finish mode 17): radial plunges along the profile, external and internal, and face grooving
(face_groove, FRONT off). The rules below are the external area mode (GROOVE sample).

Rules from GROOVE_metric (stepover 2, tip width 3, datum left):
  * the tool reference is the tip's left (-Z) corner, so a plunge at Z covers Z .. Z + tip width;
  * plunges start at (profile top Z - tip width) and step by the stepover; a plunge is inserted exactly on a
    left wall (where the profile gets shallower going -Z, e.g. Z-30) when a step would jump over it, and the last
    one is clamped to the profile end;
  * each plunge goes to the highest profile diameter under the tip (elements touching only at the tip's edges do
    not count), backs off 2 mm on diameter, and rapids out to the start X; after the last, rapid to the start;
  * with a peck depth the plunge is preceded by pecks every peck depth (see _pecks); FULL RET YES retracts out
    to the start X after each peck."""

from __future__ import annotations

from ..i18n import tr
from ..model.operations import Groove
from ..model.geometry import Point
from .contour import Seg, mm, points
from .toolpath import MAX_PASSES, Move, MoveKind, Pass, Toolpath, too_many

RETRACT_DIA_MM = 2.0


def _depth(boundary: list[Seg], z0: float, z1: float) -> float:
    xs = []
    for s in boundary:
        lo, hi = sorted((s.a.z, s.b.z))
        if lo == hi:
            if z0 < lo < z1:
                xs.append(max(s.a.x, s.b.x))
        elif min(hi, z1) - max(lo, z0) > 1e-9:
            xs.append(max(s.a.x, s.b.x))
    if not xs:
        raise ValueError(tr("grooving: no profile under Z{z0}..{z1}", z0=f"{z0:g}", z1=f"{z1:g}"))
    return max(xs)


def _left_walls(boundary: list[Seg]) -> list[float]:
    """Face Zs where the profile is shallower (larger X) just below than just above."""
    def band(z0: float, z1: float) -> float | None:
        xs = [max(s.a.x, s.b.x) for s in boundary
              if s.a.z != s.b.z and min(max(s.a.z, s.b.z), z1) - max(min(s.a.z, s.b.z), z0) > 1e-9]
        return max(xs) if xs else None

    walls = []
    for s in boundary:
        if s.a.z == s.b.z and not s.is_arc:
            above, below = band(s.a.z, s.a.z + 1e-3), band(s.a.z - 1e-3, s.a.z)
            if above is not None and below is not None and below > above:
                walls.append(s.a.z)
    return walls


def _right_walls(boundary: list[Seg]) -> list[float]:
    """Face Zs where the profile gets deeper (smaller X) just below than just above: a plunge is inserted at
    wall - tip width when a step would jump over it (GROOVE_finishoffset: -22.8 -> -23.2 -> -25.2)."""
    def band(z0: float, z1: float) -> float | None:
        xs = [max(s.a.x, s.b.x) for s in boundary
              if s.a.z != s.b.z and min(max(s.a.z, s.b.z), z1) - max(min(s.a.z, s.b.z), z0) > 1e-9]
        return max(xs) if xs else None

    walls = []
    for s in boundary:
        if s.a.z == s.b.z and not s.is_arc:
            above, below = band(s.a.z, s.a.z + 1e-3), band(s.a.z - 1e-3, s.a.z)
            if above is not None and below is not None and below < above:
                walls.append(s.a.z)
    return walls


def _allowance(path: list[Seg], fx: float, fz: float) -> list[Seg]:
    """FINISH OFFSET X/Z (GROOVE_finishoffset, U0.5 W0.2): leave material on every side of the drawn profile:
    diameters move out by 2 fx, faces move by fz towards the open side (the front face and shoulders +Z, a groove's
    +Z wall -Z), so the groove gets narrower and shallower. Corners are the crossings of the moved elements; the
    two ends move only across their own element."""
    if any(s.is_arc or (s.a.x != s.b.x and s.a.z != s.b.z) for s in path):
        raise NotImplementedError(tr("grooving finish offsets: only faces and diameters (no sample with slopes/arcs)"))
    lines = []  # ("x", value) for a diameter, ("z", value) for a face
    for s in path:
        if s.a.z == s.b.z:  # face: going up (x grows) has the material on the -Z side, so it moves +Z
            lines.append(("z", s.a.z + (fz if s.b.x > s.a.x else -fz)))
        else:
            lines.append(("x", s.a.x + 2 * fx))

    def corner(l1, l2, fallback: Point) -> Point:
        x = next((v for k, v in (l1, l2) if k == "x"), fallback.x)
        z = next((v for k, v in (l1, l2) if k == "z"), fallback.z)
        return Point(round(x, 9), round(z, 9))

    pts = [corner(lines[0], lines[0], path[0].a)]
    pts += [corner(lines[i], lines[i + 1], path[i].b) for i in range(len(path) - 1)]
    pts.append(corner(lines[-1], lines[-1], path[-1].b))
    return [Seg(a, b) for a, b in zip(pts, pts[1:])]


def _mirror(path: list[Seg]) -> list[Seg]:
    return [Seg(Point(-s.a.x, s.a.z), Point(-s.b.x, s.b.z)) for s in path]


def groove(op: Groove, path: list[Seg], nose_radius: float, units: str, decimals: int) -> Toolpath:
    if not op.front:
        return face_groove(op, path, units, decimals)
    if op.mode == "finish":
        if op.finish_offset_x or op.finish_offset_z or not op.external:
            raise NotImplementedError(tr("grooving finish mode: only external, no finish offsets (no sample)"))
        return groove_finish(op, path)
    if not op.external and op.peck_depth > 0:
        raise NotImplementedError(tr("internal grooving with pecking (no sample)"))
    # The drawn profile, not the tip path: a groove's -Z-facing wall would move by 2r with the (-r, -r) tip shift,
    # and the sample (made with the 0.4R tool 3) plunges against the drawn walls. Grooving tools have r = 0 anyway.
    boundary = _allowance(path, op.finish_offset_x, op.finish_offset_z) \
        if op.finish_offset_x or op.finish_offset_z else path
    # internal (GROOVE_internal, tool 14): the same rules with X mirrored: plunges go out from the start X to the
    # nearest wall of the bore under the tip and back off 2 towards the axis
    side = 1 if op.external else -1
    if side < 0:
        boundary = _mirror(boundary)
    zs = [round(p.z, 9) for p in points(boundary)]  # z is rounded below; unrounded ends could never be reached
    top, end = max(zs), min(zs)
    w, step = op.tip_width, op.stepover
    walls = _left_walls(boundary)
    right = [round(e - w, 9) for e in _right_walls(boundary)]
    back = round(mm(RETRACT_DIA_MM, units), decimals)
    sx = op.start.x
    # plunge positions for the tip's -Z corner, then the reference corner: DATUM RIGHT is the +Z corner
    # (GROOVE_datumright: every plunge 3 = tip width further +Z)
    plunges = []
    z = round(top - w, 9)
    while True:
        plunges.append((z, side * _depth(boundary, z, z + w)))
        if len(plunges) > MAX_PASSES:
            raise too_many(tr("grooving"))
        if z <= end:
            break
        nz = round(max(z - step, end), 9)
        skipped = [e for e in walls + right if nz < e < z]
        z = max(skipped) if skipped else nz
    if op.datum == "right":
        plunges = [(round(z + w, 9), x) for z, x in plunges]
    # Z-SHIFT / REPEATS: each repeat is another pass, the same plunges shifted by z_shift (GROOVE_zshift10_rep1)
    passes = []
    for k in range(op.repeat + 1):
        moves = []
        for z, x in plunges:
            z = round(z + k * op.z_shift, 9)
            moves.append(Move(MoveKind.RAPID, sx, z))
            moves += _pecks(op, sx, x, z, back) if op.peck_depth > 0 else []
            if op.peck_depth > 0 and op.full_retract:
                last = _peck_xs(op, sx, x)[-1] if _peck_xs(op, sx, x) else sx
                moves += [Move(MoveKind.RAPID, last + 2 * op.peck_depth, z), Move(MoveKind.FEED, x, z, op.feed)]
                bottom_back = x + 2 * op.peck_depth
            else:
                moves.append(Move(MoveKind.FEED, x, z, op.feed))
                bottom_back = x + side * back
            if op.dwell_revs:  # GROOVE_dwell2: G4 at the bottom of every plunge
                moves.append(Move(MoveKind.DWELL, x, z, dwell_revs=op.dwell_revs))
            moves += [Move(MoveKind.FEED, bottom_back, z, op.feed), Move(MoveKind.RAPID, sx, z)]
        passes.append(Pass(moves))
    return Toolpath(passes, retract=[Move(MoveKind.RAPID, sx, op.start.z)], plunges=len(plunges))


def _face_depth(boundary: list[Seg], r0: float, r1: float) -> float:
    """The shallowest (largest) Z of the profile under the tip's radial band r0..r1 (radius); elements touching
    the band only at its edges do not count."""
    zs = []
    for s in boundary:
        lo, hi = sorted((s.a.x / 2, s.b.x / 2))
        if lo == hi:
            if r0 < lo < r1:
                zs.append(max(s.a.z, s.b.z))
        elif min(hi, r1) - max(lo, r0) > 1e-9:
            zs.append(max(s.a.z, s.b.z))
    if not zs:
        raise ValueError(tr("face grooving: no profile under X{x0}..{x1}", x0=f"{2 * r0:g}", x1=f"{2 * r1:g}"))
    return max(zs)


def face_groove(op: Groove, path: list[Seg], units: str, decimals: int) -> Toolpath:
    """FACE grooving (ALPHA-T's FRONT -> FACE toggle, SD1 L210; GROOVE_face: a groove X30..36 drawn 5 deep into the
    front face, tip width 2, stepover 1): plunges along -Z, stepping outwards in X.
      * DATUM BTM (the default; stored as datum "left"): the reference is the tip's corner nearest the axis, so a
        plunge at X covers X .. X + 2 * tip width on diameter;
      * plunges start at the profile's smallest diameter and step out by the stepover (radius), the last one clamped
        to the largest diameter - 2 * tip width;
      * each plunge feeds from the start Z to the shallowest profile Z under the tip, backs off 1 mm in +Z (the
        radial 2 mm on diameter), and rapids out to the start Z; after the last, rapid to the start X.
    GROOVE_back (an older build's FRONT -> BACK, the whole turned profile picked) is the same cycle with the
    whole profile as the groove. DATUM TOP shifts the reference to the outer corner (by symmetry, no sample)."""
    if not op.external or op.peck_depth > 0 or op.z_shift or op.repeat or op.mode == "finish" \
            or op.finish_offset_x or op.finish_offset_z:
        raise NotImplementedError(tr("face grooving: only external, area mode, without pecks, X shift or finish "
                                  "offsets (no sample)"))
    rs = [p.x / 2 for p in points(path)]
    lo, hi = min(rs), max(rs)
    w, step = op.tip_width, op.stepover
    back = round(mm(RETRACT_DIA_MM, units) / 2, decimals)
    sz = op.start.z
    plunges = []
    r = lo
    while True:
        r = round(min(r, hi - w), 9)
        plunges.append((r, _face_depth(path, r, r + w)))
        if len(plunges) > MAX_PASSES:
            raise too_many(tr("face grooving"))
        if r >= hi - w - 1e-9:
            break
        r = round(r + step, 9)
    moves = []
    for r, z in plunges:
        x = round(2 * (r + w if op.datum == "right" else r), 9)
        moves.append(Move(MoveKind.RAPID, x, sz))
        moves.append(Move(MoveKind.FEED, x, z, op.feed))
        if op.dwell_revs:
            moves.append(Move(MoveKind.DWELL, x, z, dwell_revs=op.dwell_revs))
        moves += [Move(MoveKind.FEED, x, round(z + back, 9), op.feed), Move(MoveKind.RAPID, x, sz)]
    return Toolpath([Pass(moves)], retract=[Move(MoveKind.RAPID, op.start.x, sz)], plunges=len(plunges))


def groove_finish(op: Groove, path: list[Seg]) -> Toolpath:
    """FINISH mode (#127=17, GROOVE_finishmode): one pass along the drawn profile. The tip covers Z .. Z + tip width
    (datum left), so the vertices of a wall where the profile steps down (the groove's +Z wall) move by -tip width;
    datum right covers Z - tip width .. Z and moves the walls where it steps up by +tip width instead (assumed by
    symmetry, no sample). Rapid to (start X, first Z), feed along the profile, feed out to the start X."""
    if any(s.is_arc for s in path):
        raise NotImplementedError(tr("grooving finish: lines only (no sample with arcs)"))
    w = op.tip_width
    pts = points(path)
    shift = [0.0] * len(pts)
    for i, s in enumerate(path):
        if s.a.x != s.b.x:  # faces and slopes (GROOVE_finishmode_slope: both ends of the 45-degree side move)
            down = s.b.x < s.a.x
            if op.datum == "left" and down:
                shift[i] = shift[i + 1] = -w
            elif op.datum == "right" and not down:
                shift[i] = shift[i + 1] = w
    zs = [round(p.z + d, 9) for p, d in zip(pts, shift)]
    sx = op.start.x
    moves = [Move(MoveKind.RAPID, sx, zs[0])]
    moves += [Move(MoveKind.FEED, p.x, z, op.feed) for p, z in zip(pts, zs)]
    moves.append(Move(MoveKind.FEED, sx, zs[-1], op.feed))
    return Toolpath([Pass(moves)], retract=[Move(MoveKind.RAPID, sx, op.start.z)])


def _peck_xs(op: Groove, sx: float, depth: float) -> list[float]:
    """Peck diameters: one peck (radius) deeper each time, while not below the bottom (PECK_groove: 116 ... 40)."""
    step = 2 * op.peck_depth
    xs, k = [], 1
    while round(sx - k * step, 9) >= depth - 1e-9:
        xs.append(round(sx - k * step, 9))
        k += 1
    return xs


def _pecks(op: Groove, sx: float, depth: float, z: float, back: float) -> list[Move]:
    """Pecks before the final plunge. FULL RET NO: back off `back` (2 mm dia) after each peck. FULL RET YES
    (PECK_fullret_groove): back off one peck, rapid out to the start X, rapid in to one peck above the last."""
    step = 2 * op.peck_depth
    moves = []
    for n, x in enumerate(_peck_xs(op, sx, depth), 1):
        if not op.full_retract:
            moves += [Move(MoveKind.FEED, x, z, op.feed), Move(MoveKind.FEED, x + back, z, op.feed)]
            continue
        if n > 1:
            moves.append(Move(MoveKind.RAPID, x + 2 * step, z))
        moves += [Move(MoveKind.FEED, x, z, op.feed), Move(MoveKind.FEED, x + step, z, op.feed),
                  Move(MoveKind.RAPID, sx, z)]
    return moves
