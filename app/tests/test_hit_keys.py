"""ALPHA-T's one-letter commands on the Part page (hit_keys.py) and the drawing maths behind them."""

import unittest

from alphacam import drawing
from alphacam.model import Arc, Point, Project, Text
from alphacam_gtk.hit_keys import HIT_KEYS, hit_action, key_char


class HitKeys(unittest.TestCase):
    def test_alpha_t_table(self):
        """The letters of ALPHA-T's hit code table (HELP/ENG/SD503.HLP)."""
        expect = {"a": "find-arc", "c": "cam", "d": "line-la", "f": "radius", "g": "move-abs", "h": "move-inc",
                  "i": "find-intersection", "k": "corner", "l": "find-line", "m": "measure-xz", "n": "measure-la",
                  "o": "fillet", "p": "find-point", "q": "erase-drawing", "r": "fit", "s": "start", "t": "text",
                  "u": "erase-text", "v": "recall-view", "x": "line", "y": "move-la", "z": "zoom-in", "+": "freehand"}
        for k, v in expect.items():
            self.assertEqual(hit_action(k), v, k)
            self.assertEqual(hit_action(k.upper()), v, k)
        self.assertIsNone(hit_action("b"))  # blank in ALPHA-T
        self.assertIsNone(hit_action("e"))  # exit: not mapped
        self.assertIsNone(hit_action(""))

    def test_numbers_go_to_the_entry(self):
        for c in "0123456789-.@":
            self.assertEqual(hit_action(c), "type", c)

    def test_minus_erases_the_found_element(self):
        self.assertEqual(hit_action("-", selected=True), "erase")
        self.assertEqual(hit_action("-"), "type")

    def test_enter(self):
        self.assertEqual(hit_action("\r"), "accept")
        self.assertEqual(hit_action("3", picking=True), "pick-done")
        self.assertEqual(hit_action("\r", picking=True), "pick-done")
        self.assertIsNone(hit_action("x", picking=True))

    def test_help_lists_every_letter(self):
        from alphacam_gtk.help import HELP_DIR

        for lang in ("en", "uk"):
            text = (HELP_DIR / f"help.{lang}.md").read_text("utf-8")
            for k in HIT_KEYS:
                self.assertIn(f"**{k.upper()}**", text, (lang, k))


class CursorMaths(unittest.TestCase):
    def test_polar_as_alpha_t(self):
        """ALPHA-T Y 10,A from X40 Z-25 (read off its screen in DOSBox-X): 0 deg is 9 o'clock, 90 is +X."""
        o = Point(40, -25)
        self.assertEqual(drawing.polar(o, 10, 0), Point(40, -35))
        self.assertEqual(drawing.polar(o, 10, 90), Point(60, -25))
        self.assertEqual(drawing.polar(o, 10, 180), Point(40, -15))
        self.assertEqual(drawing.polar(o, 10, 270), Point(20, -25))
        p = drawing.polar(o, 10, 45)
        self.assertAlmostEqual(p.x, 54.142, places=3)
        self.assertAlmostEqual(p.z, -32.071, places=3)

    def test_measure(self):
        a = Point(40, -25)
        self.assertEqual(drawing.measure_xz(a, Point(60, -35)), (20, -10))
        self.assertEqual(drawing.measure_la(a, drawing.polar(a, 10, 45)), (10, 45))
        self.assertEqual(drawing.measure_la(a, Point(20, -25)), (10, 270))

    def _part(self):
        p = Project()
        for a, b in [((0, 0), (40, 0)), ((40, 0), (40, -25)), ((40, -25), (60, -25))]:
            drawing.add_line(p, Point(*a), Point(*b))
        drawing.add_arc_3p(p, Point(60, -25), Point(70, -30), Point(60, -35))
        drawing.add_line(p, Point(50, 5), Point(50, -40))  # crosses the part
        return p

    def test_find_point_line_arc(self):
        p = self._part()
        self.assertEqual(drawing.nearest_point(p, Point(39, 1)), Point(40, 0))
        self.assertEqual(drawing.nearest_point(p, Point(59, -30.5)), Point(60, -30))  # arc centre
        self.assertEqual(drawing.nearest_element(p, Point(39, -10), drawing.Line), "g1")
        self.assertEqual(drawing.nearest_element(p, Point(39, -10), Arc), "g3")

    def test_intersections(self):
        p = self._part()
        pts = drawing.intersections(p)
        self.assertIn(Point(50, -25), pts)  # the crossing line meets the shoulder
        self.assertIn(Point(40, 0), pts)  # ends that meet count too
        self.assertEqual(drawing.nearest_intersection(p, Point(49, -24)), Point(50, -25))

    def test_arc_line_intersection(self):
        p = Project()
        drawing.add_arc_3p(p, Point(60, -25), Point(70, -30), Point(60, -35))
        drawing.add_line(p, Point(66, 0), Point(66, -50))  # radius 33: crosses the arc twice
        pts = sorted(drawing.intersections(p), key=lambda q: q.z)
        self.assertEqual(len(pts), 2)
        self.assertAlmostEqual(pts[0].z, -30 - 4, places=6)
        drawing.add_line(p, Point(56, 0), Point(56, -50))  # radius 28: misses the arc (only on its far side)
        self.assertEqual(len(drawing.intersections(p)), 2)

    def test_text_and_erase_drawing(self):
        p = self._part()
        drawing.add_text(p, Point(80, -10), "HELLO")
        drawing.add_text(p, Point(20, -40), "SHAFT", 3)
        self.assertEqual(drawing.nearest_text(p, Point(22, -38)), 1)
        q = Project.loads(p.dumps())
        self.assertEqual(q.texts, [Text(Point(80, -10), "HELLO", 5.0), Text(Point(20, -40), "SHAFT", 3.0)])
        p.operations.append(__import__("alphacam.model", fromlist=["FinishPass"]).FinishPass(3, Point(120, 5),
                                                                                            ["g0", "g1"]))
        drawing.erase_drawing(p)
        self.assertEqual((p.geometry, p.texts, p.operations[0].contour), ([], [], []))

    def test_no_texts_key_when_empty(self):
        self.assertNotIn("texts", Project().to_dict())


class KeyLayout(unittest.TestCase):
    def test_ukrainian_layout_letters_map_to_the_key(self):
        self.assertEqual(key_char("ф", 38), "a")  # the A key
        self.assertEqual(key_char("д", 46), "l")  # the L key
        self.assertEqual(key_char("a", 38), "a")
        self.assertEqual(key_char("5", 14), "5")
        self.assertEqual(key_char("-", 20), "-")
