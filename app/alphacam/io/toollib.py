"""Read ALPHA-T / ALPHACAM tool libraries (ATOOLIB.TL1, also the ATTOOLS working copy WORKLIB.TL1).

256-byte records; record 0 is a header (first int16 = number of slots), record n is tool n:
  name[20] (space padded) · float32 nose radius · int16 front clearance angle · int16 rear clearance angle ·
  description text (space/NUL padded). Empty names are free slots. (RE_NOTES Progress 2 and 12.)

ATOOLIB.TL2 holds the tool pictures ALPHA-T draws, one 780-byte record per tool (record 0 = tool 1), in the
segment/point layout of its drawings (RE_NOTES Progress 41): int16 segment count, int16 point count, then
(start, end, centre) triples with points as -(index+1) and centre 0 for a line, >0 for an anticlockwise and <0 for a
clockwise arc about point centre-1; float32 A[64] at 0x104 and B[64] at 0x208. A runs along Z and B along the radius
(X/2), in mm, with the tool's tip at (0, 0). The .IM1/.IM2 pair is ATSETUP's alternative library of the same layout."""

from __future__ import annotations

from ..i18n import tr
import math
import struct
from pathlib import Path

from ..model.tools import Tool, ToolLibrary, tool_from_record

RECORD = 256


def read_tl1(data: bytes) -> ToolLibrary:
    if len(data) < 2 * RECORD or len(data) % RECORD:
        raise ValueError(tr("not a TL1 tool library ({len_data} bytes)", len_data=len(data)))
    tools: list[Tool] = []
    for n in range(1, len(data) // RECORD):
        rec = data[n * RECORD:(n + 1) * RECORD]
        name = rec[:20].decode("latin-1").strip(" \0")
        if not name:
            continue
        (radius,) = struct.unpack_from("<f", rec, 20)
        front, rear = struct.unpack_from("<hh", rec, 24)
        desc = rec[28:].decode("latin-1").replace("\0", " ").strip()
        tools.append(tool_from_record(n, name, round(radius, 4), front, rear, desc))
    return ToolLibrary(tools)


def load_tl1(path: str | Path) -> ToolLibrary:
    return read_tl1(Path(path).read_bytes())


TL2_RECORD = 780


def read_tl2(data: bytes) -> dict[int, list[list[tuple[float, float]]]]:
    """Tool number -> the picture as polylines of (Z, radius) points in mm, tip at (0, 0); arcs sampled."""
    if len(data) < TL2_RECORD or len(data) % TL2_RECORD:
        raise ValueError(tr("not a TL2 tool picture file ({len_data} bytes)", len_data=len(data)))
    out = {}
    for i in range(len(data) // TL2_RECORD):
        rec = data[i * TL2_RECORD:(i + 1) * TL2_RECORD]
        count, npts = struct.unpack_from("<hh", rec, 0)
        if not (0 < count <= 40 and 0 < npts <= 64):
            continue
        words = struct.unpack_from(f"<{3 * count}h", rec, 4)
        a = struct.unpack_from("<64f", rec, 0x104)
        b = struct.unpack_from("<64f", rec, 0x208)
        pts = [(a[j], b[j]) for j in range(npts)]
        lines = []
        for k in range(count):
            s, e, c = words[3 * k:3 * k + 3]
            if s >= 0 or e >= 0 or -s > npts or -e > npts or abs(c) > npts:
                continue  # 0 marks a deleted segment
            p, q = pts[-s - 1], pts[-e - 1]
            if c == 0:
                lines.append([p, q])
                continue
            cz, cr = pts[abs(c) - 1]
            r = math.hypot(p[0] - cz, p[1] - cr)
            a0, a1 = math.atan2(p[1] - cr, p[0] - cz), math.atan2(q[1] - cr, q[0] - cz)
            sweep = (a1 - a0) % (2 * math.pi) if c > 0 else -((a0 - a1) % (2 * math.pi))
            steps = max(4, int(abs(sweep) / (math.pi / 18)))
            lines.append([(cz + r * math.cos(a0 + sweep * t / steps), cr + r * math.sin(a0 + sweep * t / steps))
                          for t in range(steps + 1)])
        if lines:
            out[i + 1] = [[(round(z, 4), round(x, 4)) for z, x in line] for line in lines]
    return out


def load_tl2(path: str | Path) -> dict[int, list[list[tuple[float, float]]]]:
    return read_tl2(Path(path).read_bytes())


def sibling(path: str | Path, suffix: str) -> Path | None:
    """ATOOLIB.TL1 -> ATOOLIB.TL2 next to it, whatever the case of the name."""
    p = Path(path)
    for cand in p.parent.glob("*"):
        if cand.stem.lower() == p.stem.lower() and cand.suffix.lower() == suffix.lower():
            return cand
    return None
