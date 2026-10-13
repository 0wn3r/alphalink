"""Alpha-T .SD1 listing (what ALPHA-T writes next to the .PRG): project in, bytes out.

The SD1 is ALPHA-T's own canned-cycle listing of the program, in real (unscaled) units: N numbers step by 5, M30
ends it without one, CRLF, numbers with up to 4 decimals and no trailing point (X120, Z0.4). Within a run of motion
lines a coordinate equal to the previous one is left out; the first motion line after any other line has both.

    N10 F<feed> T<tool> G95G90M8M3G96G30L0      first tool;  N15 G92 X163.3803 Z120;  N20 G5 / N25 G6 tool change pt
    G0 X Z, G99 F T M8M3G96                      later tool change (rapid to the change point first)
    G5 X Z                                       operation start
    G115 X Z                                     limits (= PRG #125/#126)
    G70 R<1 = area pre-finish> E<0 ext, 1 int> U W                        finish pass
    G71 P Q U W D R A45 E<100, -100 int, 401 face, 101 area FACE> H<passes>   area / face clear
        P = the G21 line, Q = the G0 back to the start; R = retract (area 1, face 0.2)
    G21 E<1, -1> Q<2, 1> C0, G0 approach, G1/G2/G3 nose-centre path, G0/G1 pull-off, G20, G0 start, G170/G171
    G75 P Q U W D I R E L H C A, G0/G1 drawn profile, G175                grooving (P/Q = first/last profile line)
    G78, the finish path, G178                                           grooving finish mode
    G117 X Z(top + lead in), G76 X Z Q D I L B0 A0 E K F, G176 F           threading
    G74, the drill moves, G174                                           drilling
    G4 P<revs>, G0/G1                                                    dwell, moves

Pinned against every metric sample in samples/alphat_v12 (tests/test_sd1_write.py)."""

from __future__ import annotations

from ..i18n import tr
import math

from ..model.operations import AreaClear, Drill, Dwell, FaceClear, FinishPass, Groove, MoveTo, Thread, ToolChange
from ..model.geometry import Point
from ..model.project import Project
from ..model.tools import ToolLibrary
from ..numfmt import format_number
from ..toolpath.area import area_clear
from ..toolpath.contour import centre_path, chain, limits, mm, points, translate
from ..toolpath.drill import drill
from ..toolpath.face import face_clear
from ..toolpath.groove import groove, groove_finish
from ..toolpath.thread import plan_thread
from ..toolpath.toolpath import MoveKind
from .alphat_prg import AREA_RETRACT_MM, PREFINISH_LEAD_OUT_MM, _tool

DEC = 4
G92 = (163.3803, 120.0)  # mm; constant in every sample (inch: X6.4323 Z4.7244)
APPROACH_MM = 2.0  # G0 to the first point from 2 mm away at 45 degrees
FINISH_LEAD_OUT_MM = 0.2
FACE_RETRACT_MM = 0.2


def _n(v: float) -> str:
    s = format_number(v, DEC)
    return s[:-1] if s.endswith(".") else s


class _Listing:
    def __init__(self):
        self.lines: list[str] = []
        self.pos: tuple[float | None, float | None] = (None, None)

    @property
    def next_n(self) -> int:
        return 5 * (len(self.lines) + 2)

    def put(self, text: str) -> int:
        """A non-motion line; returns its N."""
        n = self.next_n
        self.lines.append(f"N{n}{text}")
        self.pos = (None, None)
        return n

    def move(self, g: int, x: float, z: float, extra: str = "") -> int:
        x, z = round(x, DEC), round(z, DEC)
        words = ""
        if self.pos[0] is None or x != self.pos[0] or extra:
            words += f"X{_n(x)}"
        if self.pos[1] is None or z != self.pos[1] or extra:
            words += f"Z{_n(z)}"
        n = self.next_n
        self.lines.append(f"N{n}G{g}{words}{extra}")
        self.pos = (x, z)
        return n

    def path(self, segs) -> int:
        n = 0
        for s in segs:
            if s.is_arc:
                i, k = (s.centre.x - s.a.x) / 2, s.centre.z - s.a.z
                n = self.move(3 if s.ccw else 2, s.b.x, s.b.z, f"I{_n(i)}K{_n(k)}")
            else:
                n = self.move(1, s.b.x, s.b.z)
        return n


def _leg(units: str, dist_mm: float) -> float:
    return mm(dist_mm, units) * math.cos(math.radians(45))


def _contour_cycle(out: _Listing, op, path, r: float, units: str, external: bool, cycle_line,
                   lead_out_mm: float, lead_out_rapid: bool, extend_end: bool, inward: bool, end_g: int,
                   face_mode: bool = False) -> None:
    """G70/G71 body. `cycle_line(p, q)` gives the cycle's own line text (needs the P/Q line numbers).
    face_mode (face clear passes: E401 / E101 / E-101): the first point is carried r back along its element, not
    below the axis (AREA_face_internal: X49.2 Z0.4; FACE_*: X0 Z0.4), and internal work lists G21 Q3."""
    side = 1 if external else -1
    cp = centre_path(path, r, external, extend_end)
    if face_mode and r and not cp[0].is_arc:
        s = cp[0]
        dr, dz = (s.b.x - s.a.x) / 2, s.b.z - s.a.z
        n = math.hypot(dr, dz)
        cp[0] = type(s)(Point(max(s.a.x - 2 * r * dr / n, 0.0), s.a.z - r * dz / n), s.b, s.centre, s.ccw)
    if isinstance(op, FinishPass):
        # the finish pass lists its path moved by the offsets, the first point only across its element
        # (OFFSET_finish, INT_finish_offset); area and face clear list the path as drawn
        cp = translate(cp, op.offset_x, op.offset_z, external)
    first, last = cp[0].a, cp[-1].b
    approach = _leg(units, APPROACH_MM)
    out_leg = _leg(units, lead_out_mm)
    index = len(out.lines)
    out.lines.append(None)  # cycle line, filled in once P/Q are known
    p = out.put(f"G21E{side}Q{2 if external else 3 if face_mode else 1}C0")
    out.move(0, first.x + side * 2 * approach, first.z + approach)
    out.move(1, first.x, first.z)
    out.path(cp)
    dx = (-1 if inward else 1) * side * 2 * out_leg
    out.move(0 if lead_out_rapid else 1, last.x + dx, last.z + out_leg)
    out.put("G20")
    q = out.move(0, op.start.x, op.start.z)
    out.lines[index] = f"N{5 * (index + 2)}{cycle_line(p, q)}"
    out.put(f"G{end_g}")


def sd1(project: Project, library: ToolLibrary | None = None) -> bytes:
    units = project.units
    out = _Listing()
    ops = list(project.operations)
    if not ops or not isinstance(ops[0], ToolChange):
        ops.insert(0, ToolChange(project.first_tool, project.tool_change,
                                 _tool(project, project.first_tool, library).default_feed))
    first = True
    tc_feed = ops[0].feed
    for op in ops:
        if isinstance(op, ToolChange):
            tc_feed = op.feed
            if first:
                out.put(f"F{_n(op.feed)}T{op.tool}G95G90M8M3G96G{30 if units == 'mm' else 31}L0")
                out.put(f"G92X{_n(mm(G92[0], units))}Z{_n(mm(G92[1], units))}")
                out.put(f"G5X{_n(op.change_point.x)}Z{_n(op.change_point.z)}")
                out.put(f"G6X{_n(op.change_point.x)}Z{_n(op.change_point.z)}")
                first = False
            else:
                out.move(0, op.change_point.x, op.change_point.z)
                out.put(f"G99F{_n(op.feed)}T{op.tool}M8M3G96")
            continue
        if isinstance(op, Dwell):
            out.put(f"G4P{_n(op.revs)}")
            continue
        if isinstance(op, MoveTo):
            out.move(0 if op.rapid else 1, op.point.x, op.point.z)
            continue
        _op(out, project, op, library, tc_feed)
    out.lines.append("M30")
    return "".join(line + "\r\n" for line in out.lines).encode("ascii")


def _op(out: _Listing, project: Project, op, library, tc_feed: float) -> None:
    units = project.units
    tool = _tool(project, op.tool, library)
    r_exact = mm(tool.nose_radius, units)
    r = r_exact  # the SD1 uses the exact nose radius (MET2_finish_inch: X1.6315, the PRG's rounded r gives 1.6314)
    out.put(f"G5X{_n(op.start.x)}Z{_n(op.start.z)}")
    if isinstance(op, Thread):
        outer, inner = project.contour([op.outer_line])[0], project.contour([op.inner_line])[0]
        plan = plan_thread(op, outer, inner, DEC)
        out.put(f"G115X{_n(plan.inner)}Z{_n(plan.z_end)}")
        out.put(f"G117X{_n(op.start.x)}Z{_n(plan.z_start)}")
        # Q: the thread depth on radius (THREAD_depth6: Q6)
        depth = abs(outer.a.x - inner.a.x) / 2
        out.put(f"G76X{_n(plan.inner)}Z{_n(plan.z_end + op.lead_out)}Q{_n(depth)}D{_n(plan.first_cut)}"
                f"I{_n(op.lead_in)}L{_n(op.lead_out)}B0A0E{1 if op.external else -1}K{1 if op.spring_pass else 0}"
                f"F{_n(op.pitch)}")
        out.put(f"G176F{_n(tc_feed)}")
        return
    if isinstance(op, Drill):
        out.put(f"G115X{_n(op.start.x)}Z{_n(op.depth)}")
        out.put("G74")
        tp = drill(op, units, DEC)
        _moves(out, [m for p in tp.passes for m in p.moves] + tp.retract)
        out.put("G174")
        return
    path = chain(project.contour(op.contour))
    lx, lz = limits(points(path), r_exact)
    out.put(f"G115X{_n(lx)}Z{_n(lz)}")
    if isinstance(op, FinishPass):
        _contour_cycle(out, op, path, r, units, op.external,
                       lambda p, q: f"G70R0E{0 if op.external else 1}U{_n(op.offset_x)}W{_n(op.offset_z)}",
                       FINISH_LEAD_OUT_MM, False, True, False, 170)
    elif isinstance(op, (AreaClear, FaceClear)):
        face = isinstance(op, FaceClear)
        external = getattr(op, "external", True)
        if face:
            e, tp = 401, face_clear(op, path, r, units, DEC)
        elif not op.front:
            e, tp = (101 if external else -101), face_clear(op, path, r, units, DEC, AREA_RETRACT_MM, outward=True)
        else:
            e, tp = (100 if external else -100), area_clear(op, path, r, units, DEC)
        retract = FACE_RETRACT_MM if face else AREA_RETRACT_MM
        _contour_cycle(out, op, path, r, units, external,
                       lambda p, q: f"G71P{p}Q{q}U{_n(op.offset_x)}W{_n(op.offset_z)}D{_n(op.depth_of_cut)}"
                                    f"R{_n(retract)}A45E{e}H{len(tp.passes)}",
                       retract, True, e in (100, -100), face, 171, face_mode=e not in (100, -100))
        if getattr(op, "pre_finish", False):  # G70 R1, E0 / E1 internal / E2 face, with the op's offsets
            out.put(f"G115X{_n(lx)}Z{_n(lz)}")
            fp = FinishPass(op.tool, op.start, op.contour, op.feed, op.offset_x, op.offset_z, external)
            pe = 2 if face else 0 if external else 1
            _contour_cycle(out, fp, path, r, units, external,
                           lambda p, q: f"G70R1E{pe}U{_n(op.offset_x)}W{_n(op.offset_z)}",
                           FINISH_LEAD_OUT_MM if face else PREFINISH_LEAD_OUT_MM, False, not face, face, 170)
    elif isinstance(op, Groove) and op.mode == "finish":
        out.put("G78")
        tp = groove_finish(op, path)
        _moves(out, [m for p in tp.passes for m in p.moves] + tp.retract)
        out.put("G178")
    elif isinstance(op, Groove):
        tp = groove(op, path, r, units, DEC)
        plunges = tp.plunges
        index = len(out.lines)
        out.lines.append(None)
        p = out.move(0, path[0].a.x, path[0].a.z)
        q = out.path(path)
        flags = f"{'' if op.external else '-'}{1 if op.front else 2}{1 if op.datum == 'left' else 0}" \
                f"{1 if op.full_retract else 0}"
        out.lines[index] = (f"N{5 * (index + 2)}G75P{p}Q{q}U{_n(op.finish_offset_x)}W{_n(op.finish_offset_z)}"
                            f"D{_n(op.peck_depth)}I{_n(op.stepover)}R{_n(op.z_shift)}E{_n(op.tip_width)}L{flags}"
                            f"H{plunges}C{op.repeat + 1}A{_n(op.dwell_revs)}")
        out.put("G175")
    else:
        raise NotImplementedError(tr("{type} is not supported by the SD1 writer yet", type=op.type))


def _moves(out: _Listing, moves) -> None:
    for m in moves:
        if m.kind is MoveKind.DWELL:
            out.put(f"G4P{_n(m.dwell_revs)}")
        else:
            out.move(0 if m.kind is MoveKind.RAPID else 1, m.x, m.z)
