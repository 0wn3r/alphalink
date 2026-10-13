"""Alpha-T .PRG post (DESIGN.md section 5): project in, bytes out.

Supported: tool blocks (with move/rapid and dwell appended), finish pass and area clear (external and internal,
with offsets on external ops, lines and arcs), several operations and mid-program tool changes.
Everything is pinned by golden tests against the ALPHA-T samples in samples/alphat_v12."""

from __future__ import annotations

from ..model.operations import AreaClear, Dwell, FinishPass, MoveTo, ToolChange
from ..model.project import MachineProfile, Project
from ..model.tools import Tool, standard_library
from ..numfmt import format_number
from ..toolpath.area import area_clear
from ..toolpath.contour import chain, limits, mm, points
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
        self.pos = (None, None)  # last end point, for arc I/K

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
            if m.kind is not MoveKind.DWELL and m.x is not None:
                start, self.pos = self.pos, (m.x, m.z)
            if m.kind is MoveKind.RAPID:
                self.emit("M60", f"G0X{self.x(m.x)}Z{self.z(m.z)}", "M61")
                self.feeding = False
            elif m.kind is MoveKind.FEED:
                if not self.feeding:
                    self.emit("M62")
                    self.feeding = True
                self.emit(f"G1X{self.x(m.x)}Z{self.z(m.z)}F{self.f(m.feed)}")
            elif m.kind in (MoveKind.ARC_CW, MoveKind.ARC_CCW):
                if not self.feeding:
                    self.emit("M62")
                    self.feeding = True
                g = 2 if m.kind is MoveKind.ARC_CW else 3
                i = format_number(m.centre_x - start[0], self.dec)
                k = format_number((m.centre_z - start[1]) * self.zs, self.dec)
                self.emit(f"G{g}X{self.x(m.x)}Z{self.z(m.z)}I{i}K{k}F{self.f(m.feed)}")
            elif m.kind is MoveKind.DWELL:
                self.emit(f"G4P{round(m.dwell_revs * 1000)}")
            else:
                raise NotImplementedError(f"{m.kind.value} moves are not supported by the post yet")


def _tool(project: Project, number: int) -> Tool:
    for t in project.tools:
        if t.number == number:
            return t
    return standard_library().get(number)


def _tool_block(w: _Writer, block: int, tc: ToolChange, moves: list[Move]) -> None:
    w.emit(f"N{block * 10}", "#121=1", f"#122={tc.tool}", f"#123={w.x(tc.change_point.x)}",
           f"#124={w.z(tc.change_point.z)}", "#127=1", f"#129={block}", f"#130={w.f(tc.feed)}", "M66")
    label = w.restart_jump()
    w.emit("M63")
    w.feeding = False
    w.moves(moves)
    w.emit(f"N{label}")


def _op_block(w: _Writer, block: int, project: Project, op) -> None:
    tool = _tool(project, op.tool)
    r = mm(tool.nose_radius, project.units)
    path = chain(project.contour(op.contour))
    if isinstance(op, FinishPass):
        tp = finish_pass(op, path, r, project.units, w.dec)
        kind = 14 if op.external else 15
    elif isinstance(op, AreaClear):
        tp = area_clear(op, path, r, project.units, w.dec)
        kind = 6 if op.external else 7
    else:
        raise NotImplementedError(f"{op.type} is not supported by the post yet")
    lim_x, lim_z = limits(points(path), r)
    w.emit(f"N{block * 10}", f"#120={len(tp.passes)}", "#121=1", f"#122={op.tool}",
           f"#123={w.x(op.start.x)}", f"#124={w.z(op.start.z)}", f"#125={w.x(lim_x)}", f"#126={w.z(lim_z)}",
           f"#127={kind}", "#128=1", f"#129={block}", f"#130={w.f(op.feed)}", "#131=#131+1")
    label = None
    for n, p in enumerate(tp.passes, 1):
        if n > 1:
            w.emit(f"N{label}", f"#121={n}")
        w.emit("M66")
        label = w.restart_jump()
        w.emit("M63")
        w.feeding = False
        w.moves(p.moves)
    w.moves(tp.retract)
    w.emit("M64", f"N{label}")


def post(project: Project, profile: MachineProfile | None = None) -> bytes:
    """Layout (samples MET2, AREA1, TWOOP, TOOLCHG, MOVE_RAPID):
    header; tool block (N10) with any move/rapid/dwell that follows the tool change; then per operation an op block
    and an empty block (which holds the rapid to the tool change point when a tool change comes next); a later tool
    change is another tool block; finally an M65 block and %."""
    profile = profile or MachineProfile()
    w = _Writer(project, profile)
    w.emit("%", f"O{profile.o_number}", f"#132={project.program_number}", *HEADER_LOGIC, UNITS_MCODE[project.units])

    ops = list(project.operations)
    if not ops or not isinstance(ops[0], ToolChange):
        ops.insert(0, ToolChange(project.first_tool, project.tool_change,
                                 _tool(project, project.first_tool).default_feed))
    block = 0
    i = 0
    while i < len(ops):
        op = ops[i]
        i += 1
        block += 1
        if isinstance(op, ToolChange):
            moves = []
            while i < len(ops) and isinstance(ops[i], (MoveTo, Dwell)):
                o = ops[i]
                i += 1
                if isinstance(o, Dwell):
                    moves.append(Move(MoveKind.DWELL, dwell_revs=o.revs))
                else:
                    moves.append(Move(MoveKind.RAPID if o.rapid else MoveKind.FEED, o.point.x, o.point.z,
                                      None if o.rapid else op.feed))
            _tool_block(w, block, op, moves)
            continue
        if isinstance(op, (MoveTo, Dwell)):
            raise NotImplementedError("move/dwell after an operation is not supported yet (no sample)")
        _op_block(w, block, project, op)
        # empty block after every operation
        block += 1
        w.emit(f"N{block * 10}", "M66")
        label = w.restart_jump()
        if i < len(ops) and isinstance(ops[i], ToolChange):
            w.feeding = False
            w.moves([Move(MoveKind.RAPID, ops[i].change_point.x, ops[i].change_point.z)])
        w.emit(f"N{label}")
    block += 1
    w.emit(f"N{block * 10}", "M65", "%")
    return "".join(line + "\r\n" for line in w.lines).encode("ascii")
