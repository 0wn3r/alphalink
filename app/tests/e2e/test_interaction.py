"""Touch interactions that test_workflows.py does not reach (TOUCH.md; the parity list of 2026-10-16): layout
switching, menus by finger, canvas navigation (fling, double tap, zoom buttons, long-press zoom), drawing tools by
touch (arc, corner, fillet chips, erase, keypad keys, value chip), the operation editor (−/+, header buttons), the
simulation (play, scrub), program search by touch, hold to send by finger, and touch target sizes.

Every input is a libei tap, drag, hold or key. They read the app through its test bus, so they run against the Rust
app (ALPHALINK_E2E_BRANCH, see README); the Python app has no bus and is waiting for the rewrite."""

from __future__ import annotations

import os
import re
import time
import unittest
from pathlib import Path

from .part import Arc, Billet, Line, Point, Project, post

from .test_workflows import HALF, LANDSCAPE, PORTRAIT, Surface, VirtualPort, shaft, with_op

ENABLED = os.environ.get("ALPHALINK_E2E") == "1"
MIN_TARGET_PX = 44  # the smallest finger target (GNOME HIG: "at least 44 px" on touch)


def two_ops() -> Project:
    p = with_op("area_clear")
    from .part import default_op

    p.operations.append(default_op("finish", p, 1))
    return p


class Bus(Surface):
    """Helpers on top of the test bus."""

    needs_state = False  # the POINTER_KEYBOARD.md tests read State(), which came with that work

    def launch(self, *args: str, lang: str | None = None):
        app = super().launch(*args, lang=lang)
        if self.needs_state:
            from gi.repository import GLib

            try:
                app.state()
            except GLib.Error:
                self.skipTest("this build has no State() on its test bus (before POINTER_KEYBOARD.md)")
        return app

    def spin(self, title: str):
        """The spin button of the editor row `title`."""
        return self.app.all(title, "spinbutton")[0]

    def value(self, title: str) -> float:
        return float(self.app.text(self.spin(title)))

    def editor_buttons(self, title: str):
        """The − and + of the editor row `title`, left to right."""
        row = self.app.all(title, "row")[0]
        bs = [w for w in self.app.widgets() if w.css == "button" and not w.text and w.w > 0
              and row.y <= w.y + w.h / 2 <= row.y + row.h and w.x > row.x + row.w / 2]
        return sorted(bs, key=lambda w: w.x)

    def tap(self, name=None, role=None, acc=None, contains=False) -> None:
        x, y = self.app.center(name, role, acc, contains)
        if not (0 <= x < self.s.width and 0 <= y < self.s.height):
            self.fail(f"{name or (acc.text if acc else '')!r} is off the screen at {x:.0f},{y:.0f}; scroll first")
        self.s.tap(x, y)

    def view(self):
        """Where two screen spots of the drawing are, in drawing coordinates (the view, as numbers)."""
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        self.calibrate()
        return [self.to_world(x + w * fx, y + h * fy) for fx, fy in ((0.3, 0.3), (0.7, 0.7))]

    def assertViewEqual(self, a, b, tol=0.5, msg=""):
        for (x0, z0), (x1, z1) in zip(a, b):
            self.assertAlmostEqual(x0, x1, delta=tol, msg=msg)
            self.assertAlmostEqual(z0, z1, delta=tol, msg=msg)

    def scale(self, v) -> float:
        """mm across the two spots of `view()`."""
        (x0, z0), (x1, z1) = v
        return abs(z1 - z0)

    def empty_spot(self):
        """A spot of the drawing far from the part (top right)."""
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        return x + w * 0.85, y + h * 0.12

    def double_tap(self, x, y) -> None:
        self.s.input.touches([(x, y)], hold=0.03)
        time.sleep(0.08)
        self.s.input.touches([(x, y)], hold=0.03)
        time.sleep(0.6)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class InputLayout(Bus):
    """TOUCH.md 3: the layout follows the last input in Automatic, and a saved choice holds from the start."""

    def touch_only(self) -> bool:
        return self.app.exists("Keypad", "button") and self.app.exists("Zoom In", "button")

    def test_automatic_follows_the_finger_and_the_mouse(self):
        self.launch(str(self.part(shaft())))
        self.assertFalse(self.touch_only(), "a fresh start should be in the pointer layout")
        for _ in range(2):  # two presses in a row decide
            self.tap("Operations", "label")
        time.sleep(0.5)
        self.snap("touch")
        self.assertTrue(self.touch_only(), "two taps should switch to the touch layout")
        for _ in range(2):
            self.s.click(*self.app.center("Operations", "label"))
        time.sleep(0.5)
        self.snap("pointer")
        self.assertFalse(self.touch_only(), "two clicks should switch back")

    def test_saved_touch_layout_holds_from_the_start(self):
        """Main menu → Touch by finger, quit, start again without --touch: the touch layout is all there (the
        zoom buttons and the Keypad, not only the bigger buttons)."""
        path = self.part(shaft())
        self.launch(str(path))
        self.s.key("ctrl+comma")  # Preferences ▸ Input ▸ Input layout
        time.sleep(0.8)
        self.tap(acc=self.app.labelled("Input layout", "row"))
        time.sleep(0.6)
        self.tap(acc=[w for w in self.app.widgets() if w.text == "Touch" and w.w > 0 and w.css in ("row", "label")][-1])
        time.sleep(0.5)
        self.s.key("Escape")
        time.sleep(0.5)
        self.assertTrue(self.touch_only())
        self.s.key("ctrl+q")
        time.sleep(1.5)
        self.launch(str(path))
        self.snap("restarted")
        self.assertTrue(self.app.exists("Zoom In", "button"), "the zoom buttons are missing after a restart")
        self.assertTrue(self.app.exists("Keypad", "button"), "the Keypad button is missing after a restart")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class TouchTargets(Bus):
    """Every control a finger can reach is at least MIN_TARGET_PX in both directions, on each page."""

    def small_targets(self) -> list[str]:
        sw, sh = self.s.width, self.s.height
        ws = self.app.widgets()
        rows = [r for r in ws if r.name in ("AdwSwitchRow", "AdwActionRow", "AdwComboRow") and r.w > 0]
        bad = []
        for w in ws:
            if w.css not in ("button", "toggle", "switch", "menubutton", "checkbutton") or not w.sensitive:
                continue
            if w.css in ("switch", "checkbutton") and any(
                    r.x <= w.x + w.w / 2 <= r.x + r.w and r.y <= w.y + w.h / 2 <= r.y + r.h for r in rows):
                continue  # the whole row takes the tap
            if w.w <= 0 or w.x < 0 or w.y < 0 or w.x + w.w > sw + 1 or w.y + w.h > sh + 1:
                continue  # scrolled away or hidden
            if w.w < MIN_TARGET_PX or w.h < MIN_TARGET_PX:
                bad.append(f"{w.name} {w.text or w.id!r} {w.w:.0f}×{w.h:.0f} at {w.x:.0f},{w.y:.0f}")
        return bad

    def test_controls_are_finger_sized(self):
        self.launch(str(self.part(two_ops())), "--touch")
        found = {}
        self.tap(acc=self.app.labelled("Area clear", "list item"))
        time.sleep(0.5)
        for page in ("Part", "Program", "Sheet", "Tools", "Machine"):
            self.tap(page, "page tab")
            time.sleep(0.6)
            self.snap(page)
            found[page] = self.small_targets()
        self.assertEqual({k: v for k, v in found.items() if v}, {})


class TouchTargetsByPageKeys(TouchTargets):
    """The same check in other shapes and languages: the pages by Alt+1 … Alt+5 (names differ in Ukrainian), and
    no control cut off at the screen's edge."""

    needs_state = True

    def cut_off(self) -> list[str]:
        sw, sh = self.s.width, self.s.height
        return [f"{w.name} {w.text or w.id!r} at {w.x:.0f},{w.y:.0f} {w.w:.0f}×{w.h:.0f}" for w in self.app.widgets()
                if w.css in ("button", "toggle", "menubutton") and w.sensitive and w.w > 0
                and w.x < sw and w.y < sh and w.x + w.w > 0 and w.y + w.h > 0  # on screen at all
                and (w.x < -1 or w.x + w.w > sw + 1) and w.y + w.h <= sh]  # but sticking out sideways

    def test_controls_are_finger_sized(self):
        self.launch(str(self.part(two_ops())), "--touch")
        small, cut = {}, {}
        for n, page in enumerate(("part", "program", "sheet", "tools", "machine"), 1):
            self.s.key(f"alt+{n}")
            time.sleep(0.6)
            self.snap(page)
            small[page], cut[page] = self.small_targets(), self.cut_off()
        self.assertEqual({k: v for k, v in small.items() if v}, {}, "too small for a finger")
        self.assertEqual({k: v for k, v in cut.items() if v}, {}, "cut off at the edge")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class TouchTargetsPortrait(TouchTargetsByPageKeys):
    size = PORTRAIT


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class TouchTargetsHalf(TouchTargetsByPageKeys):
    size = HALF


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class TouchTargetsUkrainian(TouchTargetsByPageKeys):
    lang = "uk"


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class MenusByTouch(Bus):
    """Popover menus driven by finger: Eduard 2026-10-16, a tapped + menu item closed the menu and added nothing."""

    def test_every_add_operation_item_by_tap(self):
        from .part import OP_MENU

        path = self.part(shaft())
        self.launch(str(path), "--touch")
        for i in range(len(OP_MENU)):
            self.tap("Add Operation (C)", "toggle button")
            items = self.app.menu_items()
            self.snap(f"menu-{i}")
            self.tap(acc=items[i])
            time.sleep(0.6)
            self.s.key("Escape")  # a pick or the editor may have started
            time.sleep(0.3)
        q = self.saved(path)
        self.assertGreaterEqual(len(q.operations), len(OP_MENU), [type(o).__name__ for o in q.operations])

    def test_erase_drawing_from_the_more_menu(self):
        path = self.part(shaft())
        self.launch(str(path), "--touch")
        self.tap("More Drawing Commands", "toggle button")
        time.sleep(0.6)  # the popover slides in: its items move until it settles
        erase = self.app.find("Erase Drawing…", "menu item")
        bar = self.app.find("More Drawing Commands", "toggle button")
        self.assertLess(erase.y + erase.h, bar.y, "the whole menu should fit above the tools (a finger can't scroll it)")
        self.tap(acc=erase)
        self.app.find("Erase All of the Drawing?", contains=True)
        self.snap("asked")
        self.tap("Erase", "button")
        time.sleep(0.5)
        self.assertEqual(self.saved(path).geometry, [])
        self.tap("Undo (Ctrl+Z)", "button")
        time.sleep(0.5)
        self.assertEqual(len(self.saved(path).geometry), 5, "Undo should bring the drawing back")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class CanvasNavigation(Bus):
    """TOUCH.md 4: fling, double tap, the zoom buttons, long-press Zoom Here / Fit Part."""

    def test_double_tap_fits_and_taps_again_go_back(self):
        self.launch(str(self.part(shaft())), "--touch")
        fit = self.view()
        x, y = self.empty_spot()
        self.s.input.touches([(x, y)], [(x - 250, y + 150)], 20, 0.3)  # pan away slowly (no fling)
        time.sleep(0.8)
        panned = self.view()
        self.assertGreater(abs(panned[0][1] - fit[0][1]), 5, "the drag should have panned")
        self.double_tap(*self.empty_spot())
        self.assertViewEqual(self.view(), fit, msg="a double tap should fit the part")
        self.double_tap(*self.empty_spot())
        self.snap("back")
        self.assertViewEqual(self.view(), panned, msg="a second double tap should return to the view before")

    def test_fling_keeps_moving_after_the_finger_lifts(self):
        self.launch(str(self.part(shaft())), "--touch")
        before = self.view()
        x, y = self.empty_spot()
        self.s.input.touches([(x, y)], [(x - 200, y)], 4, 0.0)  # fast, lifted while moving
        time.sleep(2.0)
        after = self.view()
        moved_px = abs(after[0][1] - before[0][1]) / self.mm_per_px
        self.snap("flung")
        # the finger itself moves the view 200 px; the fling adds to it
        self.assertGreater(moved_px, 500, f"the view moved {moved_px:.0f} px for a 200 px flick")

    def test_zoom_buttons(self):
        self.launch(str(self.part(shaft())), "--touch")
        fit = self.view()
        self.tap("Zoom In", "button")
        zin = self.view()
        self.assertAlmostEqual(self.scale(zin) / self.scale(fit), 0.8, delta=0.03)
        self.tap("Zoom Out", "button")
        self.tap("Zoom Out", "button")
        self.assertAlmostEqual(self.scale(self.view()) / self.scale(fit), 1.25, delta=0.05)
        self.tap("Fit Part", "button")
        self.assertViewEqual(self.view(), fit)

    def test_long_press_zoom_here_then_fit_part(self):
        self.launch(str(self.part(shaft())), "--touch")
        fit = self.view()
        x, y = self.empty_spot()
        spot = self.to_world(x, y)
        self.s.hold(x, y, 1.2, touch=True)
        items = self.app.menu_items()
        self.snap("menu")
        self.tap(acc=[i for i in items if i.text == "Zoom Here"][0])
        time.sleep(0.5)
        v = self.view()
        self.assertAlmostEqual(self.scale(v) / self.scale(fit), 0.5, delta=0.03)
        here = self.to_world(x, y)
        self.assertAlmostEqual(here[0], spot[0], delta=1.0, msg="the spot should stay under the finger")
        self.assertAlmostEqual(here[1], spot[1], delta=1.0, msg="the spot should stay under the finger")
        self.s.hold(x, y, 1.2, touch=True)
        self.tap(acc=[i for i in self.app.menu_items() if i.text == "Fit Part"][0])
        time.sleep(0.5)
        self.assertViewEqual(self.view(), fit)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class DrawingByTouch(Bus):
    """TOUCH.md 5.2 and 6: the other drawing tools, the keypad's keys and the value chip, all by finger."""

    def blank(self):
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path), "--touch")
        return path, self.calibrate()

    def test_arc_by_three_taps(self):
        path, to_screen = self.blank()
        self.tap("Arc", "radio button")
        for xd, z in ((40, 0), (60, -10), (40, -20)):  # start, a point on it, end
            self.s.tap(*to_screen(xd, z))
        time.sleep(0.4)
        self.snap("arc")
        g = self.saved(path).geometry
        self.assertEqual(len(g), 1)
        self.assertIsInstance(g[0], Arc)
        self.assertEqual({g[0].a, g[0].b}, {Point(40, 0), Point(40, -20)})

    def test_corner_joins_two_lines(self):
        p = Project(billet=Billet(100, 60), first_tool=3)
        p.geometry += [Line(Point(40, 0), Point(40, -20)), Line(Point(50, -30), Point(80, -30))]
        for i, g in enumerate(p.geometry):
            g.id = f"g{i}"
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.tap("Corner", "radio button")
        self.s.tap(*to_screen(40, -10))
        self.s.tap(*to_screen(65, -30))
        time.sleep(0.4)
        self.snap("corner")
        ends = {pt for g in self.saved(path).geometry for pt in (g.a, g.b)}
        self.assertIn(Point(40, -30), ends, "both lines should meet at X40 Z-30")

    def test_fillet_size_chip_by_tap(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.tap("Fillet", "radio button")
        self.s.tap(*to_screen(40, -25))
        self.tap("R2", "button")
        time.sleep(0.5)
        self.snap("rounded")
        arcs = [g for g in self.saved(path).geometry if isinstance(g, Arc)]
        self.assertEqual(len(arcs), 1)
        self.assertAlmostEqual(arcs[0].radius(), 2.0, places=6)

    def test_keypad_keys(self):
        """Start X40 Z0 with ⌫ and ±; then INC: @0,-25 (typed as -25 with ±) ends at X40 Z-25."""
        path, _ = self.blank()
        self.tap("Line", "radio button")
        self.tap("Keypad", "button")
        for k in ("4", "1", "⌫", "0", "→", "5", "⌫", "0", "✓"):  # X 40, Z 0
            self.tap(k, "button")
        self.tap("Keypad", "button")
        for k in ("ABS", "0", "→", "2", "5", "±", "✓"):  # INC: dX 0, dZ -25
            self.tap(k, "button")
        time.sleep(0.4)
        self.snap("typed")
        self.tap("Done", "button")
        g = self.saved(path).geometry
        self.assertEqual([(e.a, e.b) for e in g], [(Point(40, 0), Point(40, -25))])

    def chip(self):
        """The value chip: "Ø 40", "Z -25" or "X26 Z-38"."""
        end = time.time() + 5
        while True:
            hits = [w for w in self.app.widgets() if w.css == "button" and w.w > 0
                    and re.match(r"^(Ø |Z |X-?\d)", w.text)]
            if hits or time.time() > end:
                return hits[0] if hits else self.app.find("Ø ", "button", contains=True)
            time.sleep(0.2)

    def test_value_chip_follows_the_zoom_buttons(self):
        path, to_screen = self.blank()
        self.tap("Line", "radio button")
        self.s.tap(*to_screen(0, 0))
        self.s.tap(*to_screen(40, 0))
        before = self.app.rect(self.chip())
        self.tap("Zoom In", "button")
        time.sleep(0.5)
        self.snap("zoomed")
        to_screen = self.calibrate()
        end = to_screen(40, 0)
        after = self.app.rect(self.chip())
        self.assertNotEqual(before[:2], after[:2], "the chip stayed where the point was before the zoom")
        self.assertLess(abs(after[1] + after[3] / 2 - end[1]), 80, "the chip should sit by the last point")

    def chip_gap(self, x: float, z: float) -> float:
        """Pixels between the chip's nearest edge and the drawing point X (diameter), Z, by the bus's view."""
        v = self.app.state()["view"]
        dx0, dy0, _w, _h = self.app.rect(self.app.find("Drawing"))  # the view's origin is in the drawing's pixels
        px, py = dx0 + v["ox"] + z * v["scale"], dy0 + v["oy"] - x / 2 * v["scale"]
        cx, cy, cw, ch = self.app.rect(self.chip())
        dx = max(cx - px, 0, px - (cx + cw))
        dy = max(cy - py, 0, py - (cy + ch))
        return (dx * dx + dy * dy) ** 0.5

    def test_value_chip_stays_by_the_point_while_panning(self):
        """Eduard's video 2026-10-17: panning by finger while drawing left the chip behind (and doubled)."""
        path, to_screen = self.blank()
        self.tap("Line", "radio button")
        self.s.tap(*to_screen(0, 0))
        self.s.tap(*to_screen(40, -30))
        self.assertLess(self.chip_gap(40, -30), 40, "the chip should start beside the last point")
        x, y = self.empty_spot()
        self.s.input.touches([(x, y)], [(x - 150, y + 120)], 20, 0.3)  # a slow pan, the finger rests: no fling
        time.sleep(0.5)
        self.snap("panned")
        self.assertLess(self.chip_gap(40, -30), 40, "after a pan the chip should be beside the point again")
        self.s.input.touches([(x - 300, y)], [(x - 220, y)], 4, 0.0)  # a short flick right: the point stays in view
        gaps = []
        for _ in range(8):  # during the fling
            time.sleep(0.1)
            gaps.append(round(self.chip_gap(40, -30)))
        time.sleep(1.0)
        self.snap("flung")
        gaps.append(round(self.chip_gap(40, -30)))
        self.assertLess(max(gaps), 40, f"the chip lagged behind the point while the view moved: {gaps} px")

    def test_value_chip_hides_when_the_point_leaves_the_view(self):
        path, to_screen = self.blank()
        self.tap("Line", "radio button")
        self.s.tap(*to_screen(0, 0))
        self.s.tap(*to_screen(40, 0))
        self.assertTrue(self.app.exists("Ø ", "button", contains=True))
        x, y = self.empty_spot()
        for _ in range(2):  # pan the point far off to the left: 1800 px, more than the drawing is wide
            self.s.input.touches([(x, y)], [(x - 900, y)], 30, 0.3)
            time.sleep(0.8)
        self.snap("panned")
        self.assertFalse(self.app.exists("Ø ", "button", contains=True), "the chip should hide off-screen")

    def test_delete_toast_undo_by_tap(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.s.hold(*to_screen(40, -12), 1.2, touch=True)
        self.tap(acc=[i for i in self.app.menu_items() if i.text == "Delete"][0])
        self.assertEqual(len(self.saved(path).geometry), 4)
        self.tap("Undo", "button")
        time.sleep(0.5)
        self.assertEqual(len(self.saved(path).geometry), 5)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class EditorByTouch(Bus):
    """TOUCH.md 7.1: a still tap on − / + steps the value; the editor's header buttons by finger."""

    def open(self, p: Project, row: str = "Area clear"):
        path = self.part(p)
        self.launch(str(path), "--touch")
        self.tap(acc=self.app.labelled(row, "list item"))
        time.sleep(0.6)
        return path

    def test_plus_and_minus_step_the_value(self):
        path = self.open(with_op())
        before = self.value("Depth of cut")
        minus, plus = self.editor_buttons("Depth of cut")
        self.s.tap(*self.app.center(acc=plus))
        self.s.tap(*self.app.center(acc=plus))
        time.sleep(0.4)
        up = self.value("Depth of cut")
        self.assertGreater(up, before)
        self.s.tap(*self.app.center(acc=minus))
        time.sleep(0.4)
        down = self.value("Depth of cut")
        self.assertLess(down, up)
        self.assertFalse(self.app.exists("✓", "button"), "a tap on − / + must not open the keypad")
        self.assertAlmostEqual(self.saved(path).operations[0].depth_of_cut, down, places=6)

    def test_header_buttons_move_delete_and_close(self):
        path = self.open(two_ops(), "Finish pass")
        self.tap("Move Up", "button")
        time.sleep(0.5)
        self.assertEqual([type(o).__name__ for o in self.saved(path).operations], ["FinishPass", "AreaClear"])
        self.s.scroll(self.s.width - 150, self.s.height * 0.6, 15)  # Delete Operation is at the end of the editor
        time.sleep(0.4)
        self.tap("Delete Operation", "button")
        time.sleep(0.5)
        for name in ("Delete", "_Delete"):
            if self.app.exists(name, "button"):  # a confirmation, if there is one
                self.tap(name, "button")
        time.sleep(0.5)
        self.assertEqual([type(o).__name__ for o in self.saved(path).operations], ["AreaClear"])
        self.tap(acc=self.app.labelled("Area clear", "list item"))
        time.sleep(0.5)
        self.tap("Close Editor", "button")
        time.sleep(0.5)
        self.snap("closed")
        self.assertFalse(self.app.exists("Depth of cut", "spinbutton"), "the editor should close")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SimulationByTouch(Bus):
    """TOUCH.md 7.2: Play / Pause and the scrub slider by finger; the program search button by finger."""

    def moves(self) -> str:
        return self.app.find("/", "label", contains=True).text

    def test_play_pause_and_scrub(self):
        self.launch(str(self.part(with_op())), "--touch")
        self.tap("First Move", "button")
        start = self.moves()
        self.tap("Play / Pause", "button")
        time.sleep(1.5)
        self.tap("Play / Pause", "button")
        played = self.moves()
        self.assertNotEqual(played, start, "Play should step through the moves")
        time.sleep(1.0)
        self.assertEqual(self.moves(), played, "Pause should stop")
        x, y, w, h = self.app.rect(self.app.find(None, "scale"))
        self.s.input.touches([(x + 12, y + h / 2)], [(x + w / 2, y + h / 2)], 15, 0.2)
        time.sleep(0.5)
        self.snap("scrubbed")
        n, total = (int(v) for v in self.moves().split(" ")[0].split("/"))
        self.assertAlmostEqual(n / total, 0.5, delta=0.15, msg=self.moves())

    def test_program_search_button_by_tap(self):
        self.launch(str(self.part(with_op())), "--touch")
        self.tap("Program", "page tab")
        time.sleep(0.5)
        self.tap("Search (Ctrl+F)", "toggle button")
        time.sleep(0.4)
        self.s.type("N20")
        time.sleep(0.6)
        self.snap("found")
        self.assertTrue([e for e in self.app.all(None, "entry") if "N20" in self.app.text(e)])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SendByTouch(VirtualPort, Bus):
    """TOUCH.md 7.4: a finger held on Send to the Lathe sends; the screen locks against taps while it runs."""

    def test_finger_hold_sends_the_program(self):
        p = with_op()
        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", args=(str(self.part(p)), "--touch"), env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.tap("Machine", "page tab")
        time.sleep(0.5)
        self.s.hold(*self.app.center(acc=self.app.labelled("Send to the Lathe", "button")), 2.5, touch=True)
        self.snap("confirm")
        self.tap("Send", "button")
        time.sleep(0.5)
        lock = self.app.find("Touch Lock", "toggle button", contains=True)
        self.assertTrue(self.app.checked(lock), "taps should be locked while sending")
        expected = post(p)
        self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(expected))
        self.wait_for(lambda: self.app.exists("Sent ", contains=True), 20, "the send to finish")
        self.assertEqual(bytes(self.lathe.got), expected)
        self.wait_for(lambda: not self.app.checked(lock), 5, "the lock to come off after the send")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardAndMouse(Bus):
    """The same features without a finger (the input-mode parity list of 2026-10-16)."""

    def test_escape_closes_dialogs_and_nothing_else(self):
        path = self.part(shaft())
        self.launch(str(path), "--touch")
        self.s.key("ctrl+comma")
        time.sleep(0.6)
        self.snap("preferences")
        before = len(self.app.widgets())
        self.s.key("Escape")
        time.sleep(0.6)
        self.assertLess(len(self.app.widgets()), before, "Escape should close Preferences")
        self.tap("Line", "radio button")
        self.tap("Keypad", "button")
        self.app.find("✓", "button")
        self.s.key("Escape")
        time.sleep(0.5)
        self.assertFalse(self.app.exists("✓", "button"), "Escape should close the keypad")
        self.assertTrue(self.app.checked(self.app.find("Line", "radio button")), "the Line tool should stay on")
        self.assertEqual(len(self.saved(path).geometry), 5)

    def test_menu_key_and_delete_key(self):
        """Shift+F10 opens the drawing's menu at the ALPHA-T cursor; L selects the nearest line, Delete deletes."""
        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        self.s.type("g40,-12\n")  # the cursor onto g1
        self.s.key("shift+F10")
        items = self.app.menu_items()
        self.snap("menu")
        self.assertEqual([i.text for i in items][:2], ["Edit Values…", "Delete"])
        self.s.key("Escape")
        time.sleep(0.3)
        self.s.type("l")  # the nearest line
        time.sleep(0.3)
        self.s.key("Delete")
        time.sleep(0.4)
        self.assertEqual([e.id for e in self.saved(path).geometry], ["g0", "g2", "g3", "g4"])

    def test_ctrl_wheel_zooms_at_the_pointer_and_keys_zoom(self):
        self.launch(str(self.part(shaft())))
        fit = self.view()
        x, y = self.empty_spot()
        spot = self.to_world(x, y)
        self.s.move(x + 3, y + 3)
        self.s.move(x, y)
        self.s.input.key("Control_L", True)
        for _ in range(3):
            self.s.input.scroll(-1)  # up: in
            time.sleep(0.15)
        self.s.input.key("Control_L", False)
        time.sleep(0.5)
        v = self.view()
        self.assertLess(self.scale(v), self.scale(fit) * 0.8, "Ctrl+wheel up should zoom in")
        here = self.to_world(x, y)
        self.assertAlmostEqual(here[1], spot[1], delta=2.0, msg="the spot under the pointer should stay")
        self.s.key("ctrl+0")
        time.sleep(0.4)
        self.assertViewEqual(self.view(), fit, msg="Ctrl+0 should fit the part")
        self.s.key("ctrl+minus")
        time.sleep(0.4)
        self.assertGreater(self.scale(self.view()), self.scale(fit) * 1.1, "Ctrl+− should zoom out")

    def test_wheel_pans(self):
        self.launch(str(self.part(shaft())))
        before = self.view()
        self.s.scroll(*self.empty_spot(), 3)
        after = self.view()
        self.assertGreater(abs(after[0][0] - before[0][0]), 1, "the wheel should pan the drawing")

    def test_mouse_click_unlocks_the_touch_lock(self):
        self.launch(str(self.part(shaft())), "--touch")
        lock = self.app.find("Touch Lock", "toggle button", contains=True)
        self.s.tap(*self.app.center(acc=lock))
        self.assertTrue(self.app.checked(lock))
        self.s.click(*self.app.center(acc=lock))
        time.sleep(0.4)
        self.assertFalse(self.app.checked(lock), "a mouse click should unlock")

    def test_finger_drags_the_reorder_handle(self):
        path = self.part(two_ops())
        self.launch(str(path), "--touch")
        handles = self.app.all("Drag to Reorder")
        self.assertEqual(len(handles), 2)
        hx, hy = self.app.center(acc=handles[1])
        tx, ty = self.app.center(acc=self.app.labelled("Area clear", "list item"))
        self.s.input.touches([(hx, hy)], [(tx, ty - 10)], 60, 0.4)  # a slow drag, not a flick
        time.sleep(0.8)
        self.snap("reordered")
        self.assertEqual([type(o).__name__ for o in self.saved(path).operations], ["FinishPass", "AreaClear"])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SendByKeyboard(VirtualPort, Bus):
    """TOUCH.md 7.4 by keys: Space held on Send to the Lathe starts it, as a held finger does."""

    def test_space_held_sends(self):
        p = with_op()
        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", args=(str(self.part(p)),), env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.press("Machine", "page tab")
        time.sleep(0.5)
        send = self.app.labelled("Send to the Lathe", "button")
        self.s.click(*self.app.center(acc=send))  # focus; a click is not a hold
        self.assertFalse(self.app.exists("Send", "button"), "a click must not start a send")
        self.assertTrue(self.app.focused(send), "the click should leave the keyboard on the button")
        self.s.hold_key("space", 2.5)
        self.snap("confirm")
        self.app.find("Send", "button")
        self.s.key("alt+s")  # "_Send": Return would take the default, Cancel
        expected = post(p)
        self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(expected))
        self.assertEqual(bytes(self.lathe.got), expected)


# --- POINTER_KEYBOARD.md (approved 2026-10-17): one class per section, keys through libei, state from the bus ------


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardShortcutFixes(Bus):
    """§12: Delete deletes the selected element anywhere on the Part page outside a text field."""

    needs_state = True

    def test_delete_key_away_from_the_drawing(self):
        path = self.part(shaft())
        self.launch(str(path))
        self.s.type("g40,-12\n")
        self.s.type("l")  # select g1
        time.sleep(0.3)
        self.assertEqual(self.app.state()["drawing"]["selected_element"], "g1")
        self.s.click(*self.app.center("Operations", "label"))  # the focus leaves the drawing
        self.s.key("Delete")
        time.sleep(0.4)
        self.assertEqual([e.id for e in self.saved(path).geometry], ["g0", "g2", "g3", "g4"])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardOperations(Bus):
    """§6: Alt+↑ / Alt+↓ reorder, Ctrl+D duplicates, Delete deletes with an Undo, all with the list focused."""

    needs_state = True

    def focus_row(self, title: str) -> None:
        self.s.click(*self.app.center(acc=self.app.labelled(title, "list item")))
        time.sleep(0.4)

    def kinds(self, path) -> list[str]:
        return [type(o).__name__ for o in self.saved(path).operations]

    def test_alt_arrows_reorder(self):
        path = self.part(two_ops())
        self.launch(str(path))
        self.focus_row("Finish pass")
        self.s.key("alt+Up")
        time.sleep(0.4)
        self.assertEqual(self.kinds(path), ["FinishPass", "AreaClear"])
        self.s.key("alt+Down")
        time.sleep(0.4)
        self.assertEqual(self.kinds(path), ["AreaClear", "FinishPass"])
        self.assertEqual(self.app.state()["selected_op"], 1, "the moved operation should stay selected")

    def test_ctrl_d_duplicates(self):
        path = self.part(with_op())
        self.launch(str(path))
        self.focus_row("Area clear")
        self.s.key("ctrl+d")
        time.sleep(0.4)
        self.assertEqual(self.kinds(path), ["AreaClear", "AreaClear"])

    def test_delete_key_deletes_with_undo(self):
        path = self.part(two_ops())
        self.launch(str(path))
        self.focus_row("Area clear")
        self.s.key("Delete")
        time.sleep(0.5)
        self.assertEqual(self.kinds(path), ["FinishPass"])
        self.s.key("ctrl+z")
        time.sleep(0.4)
        self.assertEqual(self.kinds(path), ["AreaClear", "FinishPass"])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardPages(Bus):
    """§9: Alt+1 … Alt+5 and Ctrl+PgUp / Ctrl+PgDn switch pages."""

    needs_state = True

    def test_alt_numbers_and_ctrl_page_keys(self):
        self.launch(str(self.part(with_op())))
        seen = []
        for n in range(1, 6):
            self.s.key(f"alt+{n}")
            time.sleep(0.4)
            seen.append(self.app.state()["page"])
        self.assertEqual(len(set(seen)), 5, seen)
        self.s.key("alt+1")
        time.sleep(0.3)
        self.s.key("ctrl+Page_Down")
        time.sleep(0.4)
        self.assertEqual(self.app.state()["page"], seen[1])
        self.s.key("ctrl+Page_Up")
        time.sleep(0.4)
        self.assertEqual(self.app.state()["page"], seen[0])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardSimulation(Bus):
    """§7: F5 plays and pauses; Ctrl+[ / Ctrl+] step passes; Alt+Home / Alt+End; PgUp / PgDn on the slider."""

    needs_state = True

    def sim(self) -> int:
        return self.app.state()["sim_index"]

    def test_f5_plays_and_pauses(self):
        self.launch(str(self.part(with_op())))
        self.s.key("alt+Home")
        time.sleep(0.3)
        start = self.sim()
        self.s.key("F5")
        time.sleep(1.5)
        self.s.key("F5")
        played = self.sim()
        self.assertGreater(played, start, "F5 should play")
        time.sleep(1.0)
        self.assertEqual(self.sim(), played, "F5 again should pause")

    def test_ctrl_brackets_step_passes_and_alt_home_end(self):
        self.launch(str(self.part(with_op())))
        self.s.key("alt+Home")
        time.sleep(0.3)
        first = self.sim()
        self.s.key("ctrl+]")
        time.sleep(0.3)
        one = self.sim()
        self.s.key("ctrl+]")
        time.sleep(0.3)
        two = self.sim()
        self.assertLess(first, one)
        self.assertLess(one, two)
        self.s.key("ctrl+[")
        time.sleep(0.3)
        self.assertEqual(self.sim(), one)
        self.s.key("alt+End")
        time.sleep(0.3)
        self.assertIsNone(self.sim(), "Alt+End shows every move (no position)")

    def moves(self) -> tuple[int, int]:
        """The read-out under the drawing: "n/total · …"."""
        n, total = self.app.find("/", "label", contains=True).text.split(" ")[0].split("/")
        return int(n), int(total)

    def test_page_keys_on_the_slider(self):
        self.launch(str(self.part(with_op())))
        self.s.key("alt+Home")
        self.s.click(*self.app.center(acc=self.app.find(None, "scale")))  # the slider takes the keys
        self.s.key("Home")
        time.sleep(0.3)
        a, total = self.moves()
        self.s.key("Page_Up")  # GtkScale: Page Up goes forward on a horizontal slider, Page Down back
        time.sleep(0.3)
        b, _ = self.moves()
        self.assertAlmostEqual(b - a, max(1, total / 10), delta=1, msg=f"PgUp moved {b - a} of {total}")
        self.s.key("Page_Down")
        time.sleep(0.3)
        self.assertEqual(self.moves()[0], a)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardProgram(Bus):
    """§8: the caret selects its block's operation (Enter, or a search hit); Ctrl+= sizes the font, remembered."""

    needs_state = True

    def program(self, project):
        path = self.part(project)
        self.launch(str(path))
        self.s.key("alt+2")
        time.sleep(0.6)
        return path

    def test_search_hit_and_enter_select_the_operation(self):
        p = two_ops()
        self.program(p)
        self.s.key("ctrl+f")
        time.sleep(0.4)
        self.s.type("N40\n")  # the finish pass (N10 tool change, N20 area clear, N40 finish pass)
        time.sleep(0.5)
        st = self.app.state()
        self.assertEqual(st["operations"][st["selected_op"]], "Finish pass", st)
        self.s.key("Escape")
        time.sleep(0.3)
        # the caret two lines into the area clear's block, by keys from the top; Enter selects that operation
        lines = post(p).decode("ascii").splitlines()
        n20 = next(i for i, line in enumerate(lines) if line.startswith("N20"))
        self.s.key("ctrl+Home")
        for _ in range(n20 + 2):
            self.s.key("Down")
        self.s.key("Return")
        time.sleep(0.4)
        st = self.app.state()
        self.assertGreaterEqual(st["program_caret_line"], n20 + 2, st)  # the line numbering may start at 1
        self.assertEqual(st["operations"][st["selected_op"]], "Area clear", st)

    def test_ctrl_plus_sizes_the_font_and_it_is_remembered(self):
        path = self.program(with_op())
        before = self.app.state()["program_font_pt"]
        self.s.key("ctrl+plus")
        self.s.key("ctrl+plus")
        time.sleep(0.4)
        bigger = self.app.state()["program_font_pt"]
        self.assertGreater(bigger, before)
        self.s.key("ctrl+q")
        time.sleep(1.5)
        self.launch(str(path))
        self.assertEqual(self.app.state()["program_font_pt"], bigger, "the font size should be remembered")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardCanvas(Bus):
    """§3: Ctrl+arrows pan an eighth of the view; the view follows the ALPHA-T cursor; middle-button drag pans."""

    needs_state = True

    def origin(self):
        v = self.app.state()["view"]
        return v["ox"], v["oy"]

    def test_ctrl_arrows_pan_an_eighth(self):
        self.launch(str(self.part(shaft())))
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        ox, oy = self.origin()
        self.s.key("ctrl+Right")
        time.sleep(0.3)
        ox1, oy1 = self.origin()
        self.assertAlmostEqual(abs(ox1 - ox), w / 8, delta=w / 40, msg=f"{ox} -> {ox1}")
        self.assertAlmostEqual(oy1, oy, delta=1)
        self.s.key("ctrl+Down")
        time.sleep(0.3)
        self.assertAlmostEqual(abs(self.origin()[1] - oy1), h / 8, delta=h / 40)

    def test_view_follows_the_cursor(self):
        self.launch(str(self.part(shaft())))
        before = self.origin()
        for _ in range(12):
            self.s.key("Page_Up")  # step ×10 each: the cursor runs far off to the side
            self.s.key("Right")
        time.sleep(0.4)
        self.assertNotEqual(self.origin(), before, "the view should follow the cursor off the edge")
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        cz = self.app.state()["drawing"]["cursor"]
        self.snap("followed")
        self.assertTrue(cz, "the cursor should be reported")

    def test_middle_button_drag_pans(self):
        self.launch(str(self.part(shaft())))
        ox, oy = self.origin()
        x, y = self.empty_spot()
        self.s.move(x + 3, y + 3)
        self.s.move(x, y)
        self.s.input.button(0x112, True)  # BTN_MIDDLE
        for k in range(1, 11):
            self.s.move(x - 15 * k, y + 8 * k)
            time.sleep(0.03)
        self.s.input.button(0x112, False)
        time.sleep(0.4)
        ox1, oy1 = self.origin()
        self.assertAlmostEqual(abs(ox1 - ox), 150, delta=20)
        self.assertAlmostEqual(abs(oy1 - oy), 80, delta=20)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardDrawing(Bus):
    """§4 / §5: [ and ] walk the profile; Space adds or removes elements while picking a contour."""

    needs_state = True

    def test_bracket_keys_walk_the_profile(self):
        self.launch(str(self.part(shaft())))
        self.s.type("g0,0\n")
        self.s.type("l")  # g0, the face at Z0
        time.sleep(0.3)
        walked = [self.app.state()["drawing"]["selected_element"]]
        for _ in range(4):
            self.s.key("]")
            time.sleep(0.2)
            walked.append(self.app.state()["drawing"]["selected_element"])
        self.assertEqual(walked, ["g0", "g1", "g2", "g3", "g4"])
        self.s.key("[")
        time.sleep(0.2)
        self.assertEqual(self.app.state()["drawing"]["selected_element"], "g3")

    def test_pick_contour_with_space(self):
        p = with_op()
        p.operations[0].contour = []
        path = self.part(p)
        self.launch(str(path))
        self.s.click(*self.app.center(acc=self.app.labelled("Area clear", "list item")))
        time.sleep(0.4)
        self.press("Pick Contour on the Drawing", "button")
        time.sleep(0.4)
        self.assertIsNotNone(self.app.state()["drawing"]["picking"])
        picked = []
        for _ in range(3):  # g0 → g1, g2, g3
            self.s.key("]")
            time.sleep(0.2)
            at = self.app.state()["drawing"]["pick_at"]
            self.s.key("space")
            time.sleep(0.2)
            picked.append(at)
        self.s.key("space")  # again on the last one: out
        time.sleep(0.3)
        self.assertEqual(self.app.state()["drawing"]["picked"], picked[:2], picked)
        self.s.key("Return")
        time.sleep(0.4)
        self.assertEqual(self.saved(path).operations[0].contour, picked[:2])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardConstruction(Bus):
    """§4: a construction finished by keys: Enter takes the element at the cursor, the number dialog takes the
    typed value, Tab moves between the dashed solutions and Enter takes the highlighted one."""

    needs_state = True

    def test_parallel_line_by_keys(self):
        path = self.part(shaft())
        self.launch(str(path))
        self.press("More Drawing Commands", "toggle button")
        self.app.press(acc=[i for i in self.app.menu_items() if i.text.startswith("Parallel")][0])
        time.sleep(0.4)
        self.assertTrue(self.app.state()["drawing"]["constructing"])
        self.s.type("g40,-12\n")  # the cursor onto g1, Ø40 from Z0 to Z-25
        self.s.key("Return")  # takes g1
        time.sleep(0.5)
        self.s.type("5\n")  # the distance (radius)
        time.sleep(0.5)
        self.snap("solutions")
        self.s.key("Tab")
        time.sleep(0.3)
        self.s.key("Return")
        time.sleep(0.5)
        self.assertFalse(self.app.state()["drawing"]["constructing"])
        new = self.saved(path).geometry[-1]
        self.assertEqual(new.a.x, new.b.x, "a line along Z")
        self.assertIn(round(new.a.x, 3), (30.0, 50.0), f"5 mm from Ø40, not {new.a.x}")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardZoomAndKeypad(Bus):
    """§3 and §11: z zooms at the ALPHA-T cursor; i toggles ABS / INC in the keypad."""

    needs_state = True

    def on_screen(self, x: float, z: float) -> tuple[float, float]:
        """Where the drawing's X (diameter), Z is, from the bus's view (scale px/mm, origin ox, oy)."""
        v = self.app.state()["view"]
        return v["ox"] + z * v["scale"], v["oy"] - x / 2 * v["scale"]

    def test_z_zooms_at_the_cursor(self):
        self.launch(str(self.part(shaft())))
        self.s.type("g60,-40\n")
        time.sleep(0.3)
        scale = self.app.state()["view"]["scale"]
        before = self.on_screen(60, -40)
        self.s.type("z")
        time.sleep(0.4)
        self.assertGreater(self.app.state()["view"]["scale"], scale * 1.1, "z should zoom in")
        after = self.on_screen(60, -40)
        self.assertAlmostEqual(after[0], before[0], delta=2, msg="the cursor's point should stay where it was")
        self.assertAlmostEqual(after[1], before[1], delta=2, msg="the cursor's point should stay where it was")

    def test_i_toggles_incremental_in_the_keypad(self):
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path), "--touch")
        self.tap("Line", "radio button")
        self.tap("Keypad", "button")
        self.app.find("✓", "button")
        time.sleep(0.3)
        self.s.type("40,0\n")  # X 40, Z 0 by the keyboard
        time.sleep(0.4)
        self.tap("Keypad", "button")
        self.app.find("✓", "button")
        time.sleep(0.3)
        self.s.type("i")  # incremental
        self.s.type("0,-25\n")
        time.sleep(0.4)
        self.tap("Done", "button")
        self.assertEqual([(e.a, e.b) for e in self.saved(path).geometry], [(Point(40, 0), Point(40, -25))])


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class MoreMenuWithoutLetters(Bus):
    """§4: Text, Measure, Line by Length and Angle and Move Cursor are in the More menu (finger and pen)."""

    needs_state = True

    def test_more_menu_reaches_the_letter_commands(self):
        self.launch(str(self.part(shaft())), "--touch")
        self.tap("More Drawing Commands", "toggle button")
        time.sleep(0.6)
        items = [i.text for i in self.app.menu_items()]
        self.snap("more")
        for label in ("Add Text", "Erase Text"):
            self.assertTrue(any(i.startswith(label) for i in items), f"{label!r} not in {items}")
        self.tap(acc=self.app.find("Cursor and Measure", "menu item"))  # the submenu
        time.sleep(0.6)
        items = [i.text for i in self.app.menu_items()]
        self.snap("cursor-and-measure")
        for label in ("Measure by X,Z", "Measure by Length and Angle", "Draw Line by Length and Angle",
                      "Move Cursor to X,Z", "Move Cursor by dX,dZ"):
            self.assertTrue(any(i.startswith(label) for i in items), f"{label!r} not in {items}")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class NumbersEverywhere(Bus):
    """§11: a finger on any number field opens the keypad, not the system keyboard."""

    needs_state = True

    def number_texts(self):
        """The typing part of each visible spin button (its centre is the − button), top to bottom."""
        ws = self.app.widgets()
        spins = [w for w in ws if w.css == "spinbutton" and w.w > 0]
        texts = [t for t in ws if t.css == "text" and t.w > 0
                 and any(sp.x <= t.x + t.w / 2 <= sp.x + sp.w and sp.y <= t.y + t.h / 2 <= sp.y + sp.h for sp in spins)]
        return sorted(texts, key=lambda t: t.y)

    def keypad_sets(self, field_title: str, digits: str) -> None:
        sb = self.app.all(field_title, "spinbutton")[0]
        self.s.tap(*self.app.center(acc=sb))
        self.app.find("✓", "button")
        for k in list(digits) + ["✓"]:
            self.tap(k, "button")
        time.sleep(0.3)

    def test_edit_values_keypad_by_finger(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.s.hold(*to_screen(40, -12), 1.2, touch=True)
        self.tap(acc=[i for i in self.app.menu_items() if i.text == "Edit Values…"][0])
        time.sleep(0.5)
        self.snap("edit")
        rows = self.number_texts()
        self.assertTrue(rows, "Edit Values… should show number fields")
        self.s.tap(*self.app.center(acc=rows[0]))
        self.app.find("✓", "button")
        self.assertTrue(self.app.exists("⌫", "button"), "the keypad, not the system keyboard")

    def test_new_part_keypad_by_finger(self):
        self.launch("--touch")
        self.tap("New Part…", "button")
        time.sleep(0.5)
        rows = self.number_texts()
        self.assertTrue(rows)
        self.s.tap(*self.app.center(acc=rows[0]))
        self.app.find("✓", "button")
        self.snap("keypad")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class ShortcutsWindow(Bus):
    """§12: every key the Shortcuts window lists does what it says (read on the bus). One session, Escape between."""

    needs_state = True

    def st(self):
        return self.app.state()

    def esc(self, n: int = 2) -> None:
        for _ in range(n):
            self.s.key("Escape")
            time.sleep(0.25)

    def drawing_focus(self) -> None:
        self.s.key("alt+1")
        x, y = self.empty_spot()
        self.s.click(x, y)  # the drawing takes the keys (Select tool: a click on nothing selects nothing)
        time.sleep(0.3)

    def opens(self, key: str, what: str) -> None:
        """`key` opens something with a ✕ or more widgets than before; Escape closes it again."""
        n = len(self.app.widgets())
        self.s.key(key)
        time.sleep(0.8)
        self.assertGreater(len(self.app.widgets()), n, f"{key} should open {what}")
        self.esc()
        time.sleep(0.3)

    def test_general_files_and_view_keys(self):
        path = self.part(two_ops())
        self.launch(str(path))
        self.opens("ctrl+comma", "Preferences")
        self.opens("ctrl+question", "Keyboard Shortcuts")
        self.opens("ctrl+n", "New Part")
        self.opens("F10", "the main menu")
        self.drawing_focus()
        fit = self.st()["view"]["scale"]
        self.s.key("ctrl+plus")
        time.sleep(0.3)
        self.assertGreater(self.st()["view"]["scale"], fit, "Ctrl++ zooms in")
        self.s.key("ctrl+minus")
        self.s.key("ctrl+minus")
        time.sleep(0.3)
        self.assertLess(self.st()["view"]["scale"], fit, "Ctrl+− zooms out")
        self.s.key("ctrl+0")
        time.sleep(0.3)
        self.assertAlmostEqual(self.st()["view"]["scale"], fit, delta=fit * 0.01, msg="Ctrl+0 fits")
        self.s.type("z")
        time.sleep(0.3)
        self.assertGreater(self.st()["view"]["scale"], fit, "z zooms in")
        self.s.type("r")
        time.sleep(0.3)
        self.assertAlmostEqual(self.st()["view"]["scale"], fit, delta=fit * 0.01, msg="r fits (ALPHA-T Redraw)")
        self.s.key("ctrl+f")
        time.sleep(0.4)
        self.assertEqual(self.st()["page"], "program", "Ctrl+F searches the program")
        self.esc()

    def test_cursor_keys(self):
        self.launch(str(self.part(shaft())))
        self.drawing_focus()
        self.s.key("End")  # X0 Z0
        time.sleep(0.2)
        self.assertEqual(self.st()["drawing"]["cursor"], [0.0, 0.0], "End: the cursor to X0 Z0")
        self.s.key("Right")
        time.sleep(0.2)
        step = self.st()["drawing"]["cursor"][1]
        self.assertGreater(step, 0, "→ moves the cursor along +Z by the step")
        self.s.key("Page_Up")  # step ×10
        self.s.key("Right")
        time.sleep(0.2)
        self.assertAlmostEqual(self.st()["drawing"]["cursor"][1] - step, step * 10, delta=step * 0.01)
        self.s.key("Page_Down")
        self.s.key("Page_Down")  # step ÷100 from there: ÷10 of the first
        self.s.key("Left")
        time.sleep(0.2)
        self.assertAlmostEqual(self.st()["drawing"]["cursor"][1], step * 11 - step / 10, delta=step * 0.01)
        self.s.key("Home")  # the middle of the view
        time.sleep(0.2)
        self.assertNotEqual(self.st()["drawing"]["cursor"], [0.0, 0.0])

    def test_drawing_letter_keys(self):
        path = self.part(shaft())
        self.launch(str(path))
        self.drawing_focus()
        self.s.type("g40,-12\n")
        self.assertEqual(self.st()["drawing"]["cursor"], [40.0, -12.0], "g moves the cursor to X,Z")
        self.s.type("h0,-3\n")
        self.assertEqual(self.st()["drawing"]["cursor"], [40.0, -15.0], "h moves it by dX,dZ")
        self.s.type("l")
        time.sleep(0.2)
        self.assertEqual(self.st()["drawing"]["selected_element"], "g1", "l finds the nearest line")
        self.s.type("e")
        time.sleep(0.5)
        self.assertTrue(self.app.exists("Apply", "button"), "e edits the element")
        self.esc(1)
        self.s.key("Delete")
        time.sleep(0.3)
        self.assertEqual(self.st()["elements"], 4, "Delete deletes it")
        self.s.key("ctrl+z")
        time.sleep(0.3)
        self.assertEqual(self.st()["elements"], 5, "Ctrl+Z undoes")
        self.s.key("shift+ctrl+z")
        time.sleep(0.3)
        self.assertEqual(self.st()["elements"], 4, "Shift+Ctrl+Z redoes")
        self.s.key("ctrl+z")
        self.esc()
        self.s.type("p")
        time.sleep(0.2)
        self.assertIn(self.st()["drawing"]["cursor"], ([40.0, -25.0], [40.0, 0.0]), "p: the nearest point")
        for key, tool in (("w", "arc"), ("k", "corner"), ("b", "chamfer"), ("+", "line")):
            self.s.type(key)
            time.sleep(0.3)
            self.assertEqual(self.st()["tool"], tool, f"{key} picks the {tool} tool")
            self.esc()
        for key, what in (("f", "the radius at the nearest corner"),):
            self.drawing_focus()
            self.s.type("g40,-25\n")  # at the corner between g1 and g2
            n = len(self.app.widgets())
            self.s.type(key)
            time.sleep(0.5)
            self.assertGreater(len(self.app.widgets()), n, f"{key} should ask for {what}")
            self.esc()
        self.s.type("o")
        time.sleep(0.3)
        self.assertTrue(self.st()["drawing"]["constructing"], "o starts a blend arc")
        self.esc()
        self.opens("shift+F10", "the drawing's menu")
        for key, what in (("t", "Add Text"), ("m", "Measure by X,Z"), ("d", "Line by Length and Angle"),
                          ("q", "Erase the Whole Drawing")):
            self.drawing_focus()
            n, said = len(self.app.widgets()), self.st()["readout"]
            self.s.type(key)
            time.sleep(0.6)
            asked = len(self.app.widgets()) > n or self.st()["readout"] != said  # a dialog, or a prompt
            self.assertTrue(asked, f"{key} should ask for {what}")
            self.esc()
        self.assertEqual(self.st()["elements"], 5, "nothing above changed the drawing for good")
        self.drawing_focus()
        self.s.type("c")
        time.sleep(0.4)
        self.assertTrue(self.app.menu_items(timeout=2), "c opens Add Operation")
        self.esc()


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class PenByTestSeam(Bus):
    """§10 through the bus's ForceSource("pen"): libei clicks counted as pen presses. The app's own pen rules, not
    GTK's stylus events (the Surface checklist in POINTER_KEYBOARD.md §13 covers those)."""

    needs_state = True

    def pen(self, *args: str):
        self.launch(*args)
        from gi.repository import GLib

        try:
            self.app.force_source("pen")
        except GLib.Error:
            self.skipTest("this build has no ForceSource on its test bus")

    def test_pen_does_not_flip_the_layout_and_gets_its_helpers(self):
        self.pen(str(self.part(shaft())))
        for _ in range(2):
            self.s.click(*self.app.center("Operations", "label"))
        time.sleep(0.5)
        big = self.app.rect(self.app.find("Undo (Ctrl+Z)", "button"))
        self.assertLess(big[3], 48, "a pen should not switch to the touch layout")
        self.assertTrue(self.app.exists("Zoom Out", "button"), "the pen gets the zoom buttons")
        self.assertTrue(self.app.exists("Keypad", "button"), "the pen gets the Keypad button")

    def test_pen_long_press_opens_the_menu(self):
        p = shaft()
        self.pen(str(self.part(p)))
        to_screen = self.calibrate()
        self.s.hold(*to_screen(40, -12), 1.2)  # a pen held on g1
        items = [i.text for i in self.app.menu_items()]
        self.snap("menu")
        self.assertIn("Edit Values…", items)

    def test_pen_tap_on_an_editor_number_opens_the_keypad(self):
        self.pen(str(self.part(with_op())))
        self.s.click(*self.app.center(acc=self.app.labelled("Area clear", "list item")))
        time.sleep(0.5)
        sb = self.app.all("Feed", "spinbutton")[0]
        texts = [t for t in self.app.widgets() if t.css == "text" and t.w > 0
                 and sb.x <= t.x + t.w / 2 <= sb.x + sb.w and sb.y <= t.y + t.h / 2 <= sb.y + sb.h]
        self.s.click(*self.app.center(acc=texts[0]))
        self.app.find("✓", "button")
        self.assertTrue(self.app.exists("⌫", "button"))


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class MachineMore(VirtualPort, Bus):
    """The Machine page's other transfers over the virtual port: Restore Manual Ops (hold, pick, confirm) and the
    cable's loopback test."""

    def open_machine(self):
        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", args=(str(self.part(with_op())),), env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.s.key("alt+5")
        time.sleep(0.6)

    def test_restore_manual_ops(self):
        from .part import manops_load_bytes

        data = b"%\r\nO1001\r\nM29\r\nG65 P63 A1. B2.\r\nG65 P63 A3. B4.\r\nM30\r\n%\r\n"
        ops = self.s.root / "home" / "OPS.MOP"
        ops.write_bytes(data)
        self.open_machine()
        x, y = self.app.center(acc=self.app.labelled("Restore to the Lathe…", "button"))
        self.s.hold(x, y, 2.5)
        from .a11y import App

        App.wait(15, names=("org.gnome.Nautilus", "xdg-desktop-portal-gnome", "Nautilus"))
        time.sleep(1.0)
        self.s.key("ctrl+l")
        self.s.type(str(ops) + "\n")
        time.sleep(0.8)
        self.s.key("Return")
        self.app.find("Restore Manual Ops?", contains=True, timeout=10)
        self.snap("confirm")
        self.s.key("alt+r")
        expected = manops_load_bytes(data)
        self.lathe.send(b"\x11")
        self.lathe.read_for(20, until=len(expected))
        self.assertEqual(bytes(self.lathe.got), expected)
        self.wait_for(lambda: self.app.exists("Sent ", contains=True), 10, "the restore to finish")

    def test_loopback_with_an_echoing_cable(self):
        import threading

        self.open_machine()
        stop = threading.Event()

        def echo():  # the 2-3 bridge: whatever goes out comes back
            while not stop.is_set():
                n0 = len(self.lathe.got)
                self.lathe.read_for(0.05)
                if len(self.lathe.got) > n0:
                    self.lathe.send(bytes(self.lathe.got[n0:]))

        t = threading.Thread(target=echo, daemon=True)
        t.start()
        try:
            self.s.scroll(self.s.width * 0.6, self.s.height * 0.6, 15)  # the Cable Test is at the bottom of the page
            self.press("Run Loopback Test", "button")
            self.app.find("Run the loopback test?", contains=True)
            self.s.key("Return")  # the default: Run Test
            self.wait_for(lambda: self.app.exists("Loopback OK", contains=True), 10, "the loopback result")
            self.snap("ok")
        finally:
            stop.set()
            t.join(1)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class DrawingByTouch200(DrawingByTouch):
    """The drawing by touch at the Surface's real 200 % (the value chip went astray there, 2026-10-17)."""

    def setUp(self):
        from .harness import GnomeSession
        from .test_workflows import OUT

        self.s = GnomeSession(*LANDSCAPE, scale=2.0).__enter__()
        self.out = OUT / f"{self.__class__.__name__}-{self._testMethodName}"
        self.n = 0


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class LatheTrouble(VirtualPort, Bus):
    """A lathe or cable that misbehaves: the app must say what happened and never let a half program pass for a
    sent one."""

    def start_send(self) -> bytes:
        p = shaft()
        from .part import default_op

        p.operations += [default_op("area_clear", p, 0), default_op("finish", p, 1)]
        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", args=(str(self.part(p)),), env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.s.key("alt+5")
        time.sleep(0.6)
        self.s.hold(*self.app.center(acc=self.app.labelled("Send to the Lathe", "button")), 2.5)
        self.press("Send", "button")
        time.sleep(0.5)
        return post(p)

    def state(self) -> str:
        """The transfer dialog's message."""
        for w in self.app.widgets():
            if w.id == "state" and w.css == "label" and w.w > 0:
                return w.text
        return ""

    def finished(self) -> bool:
        return self.app.exists("Close", "button") and bool(self.state())

    def test_cable_pulled_mid_send(self):
        expected = self.start_send()
        self.lathe.send(b"\x11")
        self.lathe.read_for(10, until=600)
        self.socat.kill()  # the cable comes out
        self.socat.wait(5)
        self.wait_for(self.finished, 20, "the send to give up")
        self.snap("pulled")
        said = self.state()
        self.assertFalse(said.startswith("Sent "), f"a cut transfer reported as sent: {said!r}")
        self.assertIn("incomplete", said, "the operator must learn the program on the lathe is incomplete")
        self.assertLess(len(self.lathe.got), len(expected))
        lock = self.app.find("Touch Lock", "toggle button", contains=True)
        self.assertFalse(self.app.checked(lock), "the touch lock should come off when the send ends")

    def test_pause_storm(self):
        """Twenty pauses (DC3) and resumes (DC1) in a row: never more than 10 characters after a pause, and the
        program arrives whole."""
        expected = self.start_send()
        self.lathe.send(b"\x11")
        for _ in range(20):
            self.lathe.read_for(5, until=len(self.lathe.got) + 100)
            self.lathe.send(b"\x13")
            self.assertLessEqual(self.lathe.read_for(0.3), 10, "more than 10 characters after DC3")
            self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(expected))
        self.assertEqual(bytes(self.lathe.got), expected)
        self.wait_for(lambda: self.state().startswith("Sent "), 10, "the send to finish")

    def test_garbage_from_the_lathe(self):
        """Noise on the line (bytes that are not DC1 / DC3) is ignored."""
        expected = self.start_send()
        self.lathe.send(b"\x11")
        for junk in (b"\x00", b"\xff\xfe", b"ABC\r\n", b"%", b"\x12\x14"):
            self.lathe.read_for(5, until=len(self.lathe.got) + 200)
            self.lathe.send(junk)
        self.lathe.read_for(60, until=len(expected))
        self.assertEqual(bytes(self.lathe.got), expected)

    def test_silent_lathe_then_cancel(self):
        """The lathe never asks (no DC1): nothing goes out; Cancel stops cleanly, still nothing."""
        self.start_send()
        self.assertEqual(self.lathe.read_for(4.0), 0, "bytes sent without DC1")
        self.press("Cancel", "button")
        self.wait_for(self.finished, 10, "the cancel to finish")
        self.snap("cancelled")
        self.assertFalse(self.state().startswith("Sent "))
        self.lathe.send(b"\x11")
        self.assertEqual(self.lathe.read_for(1.0), 0, "bytes sent after Cancel")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class KeyboardOnlyJob(VirtualPort, Bus):
    """A whole job without the mouse or a finger: new part, the profile typed, an operation, save, export, send."""

    needs_state = True

    def chooser_name(self, name: str) -> None:
        from .a11y import App

        App.wait(15, names=("org.gnome.Nautilus", "xdg-desktop-portal-gnome", "Nautilus"))
        time.sleep(1.0)
        self.s.key("ctrl+a")
        self.s.type(name + "\n")
        time.sleep(1.5)

    def focus_on(self, text: str, tries: int = 60) -> None:
        """Tab until the widget labelled `text` has the keyboard."""
        target = self.app.labelled(text, "button")
        for _ in range(tries):
            if self.app.focused(target):
                return
            self.s.key("Tab")
            time.sleep(0.1)
        self.fail(f"Tab never reached {text!r}")

    def test_new_part_to_the_lathe_by_keys(self):
        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.s.key("ctrl+n")
        time.sleep(0.8)
        self.s.key("Return")  # Create, with the default billet
        time.sleep(0.8)
        self.s.type("x0,0\n40,0\n40,-25\n60,-25\n60,-50\n")
        self.s.key("Escape")
        time.sleep(0.3)
        self.assertEqual(self.app.state()["elements"], 4)
        self.s.type("c")  # ALPHA-T's CAM menu: Add Operation
        self.app.menu_items()
        self.s.key("Return")  # the first item, Area Clear
        time.sleep(0.8)
        self.assertEqual(self.app.state()["operations"], ["Area clear"])
        self.s.key("ctrl+s")
        self.chooser_name("kbd.alcam")
        path = self.s.root / "home/kbd.alcam"
        self.wait_for(path.exists, 10, "kbd.alcam")
        p = Project.load(path)
        self.assertEqual(len(p.operations), 1)
        self.s.key("ctrl+e")
        self.chooser_name("KBD.PRG")
        prg = self.s.root / "home/KBD.PRG"
        self.wait_for(prg.exists, 10, "KBD.PRG")
        self.assertEqual(prg.read_bytes(), post(p))
        self.s.key("alt+5")
        time.sleep(0.6)
        self.focus_on("Send to the Lathe")
        self.s.hold_key("space", 2.5)
        self.app.find("Send", "button")
        self.s.key("alt+s")
        self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(post(p)))
        self.assertEqual(bytes(self.lathe.got), post(p))


def big_part(steps: int = 150, ops: int = 12) -> Project:
    """A long stepped shaft: 2 × `steps` lines (300 by default) and `ops` operations over it."""
    from .part import default_op

    p = Project(billet=Billet(100, 6 * steps + 20), first_tool=3)
    pts, z = [(0.0, 0.0)], 0.0
    for i in range(steps):
        x = 20.0 + 70.0 * i / steps
        pts += [(x, z), (x, z - 6.0)] if i else [(x, 0.0), (x, -6.0)]
        z -= 6.0
    pts = [pts[0]] + pts[1:]
    p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(pts, pts[1:]) if a != b]
    for i, g in enumerate(p.geometry):
        g.id = f"g{i}"
    for i in range(ops):
        p.operations.append(default_op("area_clear" if i % 2 == 0 else "finish", p, len(p.operations)))
    return p


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class BigPart(Bus):
    """Hundreds of elements and a dozen operations: the app stays responsive (a bus call is answered only when the
    main loop is free, so its delay is how long the screen was stuck). Limits are generous, for a busy CI machine."""

    needs_state = True

    def stuck_ms(self) -> float:
        t = time.monotonic()
        self.app.state()
        return (time.monotonic() - t) * 1000

    def test_big_part_stays_responsive(self):
        p = big_part()
        path = self.part(p)
        t0 = time.monotonic()
        self.launch(str(path), "--touch")
        opened = time.monotonic() - t0
        self.assertGreaterEqual(self.app.state()["elements"], 290)
        timings = {"open s": round(opened, 1)}
        worst = 0.0
        x, y = self.empty_spot()
        import threading

        for _ in range(3):  # pans, asking the app all the while
            t = threading.Thread(target=self.s.input.touches, args=([(x, y)], [(x - 300, y + 100)], 20, 0.2))
            t.start()
            while t.is_alive():
                worst = max(worst, self.stuck_ms())
            t.join()
        timings["pan ms"] = round(worst)
        t = time.monotonic()
        self.s.key("alt+2")
        timings["program page ms"] = round((time.monotonic() - t) * 1000 + self.stuck_ms())
        self.s.key("alt+1")
        self.s.key("alt+Home")
        self.s.key("F5")
        time.sleep(1.5)
        self.s.key("F5")
        self.assertGreater(self.app.state()["sim_index"] or 0, 10, "the simulation should play")
        t = time.monotonic()
        self.s.key("ctrl+s")
        self.wait_for(lambda: path.stat().st_mtime > t0 + opened, 10, "the save")
        timings["save s"] = round(time.monotonic() - t, 1)
        print("big part timings:", timings, flush=True)
        self.assertLess(opened, 15, timings)
        self.assertLess(timings["pan ms"], 500, timings)
        self.assertLess(timings["program page ms"], 3000, timings)
        self.assertLess(timings["save s"], 5, timings)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class EditorSwipes(Bus):
    """Eduard's video 2026-10-19: a swipe that starts on an editor number scrolled the editor in jumps, about half as
    far as the finger. The editor must follow the finger 1:1 wherever the swipe starts, without going back."""

    def row_y(self, title: str):
        rows = [w for w in self.app.widgets() if w.css == "row" and w.text == title and w.w > 0]
        return rows[0].y if rows else None

    def swipe_on(self, title: str, x_of, dy: float):
        import threading

        r = self.app.all(title, "row")[0]
        x, y = x_of(r), r.y + r.h / 2
        before, trace = self.row_y(title), []
        t = threading.Thread(target=self.s.input.touches, args=([(x, y)], [(x, y + dy)], 15, 0.3))
        t.start()
        while t.is_alive():
            trace.append(self.row_y(title))
            time.sleep(0.03)
        time.sleep(0.5)
        return self.row_y(title) - before, trace

    def check(self, x_of, where: str):
        self.launch(str(self.part(with_op())), "--touch")
        self.tap(acc=self.app.labelled("Area clear", "list item"))
        time.sleep(0.6)
        moved, trace = self.swipe_on("Feed", x_of, -200)
        self.assertAlmostEqual(moved, -200, delta=16, msg=f"a 200 px swipe from {where} moved the editor {moved:.0f} px")
        steps = [b - a for a, b in zip(trace, trace[1:])]
        self.assertFalse([s for s in steps if s > 4], f"the editor went back during the swipe from {where}: {trace}")

    def test_swipe_from_a_number_follows_the_finger(self):
        self.check(lambda r: r.x + r.w - 160, "the number")

    def test_swipe_from_a_title_follows_the_finger(self):
        self.check(lambda r: r.x + 40, "the title")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SurfaceReplay(Bus):
    """Eduard's own finger, recorded on the Surface (libinput record, 2026-10-19) and replayed through libei: each
    swipe started on an editor number must scroll the editor as far as the same swipe started on a row's title, and
    never backwards."""

    RECORDING = Path(__file__).parent / "recordings/surface-editor-swipes-2026-10-19.yml"

    def editor_scroll(self, stroke, x_of) -> tuple[float, list]:
        import threading

        from .replay import placed

        self.tap(acc=self.app.labelled("Area clear", "list item"))  # fresh: the editor at its top
        time.sleep(0.6)
        self.s.key("Escape")
        time.sleep(0.3)
        self.tap(acc=self.app.labelled("Area clear", "list item"))
        time.sleep(0.6)
        r = self.app.all("Depth of cut", "row")[0]
        y_of = lambda: next((w.y for w in self.app.widgets() if w.css == "row" and w.text == "Depth of cut"), None)
        before, trace = y_of(), []
        t = threading.Thread(target=self.s.input.play, args=(placed(stroke, (x_of(r), r.y + r.h / 2), upwards=True),))
        t.start()
        while t.is_alive():
            trace.append(y_of())
            time.sleep(0.03)
        time.sleep(0.5)
        return y_of() - before, trace

    def test_recorded_swipes_on_numbers_scroll_like_on_titles(self):
        """From a number the editor moves as it does from a row's title (with the same coast after the lift), and a
        swipe never opens the keypad."""
        from .replay import load

        self.launch(str(self.part(with_op())), "--touch")
        results = []
        for i, stroke in enumerate(load(self.RECORDING)):
            on_title, _ = self.editor_scroll(stroke, lambda r: r.x + 40)
            on_number, _trace = self.editor_scroll(stroke, lambda r: r.x + r.w - 160)
            self.assertFalse(self.app.exists("✓", "button"), f"stroke {i} opened the keypad")
            results.append((i, round(on_title), round(on_number)))
        self.snap("replayed")
        bad = [r for r in results if abs(r[1] - r[2]) > 40]
        self.assertEqual(bad, [], f"(stroke, editor moved from the title, from the number): {results}")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class WindowWidths(Bus):
    """Eduard's photo 2026-10-19: in a window narrower than the layout needed, the operation editor ran past the right
    edge (its −/+ and the main menu cut off). Between the narrow breakpoint (1000) and the full screen, with the
    editor open by touch, nothing may stick out of the window, in English and in Ukrainian."""

    def overflow(self, width: int, lang: str) -> list:
        from .harness import GnomeSession

        s = GnomeSession(width, 900).__enter__()
        try:
            self.s = s
            path = s.root / "home" / "part.alcam"
            two_ops().save(path)
            self.app = s.launch_app(lang, args=(str(path), "--touch"))
            time.sleep(1.2)
            def finish_rows():
                return [w for w in self.app.widgets()
                        if w.css == "row" and w.w > 0 and w.text in ("Finish pass", "Чистовий прохід")]

            if not finish_rows():  # the narrow layout opens on the drawing: the operations are behind the back button
                back = [w for w in self.app.widgets() if w.css == "button" and w.w > 0 and w.x < 80 and w.y < 80][0]
                s.tap(*self.app.center(acc=back))
                time.sleep(0.8)
            s.tap(*self.app.center(acc=finish_rows()[-1]))  # its editor opens
            time.sleep(0.8)
            s.screenshot(self.out / f"{lang}-{width}.png")
            return sorted({f"{w.name} {w.text[:24]!r} to {w.x + w.w:.0f}" for w in self.app.widgets()
                           if w.w > 0 and w.x + w.w > width + 1 and w.css in ("button", "row", "label", "menubutton")})[:6]
        finally:
            s.__exit__(None, None, None)

    def setUp(self):  # each width gets its own session (overflow())
        from .test_workflows import OUT

        self.out = OUT / f"{self.__class__.__name__}-{self._testMethodName}"
        self.out.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def tearDown(self):
        pass

    def test_no_overflow_between_the_breakpoint_and_full_width(self):
        found = {}
        for lang in ("en", "uk"):
            for width in (1020, 1140, 1260):
                bad = self.overflow(width, lang)
                if bad:
                    found[f"{lang} {width}"] = bad
        self.assertEqual(found, {}, "controls past the window's right edge")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class EditorTwoFingers(Bus):
    """Eduard's photo 2026-10-19: a red focus frame and a text caret in an editor number. A second finger (or a
    palm) on the number while another touches the editor must not give the number's text the keyboard."""

    def test_second_finger_on_a_number_takes_no_focus(self):
        self.launch(str(self.part(with_op())), "--touch")
        self.tap(acc=self.app.labelled("Area clear", "list item"))
        time.sleep(0.6)
        sb = self.app.all("Offset X", "spinbutton")[0]
        text = [w for w in self.app.widgets() if w.css == "text" and w.w > 0
                and sb.x <= w.x + w.w / 2 <= sb.x + sb.w and sb.y <= w.y + w.h / 2 <= sb.y + sb.h][0]
        row = self.app.all("Offset X", "row")[0]
        x, y = self.app.center(acc=text)
        self.s.input.touches([(row.x + 40, y), (x, y)], None, 10, 0.2)  # a finger on the title, one on the number
        time.sleep(0.6)
        self.snap("two-fingers")
        focused = [f"{w.name} {w.text!r}" for w in self.app.widgets() if w.focus and w.css in ("text", "entry")]
        self.assertEqual(focused, [], "a touch gave an editor number's text the keyboard (red frame, caret)")
