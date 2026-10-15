"""Workflow improvements 4-6: job templates, cycle time, second setup, DXF placement."""

import unittest

from alphacam.model import Arc, Line, Point
from alphacam.model.project import Billet, Project
from alphacam.preview import preview

from .test_safety import SHAFT, part


class Templates(unittest.TestCase):
    def setUp(self):
        from alphacam_gtk import templates

        self.t = templates

    def test_builtin_shaft_template_posts(self):
        p = part(SHAFT)
        n = self.t.apply(p, self.t.builtin()[0])
        self.assertEqual(n, 4)
        self.assertEqual([type(o).__name__ for o in p.operations], ["ToolChange", "AreaClear", "ToolChange", "FinishPass"])
        self.assertEqual(p.operations[1].contour, p.operations[3].contour)  # the outer profile, picked from the part
        self.assertEqual((p.operations[1].tool, p.operations[3].tool), (1, 3))
        self.assertIsNotNone(preview(p).program)

    def test_round_trip_through_a_part(self):
        p = part(SHAFT)
        self.t.apply(p, self.t.builtin()[0])
        p.operations[1].depth_of_cut = 1.5
        tpl = self.t.from_part(p, "mine")
        self.assertNotIn("contour", tpl["operations"][1])
        self.assertNotIn("start", tpl["operations"][1])
        q = part(SHAFT)
        self.t.apply(q, tpl)
        self.assertEqual(q.operations[1].depth_of_cut, 1.5)

    def test_units_convert(self):
        p = part(SHAFT)
        p.units = "inch"
        self.t.apply(p, self.t.builtin()[0])  # mm template on an inch part
        self.assertAlmostEqual(p.operations[1].depth_of_cut, round(2.0 / 25.4, 4))

    def test_bush_uses_the_bore_and_drills_through(self):
        p = Project(billet=Billet(80, 50), first_tool=12)
        pts = [(50, 0), (50, -20), (36, -20), (36, -40), (20, -40)]
        outer = [(0, 0), (80, 0), (80, -50)]
        p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(pts, pts[1:])]
        p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(outer, outer[1:])]
        for i, g in enumerate(p.geometry):
            g.id = f"g{i}"
        self.t.apply(p, self.t.builtin()[1])
        drill, bore = p.operations[1], p.operations[3]
        self.assertEqual(drill.depth, -51.0)
        self.assertEqual(sorted(bore.contour), ["g0", "g1", "g2", "g3"])


class CycleTime(unittest.TestCase):
    def test_finish_pass_time(self):
        """A finish pass takes longer with a lower spindle limit; the first tool's block counts as a tool change."""
        from alphacam.cycletime import Machine, estimate
        from alphacam.model import FinishPass

        p = part(SHAFT)
        p.operations.append(FinishPass(3, Point(120, 5), [g.id for g in p.geometry], feed=0.1))
        m = Machine(spindle_max=1000, rapid=1e9)
        est = estimate(p, preview(p), machine=m)
        self.assertGreater(est.seconds[0], 0)
        slowest = estimate(p, preview(p), machine=Machine(spindle_max=1e9, rapid=1e9))
        self.assertLess(slowest.seconds[0], est.seconds[0])  # a lower spindle limit takes longer
        self.assertEqual(est.tool_changes, 1)  # the program's first tool block: the operator loads T3

    def test_tool_speed_from_the_library(self):
        from alphacam.cycletime import Machine, rpm
        from alphacam.model.tools import Tool

        t = Tool(3, "x", surface_speed=100.0, max_rpm=500.0)
        self.assertAlmostEqual(rpm(t, 100.0, Machine()), 1000 * 100 / (3.141592653589793 * 100))
        self.assertEqual(rpm(t, 10.0, Machine()), 500.0)


class SecondSetup(unittest.TestCase):
    def test_turned_around(self):
        from alphacam.setups import part_length, turn_around

        p = part(SHAFT)
        p.geometry.append(Arc(Point(60, -30), Point(60, -40), Point(60, -35), ccw=True, id="a"))
        p.operations.append(object())
        self.assertEqual(part_length(p), 50)
        q = turn_around(p)
        self.assertEqual((q.geometry[0].a, q.geometry[0].b), (Point(0, -50), Point(40, -50)))
        self.assertEqual(q.geometry[-2].b, Point(100, 0))  # the back face is the new Z0
        arc = q.geometry[-1]
        self.assertEqual((arc.a, arc.b, arc.ccw), (Point(60, -20), Point(60, -10), False))
        self.assertEqual((q.operations, q.program_number, q.billet.length), ([], p.program_number + 1, 50))
        self.assertEqual(len(p.operations), 1)  # the first setup is untouched


class DxfPlacement(unittest.TestCase):
    DXF = ("0\nSECTION\n2\nENTITIES\n0\nLINE\n10\n100\n20\n0\n11\n100\n21\n20\n0\nLINE\n10\n100\n20\n20\n11\n75\n"
           "21\n20\n0\nCIRCLE\n10\n50\n20\n10\n40\n5\n0\nENDSEC\n0\nEOF\n")

    def test_front_face_on_z0_and_circles(self):
        from alphacam.io.dxf import read_dxf

        r = read_dxf(self.DXF, datum=True)
        g = r.project.geometry
        self.assertEqual((g[0].a, g[0].b), (Point(0, 0), Point(40, 0)))
        self.assertEqual(len([e for e in g if isinstance(e, Arc)]), 2)  # the circle, as two half arcs
        self.assertEqual(r.skipped, {})

    def test_front_on_the_left_and_turned(self):
        from alphacam.io.dxf import read_dxf

        left = read_dxf(self.DXF, front="left", datum=True).project.geometry
        self.assertEqual((left[0].a, left[0].b), (Point(0, -55), Point(40, -55)))  # the circle is the front now
        up = read_dxf(self.DXF, rotate=90, axis="lowest", datum=True).project.geometry
        self.assertTrue(all(p.x >= 0 and p.z <= 1e-9 for e in up for p in (e.a, e.b)))


class Notes(unittest.TestCase):
    def test_notes_round_trip(self):
        p = part(SHAFT)
        p.notes = "Centre drill first"
        self.assertEqual(Project.loads(p.dumps()).notes, "Centre drill first")
        self.assertNotIn("notes", part(SHAFT).to_dict())


class SetupsInOneFile(unittest.TestCase):
    def test_setup_two_round_trips_and_has_its_own_undo(self):
        from alphacam.commands import UndoStack
        from alphacam.setups import turn_around

        p = part(SHAFT)
        p.setups = [turn_around(p)]
        q = Project.loads(p.dumps())
        self.assertEqual(len(q.setups), 1)
        self.assertEqual(q.setups[0].program_number, p.program_number + 1)
        self.assertEqual(q.setups[0].to_dict(), p.setups[0].to_dict())
        # setup 1's undo leaves setup 2 alone
        one, two = UndoStack(q), UndoStack(q.setups[0])
        two.apply("x", lambda s: setattr(s, "program_number", 99))
        one.apply("y", lambda s: setattr(s, "program_number", 42))
        one.undo()
        self.assertEqual((q.program_number, q.setups[0].program_number), (p.program_number, 99))
        self.assertFalse(one.modified)
        self.assertTrue(two.modified)
