"""Flatten a preview into drawable/hittable segments, in program order, each tagged with its operation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from alphacam.io.prg_parse import MoveKind


@dataclass
class Segment:
    kind: MoveKind
    a: tuple[float, float]  # (X diameter, Z)
    b: tuple[float, float]
    op_index: int | None  # operation the move belongs to (None: implicit tool block / empty blocks)
    line: int  # PRG line of the move
    centre: tuple[float, float] | None = None  # arcs

    def polyline(self, steps: int = 16) -> list[tuple[float, float]]:
        """Points along the segment (arcs sampled), for hit testing."""
        if self.centre is None:
            return [self.a, self.b]
        (ax, az), (bx, bz), (cx, cz) = self.a, self.b, self.centre
        # work in (z, r) with r = X/2; G3 (ARC_CCW) is counter-clockwise with Z right and X up
        a0 = math.atan2(ax / 2 - cx / 2, az - cz)
        a1 = math.atan2(bx / 2 - cx / 2, bz - cz)
        rad = math.hypot(ax / 2 - cx / 2, az - cz)
        ccw = self.kind is MoveKind.ARC_CCW
        sweep = a1 - a0
        if ccw and sweep < 0:
            sweep += 2 * math.pi
        if not ccw and sweep > 0:
            sweep -= 2 * math.pi
        return [(2 * (cx / 2 + rad * math.sin(a0 + sweep * t / steps)), cz + rad * math.cos(a0 + sweep * t / steps))
                for t in range(steps + 1)]


def segments(preview) -> list[Segment]:
    if preview is None or preview.program is None:
        return []
    owner = {}
    for op in preview.ops:
        for b in op.blocks:
            owner[b.label] = op.op_index
    out: list[Segment] = []
    pos = None
    for block in preview.program.blocks:
        if pos is None and block.start is not None:
            pos = block.start
        for m in block.moves:
            if m.x is None or m.z is None:
                continue
            end = (m.x, m.z)
            if pos is not None and m.kind is not MoveKind.DWELL:
                centre = (m.centre_x, m.centre_z) if m.centre_x is not None else None
                out.append(Segment(m.kind, pos, end, owner.get(block.label), m.line, centre))
            elif m.kind is MoveKind.DWELL:
                out.append(Segment(m.kind, end, end, owner.get(block.label), m.line))
            pos = end
    return out
