"""Eduard's reports from the Surface, each replayed by finger exactly as he did it (2026-10-20): every test here failed
on the build he reported it on, and passes with the fix.

- Select an end point by a tap, then type the next point on the keypad: the line was not drawn (the typed point only
  started a new chain, or "@ needs a previous point").
- The number dialogs (Circle and the others) were so narrow in touch mode that a row's title ran one letter per line."""

from __future__ import annotations

import time
import unittest

from .part import Billet, Line, Point, Project
from .test_interaction import ENABLED, Bus


def two_lines() -> Project:
    p = Project(billet=Billet(100, 60), first_tool=3)
    p.geometry = [Line(Point(0, 0), Point(40, 0), "g0"), Line(Point(40, 0), Point(40, -30), "g1")]
    return p


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SurfaceReports(Bus):
    needs_state = True

    def typed_from_tapped_end(self, keys: tuple[str, ...], end: Point) -> None:
        path = self.part(two_lines())
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.s.tap(*to_screen(40, -30))  # the Select tool: the end of the shoulder
        time.sleep(0.4)
        self.tap("Keypad", "button")
        self.app.find("✓", "button")
        for k in keys:
            self.tap(k, "button")
        time.sleep(0.4)
        self.snap("typed")
        self.tap("Done", "button")
        lines = [(g.a, g.b) for g in self.saved(path).geometry]
        self.assertIn((Point(40, -30), end), lines, "the typed point should draw a line from the tapped end")

    def test_incremental_point_after_tapping_an_end(self):
        self.typed_from_tapped_end(("ABS", "0", "→", "2", "0", "±", "✓"), Point(40, -50))  # INC: dX 0, dZ -20

    def test_absolute_point_after_tapping_an_end(self):
        self.typed_from_tapped_end(("6", "0", "→", "3", "0", "±", "✓"), Point(60, -30))  # X 60, Z -30

    def test_number_dialog_titles_stay_on_one_line(self):
        self.launch(str(self.part(two_lines())), "--touch")
        self.tap("More Drawing Commands", "toggle button")
        time.sleep(0.6)
        self.tap(acc=[i for i in self.app.menu_items() if i.text.startswith("Circle")][0])
        time.sleep(0.6)
        self.snap("circle")
        one_line = self.app.find("Radius", "label").h
        for title in ("Centre X (Ø)", "Centre Z"):
            h = self.app.find(title, "label").h
            self.assertLess(h, 1.5 * one_line, f"{title!r} wraps: {h} px high, one line is {one_line} px")
