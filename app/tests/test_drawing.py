import math
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

    def test_chamfer_angle(self):
        """2 x 30 deg on the X40 Z0 corner: 2 along the diameter (Z), 2 tan 30 = 1.1547 down the face (radius)."""
        p = met2()
        gid = drawing.chamfer(p, Point(40, 0), 2, 30)
        ch = next(g for g in p.geometry if g.id == gid)
        self.assertAlmostEqual(ch.a.x, 40 - 2 * 2 * math.tan(math.radians(30)), places=5)
        self.assertEqual((ch.a.z, ch.b), (0, Point(40, -2)))
        with self.assertRaises(ValueError):
            drawing.chamfer(met2(), Point(40, 0), 1, 90)

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


class MoreDrawing(unittest.TestCase):
    def test_arc_3p_matches_fillet(self):
        p = met2()
        gid = drawing.fillet(p, Point(60, -25), 5)
        fil = next(g for g in p.geometry if g.id == gid)
        q = Project()
        import math

        mid = Point(2 * (25 + 5 * math.cos(math.radians(45))), -30 + 5 * math.sin(math.radians(45)))
        new_id = drawing.add_arc_3p(q, fil.a, mid, fil.b)
        a3 = next(g for g in q.geometry if g.id == new_id)
        self.assertEqual((a3.a, a3.b, a3.centre, a3.ccw), (fil.a, fil.b, fil.centre, fil.ccw))
        with self.assertRaises(ValueError):
            drawing.add_arc_3p(q, Point(0, 0), Point(10, -5), Point(20, -10))

    def test_corner_trims_and_extends(self):
        p = Project()
        a = drawing.add_line(p, Point(40, 2), Point(40, -20))   # overshoots Z0 and stops short of Z-25
        b = drawing.add_line(p, Point(30, -25), Point(60, -25))  # starts inside X40
        self.assertEqual(drawing.corner(p, a, b), Point(40, -25))
        la, lb = p.geometry
        self.assertEqual((la.a, la.b), (Point(40, 2), Point(40, -25)))
        self.assertEqual((lb.a, lb.b), (Point(40, -25), Point(60, -25)))
        with self.assertRaises(ValueError):
            drawing.corner(p, a, a)
