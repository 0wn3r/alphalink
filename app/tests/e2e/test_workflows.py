"""End-to-end workflows on the installed Flatpak, with real keyboard, mouse and touch input, at the Surface Pro 7's
logical sizes (2736×1824 at 200 %: 1368×912 landscape, 912×1368 portrait, 684×912 half screen).

Run (about two minutes; screenshots of every step go to e2e-out/):
    ALPHALINK_E2E=1 python3 -m unittest tests.e2e.test_workflows -v

Skipped unless ALPHALINK_E2E=1: it needs GNOME Shell, PipeWire, socat and the Flatpak installed (build it first)."""

from __future__ import annotations

import os
import subprocess
import threading
import time
import unittest
from pathlib import Path

from alphacam.model import Arc, Line, Point
from alphacam.model.project import Billet, Project
from alphacam.post.alphat_prg import post

ENABLED = os.environ.get("ALPHALINK_E2E") == "1"
OUT = Path(os.environ.get("ALPHALINK_E2E_OUT", Path(__file__).resolve().parents[2] / "e2e-out"))
LANDSCAPE, PORTRAIT, HALF = (1368, 912), (912, 1368), (684, 912)


def shaft() -> Project:
    """Training manual exercise 1, drawn."""
    p = Project(billet=Billet(100, 60), first_tool=3)
    pts = [(0, 0), (40, 0), (40, -25), (60, -25), (60, -50), (100, -50)]
    p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(pts, pts[1:])]
    for i, g in enumerate(p.geometry):
        g.id = f"g{i}"
    return p


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class Surface(unittest.TestCase):
    size = LANDSCAPE
    lang = "en"

    def setUp(self):
        from .harness import GnomeSession

        self.s = GnomeSession(*self.size).__enter__()
        self.out = OUT / f"{self.__class__.__name__}-{self._testMethodName}"
        self.n = 0

    def tearDown(self):
        self.snap("end")
        self.s.__exit__(None, None, None)

    def snap(self, what: str) -> None:
        self.n += 1
        self.s.screenshot(self.out / f"{self.n:02d}-{what}.png")

    def part(self, project: Project, name: str = "part.alcam") -> Path:
        path = self.s.root / "home" / name
        project.save(path)
        return path

    def launch(self, *args: str, lang: str | None = None):
        self.app = self.s.launch_app(lang or self.lang, args=args)
        time.sleep(1.0)
        return self.app

    def press(self, name: str | None = None, role: str | None = None, contains: bool = False, acc=None) -> None:
        self.app.press(name, role, contains, acc)

    def menu(self, button: str, index: int) -> None:
        self.app.press(button, "toggle button")
        self.app.press(acc=self.app.menu_items()[index])

    def wait_for(self, pred, timeout: float = 10, what: str = "") -> None:
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return
            time.sleep(0.25)
        self.snap("timeout")
        self.fail(f"timed out waiting for {what}")

    def saved(self, path: Path) -> Project:
        self.s.key("ctrl+s")
        time.sleep(1.0)
        return Project.load(path)


class Keyboard(Surface):
    def test_new_part_from_the_start_screen(self):
        """Ctrl+N, Enter: the part opens on the drawing, which has the keyboard (the arrows act at once)."""
        self.launch()
        self.snap("start")
        self.assertTrue(self.app.exists("Open the Training Manual", "button"))
        self.s.key("ctrl+n")
        self.app.find("Create", "button")
        self.snap("new-part")
        self.s.key("Return")
        self.app.find("Line", contains=True)
        self.assertTrue(self.app.focused(self.app.find("Drawing")), "the drawing should have the keyboard")
        self.s.type("x0,0\n40,0\n")
        self.s.key("Escape")
        undo = self.app.find("Undo (Ctrl+Z)", "button")
        self.assertTrue(undo.get_state_set().contains(__import__("gi").repository.Atspi.StateType.SENSITIVE))
        self.snap("line")

    def test_exercise1_shaft_chamfer_fillet_by_keys(self):
        """Training exercise 1 with the keyboard only: points typed, chamfer and fillet with the cursor keys."""
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path))
        self.s.type("x0,0\n40,0\n@0,-25\n60,-25\n60,-50\n100,-50\n")
        self.s.key("Escape")
        self.snap("profile")
        self.s.type("g40,0\n")  # cursor on the corner
        self.s.type("b")  # Chamfer tool
        self.s.key("Return")  # the corner nearest the cursor
        self.app.find("Chamfer at X40 Z0", contains=True)
        self.snap("chamfer-popup")
        self.s.type("1\n")  # size, Enter applies
        self.s.type("g60,-50\n")
        self.s.type("f")  # radius at the corner nearest the cursor
        self.app.find("Radius at X60 Z-50", contains=True)
        self.s.type("2\n")
        p = self.saved(path)
        self.snap("done")
        arcs = [g for g in p.geometry if isinstance(g, Arc)]
        self.assertEqual(len(p.geometry), 7, p.geometry)
        self.assertEqual(len(arcs), 1)
        self.assertAlmostEqual(arcs[0].radius(), 2.0, places=3)
        self.assertIn(Point(38.0, 0.0), {q for g in p.geometry for q in (g.a, g.b)})  # 1×45° chamfer: X40 → X38 (diameter)

    def test_cursor_keys_move_the_cursor_not_the_mouse(self):
        """Arrows move the cursor by the step even with the pointer over the drawing (it must not jump back)."""
        self.launch(str(self.part(shaft())))
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        self.s.move(x + w * 0.3, y + h * 0.3)
        self.s.key("End")  # X0 Z0
        for _ in range(3):
            self.s.key("Up")  # +10 on the radius each
        self.assertIn("60.000", self.app.readout())  # diameter 60 = radius 30
        self.snap("cursor")


class Mouse(Surface):
    def test_exercise2_rough_and_finish_and_program(self):
        """Exercise 2 with the mouse: + menu, Area Clear and Finish Pass; the Program page shows the posted program."""
        path = self.part(shaft())
        self.launch(str(path))
        for index in (0, 1):  # OP_MENU: Area Clear, Finish Pass
            self.menu("Add Operation (C)", index)
            time.sleep(0.5)
        self.snap("ops")
        p = self.saved(path)
        self.assertEqual([type(o).__name__ for o in p.operations], ["AreaClear", "FinishPass"])
        self.press("Program", "page tab")
        time.sleep(0.8)
        self.snap("program")
        text = "\n".join(a.get_name() or "" for a in self.app.all(role="text"))
        expected = post(p).decode("ascii").replace("\r\n", "\n")
        tv = self.app.all(role="text")
        from gi.repository import Atspi

        shown = Atspi.Text.get_text(tv[0], 0, -1) if tv else text
        self.assertEqual(shown.strip(), expected.strip())

    def test_main_menu_has_the_gnome_items(self):
        self.launch(str(self.part(shaft())))
        self.press("Main Menu", "toggle button")
        keys = [self.app.shortcut(i) for i in self.app.menu_items()]
        # the HIG's last group, in its order: Preferences, Keyboard Shortcuts, Help, About (no key)
        self.assertEqual(keys[-4:-1], ["Control+,", "Control+?", "F1"], keys)
        self.snap("menu")
        self.s.key("Escape")
        self.s.key("ctrl+question")
        self.app.find("Drawing Keys (Part Page)", contains=True)
        self.snap("shortcuts")


class Touch(Surface):
    def calibrate(self):
        """Screen → drawing coordinates from the pointer read-out at two spots (the view is linear). The pointer is
        moved in small steps: GTK reads the first event over a widget as entering it, not as a motion."""
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        pts = []
        for fx, fy in ((0.2, 0.2), (0.8, 0.8)):
            sx, sy = x + w * fx, y + h * fy
            for d in (6, 3, 0):
                self.s.move(sx + d, sy + d)
                time.sleep(0.1)
            name = self.app.readout().split()
            pts.append((sx, sy, float(name[1]), float(name[3])))
        (sx0, sy0, x0, z0), (sx1, sy1, x1, z1) = pts
        kz, kx = (z1 - z0) / (sx1 - sx0), (x1 - x0) / (sy1 - sy0)
        self.mm_per_px = abs(kz)
        self.to_world = lambda sx, sy: (x0 + (sy - sy0) * kx, z0 + (sx - sx0) * kz)
        return lambda xd, z: (sx0 + (z - z0) / kz, sy0 + (xd - x0) / kx)

    def test_pinch_zoom_keeps_the_spot_between_the_fingers(self):
        """Two fingers (libei multi-touch): spreading them 100 → 400 px zooms in about 4× around their midpoint."""
        self.launch(str(self.part(shaft())), "--touch")
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        cx, cy = x + w * 0.55, y + h * 0.5
        self.calibrate()
        before, spot = self.mm_per_px, self.to_world(cx, cy)
        self.s.pinch(cx, cy, 100, 400)
        self.snap("pinched")
        self.calibrate()
        self.assertAlmostEqual(before / self.mm_per_px, 4.0, delta=0.6)
        after = self.to_world(cx, cy)
        self.assertLess(abs(after[0] - spot[0]) + abs(after[1] - spot[1]), 3 * before, (spot, after))

    def test_line_tool_by_taps(self):
        """Touch: tap Line, tap three points on the grid, Done; the line snaps to the 1 mm grid."""
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path), "--touch")
        self.snap("touch-layout")
        to_screen = self.calibrate()
        self.press("Line", "radio button")
        for xd, z in ((0, 0), (40, 0), (40, -25)):
            self.s.tap(*to_screen(xd, z))
        self.press("Done", "button")
        self.snap("tapped")
        p = self.saved(path)
        ends = sorted({(round(q.x), round(q.z)) for g in p.geometry for q in (g.a, g.b)})
        self.assertEqual(len(p.geometry), 2, p.geometry)
        self.assertEqual(ends, [(0, -0), (40, -25), (40, 0)] if (0, -0) == (0, 0) else ends)

    def test_send_needs_a_hold(self):
        """Send to the Lathe does nothing on a tap; it needs the hold (TOUCH.md 7.4)."""
        self.launch(str(self.part(shaft())), "--touch")
        self.press("Machine", "page tab")
        time.sleep(0.5)
        self.s.tap(*self.app.center(acc=self.app.labelled("Send to the Lathe", "button")))  # a short touch
        time.sleep(0.5)
        self.assertFalse(self.app.exists("Send this program?", contains=True))
        self.snap("after-tap")


class SmallWindow(Surface):
    size = HALF

    def test_editor_takes_the_page_and_gives_it_back(self):
        """Half a Surface screen: the part opens on the drawing; an operation's editor takes the page; × closes it."""
        p = shaft()
        from alphacam_gtk.op_factory import default_op

        p.operations.append(default_op("area_clear", p, 0))
        self.launch(str(self.part(p)))
        self.assertTrue(self.app.exists("Drawing"))
        self.snap("opens-on-drawing")
        self.press("Back", contains=True)
        self.s.click(*self.app.center(acc=self.app.labelled("Area clear", "list item")))
        self.app.find("Close Editor")
        self.assertFalse(self.app.exists("Drawing"), "the editor should have the whole page")
        self.snap("editor")
        self.press("Close Editor")
        self.app.find("Drawing")
        self.snap("back-to-drawing")


class Portrait(Surface):
    size = PORTRAIT

    def test_everything_reachable(self):
        self.launch(str(self.part(shaft())))
        for page in ("Program", "Sheet", "Tools", "Machine", "Part"):
            self.press(page, "page tab")
            time.sleep(0.4)
            self.snap(page.lower())
        self.assertTrue(self.app.exists("Main Menu"))


class Ukrainian(Surface):
    lang = "uk"

    def test_start_and_part_in_ukrainian(self):
        self.launch()
        self.app.find("Відкрити навчальний посібник", "button")
        self.snap("start")
        self.s.key("ctrl+n")
        self.app.find("Створити", "button")
        self.s.key("Return")
        self.app.find("Креслення")
        self.snap("part")


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SendOverVirtualPort(Surface):
    """The whole send, over a socat pty pair standing in for the lathe (the app is given only that port)."""

    def setUp(self):
        super().setUp()
        self.pty = self.s.root / "lathe"
        self.socat = subprocess.Popen(["socat", f"pty,raw,echo=0,link={self.pty}-app",
                                       f"pty,raw,echo=0,link={self.pty}-lathe"],
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            if Path(f"{self.pty}-lathe").exists():
                break
            time.sleep(0.1)

    def tearDown(self):
        self.socat.terminate()
        super().tearDown()

    def test_program_arrives_byte_for_byte(self):
        p = shaft()
        from alphacam_gtk.op_factory import default_op

        p.operations.append(default_op("finish", p, 0))
        expected = post(p)
        got = bytearray()

        def lathe():
            fd = os.open(f"{self.pty}-lathe", os.O_RDWR | os.O_NOCTTY)
            try:
                end = time.time() + 60
                os.set_blocking(fd, False)
                while time.time() < end and len(got) < len(expected):
                    os.write(fd, b"\x11")  # DC1: the control asks for data
                    time.sleep(0.2)
                    try:
                        got.extend(os.read(fd, 65536))
                    except BlockingIOError:
                        pass
            finally:
                os.close(fd)

        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", args=(str(self.part(p)),), env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.press("Machine", "page tab")
        time.sleep(0.5)
        self.app.find(port, contains=True)  # the only port offered is the virtual one
        self.snap("machine")
        x, y = self.app.center(acc=self.app.labelled("Send to the Lathe", "button"))
        self.s.hold(x, y, 2.5)  # hold to start (1.5 s)
        self.snap("confirm")
        self.press("Send", "button")
        t = threading.Thread(target=lathe, daemon=True)
        t.start()
        self.wait_for(lambda: self.app.exists("Sent ", contains=True), 60, "the send to finish")
        t.join(5)
        self.snap("sent")
        self.assertEqual(bytes(got[:len(expected)]), expected)
