#!/usr/bin/env python3
"""Export the cases of the GTK-free core modules for the Rust port's tests (rust/alphacam/tests/core_*.rs).

drawing, features, setups and commands: scripted edits on small parts, each step's result (or error) and the part
after it. preview, moves, cycletime, safety and checks: every project of the post fixtures plus the parts the Python
tests build, with what the Python app gives for them. The Rust crate has no PRG parser yet (io::prg_parse is ported
separately), so the programs the preview parsed are exported too and the Rust tests hand them to its preview.
tool_shapes: the outline of every standard tool and of a few made-up ones.

    python3 tools/export_rust_core_fixtures.py   # from the repository root; rewrites rust/alphacam/tests/fixtures/
                                                 # {drawing,features,setups,commands,preview,moves,cycletime,safety,
                                                 # checks,tool_shapes}
"""

import copy
import json
import math
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app")]

import alphacam.preview as preview_mod  # noqa: E402
from alphacam import drawing, features, setups  # noqa: E402
from alphacam.checks import tool_warnings  # noqa: E402
from alphacam.commands import UndoStack  # noqa: E402
from alphacam.cycletime import Machine, estimate, mmss, rpm  # noqa: E402
from alphacam.model import (  # noqa: E402
    AreaClear, Arc, Drill, FaceClear, FinishPass, Groove, Line, Point, Project, Thread, ToolChange,
)
from alphacam.model.project import Billet  # noqa: E402
from alphacam.model.tools import Tool, standard_library  # noqa: E402
from alphacam.moves import pass_starts, segments  # noqa: E402
from alphacam.safety import check  # noqa: E402
from alphacam.tool_shapes import internal, outline  # noqa: E402

FIXTURES = ROOT / "rust" / "alphacam" / "tests" / "fixtures"
POST = FIXTURES / "post"
S = Point(120, 5)
MET2 = ["g0", "g1", "g2", "g3", "g4"]
SHAFT = [(0, 0), (40, 0), (40, -25), (60, -25), (60, -50), (100, -50)]
DIVE = [(0, 0), (40, 0), (40, -20), (30, -30), (30, -40), (60, -40), (100, -40)]
GROOVED = [(0, 0), (40, 0), (40, -20), (34, -20), (34, -26), (40, -26), (40, -40), (60, -40)]


def enc(v):
    """JSON for a result: points as [x, z], tuples as lists."""
    if isinstance(v, Point):
        return [v.x, v.z]
    if isinstance(v, (list, tuple)):
        return [enc(x) for x in v]
    if isinstance(v, dict):
        return {str(k): enc(x) for k, x in v.items()}
    if isinstance(v, (Line, Arc)):
        return v.id
    return v


def pt(v):
    return Point(*v)


def part(points, billet=None, first_tool=3, units="mm"):
    p = Project(billet=billet or Billet(100, 60), first_tool=first_tool, units=units)
    p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(points, points[1:])]
    for i, g in enumerate(p.geometry):
        g.id = f"g{i}"
    return p


def drawn(points):
    """A profile drawn with add_line (ids g0, g1, ...)."""
    p = Project()
    for a, b in zip(points, points[1:]):
        drawing.add_line(p, Point(*a), Point(*b))
    return p


def state(p) -> dict:
    return json.loads(p.dumps())


def write(module: str, name: str, data) -> None:
    (FIXTURES / module).mkdir(parents=True, exist_ok=True)
    (FIXTURES / module / name).write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")


# --- scripted edits ---------------------------------------------------------------------------------------------


class Script:
    """Runs calls on a project and records each result (or error) and the project after it."""

    def __init__(self, functions):
        self.functions = functions
        self.cases = []

    def case(self, name, project, steps):
        start = before = state(project)
        out = []
        for call, args in steps:
            fn, convert = self.functions[call]
            step = {"call": call, "args": args}
            try:
                step["result"] = enc(fn(project, *convert(args)))
            except (ValueError, KeyError, NotImplementedError) as e:
                step["error"] = type(e).__name__
                step["message"] = e.args[0] if e.args else ""
            now = state(project)
            if now != before:  # the part after the step, when it changed
                step["project"] = before = now
            out.append(step)
        self.cases.append({"name": name, "project": start, "steps": out})


def ref(r):
    return (r[0], Point(*r[1])) if r[0] == "point" else tuple(r)


KIND = {None: None, "line": Line, "arc": Arc}

DRAWING = {
    "next_id": (drawing.next_id, lambda a: []),
    "endpoints": (drawing.endpoints, lambda a: []),
    "snap": (lambda p, q, tol, grid: drawing.snap(p, q, tol, grid), lambda a: [pt(a[0]), a[1], a[2]]),
    "lock_direction": (lambda p, a, b, deg: drawing.lock_direction(a, b, deg), lambda a: [pt(a[0]), pt(a[1]), a[2]]),
    "add_line": (drawing.add_line, lambda a: [pt(a[0]), pt(a[1])]),
    "delete_element": (drawing.delete_element, lambda a: a),
    "edit_element": (lambda p, gid, a, b, radius, follow: drawing.edit_element(p, gid, a, b, radius, follow),
                     lambda a: [a[0], pt(a[1]), pt(a[2]), a[3], a[4]]),
    "fillet": (drawing.fillet, lambda a: [pt(a[0]), a[1]]),
    "chamfer": (drawing.chamfer, lambda a: [pt(a[0]), a[1], a[2]]),
    "pick_order": (drawing.pick_order, lambda a: [a[0]]),
    "add_arc_3p": (drawing.add_arc_3p, lambda a: [pt(a[0]), pt(a[1]), pt(a[2])]),
    "corner": (drawing.corner, lambda a: a),
    "polar": (lambda p, o, length, angle: drawing.polar(o, length, angle), lambda a: [pt(a[0]), a[1], a[2]]),
    "measure_xz": (lambda p, a, b: drawing.measure_xz(a, b), lambda a: [pt(a[0]), pt(a[1])]),
    "measure_la": (lambda p, a, b: drawing.measure_la(a, b), lambda a: [pt(a[0]), pt(a[1])]),
    "key_points": (drawing.key_points, lambda a: []),
    "nearest_point": (drawing.nearest_point, lambda a: [pt(a[0])]),
    "distance_to": (lambda p, gid, q: drawing.distance_to(next(g for g in p.geometry if g.id == gid), q),
                    lambda a: [a[0], pt(a[1])]),
    "nearest_element": (drawing.nearest_element, lambda a: [pt(a[0]), KIND[a[1]]]),
    "intersections": (drawing.intersections, lambda a: []),
    "nearest_intersection": (drawing.nearest_intersection, lambda a: [pt(a[0])]),
    "erase_drawing": (drawing.erase_drawing, lambda a: []),
    "add_text": (drawing.add_text, lambda a: [pt(a[0]), a[1], a[2]]),
    "nearest_text": (drawing.nearest_text, lambda a: [pt(a[0])]),
    "add_circle": (drawing.add_circle, lambda a: [pt(a[0]), a[1]]),
    "tangents_point_arc": (drawing.tangents_point_arc, lambda a: [pt(a[0]), a[1]]),
    "tangents_arc_arc": (drawing.tangents_arc_arc, lambda a: a),
    "tangents_at_angle": (drawing.tangents_at_angle, lambda a: a),
    "parallels": (drawing.parallels, lambda a: a),
    "split_arc": (drawing.split_arc, lambda a: [a[0], pt(a[1])]),
    "mirror_axis": (drawing.mirror_axis, lambda a: []),
    "blends": (drawing.blends, lambda a: [ref(a[0]), ref(a[1]), a[2]]),
    "add_blend": (drawing.add_blend, lambda a: [pt(a[0]), pt(a[1]), pt(a[2])]),
    "components": (drawing.components, lambda a: []),
    "outer_profile": (drawing.outer_profile, lambda a: []),
    "groove_elements": (drawing.groove_elements, lambda a: [a[0]]),
    "thread_lines": (drawing.thread_lines, lambda a: [a[0]]),
}


def drawing_cases():
    s = Script(DRAWING)
    met2 = [(0, 0), (40, 0), (40, -25), (60, -25), (60, -50), (100, -50)]
    # test_drawing.py
    s.case("fillets_arc_sample", drawn(met2), [("fillet", [[60, -25], 5]), ("fillet", [[60, -50], 3])])
    s.case("chamfer", drawn(met2), [("chamfer", [[40, 0], 1, 45])])
    s.case("chamfer_angle", drawn(met2), [("chamfer", [[40, 0], 2, 30]), ("chamfer", [[60, -25], 1, 90])])
    s.case("errors", drawn(met2), [("fillet", [[0, 0], 1]), ("fillet", [[40, 0], 30]),
                                   ("add_line", [[1, 1], [1, 1]])])
    s.case("snap_and_lock", drawn(met2), [
        ("snap", [[40.6, -24.8], 0.5, 0.0]), ("snap", [[0.6, -70.2], 0.5, 1.0]), ("snap", [[73.2, -70.3], 0.5, 1.0]),
        ("snap", [[73.2, -70.3], 0.5, 0.0]), ("snap", [[0.2, -3.3], 0.5, 0.0]), ("snap", [[41.3, -12.7], 0.5, 2.5]),
        ("lock_direction", [[100, -50], [101.2, -80], 8.0]), ("lock_direction", [[100, -50], [140, -50.4], 8.0]),
        ("lock_direction", [[100, -50], [119, -60.5], 8.0]), ("lock_direction", [[0, 0], [30, -40], 8.0]),
        ("lock_direction", [[0, 0], [0, 0], 8.0]), ("lock_direction", [[10, -5], [3, 2.3], 8.0]),
        ("lock_direction", [[10, -5], [25, 3.1], 8.0]), ("lock_direction", [[10, -5], [-12, -16.5], 8.0])])
    sample = Project.load(POST / "golden_MET2_finish_metric.alcam")
    s.case("delete_drops_from_contours", sample, [("delete_element", ["g4"]), ("delete_element", ["g9"])])
    s.case("pick_order", drawn(met2), [("pick_order", [["g2", "g3", "g1", "g4", "g0"]]),
                                       ("pick_order", [["g0", "g3"]]), ("pick_order", [["g4", "g2", "g3"]]),
                                       ("pick_order", [["x"]])])
    q = Project()
    mid = [2 * (25 + 5 * math.cos(math.radians(45))), -30 + 5 * math.sin(math.radians(45))]
    s.case("arc_3p", q, [("add_arc_3p", [[50, -25], mid, [60, -30]]), ("add_arc_3p", [[0, 0], [10, -5], [20, -10]])])
    p = Project()
    s.case("corner", p, [("add_line", [[40, 2], [40, -20]]), ("add_line", [[30, -25], [60, -25]]),
                         ("corner", ["g0", "g1"]), ("corner", ["g0", "g0"]), ("add_line", [[0, 5], [0, -5]]),
                         ("corner", ["g0", "g2"])])
    # construction (test_drawing.py Construction)
    s.case("circle_tangents", Project(), [
        ("add_circle", [[100, -50], 10]), ("tangents_point_arc", [[100, -80], "g0"]),
        ("tangents_point_arc", [[100, -52], "g0"]), ("add_circle", [[60, -10], 5]),
        ("tangents_arc_arc", ["g0", "g2"]), ("tangents_arc_arc", ["g2", "g0"]), ("add_circle", [[100, -50], 4]),
        ("tangents_arc_arc", ["g0", "g4"]), ("add_circle", [[0, 0], -1])])
    s.case("parallel_and_angle_tangent", Project(), [
        ("add_line", [[40, 0], [40, -20]]), ("parallels", ["g0", 2]), ("parallels", ["g0", 0]),
        ("add_circle", [[100, -50], 10]), ("tangents_at_angle", ["g1", 90]), ("tangents_at_angle", ["g1", 30]),
        ("add_line", [[10, 3], [52, -17]]), ("parallels", ["g3", 3.5]), ("parallels", ["g1", 1])])
    s.case("split_mirror_blend", Project(), [
        ("add_circle", [[100, -50], 10]), ("split_arc", ["g0", [120, -50]]), ("split_arc", ["g0", [80, -50]]),
        ("mirror_axis", []), ("blends", [["point", [0, 0]], ["point", [0, -20]], 15]),
        ("add_blend", [[0, 0], [22.36068, -10], [0, -20]]), ("blends", [["point", [0, 0]], ["point", [0, -20]], 5])])
    s.case("mirror_nothing", Project(), [("add_line", [[0, 0], [0, -10]]), ("mirror_axis", [])])
    # edit_element (test_drawing.py EditElement)
    three = [(0, 0), (40, 0), (40, -25), (60, -25)]
    s.case("edit_follow", drawn(three), [("edit_element", ["g1", [42, 0], [42, -30], None, True])])
    s.case("edit_no_follow", drawn(three), [("edit_element", ["g1", [42, 0], [42, -30], None, False])])
    s.case("edit_zero_length", drawn(three), [("edit_element", ["g0", [5, 5], [5, 5], None, True]),
                                              ("edit_element", ["nope", [5, 5], [6, 5], None, True])])
    p = Project()
    drawing.add_line(p, Point(0, 0), Point(30, 0))
    s.case("edit_arc_follows", p, [
        ("add_arc_3p", [[30, 0], [30 + 10 * (1 - math.cos(math.pi / 4)) * 2, -10 * math.sin(math.pi / 4)], [50, -10]]),
        ("edit_element", ["g0", [0, 0], [32, 0], None, True])])
    s.case("edit_arc_radius", Project(), [
        ("add_arc_3p", [[30, 0], [30 + 20 * (1 - math.cos(math.pi / 4)), -10 * math.sin(math.pi / 4)], [50, -10]]),
        ("edit_element", ["g0", [30, 0], [50, -10], 15, True]), ("edit_element", ["g0", [30, 0], [50, -10], 2, True])])
    # test_hit_keys.py
    s.case("polar_measure", Project(), [
        ("polar", [[40, -25], 10, a]) for a in (0, 90, 180, 270, 45, 30, 135, 200, -60, 361.5)] + [
        ("measure_xz", [[40, -25], [60, -35]]), ("measure_la", [[40, -25], [54.142136, -32.071068]]),
        ("measure_la", [[40, -25], [20, -25]]), ("measure_la", [[40, -25], [40, -25]]),
        ("measure_la", [[40, -25], [33.3, -12.1]])])
    hit = [("add_line", [[0, 0], [40, 0]]), ("add_line", [[40, 0], [40, -25]]), ("add_line", [[40, -25], [60, -25]]),
           ("add_arc_3p", [[60, -25], [70, -30], [60, -35]]), ("add_line", [[50, 5], [50, -40]])]
    s.case("find_point_line_arc", Project(), hit + [
        ("key_points", []), ("nearest_point", [[39, 1]]), ("nearest_point", [[59, -30.5]]),
        ("nearest_element", [[39, -10], "line"]), ("nearest_element", [[39, -10], "arc"]),
        ("nearest_element", [[66, -31], None]), ("distance_to", ["g3", [70, -30]]), ("distance_to", ["g3", [40, -30]]),
        ("distance_to", ["g3", [60, -30]]), ("distance_to", ["g0", [20, 3]])])
    s.case("intersections", Project(), hit + [("intersections", []), ("nearest_intersection", [[49, -24]])])
    s.case("arc_line_intersection", Project(), [
        ("add_arc_3p", [[60, -25], [70, -30], [60, -35]]), ("add_line", [[66, 0], [66, -50]]), ("intersections", []),
        ("add_line", [[56, 0], [56, -50]]), ("intersections", []), ("nearest_intersection", [[0, 0]])])
    s.case("text_and_erase", Project(), hit + [
        ("nearest_text", [[0, 0]]), ("add_text", [[80, -10], "HELLO", 5.0]), ("add_text", [[20, -40], "SHAFT", 3]),
        ("add_text", [[0, 0], "  ", 5.0]), ("nearest_text", [[22, -38]]), ("erase_drawing", [])])
    # test_regressions.py
    s.case("fillet_using_up_a_line", drawn([(0, 0), (40, 0), (40, -10), (60, -10)]), [("fillet", [[40, -10], 10])])
    s.case("blend_with_the_arcs_own_radius", Project(), [
        ("add_circle", [[100, -50], 5]), ("blends", [["point", [110, -50]], ["arc", "g0", False], 5])])
    # default contours (test_regressions.py SmarterDefaults) and more
    for name, pts in (("shaft", SHAFT), ("dive", DIVE), ("grooved", GROOVED)):
        p = part(pts)
        if name == "grooved":
            p.geometry.append(Line(Point(38.2, 0), Point(38.2, -18), id="g7"))
        s.case(f"profile_{name}", p, [("components", []), ("outer_profile", []),
                                      ("groove_elements", [[f"g{i}" for i in range(len(pts) - 1)]]),
                                      ("thread_lines", [[f"g{i}" for i in range(len(pts) - 1)]]),
                                      ("endpoints", []), ("next_id", [])])
    p = part(GROOVED)
    p.geometry.insert(2, Line(Point(70, 10), Point(80, 10), id="loose"))
    p.geometry.append(Arc(Point(60, -40), Point(70, -45), Point(70, -40), False, id="a"))
    s.case("profile_loose_and_arc", p, [("components", []), ("outer_profile", []), ("intersections", [])])
    s.case("profile_empty", Project(), [("components", []), ("outer_profile", []), ("intersections", []),
                                        ("nearest_point", [[1, 1]]), ("nearest_element", [[1, 1], None]),
                                        ("nearest_intersection", [[1, 1]])])

    # every corner of a few profiles, filleted and chamfered at several sizes
    profiles = {"met2": met2, "dive": DIVE, "grooved": GROOVED,
                "taper": [(0, 0), (30, 0), (50, -10), (50, -30), (80, -45), (80, -60)]}
    for pname, pts in profiles.items():
        for k, c in enumerate(pts):
            for r in (0.5, 2, 5):
                s.case(f"fillet_{pname}_{k}_{r}", drawn(pts), [("fillet", [list(c), r])])
            for size, angle in ((0.5, 45), (1, 30), (3, 60), (2, 45)):
                s.case(f"chamfer_{pname}_{k}_{size}_{angle}", drawn(pts), [("chamfer", [list(c), size, angle])])
    # fillet then edit the arc's neighbours
    s.case("fillet_then_edit", drawn(met2), [("fillet", [[60, -25], 5]), ("edit_element", ["g2", [61, -25.0], [61, -50], None, True]),
                                             ("edit_element", ["g5", [55, -25], [61, -30], 6, True])])

    rnd = random.Random(20261016)

    def rp(lo=-60.0, hi=0.0):
        return [round(rnd.uniform(0, 100), 3), round(rnd.uniform(lo, hi), 3)]

    for n in range(30):
        steps = []
        for _ in range(4):
            steps.append(("add_line", [rp(), rp()]))
        for _ in range(3):
            c = rp()
            r = round(rnd.uniform(2, 20), 2)
            steps.append(("add_circle", [c, r]))
        for _ in range(2):
            steps.append(("add_arc_3p", [rp(), rp(), rp()]))
        steps += [("intersections", []), ("nearest_intersection", [rp()]), ("nearest_point", [rp()]),
                  ("nearest_element", [rp(), None]), ("nearest_element", [rp(), "arc"]),
                  ("nearest_element", [rp(), "line"])]
        steps += [("distance_to", [f"g{k}", rp()]) for k in range(12)]
        steps += [("tangents_point_arc", [rp(), "g4"]), ("tangents_arc_arc", ["g4", "g6"]),
                  ("tangents_arc_arc", ["g6", "g8"]), ("tangents_at_angle", ["g4", round(rnd.uniform(0, 360), 1)]),
                  ("parallels", ["g0", round(rnd.uniform(0.5, 10), 2)]), ("split_arc", ["g5", rp()])]
        radius = round(rnd.uniform(1, 15), 2)
        refs = [["point", rp()], ["line", "g1"], ["arc", "g4", True], ["arc", "g6", False], ["line", "g2"]]
        for i, r1 in enumerate(refs):
            for r2 in refs[i + 1:]:
                steps.append(("blends", [r1, r2, radius]))
        steps += [("components", []), ("outer_profile", []), ("mirror_axis", []), ("corner", ["g0", "g1"]),
                  ("corner", ["g2", "g3"])]
        s.case(f"random_{n}", Project(), steps)
    write("drawing", "cases.json", s.cases)
    return len(s.cases)


# --- features ---------------------------------------------------------------------------------------------------


def spec_json(t):
    return {"d": t.d, "pitch": t.pitch, "standard": t.standard, "units": t.units, "size": t.size, "tpi": t.tpi,
            "minor": t.minor, "depth": t.depth, "relief_diameter": t.relief_diameter, "relief_width": t.relief_width,
            "name": t.name}


def features_cases():
    specs = []
    for standard in features.STANDARDS:
        for size in features.sizes(standard):
            for i in range(len(features.pitches(standard, size))):
                for units in ("mm", "inch"):
                    specs.append({"args": [standard, str(size), i, units],
                                  "spec": spec_json(features.spec(standard, size, i, units))})
    direct = []
    for d, pitch, units in ((40, 1.5, "mm"), (20, 2.5, "mm"), (40, 3.0, "mm"), (50, 2.0, "mm"), (12.7, 1.27, "mm"),
                            (1.5748, 0.059055, "inch"), (0.5, 1 / 13, "inch"), (24, 3.0, "mm"), (39.9, 4.0, "mm")):
        direct.append({"args": [d, pitch, units], "spec": spec_json(features.ThreadSpec(d, pitch, units=units))})
    sizes = {st: [str(x) for x in features.sizes(st)] for st in features.STANDARDS}
    nearest = []
    for st in features.STANDARDS:
        for d in (3.0, 6.4, 12.6, 39.2, 41.0, 45.0, 100.0, 0.74, 0.5, 1.2):
            for units in ("mm", "inch"):
                nearest.append({"args": [st, d, units], "result": str(features.nearest(st, d, units))})
    coarse = [{"args": [d, p], "result": features.coarse(d, p)}
              for d, p in ((40, 3.0), (12, 1.75), (12, 1.5), (12.5, 1.75), (7, 1.0), (50, 3.0), (64, 6.0))]

    s = Script({
        "add_thread": (lambda p, gid, sp, length, relief: features.add_thread(p, gid, sp, length, relief),
                       lambda a: [a[0], features.ThreadSpec(*a[1]), a[2], a[3]]),
        "outside_corners": (features.outside_corners, lambda a: []),
        "all_corners": (features.all_corners, lambda a: a),
    })
    s.case("thread_with_relief", part(SHAFT), [("add_thread", ["g1", [42, 1.5], None, True]), ("outside_corners", [])])
    s.case("thread_without_relief", part(SHAFT), [("add_thread", ["g1", [40, 1.5], None, False])])
    s.case("thread_length", part(SHAFT), [("add_thread", ["g1", [40, 1.5], 12, True])])
    s.case("thread_length_long", part(SHAFT), [("add_thread", ["g1", [40, 2.0], 40, True])])
    s.case("thread_refuses", part(SHAFT), [("add_thread", ["g0", [40, 1.5], None, True]),
                                            ("add_thread", ["g1", [40, 9.0], None, True]),
                                            ("add_thread", ["zz", [40, 1.5], None, True])])
    s.case("thread_reversed_line", part([(0, 0), (40, 0), (40, -25), (60, -25)][::-1]),
           [("add_thread", ["g1", [40, 1.5], None, True])])
    inch = part([(0, 0), (0.5, 0), (0.5, -1.0), (1.0, -1.0)], Billet(2, 3), units="inch")
    t = features.spec("UNC", "1/2", 0, "inch")
    s.case("thread_inch", inch, [("add_thread", ["g1", [t.d, t.pitch, "UNC", "inch", "1/2", 13], None, True])])
    s.case("outside_corners", part(SHAFT), [("outside_corners", [])])
    s.case("chamfer_all", part(SHAFT), [("all_corners", ["chamfer", 1, 45.0]), ("outside_corners", [])])
    s.case("chamfer_all_30", part(DIVE), [("all_corners", ["chamfer", 1.5, 30.0]), ("outside_corners", [])])
    s.case("round_all", part(SHAFT), [("all_corners", ["fillet", 2, 45.0]), ("outside_corners", [])])
    s.case("round_all_grooved", part(GROOVED), [("outside_corners", []), ("all_corners", ["fillet", 1, 45.0])])
    s.case("too_big_is_skipped", part(SHAFT), [("all_corners", ["chamfer", 30, 45.0])])
    s.case("no_profile", Project(), [("outside_corners", []), ("all_corners", ["chamfer", 1, 45.0])])
    write("features", "cases.json", {"specs": specs, "direct": direct, "sizes": sizes, "nearest": nearest,
                                     "coarse": coarse, "edits": s.cases})
    return len(specs) + len(direct) + len(nearest) + len(coarse) + len(s.cases)


# --- setups ---------------------------------------------------------------------------------------------------


def setups_cases():
    cases = []

    def add(name, p, length=None, number=None):
        c = {"name": name, "project": state(p), "args": [length, number], "part_length": setups.part_length(p)}
        try:
            c["result"] = state(setups.turn_around(p, length, number))
        except ValueError as e:
            c["error"] = e.args[0]
        cases.append(c)

    p = part(SHAFT)
    p.geometry.append(Arc(Point(60, -30), Point(60, -40), Point(60, -35), ccw=True, id="a"))
    from alphacam.model.geometry import Text
    p.texts = [Text(Point(80, -10), "HELLO"), Text(Point(20, -40.3), "SHAFT", 3)]
    p.operations.append(FinishPass(3, S, ["g0", "g1"]))
    add("turned_around", p)
    add("explicit_length", p, 60.0, 7)
    add("nothing", Project())
    add("above_z0", part([(0, 5), (40, 5)]))
    for f in sorted(POST.glob("example_*.alcam")):
        add(f.stem, Project.load(f))
    write("setups", "cases.json", cases)
    return len(cases)


# --- commands -------------------------------------------------------------------------------------------------


def commands_cases():
    """Undo stack scripts: mutations are named so the Rust test can do the same ones."""

    def mutation(q, name, arg):
        if name == "feed":
            q.operations[arg[0]].feed = arg[1]
        elif name == "pop_op":
            q.operations.pop(arg)
        elif name == "program_number":
            q.program_number = arg
        elif name == "add_line":
            drawing.add_line(q, Point(*arg[0]), Point(*arg[1]))
        elif name == "bad":
            q.operations.append(FinishPass(3, Point(120, 5), ["g0"]))
            raise ValueError("nope")
        elif name == "fillet":
            drawing.fillet(q, Point(*arg[0]), arg[1])
        else:
            raise AssertionError(name)

    cases = []

    def run(name, project_file, script, limit=200):
        p = Project.load(POST / project_file)
        seen = []
        st = UndoStack(p, lambda proj: seen.append(len(proj.operations)), limit=limit)
        out = []
        for action, *args in script:
            r = None
            if action == "apply":
                label, mname, marg = args
                try:
                    st.apply(label, lambda q: mutation(q, mname, marg))
                except ValueError as e:
                    r = {"error": e.args[0]}
            elif action == "undo":
                r = st.undo()
            elif action == "redo":
                r = st.redo()
            elif action == "mark_saved":
                st.mark_saved()
            elif action == "mark_unsaved":
                st.mark_unsaved()
            out.append({"action": [action, *args], "result": r, "modified": st.modified, "can_undo": st.can_undo,
                        "can_redo": st.can_redo, "project": state(p), "seen": list(seen)})
        cases.append({"name": name, "file": project_file, "limit": limit, "steps": out})

    run("apply_undo_redo", "golden_TWOOP_area_finish_metric.alcam", [
        ("apply", "Feed", "feed", [0, 0.2]), ("apply", "Delete", "pop_op", 1), ("undo",), ("undo",), ("redo",),
        ("redo",), ("redo",), ("undo",), ("undo",), ("undo",)])
    run("failed_edit", "golden_MET2_finish_metric.alcam", [("apply", "Bad", "bad", None), ("undo",)])
    run("no_op_edit", "golden_MET2_finish_metric.alcam", [("apply", "Same", "feed", [0, 0.15])])
    run("new_edit_clears_redo", "golden_MET2_finish_metric.alcam", [
        ("apply", "A", "program_number", 5), ("undo",), ("apply", "B", "program_number", 6), ("redo",)])
    run("saved_marks", "golden_MET2_finish_metric.alcam", [
        ("apply", "A", "program_number", 5), ("mark_saved",), ("undo",), ("redo",), ("mark_unsaved",),
        ("apply", "Line", "add_line", [[0, 0], [10, 0]]), ("apply", "Fillet", "fillet", [[60, -25], 5]),
        ("apply", "Bad fillet", "fillet", [[0, 0], 5]), ("undo",), ("undo",), ("undo",)])
    run("limit", "golden_MET2_finish_metric.alcam", [("apply", f"P{n}", "program_number", n) for n in range(1, 6)]
        + [("undo",)] * 4, limit=3)
    write("commands", "cases.json", cases)
    return len(cases)


# --- preview, moves, cycle time, safety, checks -----------------------------------------------------------------


def program_json(prog):
    def move(m):
        d = {"kind": m.kind.value, "line": m.line}
        for k in ("x", "z", "feed", "dwell_revs", "centre_x", "centre_z"):
            if getattr(m, k) is not None:
                d[k] = getattr(m, k)
        if m.incremental:
            d["incremental"] = True
        return d

    return {"blocks": [{"label": b.label, "line": b.line, "registers": {str(k): v for k, v in b.registers.items()},
                        "m_codes": b.m_codes, "z_scale": b.z_scale, "f_scale": b.f_scale,
                        "passes": [{"number": p.number, "line": p.line, "moves": [move(m) for m in p.moves]}
                                   for p in b.passes]} for b in prog.blocks]}


def extra_projects():
    """The parts the Python tests build (test_safety, test_workflow_456, test_regressions, test_preview)."""
    out = {}
    p = part(SHAFT)
    p.operations.append(FinishPass(3, S, [g.id for g in p.geometry], offset_x=-1.0))
    out["safety_negative_allowance"] = p
    p = part([(0, 0), (40, 0), (40, -70), (100, -70)], Billet(100, 60))
    p.operations.append(FinishPass(3, S, [g.id for g in p.geometry]))
    out["safety_chuck"] = p
    for on in (True, False):
        p = part(SHAFT, Billet(100, 60, tailstock=on))
        p.operations.append(FinishPass(3, S, [g.id for g in p.geometry]))
        out[f"safety_tailstock_{on}"] = p
    for tool in (1, 4, 3, 9, 12):
        p = part(DIVE)
        p.operations.append(FinishPass(tool, S, [g.id for g in p.geometry]))
        out[f"safety_dive_finish_t{tool}"] = p
        p = part(DIVE)
        p.operations.append(AreaClear(tool, S, [g.id for g in p.geometry]))
        out[f"safety_dive_area_t{tool}"] = p
    for towards in (True, False):
        p = part(SHAFT)
        p.operations.append(AreaClear(1, S, [g.id for g in p.geometry], towards=towards))
        out[f"safety_tip_t1_{towards}"] = p
        p = part(SHAFT)
        p.operations.append(FaceClear(19, S, [g.id for g in p.geometry], towards=towards))
        out[f"safety_tip_face_t19_{towards}"] = p
    p = part(SHAFT)
    p.operations += [AreaClear(3, S, [g.id for g in p.geometry]), FinishPass(3, S, [g.id for g in p.geometry])]
    p.operations[0].offset_x = 0.3
    out["safety_area_then_finish"] = p
    p = part(SHAFT)
    p.operations.append(FinishPass(3, Point(120, 5), [g.id for g in p.geometry], feed=0.1))
    out["cycle_finish_pass"] = p
    p = Project.load(POST / "golden_TWOOP_area_finish_metric.alcam")
    p.operations.insert(1, AreaClear(3, S, ["g0", "g1"], towards=False))
    out["preview_error_op"] = p
    p = Project.load(POST / "golden_MET2_finish_metric.alcam")
    p.operations = [FaceClear(3, S, ["g0"])]
    out["preview_no_cuts"] = p
    p = Project.load(POST / "golden_MET2_finish_metric.alcam")
    p.operations = [FaceClear(3, S, list(MET2), pre_finish=True), FinishPass(3, S, list(MET2))]
    out["preview_face_prefinish"] = p
    p = Project.load(POST / "golden_THREAD_spring2_metric.alcam")
    p.operations = [Thread(3, S, "g0", "g1", pitch=1.5, spring_passes=2), FinishPass(3, S, ["g0"])]
    out["preview_spring_passes"] = p
    p = Project.load(POST / "golden_MET2_finish_metric.alcam")
    p.output_units = "inch"
    out["preview_output_inch"] = p
    p = Project.load(POST / "golden_MET2_finish_metric.alcam")
    p.operations = []
    out["preview_no_ops"] = p
    p = Project.load(POST / "golden_MET2_finish_metric.alcam")
    p.operations = [FinishPass(77, S, list(MET2))]
    out["preview_unknown_tool"] = p
    # tool checks (test_regressions.py QuickWins) and every other warning
    p = part(GROOVED, Billet(60, 60), first_tool=1)
    p.geometry.append(Line(Point(38.2, 0), Point(38.2, -18), id="g7"))
    p.operations.append(Groove(1, Point(64, 5), ["g1", "g2", "g3", "g4", "g5"]))
    out["checks_groove_turning_tool"] = copy.deepcopy(p)
    p.operations.insert(0, ToolChange(14))
    p.operations[1].tool = 14
    p.operations[1].tip_width = 9.0
    out["checks_groove_tip_width"] = copy.deepcopy(p)
    p.operations[1].tip_width = 2.0
    p.operations += [Thread(1, S, "g6", "g7", pitch=1.5), Thread(8, S, "g6", "g7", pitch=1.5),
                     Drill(3, Point(0, 5), depth=-20), FinishPass(12, S, ["g0", "g1"]),
                     AreaClear(3, S, ["g0", "g1"], external=False), Groove(11, S, ["g1", "g2", "g3"]),
                     ToolChange(31), Thread(31, S, "g6", "g7", pitch=1.5), Thread(99, S, "g6", "g7", pitch=1.5)]
    out["checks_all_warnings"] = p
    p = part([(0, 0), (1.5, 0), (1.5, -0.8), (1.3, -0.8), (1.3, -1.0), (1.5, -1.0), (1.5, -2.0)], Billet(2, 3),
             first_tool=16, units="inch")
    p.operations.append(Groove(16, Point(2.2, 0.2), ["g1", "g2", "g3", "g4", "g5"], tip_width=0.0787))
    out["checks_inch_tip_width"] = copy.deepcopy(p)
    p.operations[0].tip_width = 0.1
    out["checks_inch_tip_width_off"] = p
    # cycle time with the tools' own speeds and a drill's diameter
    for name in ("example_drill", "example_rough_and_finish", "example_thread", "example_groove"):
        p = Project.load(POST / f"{name}.alcam")
        for n in sorted({op.tool for op in p.operations if getattr(op, "tool", None)} | {p.first_tool}):
            t = copy.deepcopy(standard_library().get(n))
            t.surface_speed, t.max_rpm = 100.0 + n, 800.0 + 10 * n
            if t.type == "drill":
                t.width = 12.0
            p.tools.append(t)
        out[f"cycle_tools_{name}"] = p
    return out


MACHINES = [Machine(), Machine(spindle_max=1000, rapid=1e9), Machine(spindle_max=1e9, rapid=1e9, tool_change=12.5)]


def preview_cases():
    parsed = []
    real_parse = preview_mod.parse

    def recording_parse(data, profile=None):
        prog = real_parse(data, profile)
        parsed.append((data, prog))
        return prog

    preview_mod.parse = recording_parse
    cases = {}
    projects = [(f.stem, f"post/{f.name}", Project.load(f)) for f in sorted(POST.glob("*.alcam"))]
    (FIXTURES / "preview").mkdir(parents=True, exist_ok=True)
    for name, p in extra_projects().items():
        (FIXTURES / "preview" / f"{name}.alcam").write_text(p.dumps())
        projects.append((name, f"preview/{name}.alcam", Project.load(FIXTURES / "preview" / f"{name}.alcam")))
    previews, moves, cycles, safety, checks = {}, {}, {}, {}, {}
    for name, file, p in projects:
        parsed.clear()
        try:
            pv = preview_mod.preview(p)
        except (ValueError, KeyError, NotImplementedError) as e:
            previews[name] = {"file": file, "raises": type(e).__name__, "message": e.args[0] if e.args else ""}
            continue
        previews[name] = {
            "file": file, "data": None if pv.data is None else pv.data.decode("ascii"), "error": pv.error,
            "error_op": pv.error_op,
            # the programs it parsed: the PRG text only when it is not `data` (a prefix, or drawing units)
            "parsed": [{"data": None if d == pv.data else d.decode("ascii"), "program": program_json(prog)}
                       for d, prog in parsed],
            "ops": [[o.op_index, [b.label for b in o.blocks], o.passes, len(o.moves)] for o in pv.ops],
        }
        segs = segments(pv)
        moves[name] = {
            "segments": [[s.kind.value, s.a[0], s.a[1], s.b[0], s.b[1], s.op_index, s.line,
                          None if s.centre is None else list(s.centre), s.block, s.pass_no] for s in segs],
            "pass_starts": pass_starts(segs),
            "polylines": [[list(q) for q in s.polyline(n)] for s in segs if s.centre is not None for n in (5, 16)][:40],
        }
        cycles[name] = []
        for m in MACHINES:
            est = estimate(p, pv, machine=m)
            cycles[name].append({"machine": [m.spindle_max, m.rapid, m.tool_change],
                                 "seconds": [[k, v] for k, v in est.seconds.items()],
                                 "tool_changes": est.tool_changes, "cutting": est.cutting, "total": est.total,
                                 "mmss": mmss(est.total)})
        safety[name] = [[f.op_index, f.kind, f.message, f.x, f.z] for f in check(p, pv)]
        checks[name] = {str(k): v for k, v in tool_warnings(p).items()}
    preview_mod.parse = real_parse
    write("preview", "cases.json", previews)
    write("moves", "cases.json", moves)
    write("cycletime", "cases.json", cycles)
    write("safety", "cases.json", safety)
    write("checks", "cases.json", checks)
    # cycletime's helpers on their own
    helpers = []
    for tool in [None, Tool(3, "x", surface_speed=100.0, max_rpm=500.0)] + standard_library().tools[::5]:
        for d in (0.0, 1e-7, 10.0, 100.0, 37.25):
            for m in MACHINES[:2]:
                helpers.append({"tool": None if tool is None else tool.number,
                                "custom": tool is not None and tool.name == "x", "d": d,
                                "machine": [m.spindle_max, m.rapid, m.tool_change], "rpm": rpm(tool, d, m)})
    mm = [[s, mmss(s)] for s in (0, 0.4, 0.5, 1.5, 59.5, 61, 3599.4, 3599.5, 3600, 7322.7, 100000)]
    write("cycletime", "helpers.json", {"rpm": helpers, "mmss": mm})
    return len(previews), sum(len(v["segments"]) for v in moves.values())


# --- tool shapes ----------------------------------------------------------------------------------------------


def tool_shape_cases():
    tools = [copy.deepcopy(t) for t in standard_library().tools]
    tools += [
        Tool(40, "Int groove", type="groove", width=0.0), Tool(41, "Thread int.", type="thread"),
        Tool(42, "Drill", type="drill", width=0.0), Tool(43, "Ball", nose_radius=0.0),
        Tool(44, "Face", front_angle=30.0, rear_angle=-20.0), Tool(45, "Odd", front_angle=-30.0, rear_angle=20.0),
        Tool(46, "Bore", type="bore", front_angle=95.0, rear_angle=10.0),
        Tool(47, "Pic", outline=[[[0, 0], [5, 5.5]], [[1, 2], [3, 4], [5, 6]]]),
        Tool(48, "Back", front_angle=200.0, rear_angle=330.0),
    ]
    out = [{"tool": json.loads(json.dumps(t.__dict__)), "internal": internal(t),
            "outline": [[list(q) for q in line] for line in outline(t)]} for t in tools]
    write("tool_shapes", "cases.json", out)
    return len(out)


def main():
    for module in ("drawing", "features", "setups", "commands", "preview", "moves", "cycletime", "safety", "checks",
                   "tool_shapes"):
        if (FIXTURES / module).exists():
            shutil.rmtree(FIXTURES / module)
    print("drawing", drawing_cases())
    print("features", features_cases())
    print("setups", setups_cases())
    print("commands", commands_cases())
    print("preview (cases, segments)", preview_cases())
    print("tool_shapes", tool_shape_cases())


if __name__ == "__main__":
    main()
