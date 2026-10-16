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
from shutil import which as shutil_which

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


class FakeLathe:
    """The control's end of a socat pty pair, acting as the FANUC-based Alpha 400T does: DC1 (0x11) asks for data,
    DC3 (0x13, 0x93 from some drivers) pauses it; the sender must stop within 10 characters (FANUC alarm 087)."""

    def __init__(self, path: str):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.got = bytearray()

    def close(self) -> None:
        os.close(self.fd)

    def send(self, b: bytes) -> None:
        os.write(self.fd, b)

    def read_for(self, seconds: float, until: int | None = None) -> int:
        """Collect what arrives for `seconds` (or until `until` bytes in all); returns the number of new bytes."""
        n0, end = len(self.got), time.time() + seconds
        while time.time() < end and (until is None or len(self.got) < until):
            try:
                self.got.extend(os.read(self.fd, 65536))
            except BlockingIOError:
                time.sleep(0.01)
        return len(self.got) - n0


class VirtualPort(Surface):
    """A socat pty pair standing in for the lathe's line (the app is given only its end)."""

    def setUp(self):
        super().setUp()
        self.pty = self.s.root / "lathe"
        self.socat = subprocess.Popen(["socat", f"pty,raw,echo=0,link={self.pty}-app",
                                       f"pty,raw,echo=0,link={self.pty}-lathe"],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True)
        for _ in range(50):
            if Path(f"{self.pty}-lathe").exists():
                break
            time.sleep(0.1)
        self.lathe = FakeLathe(f"{self.pty}-lathe")

    def tearDown(self):
        self.lathe.close()
        self.socat.terminate()
        super().tearDown()


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class SendOverVirtualPort(VirtualPort):
    """The whole send, over the virtual port."""

    def start_send(self, p: Project) -> bytes:
        """Open the part with only the virtual port, hold Send to the Lathe and confirm; returns the program."""
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
        return post(p)

    def big_part(self) -> Project:
        p = shaft()
        from alphacam_gtk.op_factory import default_op

        p.operations += [default_op("area_clear", p, 0), default_op("finish", p, 1)]
        return p

    def test_program_arrives_byte_for_byte(self):
        expected = self.start_send(self.big_part())
        self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(expected))
        self.wait_for(lambda: self.app.exists("Sent ", contains=True), 20, "the send to finish")
        self.snap("sent")
        self.assertEqual(bytes(self.lathe.got), expected)

    def test_nothing_is_sent_before_dc1(self):
        """With "Wait for the control (DC1)" on, not a byte goes out until the lathe asks."""
        expected = self.start_send(self.big_part())
        self.assertEqual(self.lathe.read_for(3.0), 0, "bytes sent before DC1")
        self.snap("waiting")
        self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(expected))
        self.assertEqual(bytes(self.lathe.got), expected)

    def dc3_pauses_and_dc1_resumes(self, xoff: int) -> None:
        expected = self.start_send(self.big_part())
        self.assertGreater(len(expected), 1500)
        self.lathe.send(b"\x11")
        self.lathe.read_for(30, until=400)
        self.lathe.send(bytes([xoff]))
        on_the_way = self.lathe.read_for(0.3)
        self.assertLessEqual(on_the_way, 10, "more than 10 characters after DC3 (FANUC alarm 087)")
        late = self.lathe.read_for(2.0)
        self.snap(f"paused-{xoff:02x}")
        self.assertEqual(late, 0, "still sending 0.3 s after DC3")
        self.lathe.send(b"\x11")
        self.lathe.read_for(60, until=len(expected))
        self.assertEqual(bytes(self.lathe.got), expected)

    def test_dc3_0x93_pauses_and_dc1_resumes(self):
        self.dc3_pauses_and_dc1_resumes(0x93)

    def test_dc3_0x13_pauses_and_dc1_resumes(self):
        self.dc3_pauses_and_dc1_resumes(0x13)

def with_op(kind: str = "area_clear") -> Project:
    p = shaft()
    from alphacam_gtk.op_factory import default_op

    p.operations.append(default_op(kind, p, 0))
    return p


class TouchMore(Surface):
    """More touch workflows (libei touchscreen)."""

    def element_point(self, to_screen, g: Line) -> tuple[float, float]:
        return to_screen((g.a.x + g.b.x) / 2, (g.a.z + g.b.z) / 2)

    def test_keypad_enters_points(self):
        """Touch: the Keypad button opens the on-screen keypad; X 0 → Z 0 ✓, then X 40 → Z 0 ✓ draws a line."""
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path), "--touch")
        self.press("Line", "radio button")
        for keys in (("0", "→", "0", "✓"), ("4", "0", "→", "0", "✓")):
            self.s.tap(*self.app.center("Keypad", "button"))
            self.app.find("✓", "button")
            self.snap("keypad")
            for k in keys:
                self.s.tap(*self.app.center(k, "button"))
            time.sleep(0.4)
        self.press("Done", "button")
        p = self.saved(path)
        self.assertEqual([(g.a, g.b) for g in p.geometry], [(Point(0, 0), Point(40, 0))])

    def test_pick_contour_by_taps(self):
        """Touch: Pick Contour on the Drawing, tap three elements, Done; the operation keeps them in that order."""
        p = with_op()
        path = self.part(p)
        self.launch(str(path), "--touch")
        self.s.tap(*self.app.center(acc=self.app.labelled("Area clear", "list item")))
        time.sleep(0.5)
        self.s.tap(*self.app.center("Pick Contour on the Drawing", "button"))
        time.sleep(0.5)
        to_screen = self.calibrate()  # the editor pane and the picking banner moved the drawing
        for g in p.geometry[:3]:
            self.s.tap(*self.element_point(to_screen, g))
        self.snap("picked")
        self.press("Done", "button")
        q = self.saved(path)
        self.assertEqual(q.operations[0].contour, ["g0", "g1", "g2"])

    def test_touch_lock_ignores_taps_until_held(self):
        """Touch Lock: taps do nothing while locked; holding the lock button unlocks."""
        self.launch(str(self.part(shaft())), "--touch")
        lock = self.app.find("Touch Lock", "toggle button", contains=True)
        self.s.tap(*self.app.center(acc=lock))
        self.assertTrue(self.app.checked(lock), "the lock should be on")
        line = self.app.find("Line", "radio button")
        self.s.tap(*self.app.center(acc=line))
        self.assertFalse(self.app.checked(line), "a tap went through the lock")
        self.snap("locked")
        self.s.hold(*self.app.center(acc=lock), 2.5, touch=True)
        self.assertFalse(self.app.checked(lock), "holding should unlock")
        self.s.tap(*self.app.center(acc=line))
        self.assertTrue(self.app.checked(line))

    def test_one_finger_drag_pans(self):
        """Dragging one finger over the drawing moves it with the finger."""
        self.launch(str(self.part(shaft())), "--touch")
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        self.calibrate()
        sx, sy = x + w * 0.5, y + h * 0.5
        before = self.to_world(sx, sy)
        self.s.input.touches([(sx, sy)], [(sx + 200, sy)], steps=15)
        time.sleep(0.5)
        self.calibrate()
        after = self.to_world(sx, sy)
        # the drawing follows the finger to within a few pixels (GTK starts a touch drag after a small threshold)
        self.assertAlmostEqual(after[1] - before[1], -200 * self.mm_per_px, delta=10 * self.mm_per_px)
        self.assertAlmostEqual(after[0], before[0], delta=10 * self.mm_per_px)

    def test_editor_switch_by_touch(self):
        """A tap on an editor switch (Towards the chuck) changes the operation; the part keeps it.
        (The number rows cannot be reached this way: GTK 4 does not put libadwaita's spin rows in the
        accessibility tree, see README.)"""
        p = with_op()
        path = self.part(p)
        self.launch(str(path), "--touch")
        self.s.tap(*self.app.center(acc=self.app.labelled("Area clear", "list item")))
        before = p.operations[0].towards
        sw = [a for a in self.app.all("Towards the chuck", "switch")]
        self.s.tap(*self.app.center(acc=sw[-1]))
        self.snap("switched")
        q = self.saved(path)
        self.assertEqual(q.operations[0].towards, not before)

class KeyboardMore(Surface):
    def test_undo_and_redo(self):
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path))
        self.s.type("x0,0\n40,0\n40,-20\n")
        self.s.key("Escape")
        self.assertEqual(len(self.saved(path).geometry), 2)
        self.s.key("ctrl+z")
        self.assertEqual(len(self.saved(path).geometry), 1)
        self.s.key("shift+ctrl+z")
        self.assertEqual(len(self.saved(path).geometry), 2)

    def test_simulation_steps_through_passes(self):
        """Next Pass walks the area clear pass by pass; the label names the move, operation and program line."""
        self.launch(str(self.part(with_op())))
        self.press("First Move", "button")
        for _ in range(3):
            self.press("Next Pass", "button")
        label = self.app.find("/", "label", contains=True).get_name()
        self.assertRegex(label, r"\d+/\d+ · .* · line \d+")
        self.snap("simulation")
        self.press("Show All", "button")
        self.app.find("moves", "label", contains=True)


class Scale200(Surface):
    """The Surface's real scale: a 2736×1824 monitor at 200 % (1368×912 logical)."""

    def setUp(self):
        from .harness import GnomeSession

        self.s = GnomeSession(*LANDSCAPE, scale=2.0).__enter__()
        self.out = OUT / f"{self.__class__.__name__}-{self._testMethodName}"
        self.n = 0

    def test_hidpi_drawing_and_keys(self):
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path))
        self.assertEqual(self.s.grab().width, 2736)
        self.s.type("x0,0\n40,0\n40,-25\n")
        self.s.key("Escape")
        self.snap("hidpi")
        self.assertEqual(len(self.saved(path).geometry), 2)
        x, y, w, h = self.app.rect(self.app.find("Drawing"))
        self.assertGreater(w, 900)  # logical size, as at 100 %

    def test_hidpi_touch_line(self):
        path = self.part(Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.press("Line", "radio button")
        for xd, z in ((0, 0), (40, 0)):
            self.s.tap(*to_screen(xd, z))
        self.press("Done", "button")
        p = self.saved(path)
        self.assertEqual([(round(g.a.x), round(g.a.z), round(g.b.x), round(g.b.z)) for g in p.geometry], [(0, 0, 40, 0)])


class Rotation(Surface):
    def test_turning_the_tablet_switches_the_layout(self):
        """Landscape → portrait while running: the page tabs move to the bottom, the drawing stays; and back."""
        self.launch(str(self.part(shaft())))
        self.assertTrue(self.app.exists("Operations", contains=True))
        self.s.rotate(1)
        time.sleep(1.0)
        self.app.origin = (0, self.s._find_top())
        self.snap("portrait")
        self.app.find("Drawing")
        tabs = self.app.all("Machine", "page tab")
        self.assertTrue(tabs)
        self.assertGreater(self.app.rect(tabs[0])[1], self.s.height / 2, "the tabs should be at the bottom")
        self.s.rotate(0)
        time.sleep(1.0)
        self.snap("landscape")
        tabs = self.app.all("Machine", "page tab")
        self.assertLess(self.app.rect(tabs[0])[1], 120, "the tabs should be back in the header")


class FileDialogs(Surface):
    """The real Open, Save As and Export dialogs: GNOME's file chooser portal (Files, in GNOME 50) answers the
    Flatpak's requests, as on the desktop."""

    def chooser(self):
        from .a11y import App

        c = App.wait(15, names=("org.gnome.Nautilus", "xdg-desktop-portal-gnome", "Nautilus"))
        time.sleep(1.0)
        return c

    def choose(self, path: Path) -> None:
        c = self.chooser()
        self.snap("chooser")
        self.s.key("ctrl+l")
        self.s.type(str(path) + "\n")
        time.sleep(0.8)
        c.press("Open", "button")

    def save_as(self, name: str) -> None:
        FileDialogs.chooser(self)
        self.snap("save-chooser")
        self.s.key("ctrl+a")
        self.s.type(name + "\n")
        time.sleep(1.5)

    def test_open_from_the_dialog(self):
        path = self.part(with_op(), "opened.alcam")
        self.launch()
        self.s.key("ctrl+o")
        self.choose(path)
        self.wait_for(lambda: "opened.alcam" in (self.app.acc.get_child_at_index(0).get_name() or ""), 10,
                      "the part to open")
        self.snap("opened")

    def test_save_as_and_export(self):
        """Save As writes the part; Export PRG writes the program (and its .SD1) byte for byte."""
        p = with_op()
        path = self.part(p)
        self.launch(str(path))
        self.s.key("shift+ctrl+s")
        self.save_as("copy.alcam")
        copy = self.s.root / "home/copy.alcam"
        self.wait_for(copy.exists, 10, "copy.alcam")
        self.assertEqual(Project.load(copy).geometry, Project.load(path).geometry)
        self.s.key("ctrl+e")
        self.save_as("SHAFT.PRG")
        prg = self.s.root / "home/SHAFT.PRG"
        self.wait_for(prg.exists, 10, "SHAFT.PRG")
        self.assertEqual(prg.read_bytes(), post(p))
        self.assertTrue((self.s.root / "home/SHAFT.SD1").exists())
        self.snap("exported")


ALPHAT = Path(os.environ.get("ALPHALINK_ALPHAT", Path.home() / "alpha-work/vendor/AlphaT"))
SAMPLES = Path(__file__).resolve().parents[3] / "samples/alphat_v12"
GREEN = (85, 255, 85)  # ALPHA-T draws the part profile in EGA light green


def alphat_screen(s, drawings: dict[str, Path], name: str):
    """ALPHA-T (the original DOS program, in DOSBox-X inside the test session, keys through libei) retrieves
    drawing `name`; returns its 640×480 screen. `drawings`: NAME → .SDG (the .SDT next to it is copied too)."""
    import shutil

    from PIL import Image

    c = s.root / "dos"
    at = c / "ALPHA-T"
    if not at.exists():
        shutil.copytree(ALPHAT, at)  # a scratch copy: the vendor folder is never touched
        for f in ("ALPHTZIP.EXE", "ATSETUP.EXE"):
            (at / f).unlink(missing_ok=True)
        for d in ("DRAWINGS", "MANOPS", "MACROS", "DXF"):
            (at / d).mkdir(exist_ok=True)
        (at / "PORT.CFG").write_text("1")
    for n, sdg in drawings.items():
        shutil.copy(sdg, at / f"DRAWINGS/{n}.SDG")
        shutil.copy(sdg.with_suffix(sdg.suffix[:-1] + ("T" if sdg.suffix.isupper() else "t")), at / f"DRAWINGS/{n}.SDT")
    conf = s.root / "dos.conf"
    conf.write_text(f"[sdl]\noutput=surface\n[dosbox]\nmemsize=16\n[cpu]\ncycles=fixed 30000\n[mixer]\nnosound=true\n"
                    f"[autoexec]\nmount c \"{c}\"\nc:\ncd \\\\ALPHA-T\nALPHA-T\n")
    env = dict(s.env, SDL_VIDEODRIVER="wayland", SDL_AUDIODRIVER="dummy")
    p = subprocess.Popen(["dosbox-x", "-conf", str(conf), "-fastlaunch"], env=env, stdin=subprocess.DEVNULL,
                         stdout=open(s.root / "dos.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    try:
        time.sleep(6)
        s.key("Return")  # ALPHA-T: Enter retrieves a drawing
        time.sleep(2)
        s.type(name + "\n")
        time.sleep(5)
        im = s.grab().convert("RGB")
    finally:
        os.killpg(p.pid, 9)  # DOSBox-X does not stop on SIGTERM
        p.wait(5)
    w, h = im.size
    black = Image.eval(im.crop((0, 40, w, h)).convert("L"), lambda v: 255 if v == 0 else 0)
    x0, y0, x1, y1 = black.getbbox()  # the DOS screen is the only pure black area below the top bar
    return im.crop((x0, y0 + 40, x0 + 640, y0 + 40 + 480))


def green_pixels(im) -> set:
    """The profile's pixels, relative to its own top left (the DOSBox-X window may land a pixel off)."""
    pts = {(x, y) for x in range(im.width) for y in range(im.height)
           if sum(abs(a - b) for a, b in zip(im.getpixel((x, y)), GREEN)) < 60}
    if not pts:
        return pts
    x0, y0 = min(x for x, _ in pts), min(y for _, y in pts)
    return {(x - x0, y - y0) for x, y in pts}


@unittest.skipUnless(ENABLED and ALPHAT.exists() and shutil_which("dosbox-x"), "needs ALPHA-T and dosbox-x")
class AlphaTReads(Surface):
    """ALPHA-T itself opens what the app writes."""

    def test_app_written_drawing_looks_like_alpha_t_own(self):
        """The app's .SDG of the AREA1 shaft and ALPHA-T's own AREA1 drawing show the same part profile."""
        from alphacam.io.sdg import load_sdg
        from alphacam.io.sdg_write import save_sdg

        own = SAMPLES / "AREA1_metric.SDG"
        mine = save_sdg(load_sdg(own), self.s.root / "MINE.SDG")[0]
        a = alphat_screen(self.s, {"OWN": own}, "OWN")
        b = alphat_screen(self.s, {"MINE": mine}, "MINE")
        self.out.mkdir(parents=True, exist_ok=True)
        a.save(self.out / "alpha-t-own.png")
        b.save(self.out / "alpha-t-app.png")
        ga, gb = green_pixels(a), green_pixels(b)
        self.assertGreater(len(ga), 100)
        self.assertEqual(ga, gb)

    def test_part_drawn_in_the_app_opens_in_alpha_t(self):
        """Draw a part by keys, Main Menu ▸ Save as ALPHA-T Drawing… (the real file dialog), open it in ALPHA-T: the
        profile appears and ALPHA-T's menu is up (no error)."""
        path = self.part(Project(billet=Billet(80, 60), first_tool=3))
        self.launch(str(path))
        self.s.type("x0,0\n30,0\n@0,-20\n50,-20\n50,-40\n80,-40\n")
        self.s.key("Escape")
        self.s.type("g30,0\n")
        self.s.type("b")
        self.s.key("Return")
        self.s.type("2\n")
        self.press("Main Menu", "toggle button")
        self.app.press(acc=self.app.menu_items()[4])  # Save as ALPHA-T Drawing…
        FileDialogs.save_as(self, "E2EAPP.SDG")
        sdg = self.s.root / "home/E2EAPP.SDG"
        self.wait_for(sdg.exists, 10, "E2EAPP.SDG")
        im = alphat_screen(self.s, {"E2EAPP": sdg}, "E2EAPP")
        self.out.mkdir(parents=True, exist_ok=True)
        im.save(self.out / "alpha-t.png")
        self.assertGreater(len(green_pixels(im)), 100, "ALPHA-T shows no part profile")
        menu = im.crop((536, 56, 632, 72))  # "CAD MENU 1": ALPHA-T is up, not stopped on an error
        self.assertGreater(len({menu.getpixel((x, y)) for x in range(menu.width) for y in range(menu.height)}), 1)


class TouchPlacement(Surface):
    """TOUCH.md 4, 5.1, 5.2 and 6: closing a profile, the value chip, the long-press menu, the editor's keypad."""

    def start_line(self, project=None):
        path = self.part(project or Project(billet=Billet(100, 60), first_tool=3))
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.press("Line", "radio button")
        return path, to_screen

    def test_tapping_the_first_point_closes_the_profile(self):
        path, to_screen = self.start_line()
        for xd, z in ((0, 0), (40, 0), (40, -25), (0, -25), (0, 0)):
            self.s.tap(*to_screen(xd, z))
        self.app.find("Profile closed", contains=True, showing=False)
        self.snap("closed")
        done = self.app.find("Done", "button")
        from gi.repository import Atspi

        self.assertFalse(done.get_state_set().contains(Atspi.StateType.SENSITIVE), "the line should be finished")
        self.assertEqual(len(self.saved(path).geometry), 4)

    def test_value_chip_corrects_the_last_point(self):
        """Tap roughly; the chip on the segment shows its exact end; the keypad corrects it to Ø 40."""
        path, to_screen = self.start_line()
        self.s.tap(*to_screen(0, 0))
        self.s.tap(*to_screen(37, 0))  # a little short
        chip = self.app.find("Ø ", "button", contains=True)
        self.assertNotIn("40", chip.get_name())  # the rough tap, about 37
        self.snap("chip")
        self.s.tap(*self.app.center(acc=chip))
        self.app.find("✓", "button")
        for k in ("4", "0", "✓"):
            self.s.tap(*self.app.center(k, "button"))
        time.sleep(0.5)
        self.assertIn("40", self.app.find("Ø ", "button", contains=True).get_name())
        self.press("Done", "button")
        p = self.saved(path)
        self.assertEqual([(g.a, g.b) for g in p.geometry], [(Point(0, 0), Point(40, 0))])

    def test_long_press_menu_deletes_the_element(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        g = p.geometry[1]
        self.s.hold(*to_screen((g.a.x + g.b.x) / 2, (g.a.z + g.b.z) / 2), 1.2, touch=True)
        items = self.app.menu_items()
        self.snap("menu")
        self.assertEqual(len(items), 4)  # Edit Values…, Delete | Zoom Here, Fit Part
        self.app.press(acc=items[1])
        q = self.saved(path)
        self.assertEqual([e.id for e in q.geometry], ["g0", "g2", "g3", "g4"])

    def test_editor_number_opens_the_keypad(self):
        """A finger on the Feed value opens the keypad (no system keyboard); 0.2 ✓ sets it."""
        p = with_op()
        path = self.part(p)
        self.launch(str(path), "--touch")
        self.s.tap(*self.app.center(acc=self.app.labelled("Area clear", "list item")))
        time.sleep(0.5)
        # libadwaita's spin rows are missing from GTK's accessibility tree (README): the Feed row is the last of the
        # four equal number rows between "5 elements" and the "Towards the chuck" switch
        top = self.app.rect(self.app.labelled("5 elements", "list item"))
        sw = self.app.rect(self.app.all("Towards the chuck", "switch")[0])
        h = (sw[1] - (top[1] + top[3])) / 4
        y = sw[1] - h / 2
        x = sw[0] + sw[2] - 160  # the number, left of − and +
        self.s.tap(x, y)
        self.app.find("✓", "button")
        self.snap("keypad")
        for k in ("0", ".", "2", "✓"):
            self.s.tap(*self.app.center(k, "button"))
        q = self.saved(path)
        self.assertAlmostEqual(q.operations[0].feed, 0.2, places=6)


class TouchMovePoint(Surface):
    """TOUCH.md 5.1/5.2: drag a selected element's end handle; the loupe shows the point; the profile stays joined."""

    def test_drag_a_handle_moves_the_point_and_its_neighbour(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        g1 = p.geometry[1]  # X40 from Z0 to Z-25
        self.s.tap(*to_screen(40, -12))  # select it: handles at its ends
        time.sleep(0.4)
        sx, sy = to_screen(40, -25)
        tx, ty = to_screen(40, -30)
        self.s.input.touches([(sx, sy)], [(tx, ty)], steps=12, hold=0.4)
        time.sleep(0.6)
        self.snap("moved")
        q = self.saved(path)
        geo = {g.id: g for g in q.geometry}
        self.assertEqual(geo["g1"].b, Point(40, -30))
        self.assertEqual(geo["g2"].a, Point(40, -30))  # the step that met it followed
        self.assertEqual(geo["g1"].a, g1.a)

    def test_loupe_shows_while_dragging(self):
        p = shaft()
        self.launch(str(self.part(p)), "--touch")
        to_screen = self.calibrate()
        self.s.tap(*to_screen(40, -12))
        time.sleep(0.4)
        sx, sy = to_screen(40, -25)
        dev = self.s.input
        from .ei import _lib

        d = dev._device(8)
        t = _lib.ei_device_touch_new(d)
        _lib.ei_touch_down(t, sx, sy)
        dev._frame(d)
        for k in range(1, 8):
            _lib.ei_touch_motion(t, sx - 6 * k, sy)
            dev._frame(d)
            time.sleep(0.03)
        time.sleep(0.4)
        img = self.s.grab()
        self.snap("loupe")
        # the loupe's ring is drawn in the rapid colour about 136 px above the finger
        k = img.width / self.s.width
        ring = [img.getpixel((int((sx - 42) * k + dx), int((sy - 80 - 56 * 2) * k))) for dx in range(-60, 60)]
        _lib.ei_touch_up(t)
        dev._frame(d)
        _lib.ei_touch_unref(t)
        self.assertTrue(any(r > 200 and g < 160 and b < 100 for r, g, b in [c[:3] for c in ring]), "no loupe ring")


class TouchMore2(Surface):
    """TOUCH.md 5.2 ambiguity popover, 7.1 drag-handle reorder, 7.2 tap a toolpath, 7.3 search and Go to N."""

    def test_corner_tap_offers_both_lines(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path), "--touch")
        to_screen = self.calibrate()
        self.s.tap(*to_screen(40, -25))  # the corner between g1 (Ø40) and g2 (Z-25)
        a = self.app.find("Line Ø40", "button")
        self.assertTrue(self.app.exists("Line Z-25", "button"))
        self.snap("choose")
        self.s.tap(*self.app.center(acc=a))
        time.sleep(0.4)
        self.app.find("g1 ", "label", contains=True, showing=False)  # the read-out describes the chosen line

    def test_drag_handle_reorders_operations(self):
        p = with_op("area_clear")
        from alphacam_gtk.op_factory import default_op

        p.operations.append(default_op("finish", p, 1))
        path = self.part(p)
        self.launch(str(path))
        handles = self.app.all("Drag to Reorder")
        self.assertEqual(len(handles), 2)
        hx, hy = self.app.center(acc=handles[1])
        tx, ty = self.app.center(acc=self.app.labelled("Area clear", "list item"))
        self.s.move(hx + 3, hy + 3)
        self.s.move(hx, hy)
        self.s.input.button(0x110, True)
        for k in range(1, 11):
            self.s.move(hx + (tx - hx) * k / 10, hy + (ty - hy) * k / 10)
            time.sleep(0.05)
        time.sleep(0.3)
        self.s.input.button(0x110, False)
        time.sleep(0.6)
        self.snap("reordered")
        q = self.saved(path)
        self.assertEqual([type(o).__name__ for o in q.operations], ["FinishPass", "AreaClear"])

    def test_tap_a_toolpath_jumps_the_simulation(self):
        self.launch(str(self.part(with_op("finish"))), "--touch")
        to_screen = self.calibrate()
        self.s.tap(*to_screen(60, -40))  # on the finish pass along Ø60
        time.sleep(0.5)
        label = self.app.find("/", "label", contains=True).get_name()
        self.assertRegex(label, r"\d+/\d+ · ")
        self.snap("jumped")

    def test_search_and_go_to_block(self):
        self.launch(str(self.part(with_op("area_clear"))))
        self.s.key("ctrl+f")
        time.sleep(0.5)
        self.s.type("N20")
        time.sleep(0.6)
        img = self.s.grab().convert("RGB")
        self.snap("found")
        k = img.width / self.s.width
        yellow = sum(1 for x in range(0, img.width, 2) for y in range(int(80 * k), img.height, 2)
                     if (lambda c: c[0] > 240 and 200 < c[1] < 245 and c[2] < 170)(img.getpixel((x, y))))
        self.assertGreater(yellow, 40, "no highlighted match")  # the "N20" label line, in the match colour
        from gi.repository import Atspi

        def search_entries():
            return [e for e in self.app.all(showing=True) if e.get_role_name() == "entry"
                    and "N20" in (Atspi.Text.get_text(e, 0, -1) if e.get_text_iface() else "")]

        self.assertTrue(search_entries(), "the search entry should be found while open")
        self.s.key("Escape")  # closes the search bar, as in GNOME apps
        time.sleep(0.6)
        self.snap("closed")
        self.assertFalse(search_entries(), "the search bar is still open")


class QuickWinsUI(Surface):
    """The workflow document's quick wins, through the UI."""

    def test_grooving_brings_its_own_tool_change_and_duplicates(self):
        p = shaft()
        p.first_tool = 3
        path = self.part(p)
        self.launch(str(path))
        self.menu("Add Operation (C)", 3)  # OP_MENU: Area Clear, Finish Pass, Face Clear, Grooving
        time.sleep(0.6)
        self.app.find("Tool change to T", contains=True, showing=False)
        self.snap("grooving")
        q = self.saved(path)
        self.assertEqual([type(o).__name__ for o in q.operations], ["ToolChange", "Groove"])
        self.assertEqual(q.operations[0].tool, q.operations[1].tool)
        self.assertEqual(q.operations[1].tool, 16)  # 2mm Groove/Parting: external, like the operation
        self.s.scroll(self.s.width - 200, 400, 15)  # the button is at the end of the editor
        dup = self.app.labelled("Duplicate Operation", "button")
        self.s.click(*self.app.center(acc=dup))
        time.sleep(0.5)
        q = self.saved(path)
        self.assertEqual([type(o).__name__ for o in q.operations], ["ToolChange", "Groove", "Groove"])

    def test_simulation_speed_and_start_of_operation(self):
        p = with_op("area_clear")
        from alphacam_gtk.op_factory import default_op

        p.operations.append(default_op("finish", p, 1))
        self.launch(str(self.part(p)))
        speed = self.app.find("1×", "button")
        self.app.press(acc=speed)
        self.app.find("2×", "button")
        self.s.click(*self.app.center(acc=self.app.labelled("Finish pass", "list item")))
        time.sleep(0.4)
        self.press("Start of the Selected Operation", "button")
        label = self.app.find("/", "label", contains=True).get_name()
        self.assertIn("op 2", label)  # the simulation stands at the finish pass
        self.snap("at-finish")


class SafetyCheck(Surface):
    def test_banner_marks_and_still_exports(self):
        """A finish pass with a negative allowance cuts into the part: the banner says where, Show goes there, the
        drawing marks it, the operation carries the warning, and Export PRG still works."""
        from alphacam.model import FinishPass

        p = shaft()
        p.operations.append(FinishPass(3, Point(120, 5), [g.id for g in p.geometry], offset_x=-1.0))
        self.launch(str(self.part(p)))
        banner = self.app.find("Safety check:", contains=True, showing=False)
        self.assertIn("cuts into the part", banner.get_name())
        self.snap("banner")
        self.press("Show", "button")
        time.sleep(0.5)
        self.snap("shown")
        from gi.repository import Atspi

        export = self.app.find("Export PRG", "button")
        self.assertTrue(export.get_state_set().contains(Atspi.StateType.SENSITIVE), "export must stay possible")
        self.assertTrue(self.app.exists("cuts into the part", contains=True, showing=False))  # the row's icon


class StandardFeatures(Surface):
    """Workflow improvements step 3 (a standard thread, every outside corner at once) and the right-click menu."""

    def more(self, kind: str) -> None:
        from alphacam_gtk.drawing_tools import CONSTRUCT_MENU, FEATURE_MENU

        self.menu("More", len(CONSTRUCT_MENU) + [k for _l, k in FEATURE_MENU].index(kind))

    def test_right_click_opens_the_element_menu(self):
        from .harness import BTN_RIGHT

        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        to_screen = self.calibrate()
        self.s.click(*to_screen(50, -25), button=BTN_RIGHT)  # the step face, g2
        items = self.app.menu_items()
        self.snap("menu")
        self.assertEqual(len(items), 4)  # Edit Values…, Delete | Zoom Here, Fit Part
        self.app.press(acc=items[1])
        self.assertEqual([e.id for e in self.saved(path).geometry], ["g0", "g1", "g3", "g4"])

    def test_standard_thread_on_the_selected_line(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        to_screen = self.calibrate()
        self.s.click(*to_screen(40, -12))  # selects g1, the Ø40 diameter
        time.sleep(0.4)
        self.more("thread")
        self.app.find("Standard Thread", contains=True)
        self.snap("dialog")
        self.press("Add", "button")
        time.sleep(0.6)
        q = self.saved(path)
        thread = q.operations[-1]
        self.assertEqual(type(thread).__name__, "Thread")
        self.assertEqual(thread.pitch, 3.0)  # M40, the line's size: fine pitches only, 3 first
        minor = next(g for g in q.geometry if g.id == thread.inner_line)
        self.assertAlmostEqual(minor.a.x, 40 - 1.22687 * 3, places=2)
        self.assertEqual(len(q.geometry), len(p.geometry) + 3 + 1)  # the relief groove and the minor line

    def test_chamfer_every_outside_corner(self):
        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        self.more("corners-chamfer")
        self.app.find("Chamfer Outside Corners")
        self.press("Apply", "button")
        self.app.find("2 corners done", contains=True, showing=False)
        self.assertEqual(len(self.saved(path).geometry), len(p.geometry) + 2)

    def test_standard_thread_asks_for_the_line_while_drawing(self):
        """With the Line tool active and nothing selected, Standard Thread asks for the line; the tap picks it and
        draws nothing."""
        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        to_screen = self.calibrate()
        self.press("Line", "radio button")
        self.more("thread")
        self.app.find("tap the diameter line", contains=True, showing=False)
        time.sleep(0.4)
        to_screen = self.calibrate()  # the banner moved the drawing down
        self.s.click(*to_screen(40, -12))
        self.app.find("Standard Thread", contains=True)
        self.press("Add", "button")
        time.sleep(0.6)
        q = self.saved(path)
        self.assertEqual(type(q.operations[-1]).__name__, "Thread")
        self.assertEqual(len(q.geometry), len(p.geometry) + 3 + 1)  # the relief groove and the minor line only

    def test_inch_part_gets_a_unified_thread(self):
        p = Project(billet=Billet(2, 3), first_tool=3, units="inch")
        pts = [(0, 0), (0.5, 0), (0.5, -1.0), (1.0, -1.0)]
        p.geometry += [Line(Point(*a), Point(*b)) for a, b in zip(pts, pts[1:])]
        for i, g in enumerate(p.geometry):
            g.id = f"g{i}"
        path = self.part(p)
        self.launch(str(path))
        to_screen = self.calibrate()
        self.s.click(*to_screen(0.5, -0.5))  # selects g1, the Ø1/2 diameter
        time.sleep(0.4)
        self.more("thread")
        self.app.find("Standard Thread", contains=True)
        self.snap("dialog")
        self.press("Add", "button")
        self.app.find("1/2-13 UNC", contains=True, showing=False)  # the toast
        q = self.saved(path)
        self.assertAlmostEqual(q.operations[-1].pitch, 1 / 13, places=6)


@unittest.skipUnless(ENABLED, "end-to-end UI tests: set ALPHALINK_E2E=1")
class BackupOverVirtualPort(VirtualPort):
    """Manual Ops backup (lathe -> app) over the virtual port; the fake lathe plays the control's punch output."""

    def start_backup(self):
        port = os.path.realpath(f"{self.pty}-app")
        self.app = self.s.launch_app("en", args=(str(self.part(shaft())),), env={"ALPHALINK_TEST_PORTS": port})
        time.sleep(1.0)
        self.press("Machine", "page tab")
        time.sleep(0.5)
        x, y = self.app.center(acc=self.app.labelled("Back Up Manual Ops from the Lathe…", "button"))
        for d in (6, 3, 0):  # GTK takes the first event over a widget as entering it
            self.s.move(x + d, y)
            time.sleep(0.1)
        self.s.click(x, y)
        self.app.find("Waiting for data", contains=True)

    def saved_backup(self, name: str) -> bytes:
        FileDialogs.chooser(self)
        self.s.key("ctrl+a")
        self.s.type(name + "\n")
        path = self.s.root / "home" / name
        self.wait_for(path.exists, 10, name)
        return path.read_bytes()

    def test_backup_ends_at_dc4(self):
        data = b"\x12%\r\n:0001 STOP\r\nX40.Z-25.\r\n\x14"
        self.start_backup()
        self.lathe.send(data)
        self.app.find("Received", contains=True, showing=False)
        self.assertEqual(self.saved_backup("FULL.MOP"), data)

    def test_pauses_then_stop_and_keep(self):
        """The control pauses between operations: the app waits through it and says so; with no end mark, Stop and
        Keep saves what arrived, marked PARTIAL."""
        data = b"%\r\n:0001 STOP\r\nX40.Z-25.\r\n"
        self.start_backup()
        self.lathe.send(data[:10])
        self.app.find("paused", contains=True, showing=False, timeout=10)
        self.snap("paused")
        self.lathe.send(data[10:])
        time.sleep(1)
        self.press("Stop and Keep", "button")
        self.app.find("without the end mark", contains=True, showing=False, timeout=10)
        self.snap("partial")
        self.assertEqual(self.saved_backup("PART.MOP"), data)
        raw = list((self.s.root / "home/.var/app").glob("*/.local/state/alphalink-cam/logs/*-manops.raw"))
        self.assertEqual([r.read_bytes() for r in raw], [data])


class WorkflowSteps456(Surface):
    """Workflow improvements 4-6 through the UI: a job template, the printable sheet, the second setup."""

    def test_template_from_the_add_menu(self):
        from alphacam_gtk.op_factory import OP_MENU

        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        self.menu("Add Operation (C)", len(OP_MENU))  # the first template: Shaft, rough T1 and finish T3
        self.app.find("Added 4 operations", contains=True, showing=False)
        self.snap("template")
        q = self.saved(path)
        self.assertEqual([type(o).__name__ for o in q.operations], ["ToolChange", "AreaClear", "ToolChange", "FinishPass"])

    def test_sheet_pdf_with_cycle_time(self):
        from alphacam.model import AreaClear

        p = shaft()
        p.operations.append(AreaClear(3, Point(120, 5), [g.id for g in p.geometry]))
        self.launch(str(self.part(p)))
        self.press("Sheet", "page tab")
        self.app.find("About ", contains=True)  # the estimate in the bar
        self.press("Save as PDF…", "button")
        FileDialogs.save_as(self, "SHEET.pdf")
        pdf = self.s.root / "home/SHEET.pdf"
        self.wait_for(pdf.exists, 10, "SHEET.pdf")
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))

    def test_turn_part_around(self):
        """Setup 2 in the same file: the switch above the operations, each setup with its own program."""
        p = shaft()
        path = self.part(p)
        self.launch(str(path))
        self.press("Main Menu", "toggle button")
        self.app.press(acc=self.app.menu_items()[10])  # New, Open, Save, Save As, Save SDG | Undo, Redo | Fit, Grid,
        self.app.find("Turn Part Around", contains=True)  # Part Settings, Turn Part Around…
        self.snap("dialog")
        self.press("Turn Around", "button")
        self.app.find("Setup 2, program", contains=True, showing=False)
        self.snap("setup-2")
        self.menu("Add Operation (C)", 0)  # an area clear in setup 2
        time.sleep(0.5)
        q = self.saved(path)  # Ctrl+S: the part's own file, both setups
        self.assertEqual(len(q.setups), 1)
        two = q.setups[0]
        self.assertEqual((two.program_number, len(two.operations), len(q.operations)), (p.program_number + 1, 1, 0))
        self.assertIn(Point(100, 0), [g.b for g in two.geometry] + [g.a for g in two.geometry])
        self.s.click(*self.app.center(acc=self.app.labelled("Setup 1")))
        time.sleep(0.5)
        self.app.find("Setup 1", contains=True)
        self.assertFalse(self.app.exists("Area clear", contains=True))  # setup 1 has no operations
        self.snap("setup-1")
