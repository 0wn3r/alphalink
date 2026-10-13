"""Face clear (#127=8): radial passes at constant Z, cutting towards the axis (FACE sample).

Rules from FACE_metric (MET2 profile, depth 2, retract 0.2):
  * the profile's faces (constant-Z elements) split it into zones; each zone is cut from its top face down in steps
    of the depth of cut, the last pass clamped onto the next face (Z-2 ... -24, -25, -27 ... -49, -50);
  * pass: rapid to (start X, Z), feed to the profile, 45-degree pull-off (-2R in X, +R in Z, R = retract),
    rapid to (start X - 2r, pull-off Z + r) with the nose radius r (FACE_tool1: X118.4 for the 0.8R rougher, X119.2
    for the 0.4R finisher); after the last pass, rapid to the start;
  * the cut ends on the element spanning Z, half-open towards the face below (lo <= Z < hi); on slopes and arcs
    (FACE_slope, FACE_arcs) at the tip path's diameter at Z. ALPHA-T's values there differ from the exact ones by
    up to 0.001 on diameter, either way (a numeric search, presumably), so those two samples are known diffs.

Area clear with FACE instead of FRONT (AREA_front_face, SD1 E101) posts the same passes as #127=8, but with the
area clear retract (1 mm) and the pull-off going outwards: (+2R in X, +R in Z)."""

from __future__ import annotations

from ..i18n import tr
import math

from ..model.operations import FaceClear
from .contour import Seg, mm, tip_path, translate, x_at
from .toolpath import MAX_PASSES, Move, MoveKind, Pass, Toolpath, too_many

RETRACT_MM = 0.2  # face clear default (SD1 R0.2)


def _end_x(boundary: list[Seg], z: float, external: bool = True) -> float:
    hits = [x_at(s, z) for s in boundary if s.a.z != s.b.z and min(s.a.z, s.b.z) <= z < max(s.a.z, s.b.z)]
    if not hits:
        raise ValueError(tr("face clear: no profile at Z{z}", z=f"{z:g}"))
    return max(hits) if external else min(hits)


def face_clear(op: FaceClear, path: list[Seg], nose_radius: float, units: str, decimals: int,
               retract_mm: float = RETRACT_MM, outward: bool = False) -> Toolpath:
    """Internal work (area clear FACE on a bore, AREA_face_internal) mirrors X: passes feed outwards from the start X
    to the bore wall, the pull-off and the rapid move towards the axis. A zone also starts at the top of the profile
    when that is not a face (a bore starting at Z0)."""
    if not op.towards:
        # FACE_away_tool5 / _tool3_nocheck: ALPHA-T writes a single pass at Z0, still feeding towards the centre
        raise NotImplementedError(tr("face clear AWAY: ALPHA-T itself writes only one pass at Z0 for it, so the app "
                                  "does not offer it; use TOWARDS"))
    external = getattr(op, "external", True)
    side = 1 if external else -1
    # offsets translate the profile like area clear (FACE_offset: X40 -> 41, faces Z0/-25/-50 -> 0.2/-24.8/-49.8)
    boundary = translate(tip_path(path, nose_radius, external), op.offset_x, op.offset_z, external)
    levels = {s.a.z for s in boundary if s.a.z == s.b.z and not s.is_arc}
    levels.add(max(max(s.a.z, s.b.z) for s in boundary))
    levels = sorted(levels, reverse=True)
    r = round(mm(retract_mm, units), decimals)
    pull = side * (2 * r if outward else -2 * r)
    sx, d = op.start.x, op.depth_of_cut
    passes = []
    for top, bottom in zip(levels, levels[1:]):
        if len(passes) + (top - bottom) / d > MAX_PASSES:
            raise too_many(tr("face clear"))
        for k in range(1, math.ceil(round((top - bottom) / d, 9)) + 1):
            z = round(max(top - k * d, bottom), 9)
            x = _end_x(boundary, z, external)
            passes.append(Pass([
                Move(MoveKind.RAPID, sx, z),
                Move(MoveKind.FEED, x, z, op.feed),
                Move(MoveKind.FEED, x + pull, z + r, op.feed),
                Move(MoveKind.RAPID, sx - side * 2 * nose_radius, z + r + nose_radius),
            ]))
    return Toolpath(passes, retract=[Move(MoveKind.RAPID, sx, op.start.z)])
