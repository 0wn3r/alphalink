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


class Construction(unittest.TestCase):
    """ALPHA-T's other drawing commands (German manual pp. 20-34)."""

    def setUp(self):
        from alphacam.model.project import Project

        self.p = Project()

    def test_circle_is_two_halves_split_at_3_oclock(self):
        a, b = drawing.add_circle(self.p, Point(100, -50), 10)
        g1, g2 = self.p.geometry
        self.assertEqual((g1.a, g1.b, g2.a, g2.b), (Point(100, -40), Point(100, -60), Point(100, -60), Point(100, -40)))

    def test_tangents_touch_the_circle(self):
        arc = drawing.add_circle(self.p, Point(100, -50), 10)[0]
        for a, t in drawing.tangents_point_arc(self.p, Point(100, -80), arc):
            self.assertAlmostEqual(t.distance(Point(100, -50)), 10, places=5)
            # tangent: radius at t is perpendicular to the line
            v1 = ((t.x - a.x) / 2, t.z - a.z)
            v2 = ((t.x - 100) / 2, t.z + 50)
            self.assertAlmostEqual(v1[0] * v2[0] + v1[1] * v2[1], 0, places=4)
        other = drawing.add_circle(self.p, Point(60, -10), 5)[0]
        lines = drawing.tangents_arc_arc(self.p, arc, other)
        self.assertEqual(len(lines), 4)
        for t1, t2 in lines:
            self.assertAlmostEqual(t1.distance(Point(100, -50)), 10, places=5)
            self.assertAlmostEqual(t2.distance(Point(60, -10)), 5, places=5)

    def test_parallel_and_angle_tangent(self):
        line = drawing.add_line(self.p, Point(40, 0), Point(40, -20))
        (a1, b1), (a2, b2) = drawing.parallels(self.p, line, 2)
        self.assertEqual({a1.x, a2.x}, {44.0, 36.0})
        self.assertAlmostEqual(a1.distance(b1), 30, places=5)  # 1.5 x the 20 mm line
        arc = drawing.add_circle(self.p, Point(100, -50), 10)[0]
        zs = sorted(a.z for a, _b in drawing.tangents_at_angle(self.p, arc, 90))
        self.assertEqual(zs, [-60.0, -40.0])

    def test_split_mirror_blend(self):
        arc = drawing.add_circle(self.p, Point(100, -50), 10)[0]
        drawing.split_arc(self.p, arc, Point(120, -50))
        self.assertEqual(len(self.p.geometry), 3)
        drawing.mirror_z(self.p, -100)
        self.assertEqual(len(self.p.geometry), 6)
        self.assertIn(Point(100, -160), [g.a for g in self.p.geometry] + [g.b for g in self.p.geometry])
        sols = drawing.blends(self.p, ("point", Point(0, 0)), ("point", Point(0, -20)), 15)
        self.assertEqual(sorted(round(c.x, 3) for _a, c, _b in sols), [-22.361, 22.361])
        gid = drawing.add_blend(self.p, *sols[0])
        self.assertTrue(gid)


class EditElement(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        for a, b in (((0, 0), (40, 0)), ((40, 0), (40, -25)), ((40, -25), (60, -25))):
            drawing.add_line(self.p, Point(*a), Point(*b))
        self.ids = [g.id for g in self.p.geometry]

    def test_connected_ends_follow(self):
        drawing.edit_element(self.p, self.ids[1], Point(42, 0), Point(42, -30))
        g = self.p.geometry
        self.assertEqual((g[0].b, g[1].a, g[1].b, g[2].a), (Point(42, 0), Point(42, 0), Point(42, -30), Point(42, -30)))
        self.assertEqual(g[2].b, Point(60, -25))

    def test_without_follow_only_that_element(self):
        drawing.edit_element(self.p, self.ids[1], Point(42, 0), Point(42, -30), follow=False)
        self.assertEqual(self.p.geometry[0].b, Point(40, 0))

    def test_zero_length_refused(self):
        with self.assertRaises(ValueError):
            drawing.edit_element(self.p, self.ids[0], Point(5, 5), Point(5, 5))

    def test_arc_keeps_side_and_follows(self):
        p = Project()
        drawing.add_line(p, Point(0, 0), Point(30, 0))
        arc_id = drawing.add_arc_3p(p, Point(30, 0), Point(30 + 10 * (1 - math.cos(math.pi / 4)) * 2, -10 * math.sin(math.pi / 4)), Point(50, -10))
        arc = next(g for g in p.geometry if g.id == arc_id)
        r, ccw, side_before = arc.radius(), arc.ccw, arc.centre
        line_id = p.geometry[0].id
        drawing.edit_element(p, line_id, Point(0, 0), Point(32, 0))  # the arc start moves with the line end
        arc = next(g for g in p.geometry if g.id == arc_id)
        self.assertEqual(arc.a, Point(32, 0))
        self.assertAlmostEqual(arc.radius(), r, places=5)
        self.assertAlmostEqual(arc.a.distance(arc.centre), arc.b.distance(arc.centre), places=5)
        self.assertEqual(arc.ccw, ccw)
        self.assertLess(abs(arc.centre.z - side_before.z) + abs(arc.centre.x - side_before.x), 5)

    def test_arc_new_radius_and_too_small(self):
        p = Project()
        arc_id = drawing.add_arc_3p(p, Point(30, 0), Point(30 + 20 * (1 - math.cos(math.pi / 4)), -10 * math.sin(math.pi / 4)), Point(50, -10))
        arc = p.geometry[0]
        drawing.edit_element(p, arc_id, arc.a, arc.b, radius=15)
        self.assertAlmostEqual(p.geometry[0].radius(), 15, places=5)
        with self.assertRaises(ValueError):
            drawing.edit_element(p, arc_id, Point(30, 0), Point(50, -10), radius=2)
