"""Face clear (#127=8): radial passes at constant Z, cutting towards the axis (FACE sample).

Rules from FACE_metric (MET2 profile, depth 2, retract 0.2):
  * the profile's faces (constant-Z elements) split it into zones; each zone is cut from its top face down in steps
    of the depth of cut, the last pass clamped onto the next face (Z-2 ... -24, -25, -27 ... -49, -50);
  * pass: rapid to (start X, Z), feed to the profile, 45-degree pull-off (-2R in X, +R in Z, R = retract),
    rapid to (start X - 4R, Z + 3R); after the last pass, rapid to the start. The last rapid matches the sample but
    could equally be (start X - 2r, pull-off Z + r) with nose radius r = 0.4 = 2R: one sample cannot tell;
  * the cut ends on the vertical element spanning Z, half-open towards the face below (lo <= Z < hi)."""

from __future__ import annotations

import math

from ..model.operations import FaceClear
from .contour import Seg, mm, tip_path
from .toolpath import Move, MoveKind, Pass, Toolpath

RETRACT_MM = 0.2  # face clear default (SD1 R0.2)


def _end_x(boundary: list[Seg], z: float) -> float:
    hits = [s.a.x for s in boundary if s.a.x == s.b.x and min(s.a.z, s.b.z) <= z < max(s.a.z, s.b.z)]
    if not hits:
        raise ValueError(f"face clear: no profile at Z{z:g}")
    return max(hits)


def face_clear(op: FaceClear, path: list[Seg], nose_radius: float, units: str, decimals: int) -> Toolpath:
    if op.offset_x or op.offset_z or op.pre_finish or not op.towards:
        raise NotImplementedError("face clear: only towards, without offsets or pre-finish (no sample)")
    boundary = tip_path(path, nose_radius)
    if any(s.is_arc or (s.a.x != s.b.x and s.a.z != s.b.z) for s in boundary):
        raise NotImplementedError("face clear: only profiles of faces and diameters (no sample with slopes/arcs)")
    levels = sorted({s.a.z for s in boundary if s.a.z == s.b.z}, reverse=True)
    r = round(mm(RETRACT_MM, units), decimals)
    sx, d = op.start.x, op.depth_of_cut
    passes = []
    for top, bottom in zip(levels, levels[1:]):
        for k in range(1, math.ceil(round((top - bottom) / d, 9)) + 1):
            z = round(max(top - k * d, bottom), 9)
            x = _end_x(boundary, z)
            passes.append(Pass([
                Move(MoveKind.RAPID, sx, z),
                Move(MoveKind.FEED, x, z, op.feed),
                Move(MoveKind.FEED, x - 2 * r, z + r, op.feed),
                Move(MoveKind.RAPID, sx - 4 * r, z + 3 * r),
            ]))
    return Toolpath(passes, retract=[Move(MoveKind.RAPID, sx, op.start.z)])
