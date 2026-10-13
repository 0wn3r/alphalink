"""Read ALPHA-T / ALPHACAM tool libraries (ATOOLIB.TL1, also the ATTOOLS working copy WORKLIB.TL1).

256-byte records; record 0 is a header (first int16 = number of slots), record n is tool n:
  name[20] (space padded) · float32 nose radius · int16 front clearance angle · int16 rear clearance angle ·
  description text (space/NUL padded). Empty names are free slots. (RE_NOTES Progress 2 and 12.)
The .TL2 / .IM files hold the tool outlines and are not needed here."""

from __future__ import annotations

import struct
from pathlib import Path

from ..model.tools import Tool, ToolLibrary, tool_from_record

RECORD = 256


def read_tl1(data: bytes) -> ToolLibrary:
    if len(data) < 2 * RECORD or len(data) % RECORD:
        raise ValueError(f"not a TL1 tool library ({len(data)} bytes)")
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
