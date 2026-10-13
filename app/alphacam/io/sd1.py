"""Rebuild a project's operations from ALPHA-T's .SD1 listing.

ALPHA-T keeps no operations in the drawing: the .SDG holds geometry and billet, and the .SDT is the same for every
operation. The operations survive only in the programs it writes. The .SD1 (an ISO-style listing written next to
the .PRG) names every operation with its parameters as canned cycles:

  N10 F<feed> T<tool> ... G30 (mm) / G31 (inch)    first tool and its feed
  N20 G5 X Z / N25 G6 X Z                          tool change point
  G4 P<revs>                                       dwell (in the tool block)
  G1 / G0 ...                                      move / rapid to a point (in the tool block)
  G5 X Z                                           tool start of the next operation
  G115 X Z                                         operation limits (drilling: the hole bottom)
  G70 R E U W                                      finish pass (E1 internal, U/W offsets; R1 = pre-finish of the area)
  G71 ... D R E U W H                              area clear (E100, E-100 internal, E101 FACE direction) / face clear (E401), D depth
  G75 ... D I E L C                                grooving: D peck, I stepover, E tip width, L11x full retract
  G78 ... G178                                     grooving finish mode (path lines only)
  G117 X Z, G76 X Z ... D E F                      threading: start, final diameter and end, first cut, E1/-1, pitch
  G74 + G1 pecks                                   drilling (peck = start Z - first G1 Z), G4 inside = dwell
  G99 F T                                          tool change later in the program

The contour of every profile operation is the drawing's chain. The rebuilt project can be checked by posting it
and comparing with the .PRG ALPHA-T wrote (see verify())."""

from __future__ import annotations

import re
from pathlib import Path

from ..drawing import pick_order
from ..model.geometry import Line, Point
from ..model.operations import AreaClear, Drill, Dwell, FaceClear, FinishPass, Groove, MoveTo, Thread, ToolChange

RE_WORD = re.compile(r"([A-Z])(-?\d*\.?\d+)")


def _words(line: str) -> tuple[list[int], dict[str, float]]:
    gs, w = [], {}
    for k, v in RE_WORD.findall(line):
        if k == "G":
            gs.append(int(float(v)))
        elif k != "N":
            w.setdefault(k, float(v))
    return gs, w


def read_sd1(text: str, project) -> list:
    """Operations for `project` (geometry already loaded) from the SD1 text."""
    try:
        contour = pick_order(project, [g.id for g in project.geometry])
    except (ValueError, KeyError):
        contour = [g.id for g in project.geometry]
    ops: list = []
    tool, feed = project.first_tool, 0.15
    change = project.tool_change
    start: Point | None = None
    pos = {"X": 0.0, "Z": 0.0}
    state = "header"  # header -> tool (moves/dwells belong to the tool block) -> op
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        gs, w = _words(lines[i])
        i += 1
        if "T" in w and (not gs or 95 in gs or 99 in gs):  # N10 F T ... or G99 F T
            tool, feed = int(w["T"]), w.get("F", feed)
            ops.append(ToolChange(tool, change, feed))
            state = "tool"
            continue
        if 92 in gs:
            continue
        if 5 in gs and i < len(lines) and 6 in _words(lines[i])[0]:  # G5 + G6 pair: tool change point
            change = Point(w["X"], w["Z"])
            if ops and isinstance(ops[-1], ToolChange):
                ops[-1].change_point = change
            i += 1
            continue
        if 5 in gs:
            start = Point(w["X"], w["Z"])
            pos.update(X=w["X"], Z=w["Z"])
            state = "op"
            continue
        if 4 in gs and state in ("tool", "after"):
            ops.append(Dwell(w["P"]))
            continue
        if any(g in gs for g in (170, 171, 174, 175, 176, 178)):  # end of an operation's cycle
            state = "after"
            continue
        if state == "after" and 0 in gs and i < len(lines) and 99 in _words(lines[i])[0]:
            continue  # the rapid to the tool change point before a G99 tool change
        if state in ("tool", "after") and (0 in gs or 1 in gs) and ("X" in w or "Z" in w):
            pos.update({k: w[k] for k in ("X", "Z") if k in w})
            ops.append(MoveTo(Point(pos["X"], pos["Z"]), rapid=0 in gs, feed=feed))
            continue
        if 115 in gs:
            limit = Point(w["X"], w["Z"])
            state = "op"
            continue
        if 70 in gs:
            if w.get("R") == 1 and ops and isinstance(ops[-1], AreaClear):
                ops[-1].pre_finish = True
            else:
                ops.append(FinishPass(tool, start, list(contour), feed=feed, offset_x=w.get("U", 0.0),
                                      offset_z=w.get("W", 0.0), external=w.get("E", 0) != 1))
            continue
        if 71 in gs:
            e = int(w.get("E", 100))
            common = dict(depth_of_cut=w.get("D", 2.0), offset_x=w.get("U", 0.0), offset_z=w.get("W", 0.0), feed=feed)
            if e == 401:
                ops.append(FaceClear(tool, start, list(contour), **common))
            elif e in (100, -100, 101):
                ops.append(AreaClear(tool, start, list(contour), external=e > 0, front=e != 101, **common))
            else:
                raise NotImplementedError(f"G71 E{e} (area clear option) is not supported yet")
            continue
        if 75 in gs:
            flags = int(w.get("L", 110))  # L1<datum: 1 left, 0 right><full retract: 1 yes>
            ops.append(Groove(tool, start, list(contour), peck_depth=w.get("D", 0.0), stepover=w.get("I", 2.0),
                              tip_width=w.get("E", 3.0), full_retract=flags % 10 == 1,
                              datum="left" if flags // 10 % 10 == 1 else "right", z_shift=w.get("R", 0.0),
                              repeat=max(int(w.get("C", 1)) - 1, 0), dwell_revs=w.get("A", 0.0), feed=feed))
            continue
        if 78 in gs:  # grooving FINISH mode: no parameters (the path lines up to G178 are its output)
            ops.append(Groove(tool, start, list(contour), mode="finish", feed=feed))
            continue
        if 117 in gs:  # thread approach point = (start X, thread top + lead-in); the G5 start stays the start
            continue
        if 76 in gs:
            external = w.get("E", 1) > 0
            final, z_end = w["X"], w["Z"]
            lines_z = [g for g in project.geometry if isinstance(g, Line) and g.a.x == g.b.x
                       and abs(min(g.a.z, g.b.z) - z_end) < 1e-6]
            at_final = next((g for g in lines_z if abs(g.a.x - final) < 1e-6), None)
            other = next((g for g in lines_z if g is not at_final), None)
            if at_final is None or other is None:
                raise ValueError("threading: the thread lines are not in the drawing")
            outer, inner = (other, at_final) if external else (at_final, other)
            ops.append(Thread(tool, start, outer.id, inner.id, pitch=w["F"], cut_depth=w.get("D", 1.0),
                              external=external, lead_in=w.get("I", 5.0), lead_out=w.get("L", 0.0),
                              spring_pass=w.get("K", 0) > 0))
            continue
        if 74 in gs:
            peck, dwell = 0.0, 0.0
            while i < len(lines):
                g2, w2 = _words(lines[i])
                if 174 in g2:
                    break
                if 1 in g2 and "Z" in w2 and not peck:
                    peck = round(start.z - w2["Z"], 6)
                if 4 in g2:
                    dwell = w2["P"]
                i += 1
            depth = limit.z
            ops.append(Drill(tool, start, depth=depth, peck=0.0 if abs(peck - (start.z - depth)) < 1e-9 else peck,
                             feed=feed, dwell_revs=dwell))
            continue
        # profile points, G20/G21/G17x/M30: nothing to do
    return ops


def load_sd1(path: str | Path, project) -> list:
    return read_sd1(Path(path).read_text("ascii", errors="replace"), project)


def sibling(path: Path, suffix: str) -> Path | None:
    for cand in (path.with_suffix(suffix.upper()), path.with_suffix(suffix.lower())):
        if cand.exists():
            return cand
    return None


def verify(project, prg_path: Path) -> bool:
    """True if posting the project gives exactly the bytes ALPHA-T wrote."""
    from ..post.alphat_prg import post

    try:
        return post(project) == prg_path.read_bytes()
    except (NotImplementedError, ValueError, KeyError, OSError):
        return False
