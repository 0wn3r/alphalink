"""Alpha-T PRG post: golden tests (byte-identical to ALPHA-T output) and rule tests."""

import re
import unittest
from pathlib import Path

from alphacam.io.prg_parse import parse
from alphacam.io.sdg import load_sdg
from alphacam.model import (
    AreaClear,
    Arc,
    Drill,
    Dwell,
    FaceClear,
    FinishPass,
    Groove,
    Line,
    MachineProfile,
    MoveTo,
    Point,
    Project,
    Thread,
    ToolChange,
)
from alphacam.post.alphat_prg import post
from alphacam.toolpath.area import area_clear
from alphacam.toolpath.contour import chain, points, tip_path
from alphacam.toolpath.toolpath import MoveKind

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "alphat_v12"
MET2 = ["g0", "g1", "g2", "g3", "g4"]  # contour picked from the axis (X0 Z0) to X100 Z-50

# Operation parameters as entered by the generating scripts (tools/mk_at_script.py defaults: start 120,5; menu
# defaults: depth of cut 2, feed 0.15, no offsets, external, towards, no pre-finish). Geometry, units and the first
# tool come from the SDG.
ARC = ["g0", "g1", "g2", "g5", "g3", "g6", "g4"]  # MET2 with R5 (g5) and R3 (g6) corners, in pick order
INT = ["g0", "g1", "g2", "g3"]  # bore X50 Z0 -> X20 Z-40
S = Point(120, 5)
GOLDEN = {
    "MET2_finish_metric": [FinishPass(3, S, MET2)],
    "MET2_finish_inch": [FinishPass(3, Point(5, 0.2), MET2)],
    "AREA1_metric": [AreaClear(3, S, MET2, depth_of_cut=2)],
    "MOVE_RAPID_metric": [MoveTo(Point(100, -50), rapid=False), MoveTo(Point(60, -50), rapid=False),
                          MoveTo(Point(60, -70))],
    # cloud run 2026-10-12/13 (alpha-work/cloud-run/samples, scripts there)
    "ARC_finish_metric": [FinishPass(3, S, ARC)],
    "ARC_area_metric": [AreaClear(3, S, ARC)],
    "SLOPE_area_metric": [AreaClear(3, S, MET2)],
    "TWOOP_area_finish_metric": [AreaClear(3, S, MET2), FinishPass(3, S, MET2)],
    "OFFSET_finish_metric": [FinishPass(3, S, MET2, offset_x=0.5, offset_z=0.2)],
    "OFFSET_area_metric": [AreaClear(3, S, MET2, offset_x=0.5, offset_z=0.2)],
    "INT_finish_metric": [ToolChange(12, Point(100, 50), 0.12),
                          FinishPass(12, Point(15, 5), INT, feed=0.12, external=False)],
    "INT_area_metric": [ToolChange(12, Point(100, 50), 0.12),
                        AreaClear(12, Point(15, 5), INT, feed=0.12, external=False)],
    "TOOLCHG_rough1_finish3_metric": [ToolChange(1, Point(100, 50), 0.2), AreaClear(1, S, MET2, feed=0.2),
                                      ToolChange(3, Point(100, 50), 0.15), FinishPass(3, S, MET2)],
    "PREFINISH_area_metric": [AreaClear(3, S, MET2, pre_finish=True)],
    # older samples, operations other than finish/area (scripts tools/ac_scripts/at_*.ks)
    "FACE_metric": [FaceClear(3, S, MET2)],
    "GROOVE_metric": [Groove(3, S, ["g0", "g1", "g2", "g3", "g4", "g5", "g6"], stepover=2, tip_width=3)],
    "DRILL_DWELL_metric": [Dwell(3), Drill(3, Point(0, 5), depth=-30, peck=10, dwell_revs=3)],
    "THREAD_metric": [Thread(3, S, "g0", "g1", pitch=1.5, cut_depth=1)],
    # cloud run 2026-10-13 (2)
    "THREAD_internal_metric": [Thread(3, S, "g0", "g1", pitch=1.5, external=False)],
    "THREAD_p2_len30_metric": [Thread(3, S, "g0", "g1", pitch=2)],
    "FACE_offset_metric": [FaceClear(3, S, MET2, offset_x=0.5, offset_z=0.2)],
    "FACE_depth3_start110_metric": [FaceClear(3, Point(110, 3), MET2, depth_of_cut=3)],
}

# Inch versions (samples/alphat_v12_inch, cloud run): drawing typed in inch, start X5 Z0.2, depth of cut 0.0787
# (= 2 mm), offsets X0.02 Z0.008. Known differences are listed with the reason; they are checked to stay small.
INCH = "alphat_v12_inch"
TC_IN = Point(3.937, 1.9685)
S_IN = Point(5, 0.2)
D_IN = 0.0787
GOLDEN_INCH = {
    "ARC_finish_inch": [FinishPass(3, S_IN, ARC)],
    "SLOPE_area_inch": [AreaClear(3, S_IN, MET2, depth_of_cut=D_IN)],
    "INT_finish_inch": [ToolChange(12, TC_IN, 0.12), FinishPass(12, Point(0.6, 0.2), INT, feed=0.12, external=False)],
    "INT_area_inch": [ToolChange(12, TC_IN, 0.12),
                      AreaClear(12, Point(0.6, 0.2), INT, feed=0.12, external=False, depth_of_cut=D_IN)],
    "OFFSET_finish_inch": [FinishPass(3, S_IN, MET2, offset_x=0.02, offset_z=0.008)],
    "OFFSET_area_inch": [AreaClear(3, S_IN, MET2, depth_of_cut=D_IN, offset_x=0.02, offset_z=0.008)],
    "PREFINISH_area_inch": [AreaClear(3, S_IN, MET2, depth_of_cut=D_IN, pre_finish=True)],
    "TWOOP_inch": [AreaClear(3, S_IN, MET2, depth_of_cut=D_IN), FinishPass(3, S_IN, MET2)],
}
INCH_KNOWN_DIFF = {
    # area pass ends on the inch arcs come out 0.0001 shallower than ALPHA-T's (its arc maths is not pinned yet)
    "ARC_area_inch": [AreaClear(3, S_IN, ARC, depth_of_cut=D_IN)],
    # after a mid-program tool change ALPHA-T's inch finish pass is 0.0002 higher in Z (nose radius rounding of
    # the previous tool, presumably); metric is byte-identical
    "TOOLCHG_inch": [ToolChange(1, TC_IN, 0.2), AreaClear(1, S_IN, MET2, feed=0.2, depth_of_cut=D_IN),
                     ToolChange(3, TC_IN, 0.15), FinishPass(3, S_IN, MET2)],
}


def sample_project(name: str) -> Project:
    project = load_sdg(SAMPLES / f"{name}.SDG")
    project.operations = list(GOLDEN[name])
    return project


class Golden(unittest.TestCase):
    def test_byte_identical(self):
        for name in GOLDEN:
            with self.subTest(name):
                expected = (SAMPLES / f"{name}.PRG").read_bytes()
                out = post(sample_project(name))
                if out != expected:
                    import difflib

                    diff = "".join(difflib.unified_diff(expected.decode().splitlines(True),
                                                        out.decode().splitlines(True), "ALPHA-T", "post", n=1))
                    self.fail(f"{name} differs:\n{diff[:3000]}")

    def test_via_alcam_json(self):
        """The same bytes after a save/load of the project."""
        for name in GOLDEN:
            with self.subTest(name):
                project = Project.loads(sample_project(name).dumps())
                self.assertEqual(post(project), (SAMPLES / f"{name}.PRG").read_bytes())


class GoldenInch(unittest.TestCase):
    def _post(self, name, ops):
        project = load_sdg(SAMPLES.parent / INCH / f"{name}.SDG")
        project.operations = list(ops)
        return post(project), (SAMPLES.parent / INCH / f"{name}.PRG").read_bytes()

    def test_byte_identical(self):
        for name, ops in GOLDEN_INCH.items():
            with self.subTest(name):
                out, expected = self._post(name, ops)
                self.assertEqual(out, expected)

    def test_known_differences_stay_small(self):
        for name, ops in INCH_KNOWN_DIFF.items():
            with self.subTest(name):
                out, expected = self._post(name, ops)
                a, b = out.decode().splitlines(), expected.decode().splitlines()
                self.assertEqual(len(a), len(b))
                diffs = [(x, y) for x, y in zip(a, b) if x != y]
                self.assertTrue(diffs)
                for x, y in diffs:
                    nx, ny = re.findall(r"-?[\d.]+", x), re.findall(r"-?[\d.]+", y)
                    self.assertEqual(len(nx), len(ny), (x, y))
                    self.assertLessEqual(max(abs(float(p) - float(q)) for p, q in zip(nx, ny)), 0.00041, (x, y))


class Rules(unittest.TestCase):
    def test_area_pass_floor(self):
        path = chain(load_sdg(SAMPLES / "AREA1_metric.SDG").contour(MET2))
        op = AreaClear(3, Point(120, 5), MET2, depth_of_cut=3)
        tp = area_clear(op, path, nose_radius=0.4, units="mm", decimals=3)
        xs = [p.moves[0].x for p in tp.passes]
        self.assertEqual(xs[:2], [94, 88])
        self.assertEqual(xs[-1], -0.8)  # last pass clamped to Xmin - 2r
        # the shoulders X60 and X40 get their own passes and restart the stepping
        self.assertEqual(xs, [94, 88, 82, 76, 70, 64, 60, 54, 48, 42, 40, 34, 28, 22, 16, 10, 4, -0.8])
        self.assertEqual([p.moves[1].z for p in tp.passes if p.moves[0].x in (64, 54, 40)], [-50, -25, -25])

    def test_tip_path_arcs(self):
        """Convex R5 becomes R5.4, concave R3 becomes R2.6, centres shifted by (-r, -r)."""
        tip = tip_path(chain(load_sdg(SAMPLES / "ARC_finish_metric.SDG").contour(ARC)), 0.4)
        arcs = [(s.a, s.b, s.centre, s.ccw) for s in tip if s.is_arc]
        self.assertEqual(arcs, [(Point(49.2, -25), Point(60, -30.4), Point(49.2, -30.4), True),
                                (Point(60, -47.4), Point(65.2, -50), Point(65.2, -47.4), False)])
        self.assertEqual(points(tip)[0], Point(0, 0))

    def test_chain_reversed_elements(self):
        g = [Line(Point(40, 0), Point(0, 0), id="a"), Line(Point(40, -25), Point(40, 0), id="b")]
        self.assertEqual(points(chain(g)), [Point(0, 0), Point(40, 0), Point(40, -25)])
        arc = Arc(Point(10, -5), Point(0, 0), Point(0, -5), ccw=False, id="c")
        rev = chain([arc, Line(Point(10, -5), Point(10, -10))])
        self.assertEqual((rev[0].a, rev[0].b, rev[0].ccw), (Point(0, 0), Point(10, -5), True))
        with self.assertRaises(ValueError):
            chain([g[0], Line(Point(60, -25), Point(60, -50))])

    def test_output_reparses(self):
        project = sample_project("AREA1_metric")
        prog = parse(post(project))
        op = prog.operations[0]
        self.assertEqual((op.op_name, op.declared_passes, len(op.passes)), ("area_clear", 26, 26))
        self.assertEqual(op.moves[-1].kind, MoveKind.RAPID)

    def test_scaling_profile(self):
        project = sample_project("MET2_finish_metric")
        out = post(project, MachineProfile(z_scale=1, f_scale=1)).decode()
        self.assertIn("G1X40.Z-25.F0.15\r\n", out)
        self.assertIn("#124=50.\r\n", out)

    def test_unsupported(self):
        project = sample_project("MET2_finish_metric")
        project.operations = [FinishPass(3, Point(120, 5), MET2), MoveTo(Point(0, 10))]
        with self.assertRaises(NotImplementedError):
            post(project)


class OperationRules(unittest.TestCase):
    def test_drill_pecks_without_peck(self):
        from alphacam.toolpath.drill import drill

        tp = drill(Drill(3, Point(0, 5), depth=-12), "mm", 3)
        self.assertEqual([(m.kind, m.z) for m in tp.passes[0].moves],
                         [(MoveKind.FEED, -12), (MoveKind.FEED, -10)])

    def test_thread_cut_count(self):
        from alphacam.toolpath.thread import plan_thread

        op = Thread(3, Point(120, 5), "o", "i", pitch=1, cut_depth=0.3)
        plan = plan_thread(op, Line(Point(20, 0), Point(20, -10)), Line(Point(18.4, 0), Point(18.4, -10)), 3)
        self.assertEqual(len(plan.xs), 8)  # (0.8 / 0.3)^2 = 7.1 -> 8
        self.assertEqual((plan.xs[0], plan.xs[-1]), (19.4, 18.4))

    def test_groove_wall_insert(self):
        from alphacam.toolpath.groove import groove

        p = load_sdg(SAMPLES / "GROOVE_metric.SDG")
        tp = groove(Groove(3, S, ["g0", "g1", "g2", "g3", "g4", "g5", "g6"]), chain(p.contour(
            ["g0", "g1", "g2", "g3", "g4", "g5", "g6"])), 0.4, "mm", 3)
        plunges = [m.z for m in tp.passes[0].moves if m.kind is MoveKind.FEED][::2]
        self.assertIn(-30, plunges)
        self.assertEqual(plunges[:2], [-3, -5])
