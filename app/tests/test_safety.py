"""The safety check before export (alphacam/safety.py): silent on ALPHA-T's own programs, loud on real problems."""

import unittest

from alphacam.model import AreaClear, FinishPass, Line, Point
from alphacam.model.project import Billet, Project
from alphacam.preview import preview
from alphacam.safety import check

from .test_post import GOLDEN, sample_project


def part(points, billet=Billet(100, 60)):
    p = Project(billet=billet, first_tool=3)
    p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(points, points[1:])]
    for i, g in enumerate(p.geometry):
        g.id = f"g{i}"
    return p


SHAFT = [(0, 0), (40, 0), (40, -25), (60, -25), (60, -50), (100, -50)]


def kinds(p):
    return {f.kind for f in check(p, preview(p))}


class NoFalseAlarms(unittest.TestCase):
    def test_alpha_t_samples_are_clean(self):
        for name in GOLDEN:
            with self.subTest(name):
                p = sample_project(name)
                if name in ("FINISH_dive2_metric", "FINISH_int_dive2_metric", "FINISH_relief_groove_metric",
                            "FINISH_radius_groove_metric", "JOB_shaft_metric"):  # the steep wall really stays uncut (ALPHA-T's collision check)
                    self.assertEqual({f.kind for f in check(p, preview(p))}, {"uncut"})
                    continue
                self.assertEqual(check(p, preview(p)), [])


class Fires(unittest.TestCase):
    def test_negative_allowance_gouges(self):
        p = part(SHAFT)
        p.operations.append(FinishPass(3, Point(120, 5), [g.id for g in p.geometry], offset_x=-1.0))
        self.assertIn("gouge", kinds(p))

    def test_contour_behind_the_billet_hits_the_jaws(self):
        p = part([(0, 0), (40, 0), (40, -70), (100, -70)], Billet(100, 60))
        p.operations.append(FinishPass(3, Point(120, 5), [g.id for g in p.geometry]))
        self.assertIn("chuck", kinds(p))

    def test_finishing_from_the_centre_hits_the_tailstock(self):
        p = part(SHAFT, Billet(100, 60, tailstock=True))
        p.operations.append(FinishPass(3, Point(120, 5), [g.id for g in p.geometry]))  # starts at X0 Z0
        self.assertIn("tailstock", kinds(p))
        p.billet.tailstock = False
        self.assertNotIn("tailstock", kinds(p))

    def test_a_dive_too_steep_for_the_tool(self):
        dive = [(0, 0), (40, 0), (40, -20), (30, -30), (30, -40), (60, -40), (100, -40)]
        p = part(dive)
        p.operations.append(FinishPass(1, Point(120, 5), [g.id for g in p.geometry]))  # 80° rougher clears 7°
        self.assertIn("uncut", kinds(p))  # ALPHA-T's collision check goes down at 7° and leaves the rest
        p.operations[0].tool = 4  # 35° finisher clears 51°: fine
        self.assertNotIn("uncut", kinds(p))
        self.assertNotIn("angle", kinds(p))

    def test_area_clear_on_the_same_dive_is_clean_with_a_35_degree_tool(self):
        dive = [(0, 0), (40, 0), (40, -20), (30, -30), (30, -40), (60, -40), (100, -40)]
        p = part(dive)
        p.operations.append(AreaClear(4, Point(120, 5), [g.id for g in p.geometry]))
        self.assertNotIn("gouge", kinds(p))


class TipAngle(unittest.TestCase):
    """ALPHA-T's own tool test before an area clear or face clear (RE_NOTES Progress 42)."""

    def test_the_rule_on_alpha_t_standard_tools(self):
        from alphacam.safety import tip_angle_ok

        self.assertTrue(tip_angle_ok(87, 7))  # rougher, external, towards the chuck
        self.assertFalse(tip_angle_ok(87, 7, towards=False))
        self.assertTrue(tip_angle_ok(93, 173, towards=False))  # left-hand rougher, away
        self.assertFalse(tip_angle_ok(93, 173))
        self.assertTrue(tip_angle_ok(-87, -7, external=False))  # boring bar
        self.assertFalse(tip_angle_ok(-87, -7))
        self.assertTrue(tip_angle_ok(120, 60))  # neutral: towards only
        self.assertFalse(tip_angle_ok(120, 60, towards=False))
        self.assertFalse(tip_angle_ok(7, 87))  # front edge flatter than the rear one
        self.assertTrue(tip_angle_ok(0, 0, towards=False))  # no angles: no test (and no collision check)
        # face clear: AWAY refused tools 3, 9 and 19 in ALPHA-T (E411 sample)
        for front, rear in ((87, 31), (118, 62), (28, -28)):
            self.assertFalse(tip_angle_ok(front, rear, towards=False, face=True))
        self.assertTrue(tip_angle_ok(28, -28, face=True))
        self.assertFalse(tip_angle_ok(118, 62, face=True))

    def test_area_clear_away_with_a_right_hand_rougher(self):
        p = part(SHAFT)
        p.operations.append(AreaClear(1, Point(120, 5), [g.id for g in p.geometry]))
        self.assertNotIn("tip", kinds(p))
        p.operations[0].towards = False
        self.assertIn("tip", kinds(p))
