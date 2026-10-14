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
        self.assertIn("angle", kinds(p))
        p.operations[0].tool = 4  # 35° finisher clears 51°: fine
        self.assertNotIn("angle", kinds(p))

    def test_area_clear_on_the_same_dive_is_clean_with_a_35_degree_tool(self):
        dive = [(0, 0), (40, 0), (40, -20), (30, -30), (30, -40), (60, -40), (100, -40)]
        p = part(dive)
        p.operations.append(AreaClear(4, Point(120, 5), [g.id for g in p.geometry]))
        self.assertNotIn("gouge", kinds(p))
