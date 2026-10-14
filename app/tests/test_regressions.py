"""Bugs found by the bug hunt of 2026-10-14, kept from coming back."""

import unittest

from alphacam import drawing
from alphacam.model import AreaClear, FaceClear, FinishPass, Groove, Line, Point, Project, Thread
from alphacam.post.alphat_prg import post
from alphacam.preview import preview
from tests.test_post import MET2, S, sample_project


def _profile(points):
    p = Project()
    for a, b in zip(points, points[1:]):
        drawing.add_line(p, Point(*a), Point(*b))
    return p


class Regressions(unittest.TestCase):
    def test_groove_on_unrounded_z_ends(self):
        """-34.800000000000004 (from @dX,dZ sums) used to keep the plunge loop going for ever."""
        p = _profile([(60, 0), (60, -29.6), (40, -29.6), (40, -34.800000000000004), (60, -34.800000000000004)])
        p.operations = [Groove(3, S, [g.id for g in p.geometry])]
        self.assertTrue(post(p))

    def test_operation_without_passes_is_an_error_not_NNone(self):
        p = sample_project("MET2_finish_metric")
        p.operations = [FaceClear(3, S, ["g0"])]  # only the front face: nothing to cut
        with self.assertRaises(ValueError):
            post(p)
        pv = preview(p)
        self.assertIsNotNone(pv.error)
        self.assertNotIn(b"NNone", pv.data or b"")

    def test_preview_blocks_for_face_prefinish_and_spring_passes(self):
        p = sample_project("MET2_finish_metric")
        p.operations = [FaceClear(3, S, list(MET2), pre_finish=True), FinishPass(3, S, list(MET2))]
        pv = preview(p)
        self.assertEqual([b.op_name for b in pv.for_op(1).blocks], ["finish"])
        t = sample_project("THREAD_spring2_metric")
        t.operations = [Thread(3, S, "g0", "g1", pitch=1.5, spring_passes=2), FinishPass(3, S, ["g0"])]
        pv = preview(t)
        self.assertEqual([b.op_name for b in pv.for_op(0).blocks], ["thread", "thread_spring"])

    def test_fillet_using_up_a_line(self):
        p = _profile([(0, 0), (40, 0), (40, -10), (60, -10)])
        drawing.fillet(p, Point(40, -10), 10)  # uses up the 10 mm (radius) step X40 -> X60 completely
        self.assertFalse([g for g in p.geometry if isinstance(g, Line) and g.a == g.b])

    def test_blend_with_the_arcs_own_radius(self):
        p = Project()
        arc = drawing.add_circle(p, Point(100, -50), 5)[0]
        self.assertIsInstance(drawing.blends(p, ("point", Point(110, -50)), ("arc", arc, False), 5), list)

    def test_sdg_writer_refuses_more_than_255_elements(self):
        from alphacam.io.sdg_write import write_sdg

        p = _profile([(i % 2 * 10, -i) for i in range(300)])
        with self.assertRaises(ValueError):
            write_sdg(p)

    def test_text_with_brackets_survives_saving(self):
        from alphacam.model.geometry import Text

        p = Project()
        p.texts = [Text(Point(1, 2), "Bore [ H7 ]   ,   deburr")]
        self.assertEqual(Project.loads(p.dumps()).texts[0].text, "Bore [ H7 ]   ,   deburr")

    def test_typing_errors_do_not_freeze(self):
        p = sample_project("THREAD_metric")
        p.operations = [Thread(3, S, "g0", "g1", pitch=1.5, cut_depth=0.01)]  # a million cuts
        with self.assertRaises(ValueError):
            post(p)
        q = sample_project("MET2_finish_metric")
        q.operations = [AreaClear(3, S, list(MET2), depth_of_cut=0.001)]
        with self.assertRaises(ValueError):
            post(q)

    def test_inch_defaults_for_new_operations(self):
        from alphacam.model.project import Billet
        from alphacam_gtk.op_factory import default_op

        p = Project(units="inch", billet=Billet(diameter=1.5, length=2.0))
        drawing.add_line(p, Point(0, 0), Point(1, 0))
        area = default_op("area_clear", p, 0)
        self.assertAlmostEqual(area.start.x, 1.5 + 20 / 25.4, places=3)
        groove = default_op("groove", p, 0)
        self.assertLess(groove.tip_width, 0.2)


class SmarterDefaults(unittest.TestCase):
    """Workflow quick wins: new operations pick the outer profile, the groove, the thread lines."""

    def groove_part(self):
        from alphacam.model import Line, Point
        from alphacam.model.project import Billet, Project

        p = Project(billet=Billet(60, 60))
        pts = [(0, 0), (40, 0), (40, -20), (34, -20), (34, -26), (40, -26), (40, -40), (60, -40)]
        p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(pts, pts[1:])]
        p.geometry.append(Line(Point(38.2, 0), Point(38.2, -18)))  # the thread's minor diameter, loose
        for i, g in enumerate(p.geometry):
            g.id = f"g{i}"
        return p

    def test_area_clear_takes_only_the_profile(self):
        from alphacam_gtk.op_factory import default_op

        op = default_op("area_clear", self.groove_part(), 0)
        self.assertEqual(op.contour, [f"g{i}" for i in range(7)])

    def test_groove_preselects_the_groove_and_its_neighbours(self):
        from alphacam_gtk.op_factory import default_op

        op = default_op("groove", self.groove_part(), 0)
        self.assertEqual(op.contour, ["g1", "g2", "g3", "g4", "g5"])

    def test_thread_takes_the_major_and_minor_lines(self):
        from alphacam_gtk.op_factory import default_op

        op = default_op("thread", self.groove_part(), 0)
        self.assertEqual((op.outer_line, op.inner_line), ("g1", "g7"))


class QuickWins(unittest.TestCase):
    def test_tool_for_picks_a_grooving_tool_and_a_thread_tool(self):
        from alphacam.model.tools import standard_library
        from alphacam_gtk.op_factory import tool_for

        p = SmarterDefaults().groove_part()
        lib = standard_library()
        self.assertEqual(lib.get(tool_for("groove", p, 0)).type, "groove")
        self.assertEqual(lib.get(tool_for("thread", p, 0)).type, "thread")
        self.assertIsNone(tool_for("area_clear", p, 0))  # T1 turns: no change needed

    def test_tool_warnings(self):
        from alphacam.checks import tool_warnings
        from alphacam.model import ToolChange
        from alphacam_gtk.op_factory import default_op

        p = SmarterDefaults().groove_part()
        p.operations += [default_op("groove", p, 0)]  # T1, a turning tool
        w = tool_warnings(p)
        self.assertTrue(any("not a grooving tool" in m for m in w[0]))
        p.operations.insert(0, ToolChange(14))
        p.operations[1].tool = 14
        p.operations[1].tip_width = 9.0
        w = tool_warnings(p)
        self.assertTrue(any("tip width" in m for m in w[1]), w)

    def test_alphat_name(self):
        from alphacam_gtk.window import alphat_name

        self.assertEqual(alphat_name("shaft v2.final"), "SHAFTV2F")
        self.assertEqual(alphat_name("вал"), "PART")
