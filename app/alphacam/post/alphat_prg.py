"""Alpha-T .PRG post (DESIGN.md section 5): project in, bytes out.

Supported: tool blocks (with move/rapid and dwell appended), finish pass (external, internal, face) and area clear
(external and internal, with offsets, lines and arcs, pre-finish, follow, retract distance), face clear, grooving
(radial, internal, face), drilling (pecks, full retract, retract distance), threading (infeed angle, spring passes),
several operations and mid-program tool changes; programs in other units than the drawing (Project.output_units).
Everything is pinned by golden tests against the ALPHA-T samples in samples/alphat_v12."""

from __future__ import annotations

from ..i18n import tr
from ..model.operations import AreaClear, Drill, Dwell, FaceClear, FinishPass, Groove, MoveTo, Thread, ToolChange
from ..model.project import MachineProfile, Project
from ..model.tools import Tool, ToolLibrary, standard_library
from ..numfmt import format_number
from ..toolpath.area import area_clear, collision_angle, retract_mm
from ..toolpath.contour import front_first, chain, limits, mm, points
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
    """Formats the program's numbers. Toolpaths are computed in the drawing's units (rounded to `tdec`); a program
    in other units (Project.output_units, ALPHA-T's "Select Units To Store Output In") converts each value at the
    end, rounding the real value to the output decimals before the x2 scaling, as ALPHA-T does (UNITS_metricdwg_
    inchout: Z-25 mm -> 0.9843 -> Z-1.9686; X96 -> radius 1.8898 -> X3.7796; feed 0.15 -> 0.0059 -> 0.0118)."""

    def __init__(self, project: Project, profile: MachineProfile):
        out = project.output_units or project.units
        self.units = out
        self.dec = profile.decimals(out)
        self.tdec = profile.decimals(project.units)
        self.k = 1.0 if out == project.units else (1 / 25.4 if out == "inch" else 25.4)
        self.zs, self.fs = profile.z_scale, profile.f_scale
        self.lines: list[str] = []
        self.restart = 0  # restart points so far: IF[#140EQk] GOTO 50(k+1)
        self.feeding = False  # inside an M62 feed group
        self.pos = (None, None)  # last end point, for arc I/K

    def _c(self, v: float) -> float:
        """A real value in the output units (rounded first, when converted)."""
        return v if self.k == 1.0 else round(v * self.k, self.dec)

    def x(self, v: float) -> str:  # a diameter: its radius is converted
        return format_number(v if self.k == 1.0 else 2 * self._c(v / 2), self.dec)

    def z(self, v: float) -> str:
        return format_number(self._c(v) * self.zs, self.dec)

    def f(self, v: float) -> str:
        return format_number(self._c(v) * self.fs, self.dec)

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
                i = self.x(m.centre_x - start[0])
                k = self.z(m.centre_z - start[1])
                self.emit(f"G{g}X{self.x(m.x)}Z{self.z(m.z)}I{i}K{k}F{self.f(m.feed)}")
            elif m.kind is MoveKind.DWELL:
                self.emit(f"G4P{round(m.dwell_revs * 1000)}")
            else:
                raise NotImplementedError(tr("{value} moves are not supported by the post yet", value=m.kind.value))


def _tool(project: Project, number: int, library: ToolLibrary | None = None) -> Tool:
    """The part's own tools first, then the user's library, then ALPHA-T's standard ATOOLIB."""
    for t in project.tools:
        if t.number == number:
            return t
    if library is not None:
        try:
            return library.get(number)
        except KeyError:
            pass
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
    if not tp.passes:
        raise ValueError(tr("{op}: no cuts with this contour (nothing between the tool start and the profile)",
                            op=tr(getattr(op, "type", "operation").replace("_", " "))))
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


def _thread_block(w: _Writer, block: int, project: Project, op: Thread) -> int:
    """THREAD sample layout: one restart point for the whole op; per cut M60 G0 to (X, start Z) M61, M62, #121=n, M66,
    G32 to one pitch before the end, U/W pull-out, G0 out to the start X (no M60/M61), M60 G0 back to start Z M61.
    Start Z = thread top + lead-in. A spring pass (THREAD_spring1) is its own block #127=21 right after, with one
    cut at the final diameter; the thread block then ends without M64. Returns the last block number used."""
    outer, inner = project.contour([op.outer_line])[0], project.contour([op.inner_line])[0]
    plan = plan_thread(op, outer, inner, w.tdec)
    pitch = w.f(op.pitch)
    sx, sz = op.start.x, plan.z_start

    def cut(n: int, x: float) -> None:
        w.moves([Move(MoveKind.RAPID, x, plan.cut_start_z(len(plan.xs) if x == plan.xs[-1] else n))])
        w.emit("M62", f"#121={n}", "M66", f"G32X{w.x(x)}Z{w.z(plan.z_end + op.pitch)}F{pitch}",
               f"U{w.x(plan.pullout_u)}W{w.z(-op.pitch)}",
               f"G0X{w.x(sx)}Z{w.z(plan.z_end)}")
        w.feeding = False
        w.moves([Move(MoveKind.RAPID, sx, sz)])

    _header(w, block, op, len(plan.xs), 20, (plan.inner, plan.z_end), pitch)
    w.emit("M66")
    label = w.restart_jump()
    w.emit("M63")
    w.feeding = False
    w.moves([Move(MoveKind.RAPID, sx, sz)])
    for n, x in enumerate(plan.xs, 1):
        cut(n, x)
    if op.spring_passes <= 0:
        w.emit("M64", f"N{label}")
        return block
    w.emit(f"N{label}")
    block += 1
    _header(w, block, op, op.spring_passes, 21, (plan.inner, plan.z_end), pitch)
    w.emit("M66")
    label = w.restart_jump()
    w.emit("M63")
    w.feeding = False
    for n in range(1, op.spring_passes + 1):
        cut(n, plan.xs[-1])
    w.emit("M64", f"N{label}")
    return block


def _op_blocks(w: _Writer, block: int, project: Project, op) -> int:
    """Emit the block(s) for one operation, each followed by its empty block (left open: the caller closes the
    last one). Returns the last block number used."""
    tool = _tool(project, op.tool, w.library)
    r_exact = mm(tool.nose_radius, project.units)
    # the toolpaths use the nose radius rounded to the output decimals (inch: 0.4 mm -> 0.0157, ARC_finish_inch),
    # the #125/#126 limits the exact one (-0.063 = -4 x 0.015748)
    r = round(r_exact, w.tdec)
    if isinstance(op, Thread):
        return _thread_block(w, block, project, op)
    if isinstance(op, Drill):
        _generic_block(w, block, op, drill(op, project.units, w.tdec), 18, (op.start.x, op.depth))
        return block
    path = chain(project.contour(op.contour))
    if isinstance(op, (AreaClear, FaceClear)):
        path = front_first(path)
    limit = limits(points(path), r_exact)
    if isinstance(op, FinishPass):
        _generic_block(w, block, op, finish_pass(op, path, r, project.units, w.tdec,
                                                 angle=collision_angle(tool, op)),
                       16 if op.face else 14 if op.external else 15, limit)
    elif isinstance(op, AreaClear) and not op.front:
        # FACE direction: face-clear passes; #127=8 external (AREA_front_face), 7 internal (AREA_face_internal)
        if op.pre_finish:
            raise NotImplementedError(tr("area clear FACE with pre-finish (no sample)"))
        _generic_block(w, block, op, face_clear(op, path, r, project.units, w.tdec, retract_mm(op, project.units),
                                                     outward=True),
                       8 if op.external else 7, limit)
    elif isinstance(op, AreaClear):
        _generic_block(w, block, op, area_clear(op, path, r, project.units, w.tdec, collision_angle(tool, op)),
                       6 if op.external else 7, limit)
        if op.pre_finish:
            # its own block after an empty one: #127=10, internal 11 (AREA_prefinish_internal), with the area's
            # offsets (AREA_prefinish_offset) and a 1 mm pull-off
            _empty_block(w, block + 1, None)
            block += 2
            fp = FinishPass(op.tool, op.start, op.contour, op.feed, op.offset_x, op.offset_z, op.external)
            _generic_block(w, block, fp, finish_pass(fp, path, r, project.units, w.tdec,
                                                         round(mm(retract_mm(op, project.units, PREFINISH_LEAD_OUT_MM),
                                                                  project.units), w.tdec), angle=collision_angle(tool, op)),
                           10 if op.external else 11, limit)
    elif isinstance(op, FaceClear):
        _generic_block(w, block, op, face_clear(op, path, r, project.units, w.tdec), 8, limit)
        if op.pre_finish:  # FACE_prefinish: #127=12, ends on the offset end, 0.2 mm pull-off inwards
            _empty_block(w, block + 1, None)
            block += 2
            fp = FinishPass(op.tool, op.start, op.contour, op.feed, op.offset_x, op.offset_z)
            _generic_block(w, block, fp, finish_pass(fp, path, r, project.units, w.tdec, face_end=True), 12, limit)
    elif isinstance(op, Groove):
        _generic_block(w, block, op, groove(op, path, r, project.units, w.tdec),
                       17 if op.mode == "finish" else 9, limit)
    else:
        raise NotImplementedError(tr("{type} is not supported by the post yet", type=op.type))
    return block


def _moves_for(ops, feed: float) -> list[Move]:
    out = []
    for o in ops:
        if isinstance(o, Dwell):
            out.append(Move(MoveKind.DWELL, dwell_revs=o.revs))
        else:
            out.append(Move(MoveKind.RAPID if o.rapid else MoveKind.FEED, o.point.x, o.point.z,
                            None if o.rapid else feed))
    return out


def _empty_block(w: _Writer, block: int, next_tool: ToolChange | None, moves: list[Move] | None = None) -> None:
    """The empty block after an operation: moves/dwells that follow the operation, then the rapid to the tool
    change point when a tool change follows."""
    w.emit(f"N{block * 10}", "M66")
    label = w.restart_jump()
    w.feeding = False
    if moves:
        w.moves(moves)
    if next_tool is not None:
        w.feeding = False
        w.moves([Move(MoveKind.RAPID, next_tool.change_point.x, next_tool.change_point.z)])
    w.emit(f"N{label}")


def post(project: Project, profile: MachineProfile | None = None, library: ToolLibrary | None = None) -> bytes:
    """Layout (samples MET2, AREA1, TWOOP, TOOLCHG, MOVE_RAPID):
    header; tool block (N10) with any move/rapid/dwell that follows the tool change; then per operation an op block
    and an empty block (which holds the rapid to the tool change point when a tool change comes next); a later tool
    change is another tool block; finally an M65 block and %."""
    profile = profile or MachineProfile()
    w = _Writer(project, profile)
    w.library = library
    w.emit("%", f"O{profile.o_number}", f"#132={project.program_number}", *HEADER_LOGIC, UNITS_MCODE[project.output_units or project.units])

    ops = list(project.operations)
    if not ops or not isinstance(ops[0], ToolChange):
        ops.insert(0, ToolChange(project.first_tool, project.tool_change,
                                 _tool(project, project.first_tool, w.library).default_feed))
    block = 0
    i = 0
    tc_feed = 0.15
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
            tc_feed = op.feed
            continue
        if isinstance(op, (MoveTo, Dwell)):
            raise NotImplementedError(tr("move/dwell before the first tool change is not supported"))
        block = _op_blocks(w, block, project, op) + 1
        # moves and dwells after an operation go into its empty block (FINISH_then_move_rapid_dwell)
        after = []
        while i < len(ops) and isinstance(ops[i], (MoveTo, Dwell)):
            after.append(ops[i])
            i += 1
        _empty_block(w, block, ops[i] if i < len(ops) and isinstance(ops[i], ToolChange) else None,
                     _moves_for(after, tc_feed))
    block += 1
    w.emit(f"N{block * 10}", "M65", "%")
    return "".join(line + "\r\n" for line in w.lines).encode("ascii")
