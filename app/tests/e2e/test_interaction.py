"""Touch interactions that test_workflows.py does not reach (TOUCH.md; the parity list of 2026-10-16): layout
switching, menus by finger, canvas navigation (fling, double tap, zoom buttons, long-press zoom), drawing tools by
touch (arc, corner, fillet chips, erase, keypad keys, value chip), the operation editor (−/+, header buttons), the
simulation (play, scrub), program search by touch, hold to send by finger, and touch target sizes.

Every input is a libei tap, drag, hold or key. They read the app through its test bus, so they run against the Rust
app (ALPHALINK_E2E_BRANCH, see README); the Python app has no bus and is waiting for the rewrite."""

from __future__ import annotations

import os
import time
import unittest

from alphacam.model import Arc, Line, Point
from alphacam.model.project import Billet, Project
from alphacam.post.alphat_prg import post

from .test_workflows import LANDSCAPE, Surface, VirtualPort, shaft, with_op

ENABLED = os.environ.get("ALPHALINK_E2E") == "1" and bool(os.environ.get("ALPHALINK_E2E_BRANCH"))
MIN_TARGET_PX = 44  # the smallest finger target (GNOME HIG: "at least 44 px" on touch)


def two_ops() -> Project:
    p = with_op("area_clear")
    from alphacam_gtk.op_factory import default_op

    p.operations.append(default_op("finish", p, 1))
    return p


class Bus(Surface):
    """Helpers on top of the test bus."""

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
        self.s.tap(*self.app.center(name, role, acc, contains))

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


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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
        self.tap("Main Menu", "toggle button")
        self.tap(acc=[i for i in self.app.menu_items() if i.text == "Touch"][0])
        time.sleep(0.5)
        self.assertTrue(self.touch_only())
        self.s.key("ctrl+q")
        time.sleep(1.5)
        self.launch(str(path))
        self.snap("restarted")
        self.assertTrue(self.app.exists("Zoom In", "button"), "the zoom buttons are missing after a restart")
        self.assertTrue(self.app.exists("Keypad", "button"), "the Keypad button is missing after a restart")


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
class MenusByTouch(Bus):
    """Popover menus driven by finger: Eduard 2026-10-16, a tapped + menu item closed the menu and added nothing."""

    def test_every_add_operation_item_by_tap(self):
        from alphacam_gtk.op_factory import OP_MENU

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
        self.tap(acc=[i for i in self.app.menu_items() if i.text == "Erase Drawing…"][0])
        self.app.find("Erase All of the Drawing?", contains=True)
        self.snap("asked")
        self.tap("Erase", "button")
        time.sleep(0.5)
        self.assertEqual(self.saved(path).geometry, [])
        self.tap("Undo (Ctrl+Z)", "button")
        time.sleep(0.5)
        self.assertEqual(len(self.saved(path).geometry), 5, "Undo should bring the drawing back")


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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
        self.assertGreater(moved_px, 230, f"the view moved {moved_px:.0f} px for a 200 px flick")

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


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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
        return self.app.find("Ø ", "button", contains=True)

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

    def test_value_chip_hides_when_the_point_leaves_the_view(self):
        path, to_screen = self.blank()
        self.tap("Line", "radio button")
        self.s.tap(*to_screen(0, 0))
        self.s.tap(*to_screen(40, 0))
        self.assertTrue(self.app.exists("Ø ", "button", contains=True))
        x, y = self.empty_spot()
        self.s.input.touches([(x, y)], [(x - 900, y)], 30, 0.3)  # pan the point far off to the left
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


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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


@unittest.skipUnless(ENABLED, "set ALPHALINK_E2E=1 and ALPHALINK_E2E_BRANCH (the app's test bus)")
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
