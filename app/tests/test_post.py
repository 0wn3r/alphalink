"""Alpha-T PRG post: golden tests (byte-identical to ALPHA-T output) and rule tests."""

import unittest
from pathlib import Path

from alphacam.io.prg_parse import parse
from alphacam.io.sdg import load_sdg
from alphacam.model import AreaClear, Arc, FinishPass, Line, MachineProfile, MoveTo, Point, Project
from alphacam.post.alphat_prg import post
from alphacam.toolpath.area import area_clear
from alphacam.toolpath.contour import chain
from alphacam.toolpath.toolpath import MoveKind

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "alphat_v12"
MET2 = ["g0", "g1", "g2", "g3", "g4"]  # contour picked from the axis (X0 Z0) to X100 Z-50

# Operation parameters as entered by the generating scripts (tools/mk_at_script.py defaults: start 120,5; menu
# defaults: depth of cut 2, feed 0.15, no offsets, external, towards, no pre-finish). Geometry, units and the first
# tool come from the SDG.
GOLDEN = {
    "MET2_finish_metric": [FinishPass(3, Point(120, 5), MET2)],
    "MET2_finish_inch": [FinishPass(3, Point(5, 0.2), MET2)],
    "AREA1_metric": [AreaClear(3, Point(120, 5), MET2, depth_of_cut=2)],
    "MOVE_RAPID_metric": [MoveTo(Point(100, -50), rapid=False), MoveTo(Point(60, -50), rapid=False),
                          MoveTo(Point(60, -70))],
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


class Rules(unittest.TestCase):
    def test_area_pass_floor(self):
        pts = chain(load_sdg(SAMPLES / "AREA1_metric.SDG").contour(MET2))
        op = AreaClear(3, Point(120, 5), MET2, depth_of_cut=3)
        path = area_clear(op, pts, nose_radius=0.4, units="mm", decimals=3)
        xs = [p.moves[0].x for p in path.passes]
        self.assertEqual(xs[:2], [94, 88])
        self.assertEqual(xs[-1], -0.8)  # last pass clamped to Xmin - 2r
        self.assertEqual(len(xs), 17)  # ceil(100.8 / 6)
        self.assertEqual([p.moves[1].z for p in path.passes if p.moves[0].x in (64, 58, 40)], [-50, -25, -25])

    def test_chain_reversed_elements(self):
        g = [Line(Point(40, 0), Point(0, 0), id="a"), Line(Point(40, -25), Point(40, 0), id="b")]
        self.assertEqual(chain(g), [Point(0, 0), Point(40, 0), Point(40, -25)])
        with self.assertRaises(ValueError):
            chain([g[0], Line(Point(60, -25), Point(60, -50))])
        with self.assertRaises(NotImplementedError):
            chain([Arc(Point(0, 0), Point(10, -5), Point(0, -5))])

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
        project.operations = [FinishPass(3, Point(120, 5), MET2, offset=0.5)]
        with self.assertRaises(NotImplementedError):
            post(project)
