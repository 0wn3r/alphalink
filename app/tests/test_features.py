"""Feature helpers (workflow improvements, step 3): standard threads and every outside corner in one go."""

import unittest

from alphacam.features import METRIC, ThreadSpec, add_thread, all_corners, nearest_size, outside_corners
from alphacam.model import Line, Point
from alphacam_gtk.op_factory import default_op

from .test_safety import SHAFT, part


class Threads(unittest.TestCase):
    def test_iso_dimensions(self):
        s = ThreadSpec(40, 1.5)
        self.assertEqual((s.minor, s.depth, s.relief_diameter, s.relief_width), (38.16, 0.92, 37.8, 4.5))
        self.assertEqual(ThreadSpec(20, 2.5).name, "M20")
        self.assertEqual(s.name, "M40 × 1.5")
        self.assertEqual(METRIC[12][0], 1.75)

    def test_nearest_size(self):
        self.assertEqual(nearest_size(39.2), 39)
        self.assertEqual(nearest_size(41), 40)
        self.assertEqual(ThreadSpec(40, 3.0).name, "M40 × 3")  # M40 has fine pitches only

    def test_thread_with_relief(self):
        p = part(SHAFT)
        spec = ThreadSpec(42, 1.5)
        minor = add_thread(p, "g1", spec)
        lines = {g.id: g for g in p.geometry}
        # the line and its neighbours follow the nominal diameter
        self.assertEqual(lines["g0"].b, Point(42, 0))
        self.assertEqual(lines["g1"].a, Point(42, 0))
        self.assertEqual(lines["g1"].b, Point(42, -20.5))
        self.assertEqual(lines["g2"].a, Point(42, -25))
        # the groove: down to the relief diameter, along, back up
        groove = [g for g in p.geometry if isinstance(g, Line) and spec.relief_diameter in (g.a.x, g.b.x)]
        self.assertEqual(len(groove), 3)
        m = lines.get(minor) or next(g for g in p.geometry if g.id == minor)
        self.assertEqual((m.a, m.b), (Point(40.16, 0), Point(40.16, -22.75)))
        op = default_op("thread", p, 0)
        self.assertEqual((op.outer_line, op.inner_line), ("g1", minor))

    def test_thread_without_relief(self):
        p = part(SHAFT)
        minor = add_thread(p, "g1", ThreadSpec(40, 1.5), relief=False)
        m = next(g for g in p.geometry if g.id == minor)
        self.assertEqual(m.b, Point(38.16, -25))
        self.assertEqual(len(p.geometry), len(SHAFT))

    def test_refuses(self):
        p = part(SHAFT)
        with self.assertRaises(ValueError):
            add_thread(p, "g0", ThreadSpec(40, 1.5))  # the face, not a diameter
        with self.assertRaises(ValueError):
            add_thread(p, "g1", ThreadSpec(40, 9.0))  # a 27 mm groove on a 25 mm line


class Corners(unittest.TestCase):
    def test_outside_corners_only(self):
        self.assertEqual(outside_corners(part(SHAFT)), [Point(40, 0), Point(60, -25)])

    def test_chamfer_all(self):
        p = part(SHAFT)
        self.assertEqual(all_corners(p, "chamfer", 1), 2)
        # each corner is two chamfer ends now
        self.assertEqual(outside_corners(p), [Point(38, 0), Point(40, -1), Point(58, -25), Point(60, -26)])

    def test_round_all(self):
        p = part(SHAFT)
        self.assertEqual(all_corners(p, "fillet", 2), 2)

    def test_too_big_is_skipped(self):
        p = part(SHAFT)
        self.assertEqual(all_corners(p, "chamfer", 30), 0)


class InchThreads(unittest.TestCase):
    def test_unified_dimensions(self):
        from alphacam.features import nearest, sizes, spec

        t = spec("UNC", "1/2", 0, "inch")
        self.assertEqual((t.d, t.name), (0.5, "1/2-13 UNC"))
        self.assertAlmostEqual(t.pitch, 1 / 13)
        self.assertEqual(t.minor, round(0.5 - 1.19078 / 13, 4))
        self.assertEqual(spec("UNF", "1/2").name, "1/2-20 UNF")
        self.assertNotIn("2", sizes("UNF"))  # no UNF above 1-1/2
        self.assertEqual(nearest("UNC", 12.6), "1/2")  # a mm drawing
        self.assertEqual(nearest("UNC", 0.74, "inch"), "3/4")

    def test_converted_between_units(self):
        from alphacam.features import spec

        t = spec("UNC", "1", 0, "mm")  # 1-8 UNC on a mm part
        self.assertEqual((t.d, round(t.pitch, 6)), (25.4, 3.175))
        m = spec("M", 40, 2, "inch")  # M40 x 1.5 on an inch part
        self.assertEqual(m.name, "M40 × 1.5")
        self.assertAlmostEqual(m.pitch, 1.5 / 25.4)

    def test_inch_part(self):
        from alphacam.features import spec
        from alphacam.model.project import Billet, Project

        p = Project(billet=Billet(2, 3), first_tool=3, units="inch")
        pts = [(0, 0), (0.5, 0), (0.5, -1.0), (1.0, -1.0)]
        p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(pts, pts[1:])]
        for i, g in enumerate(p.geometry):
            g.id = f"g{i}"
        t = spec("UNC", "1/2", 0, "inch")
        minor = add_thread(p, "g1", t)
        m = next(g for g in p.geometry if g.id == minor)
        self.assertEqual(m.a, Point(t.minor, 0))
        self.assertIn(Point(t.relief_diameter, round(-1.0 + t.relief_width, 3)),
                      [g.a for g in p.geometry] + [g.b for g in p.geometry])
