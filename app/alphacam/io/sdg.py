"""Import ALPHA-T / ALPHACAM .SDG drawings (layout from tools/sdg_dump.py, docs/RE_NOTES.md).

Segment table at 0x0000 (word0 = 0x3000|count, word1 = points, then int16 (start, end, centre) triples; the
4-byte marker at 0x1FC and every 0x100 after it is skipped), float32 A[768] at 0x0C00 and B[768] at 0x1800.
Points are relative to the G92 reference in the settings block at 0x5700:
    Z = A - Zref        X (diameter) = 2 * (B - Xref/2)      all in mm, also in inch drawings.
Settings floats: [3] Zref, [4] Xref/2, [5] units (0 mm, 1 inch), [6] billet length, [7] billet diameter,
[8] first tool. Inch drawings are converted to inch (4 decimals)."""

from __future__ import annotations

import struct
from pathlib import Path

from ..model.geometry import Arc, Line, Point
from ..model.project import Billet, Project, Reference

MM_PER_INCH = 25.4


def _table(data: bytes, base: int) -> list[int]:
    words, pos = [], 0
    while pos < 0xC00:
        if pos == 0x1FC or (pos > 0x1FC and (pos - 0x1FC) % 0x100 == 0):
            pos += 4
            continue
        words.append(struct.unpack_from("<h", data, base + pos)[0])
        pos += 2
    return words


def read_sdg(data: bytes) -> Project:
    if len(data) < 0x5740:
        raise ValueError(f"SDG too short ({len(data)} bytes)")
    st = struct.unpack_from("<16f", data, 0x5700)
    zref, xr2 = st[3], st[4]
    units = "inch" if st[5] else "mm"
    scale = MM_PER_INCH if units == "inch" else 1.0

    words = _table(data, 0)
    count, npts = words[0] & 0xFF, words[1]
    a = struct.unpack_from("<768f", data, 0x0C00)
    b = struct.unpack_from("<768f", data, 0x1800)
    pts = [Point(round(2 * (b[i] - xr2) / scale, 4), round((a[i] - zref) / scale, 4)) for i in range(npts)]

    geometry = []
    for k in range(count):
        s, e, c = words[2 + 3 * k : 5 + 3 * k]
        start, end = pts[-s - 1], pts[-e - 1]
        if c == 0:
            geometry.append(Line(start, end, id=f"g{k}"))
        else:
            geometry.append(Arc(start, end, pts[abs(c) - 1], ccw=c > 0, id=f"g{k}"))

    return Project(
        units=units,
        billet=Billet(diameter=st[7], length=st[6]),
        reference=Reference(g92_x=round(2 * xr2, 4), g92_z=round(zref, 4)),
        first_tool=int(st[8]),
        # ALPHA-T's tool change point X100 Z50 (mm) is not in the SDG; it is the same in every sample.
        tool_change=Point(round(100 / scale, 4), round(50 / scale, 4)),
        geometry=geometry,
    )


def load_sdg(path: str | Path) -> Project:
    return read_sdg(Path(path).read_bytes())
