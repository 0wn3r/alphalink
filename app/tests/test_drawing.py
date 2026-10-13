import unittest

from alphacam import drawing
from alphacam.model import Arc, Line, Point, Project

from .test_post import SAMPLES, sample_project
from alphacam.io.sdg import load_sdg


def met2() -> Project:
    p = Project()
    pts = [Point(0, 0), Point(40, 0), Point(40, -25), Point(60, -25), Point(60, -50), Point(100, -50)]
    for a, b in zip(pts, pts[1:]):
        drawing.add_line(p, a, b)
    return p


class Drawing(unittest.TestCase):
    def test_fillets_reproduce_the_arc_sample(self):
        p = met2()
        drawing.fillet(p, Point(60, -25), 5)
        drawing.fillet(p, Point(60, -50), 3)
        ref = load_sdg(SAMPLES / "ARC_finish_metric.SDG")
        key = lambda g: (type(g).__name__, g.a, g.b, getattr(g, "centre", None), getattr(g, "ccw", None))
        self.assertEqual(sorted(map(key, p.geometry), key=str), sorted(map(key, ref.geometry), key=str))

    def test_fillet_then_post_matches_alpha_t(self):
        from alphacam.model import FinishPass
        from alphacam.post.alphat_prg import post

        p = met2()
        arc1 = drawing.fillet(p, Point(60, -25), 5)
        arc2 = drawing.fillet(p, Point(60, -50), 3)
        ref = load_sdg(SAMPLES / "ARC_finish_metric.SDG")
        p.billet, p.first_tool = ref.billet, ref.first_tool
        p.operations = [FinishPass(3, Point(120, 5), ["g0", "g1", "g2", arc1, "g3", arc2, "g4"])]
        self.assertEqual(post(p), (SAMPLES / "ARC_finish_metric.PRG").read_bytes())

    def test_chamfer(self):
        p = met2()
        gid = drawing.chamfer(p, Point(40, 0), 1)
        ch = next(g for g in p.geometry if g.id == gid)
        self.assertEqual((ch.a, ch.b), (Point(38, 0), Point(40, -1)))
        self.assertEqual(p.geometry[0].b, Point(38, 0))
        self.assertEqual(p.geometry[1].a, Point(40, -1))

    def test_errors(self):
        p = met2()
        with self.assertRaises(ValueError):
            drawing.fillet(p, Point(0, 0), 1)  # only one line there
        with self.assertRaises(ValueError):
            drawing.fillet(p, Point(40, 0), 30)  # longer than the 20 mm face
        with self.assertRaises(ValueError):
            drawing.add_line(p, Point(1, 1), Point(1, 1))

    def test_snap_and_lock(self):
        p = met2()
        self.assertEqual(drawing.snap(p, Point(40.6, -24.8), tol=0.5), (Point(40, -25), "end"))
        self.assertEqual(drawing.snap(p, Point(0.6, -70.2), tol=0.5, grid=1.0), (Point(0, -70), "axis"))
        self.assertEqual(drawing.snap(p, Point(73.2, -70.3), tol=0.5, grid=1.0), (Point(74, -70), "grid"))
        self.assertEqual(drawing.lock_direction(Point(100, -50), Point(101.2, -80)), Point(100, -80))  # along Z
        self.assertEqual(drawing.lock_direction(Point(100, -50), Point(140, -50.4)), Point(140, -50))  # along X
        p45 = drawing.lock_direction(Point(100, -50), Point(119, -60.5))
        self.assertAlmostEqual((p45.x - 100) / 2, -(p45.z + 50), places=5)  # 45 degrees: radial = axial
        self.assertEqual(drawing.lock_direction(Point(0, 0), Point(30, -40)), Point(30, -40))  # 37 deg: free

    def test_delete_drops_from_contours(self):
        p = sample_project("MET2_finish_metric")
        drawing.delete_element(p, "g4")
        self.assertEqual(p.operations[0].contour, ["g0", "g1", "g2", "g3"])

    def test_pick_order(self):
        p = met2()
        self.assertEqual(drawing.pick_order(p, ["g2", "g3", "g1", "g4", "g0"]), ["g0", "g1", "g2", "g3", "g4"])
        with self.assertRaises(ValueError):
            drawing.pick_order(p, ["g0", "g3"])
