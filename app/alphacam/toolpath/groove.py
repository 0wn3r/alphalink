"""Grooving (#127=9), external, area mode: radial plunges along the profile (GROOVE sample).

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

from ..model.operations import Groove
from .contour import Seg, mm, points
from .toolpath import Move, MoveKind, Pass, Toolpath

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
        raise ValueError(f"grooving: no profile under Z{z0:g}..{z1:g}")
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


def groove(op: Groove, path: list[Seg], nose_radius: float, units: str, decimals: int) -> Toolpath:
    if (op.datum != "left" or not op.external or not op.front or op.z_shift or op.repeat
            or op.mode != "area" or op.dwell_revs or op.finish_offset_x or op.finish_offset_z):
        raise NotImplementedError("grooving: only external area grooving with default options (no sample)")
    # The drawn profile, not the tip path: a groove's -Z-facing wall would move by 2r with the (-r, -r) tip shift,
    # and the sample (made with the 0.4R tool 3) plunges against the drawn walls. Grooving tools have r = 0 anyway.
    boundary = path
    zs = [p.z for p in points(boundary)]
    top, end = max(zs), min(zs)
    w, step = op.tip_width, op.stepover
    walls = _left_walls(boundary)
    back = round(mm(RETRACT_DIA_MM, units), decimals)
    sx = op.start.x
    moves = []
    z = round(top - w, 9)
    while True:
        x = _depth(boundary, z, z + w)
        moves.append(Move(MoveKind.RAPID, sx, z))
        moves += _pecks(op, sx, x, z, back) if op.peck_depth > 0 else []
        if op.peck_depth > 0 and op.full_retract:
            last = _peck_xs(op, sx, x)[-1] if _peck_xs(op, sx, x) else sx
            moves += [Move(MoveKind.RAPID, last + 2 * op.peck_depth, z), Move(MoveKind.FEED, x, z, op.feed),
                      Move(MoveKind.FEED, x + 2 * op.peck_depth, z, op.feed)]
        else:
            moves += [Move(MoveKind.FEED, x, z, op.feed), Move(MoveKind.FEED, x + back, z, op.feed)]
        moves.append(Move(MoveKind.RAPID, sx, z))
        if z <= end:
            break
        nz = round(max(z - step, end), 9)
        skipped = [e for e in walls if nz < e < z]
        z = max(skipped) if skipped else nz
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
