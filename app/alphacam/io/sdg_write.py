"""Write ALPHA-T .SDG drawings (and the matching empty .SDT text table), so ALPHA-T can open parts made here.

The layout is the one io/sdg.py reads (docs/RE_NOTES.md Progress 8 and 11), all values in mm:
  0x0000  segment table: word 0x3000 | segments, word points, then (start, end, centre) int16 per segment; a line
          stores -(i + 1) for its start and end points and 0 as the centre, an arc the centre as +(i + 1) for
          CCW and -(i + 1) for CW. The 4 bytes at 0x1FC and every 0x100 after it are skipped.
  0x0C00  float32 A[768] = Z + Zref           0x1800  float32 B[768] = X / 2 + Xref / 2
  0x2400  word 0x0400 (constant)
  0x2700  the same three blocks for the billet and chuck layer (segment centre word 0x7000): billet outline on
          both sides from Z0 to -(length + 20), and the jaws in the last 20 mm (6, 7, 7 long, dia +10/+20/+30)
  0x4B00  a third layer's table, empty (word 0x3000)
  The rest of the 0x2700 and 0x4B00 tables after the last entry holds leftover screen memory in ALPHA-T's files
  (menu texts with their colour bytes, the same in every sample); it is not part of the drawing and is written as
  zeros here.
  0x5700  settings float32: [0]/[11] screen scale, [1]/[2] view origin, [3] Zref = billet length + 20, [4] Xref/2,
          [5] units (0 mm, 1 inch), [6] billet length, [7] billet diameter, [8] first tool, [9]/[10] tool start
          (dia + 20, 10), [13]/[14]/[15] view limits.
Every sample's geometry, billet layer and settings come back byte for byte (tests/test_sdg_write.py); the view
values are ALPHA-T's for a fresh drawing (a sample that was zoomed or panned keeps its own)."""

from __future__ import annotations

import struct
from pathlib import Path

from ..i18n import tr
from ..model.geometry import Arc, Line, Point
from ..model.project import Project

SIZE = 0x6F00  # 28416
SDT_SIZE = 0x900  # 2304
XREF2 = 81.69014739990234  # G92 X163.3803 / 2, fixed
MM_PER_INCH = 25.4
MAX_POINTS = 768
SCREEN_W = 534.0  # the view fits Z into this many pixels ...
FIT = 425.0  # ... and the larger of the Z and X extents into this many



def _table_offsets(base: int) -> list[int]:
    """Byte offsets of the int16 words of a segment table, skipping the 4-byte markers."""
    offs, pos = [], 0
    while pos < 0xC00:
        if pos == 0x1FC or (pos > 0x1FC and (pos - 0x1FC) % 0x100 == 0):
            pos += 4
            continue
        offs.append(base + pos)
        pos += 2
    return offs


def _layer(buf: bytearray, table: int, a_at: int, b_at: int, points: list[tuple[float, float]],
           segs: list[tuple[int, int, int]], zref: float) -> None:
    if len(points) > MAX_POINTS:
        raise ValueError(tr("the drawing has too many points for ALPHA-T ({n} > {max})", n=len(points), max=MAX_POINTS))
    words = [0x3000 | len(segs), len(points)] + [w for s in segs for w in s]
    offs = _table_offsets(table)
    if len(words) > len(offs):
        raise ValueError(tr("the drawing has too many elements for ALPHA-T"))
    for off, w in zip(offs, words):
        struct.pack_into("<h", buf, off, w)
    for i, (x_mm, z_mm) in enumerate(points):
        struct.pack_into("<f", buf, a_at + 4 * i, z_mm + zref)
        struct.pack_into("<f", buf, b_at + 4 * i, x_mm / 2 + XREF2)


def _billet_layer(dia: float, length: float) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """Billet outline and stepped chuck jaws, in ALPHA-T's point and segment order (AREA1_metric)."""
    zc = -(length + 20)
    pts = [(-dia, zc), (-dia, 0.0), (dia, zc), (dia, 0.0)]
    for side in (1, -1):
        jaw = [(side * (dia + 30), zc), (side * (dia + 30), zc + 6), (side * (dia + 20), zc + 6),
               (side * (dia + 20), zc + 13), (side * (dia + 10), zc + 13), (side * (dia + 10), zc + 20),
               (side * dia, zc + 20)]
        pts += jaw
    idx = lambda k: -(k + 1)  # noqa: E731
    segs = [(idx(1), idx(0)), (idx(0), idx(2)), (idx(2), idx(3)), (idx(3), idx(1)), (idx(2), idx(4))]
    segs += [(idx(k), idx(k + 1)) for k in range(4, 10)]
    segs += [(idx(0), idx(11))] + [(idx(k), idx(k + 1)) for k in range(11, 17)]
    return pts, [(s, e, 0x7000) for s, e in segs]


def write_sdg(project: Project) -> bytes:
    scale = MM_PER_INCH if project.units == "inch" else 1.0
    dia, length = project.billet.diameter * scale, project.billet.length * scale
    zref = length + 20
    buf = bytearray(SIZE)

    # part geometry: points numbered in order of first use, shared between the elements that meet there (so the
    # contour stays connected); a drawing drawn as a chain gets ALPHA-T's own numbering
    pts: list[tuple[float, float]] = []

    def index(p: Point) -> int:
        q = (round(p.x * scale, 6), round(p.z * scale, 6))
        if q not in pts:
            pts.append(q)
        return pts.index(q)

    segs = []
    for g in project.geometry:  # ALPHA-T numbers a new element's end point before its start point
        if not isinstance(g, (Line, Arc)):
            continue
        e, s = index(g.b), index(g.a)
        if isinstance(g, Arc):
            c = index(g.centre) + 1
            segs.append((-(s + 1), -(e + 1), c if g.ccw else -c))
        else:
            segs.append((-(s + 1), -(e + 1), 0))
    _layer(buf, 0x0000, 0x0C00, 0x1800, pts, segs, zref)
    struct.pack_into("<h", buf, 0x2400, 0x0400)
    bpts, bsegs = _billet_layer(dia, length)
    _layer(buf, 0x2700, 0x3300, 0x3F00, bpts, bsegs, zref)
    struct.pack_into("<h", buf, 0x4B00, 0x3000)  # a third layer, always empty

    lo, hi = XREF2 - (dia + 30) / 2, XREF2 + (dia + 30) / 2
    zspan = zref + 10
    fit = FIT / max(hi - lo, zspan)
    settings = [fit, -(SCREEN_W / fit - zspan) / 2, lo - 5 / fit, zref, XREF2, 1.0 if project.units == "inch" else 0.0,
                length, dia, float(project.first_tool), dia + 20, 10.0, fit, 0.0, lo, zspan, hi]
    struct.pack_into("<16f", buf, 0x5700, *settings)
    return bytes(buf)


def write_sdt(project: Project) -> bytes:
    """The text table ALPHA-T writes for a drawing without text (texts stay in the .alcam part): word 0 = the part's
    element count, word 1 = the billet layer's (18), and constant words at 0x80 / 0xCC (every sample)."""
    buf = bytearray(SDT_SIZE)
    n = sum(1 for g in project.geometry if isinstance(g, (Line, Arc)))
    struct.pack_into("<hh", buf, 0, n, 18)
    struct.pack_into("<hhh", buf, 0x80, 112, 40, 76)
    struct.pack_into("<h", buf, 0xCC, 12)
    return bytes(buf)


def save_sdg(project: Project, path: str | Path) -> list[Path]:
    """Write NAME.SDG and an empty NAME.SDT next to it; returns the files written."""
    path = Path(path)
    sdg = path.with_suffix(".SDG") if path.suffix.lower() != ".sdg" else path
    sdg.write_bytes(write_sdg(project))
    sdt = sdg.with_suffix(".SDT" if sdg.suffix == ".SDG" else ".sdt")
    sdt.write_bytes(write_sdt(project))
    return [sdg, sdt]
