"""Cycle time estimate (workflow improvements, step 5): from the moves of the preview and the feeds.

The 400T turns at constant surface speed (G96) in the Harrison macros, so a feed move's time depends on the diameter:
rpm = 1000 Vc / (pi D), capped by the spindle limit. Each tool can carry its surface speed (m/min) and a spindle limit
of its own in the library; otherwise a default per tool type is used. Threads and drills run at the rpm of their
largest diameter. Tool changes are manual (the operator indexes the turret), so they are counted, and their time is an
allowance the user sets. It is an estimate: acceleration and the control's block time are left out."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .io.prg_parse import MoveKind

# m/min when a tool has none of its own (carbide in steel, on the safe side)
DEFAULT_SURFACE_SPEED = {"turn": 180.0, "bore": 140.0, "groove": 90.0, "thread": 60.0, "drill": 60.0}


@dataclass
class Machine:
    spindle_max: float = 2500.0  # rpm
    rapid: float = 6000.0  # mm/min
    tool_change: float = 30.0  # s for the operator to change a tool


@dataclass
class Estimate:
    seconds: dict[int | None, float] = field(default_factory=dict)  # operation index -> time
    tool_changes: int = 0
    machine: Machine = field(default_factory=Machine)

    @property
    def cutting(self) -> float:
        return sum(self.seconds.values())

    @property
    def total(self) -> float:
        return self.cutting + self.tool_changes * self.machine.tool_change


def surface_speed(tool) -> float:
    if tool is not None and getattr(tool, "surface_speed", 0.0) > 0:
        return tool.surface_speed
    return DEFAULT_SURFACE_SPEED.get(getattr(tool, "type", "turn"), DEFAULT_SURFACE_SPEED["turn"])


def rpm(tool, diameter_mm: float, machine: Machine) -> float:
    limit = machine.spindle_max
    if tool is not None and getattr(tool, "max_rpm", 0.0) > 0:
        limit = min(limit, tool.max_rpm)
    if diameter_mm <= 1e-6:
        return limit
    return min(1000.0 * surface_speed(tool) / (math.pi * diameter_mm), limit)


def estimate(project, preview, library=None, machine: Machine | None = None) -> Estimate:
    from .checks import _tool  # the part's tools, the library, ALPHA-T's standard tools

    machine = machine or Machine()
    est = Estimate(machine=machine)
    if preview is None or preview.program is None:
        return est
    k = 25.4 if project.units == "inch" else 1.0  # everything below in mm
    owner = {b.label: op.op_index for op in preview.ops for b in op.blocks}
    pos = None
    for block in preview.program.blocks:
        name = getattr(block, "op_name", "")
        if name == "tool_change":
            est.tool_changes += 1
        tool = _tool(project, block.tool, library) if getattr(block, "tool", None) else None
        feed = (block.feed or 0.0) * k  # mm/rev (threads: the pitch)
        i = owner.get(block.label)
        if pos is None and block.start is not None:
            pos = block.start
        fixed = None  # threads and drills turn at one speed: the largest diameter of the block
        if name in ("thread", "thread_spring", "drill"):
            xs = [abs(m.x) for p in block.passes for m in p.moves if m.x is not None]
            d = (max(xs) if xs else 0.0) * k
            if name == "drill" and tool is not None and tool.width:
                d = tool.width  # the drill's own diameter (library sizes are mm)
            fixed = rpm(tool, d, machine)
        t = 0.0
        for p in block.passes:
            for m in p.moves:
                if m.kind is MoveKind.DWELL:
                    t += 60.0 * (m.dwell_revs or 0.0) / (fixed or rpm(tool, (pos[0] if pos else 0.0) * k, machine))
                    continue
                if m.x is None or m.z is None:
                    continue
                end = (m.x, m.z)
                if pos is not None:
                    length = _length(m, pos, end) * k
                    if m.kind is MoveKind.RAPID:
                        t += 60.0 * length / machine.rapid
                    elif feed > 0:
                        d = (abs(pos[0]) + abs(end[0])) / 2 * k  # mean diameter of the move
                        t += 60.0 * length / (feed * (fixed or rpm(tool, d, machine)))
                pos = end
        if t:
            est.seconds[i] = est.seconds.get(i, 0.0) + t
    return est


def _length(m, a, b) -> float:
    """Path length on the radius (X is a diameter), arcs along the arc."""
    if m.kind in (MoveKind.ARC_CW, MoveKind.ARC_CCW) and m.centre_x is not None:
        from .moves import Segment

        pts = Segment(m.kind, a, b, None, 0, (m.centre_x, m.centre_z)).polyline(24)
    else:
        pts = [a, b]
    return sum(math.hypot((xb - xa) / 2, zb - za) for (xa, za), (xb, zb) in zip(pts, pts[1:]))


def mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
