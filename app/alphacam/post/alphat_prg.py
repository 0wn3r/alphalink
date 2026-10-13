"""Alpha-T .PRG post (DESIGN.md section 5): project in, bytes out.

Supported: tool blocks (with move/rapid and dwell appended), finish pass and area clear (external and internal,
with offsets on external ops, lines and arcs, area pre-finish), face clear, grooving, drilling, threading, several
operations and mid-program tool changes.
Everything is pinned by golden tests against the ALPHA-T samples in samples/alphat_v12."""

from __future__ import annotations

from ..model.operations import AreaClear, Drill, Dwell, FaceClear, FinishPass, Groove, MoveTo, Thread, ToolChange
from ..model.project import MachineProfile, Project
from ..model.tools import Tool, standard_library
from ..numfmt import format_number
from ..toolpath.area import area_clear
from ..toolpath.contour import chain, limits, mm, points
from ..toolpath.drill import drill
from ..toolpath.face import face_clear
from ..toolpath.finish import finish_pass
from ..toolpath.groove import groove
from ..toolpath.thread import plan_thread
from ..toolpath.toolpath import Move, MoveKind, Toolpath

PREFINISH_LEAD_OUT_MM = 1.0  # area clear pre-finish block (#127=10): pull-off 1 mm instead of 0.2

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


def _header(w: _Writer, block: int, op, passes: int, kind: int, limit: tuple[float, float], feed: str) -> None:
    w.emit(f"N{block * 10}", f"#120={passes}", "#121=1", f"#122={op.tool}",
           f"#123={w.x(op.start.x)}", f"#124={w.z(op.start.z)}", f"#125={w.x(limit[0])}", f"#126={w.z(limit[1])}",
           f"#127={kind}", "#128=1", f"#129={block}", f"#130={feed}", "#131=#131+1")


def _generic_block(w: _Writer, block: int, op, tp: Toolpath, kind: int, limit: tuple[float, float]) -> None:
    _header(w, block, op, len(tp.passes), kind, limit, w.f(op.feed))
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


def _thread_block(w: _Writer, block: int, project: Project, op: Thread) -> None:
    """THREAD sample layout: one restart point for the whole op; per cut M60 G0 to (X, start Z) M61, M62, #121=n, M66,
    G32 to one pitch before the end, U/W pull-out, G0 out to the start X (no M60/M61), M60 G0 back to start Z M61."""
    outer, inner = project.contour([op.outer_line])[0], project.contour([op.inner_line])[0]
    plan = plan_thread(op, outer, inner, w.dec)
    pitch = w.f(op.pitch)
    _header(w, block, op, len(plan.xs), 20, (plan.inner, plan.z_end), pitch)
    w.emit("M66")
    label = w.restart_jump()
    w.emit("M63")
    sx, sz = op.start.x, op.start.z
    w.feeding = False
    w.moves([Move(MoveKind.RAPID, sx, sz)])
    for n, x in enumerate(plan.xs, 1):
        w.moves([Move(MoveKind.RAPID, x, sz)])
        w.emit("M62", f"#121={n}", "M66", f"G32X{w.x(x)}Z{w.z(plan.z_end + op.pitch)}F{pitch}",
               f"U{format_number(plan.pullout_u, w.dec)}W{format_number(-op.pitch * w.zs, w.dec)}",
               f"G0X{w.x(sx)}Z{w.z(plan.z_end)}")
        w.feeding = False
        w.moves([Move(MoveKind.RAPID, sx, sz)])
    w.emit("M64", f"N{label}")


def _op_blocks(w: _Writer, block: int, project: Project, op) -> int:
    """Emit the block(s) for one operation, each followed by its empty block (left open: the caller closes the
    last one). Returns the last block number used."""
    tool = _tool(project, op.tool)
    r = mm(tool.nose_radius, project.units)
    if isinstance(op, Thread):
        _thread_block(w, block, project, op)
        return block
    if isinstance(op, Drill):
        _generic_block(w, block, op, drill(op, project.units, w.dec), 18, (op.start.x, op.depth))
        return block
    path = chain(project.contour(op.contour))
    limit = limits(points(path), r)
    if isinstance(op, FinishPass):
        _generic_block(w, block, op, finish_pass(op, path, r, project.units, w.dec), 14 if op.external else 15, limit)
    elif isinstance(op, AreaClear):
        if op.pre_finish and (not op.external or op.offset_x or op.offset_z):
            raise NotImplementedError("pre-finish only for external area clear without offsets (no sample)")
        _generic_block(w, block, op, area_clear(op, path, r, project.units, w.dec), 6 if op.external else 7, limit)
        if op.pre_finish:
            _empty_block(w, block + 1, None)
            block += 2
            fp = FinishPass(op.tool, op.start, op.contour, op.feed)
            _generic_block(w, block, fp, finish_pass(fp, path, r, project.units, w.dec, PREFINISH_LEAD_OUT_MM),
                           10, limit)
    elif isinstance(op, FaceClear):
        _generic_block(w, block, op, face_clear(op, path, r, project.units, w.dec), 8, limit)
    elif isinstance(op, Groove):
        _generic_block(w, block, op, groove(op, path, r, project.units, w.dec), 9, limit)
    else:
        raise NotImplementedError(f"{op.type} is not supported by the post yet")
    return block


def _empty_block(w: _Writer, block: int, next_tool: ToolChange | None) -> None:
    """The empty block after an operation; it holds the rapid to the tool change point when a tool change follows."""
    w.emit(f"N{block * 10}", "M66")
    label = w.restart_jump()
    if next_tool is not None:
        w.feeding = False
        w.moves([Move(MoveKind.RAPID, next_tool.change_point.x, next_tool.change_point.z)])
    w.emit(f"N{label}")


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
        block = _op_blocks(w, block, project, op) + 1
        _empty_block(w, block, ops[i] if i < len(ops) and isinstance(ops[i], ToolChange) else None)
    block += 1
    w.emit(f"N{block * 10}", "M65", "%")
    return "".join(line + "\r\n" for line in w.lines).encode("ascii")
