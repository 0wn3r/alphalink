"""Alpha-T .PRG post (DESIGN.md section 5): project in, bytes out.

Supported (M1): the N10 tool block (with move/rapid and dwell appended to it), finish pass and area clear.
Everything is pinned by golden tests against the ALPHA-T samples in samples/alphat_v12."""

from __future__ import annotations

from ..model.operations import AreaClear, Dwell, FinishPass, MoveTo, ToolChange
from ..model.project import MachineProfile, Project
from ..model.tools import Tool, standard_library
from ..numfmt import format_number
from ..toolpath.area import area_clear
from ..toolpath.contour import chain, limits, mm
from ..toolpath.finish import finish_pass
from ..toolpath.toolpath import Move, MoveKind

HEADER_LOGIC = [
    "#140=0",
    "IF[#1006EQ1]GOTO[[#129+1]*10]",
    "IF[#1005EQ1]GOTO[#129*10]",
    "#131=#131-1",
    "IF[#1009EQ1]GOTO[#129*10]",
    "#131=0",
]
UNITS_MCODE = {"mm": "M71", "inch": "M70"}


class _Writer:
    def __init__(self, project: Project, profile: MachineProfile):
        self.units = project.units
        self.dec = profile.decimals(project.units)
        self.zs, self.fs = profile.z_scale, profile.f_scale
        self.lines: list[str] = []
        self.restart = 0  # restart points so far: IF[#140EQk] GOTO 50(k+1)
        self.feeding = False  # inside an M62 feed group

    def x(self, v: float) -> str:
        return format_number(v, self.dec)

    def z(self, v: float) -> str:
        return format_number(v * self.zs, self.dec)

    def f(self, v: float) -> str:
        return format_number(v * self.fs, self.dec)

    def emit(self, *lines: str) -> None:
        self.lines.extend(lines)

    def restart_jump(self) -> int:
        """IF[#140EQk] GOTO <label>; returns the label to place where execution resumes."""
        self.restart += 1
        label = 501 + self.restart
        self.emit(f"IF[#140EQ{self.restart}] GOTO {label}")
        return label

    def moves(self, moves: list[Move]) -> None:
        for m in moves:
            if m.kind is MoveKind.RAPID:
                self.emit("M60", f"G0X{self.x(m.x)}Z{self.z(m.z)}", "M61")
                self.feeding = False
            elif m.kind is MoveKind.FEED:
                if not self.feeding:
                    self.emit("M62")
                    self.feeding = True
                self.emit(f"G1X{self.x(m.x)}Z{self.z(m.z)}F{self.f(m.feed)}")
            elif m.kind is MoveKind.DWELL:
                self.emit(f"G4P{round(m.dwell_revs * 1000)}")
            else:
                raise NotImplementedError(f"{m.kind.value} moves are not supported by the post yet")


def _tool(project: Project, number: int) -> Tool:
    for t in project.tools:
        if t.number == number:
            return t
    return standard_library().get(number)


def post(project: Project, profile: MachineProfile | None = None) -> bytes:
    profile = profile or MachineProfile()
    w = _Writer(project, profile)
    w.emit("%", f"O{profile.o_number}", f"#132={project.program_number}", *HEADER_LOGIC, UNITS_MCODE[project.units])

    ops = list(project.operations)
    if ops and isinstance(ops[0], ToolChange):
        tc = ops.pop(0)
    else:
        tc = ToolChange(project.first_tool, project.tool_change, _tool(project, project.first_tool).default_feed)
    tool_moves = []
    while ops and isinstance(ops[0], (MoveTo, Dwell)):
        o = ops.pop(0)
        if isinstance(o, Dwell):
            tool_moves.append(Move(MoveKind.DWELL, dwell_revs=o.revs))
        else:
            tool_moves.append(Move(MoveKind.RAPID if o.rapid else MoveKind.FEED, o.point.x, o.point.z,
                                   None if o.rapid else tc.feed))

    # N10 tool block
    w.emit("N10", "#121=1", f"#122={tc.tool}", f"#123={w.x(tc.change_point.x)}", f"#124={w.z(tc.change_point.z)}",
           "#127=1", "#129=1", f"#130={w.f(tc.feed)}", "M66")
    label = w.restart_jump()
    w.emit("M63")
    w.feeding = False
    w.moves(tool_moves)
    w.emit(f"N{label}")

    block = 1
    for op in ops:
        block += 1
        tool = _tool(project, op.tool)
        r = mm(tool.nose_radius, project.units)
        if isinstance(op, FinishPass):
            pts = chain(project.contour(op.contour))
            path = finish_pass(op, pts, project.units, w.dec)
        elif isinstance(op, AreaClear):
            pts = chain(project.contour(op.contour))
            path = area_clear(op, pts, r, project.units, w.dec)
        else:
            raise NotImplementedError(f"{op.type} is not supported by the post yet")
        lim_x, lim_z = limits(pts, r)
        w.emit(f"N{block * 10}", f"#120={len(path.passes)}", "#121=1", f"#122={op.tool}",
               f"#123={w.x(op.start.x)}", f"#124={w.z(op.start.z)}", f"#125={w.x(lim_x)}", f"#126={w.z(lim_z)}",
               f"#127={op.op_kind}", "#128=1", f"#129={block}", f"#130={w.f(op.feed)}", "#131=#131+1")
        for n, p in enumerate(path.passes, 1):
            if n > 1:
                w.emit(f"N{label}", f"#121={n}")
            w.emit("M66")
            label = w.restart_jump()
            w.emit("M63")
            w.feeding = False
            w.moves(p.moves)
        w.moves(path.retract)
        w.emit("M64", f"N{label}")

    if ops:
        # An empty block follows the operations in every sample (assumed: once, after the last operation).
        block += 1
        w.emit(f"N{block * 10}", "M66")
        label = w.restart_jump()
        w.emit(f"N{label}")
    block += 1
    w.emit(f"N{block * 10}", "M65", "%")
    return "".join(line + "\r\n" for line in w.lines).encode("ascii")
