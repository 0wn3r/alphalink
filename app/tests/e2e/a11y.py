"""Finding the app's widgets through the accessibility tree (AT-SPI), as a screen reader does."""

from __future__ import annotations

import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

APP_NAMES = ("alphalink-cam", "AlphaLink CAM", "io.github._0wn3r.AlphaLinkCAM", "python3")


class NotFound(LookupError):
    pass


class App:
    def __init__(self, acc):
        self.acc = acc
        self.origin = (0, 0)  # the window's top left on the screen (Wayland only gives window coordinates)

    @classmethod
    def wait(cls, timeout: float = 30) -> "App":
        Atspi.init()
        end = time.time() + timeout
        while time.time() < end:
            desktop = Atspi.get_desktop(0)
            for i in range(desktop.get_child_count()):
                a = desktop.get_child_at_index(i)
                if a is not None and (a.get_name() or "") in APP_NAMES and a.get_child_count() > 0:
                    return cls(a)
            time.sleep(0.5)
        raise TimeoutError("the app did not show up in the accessibility tree")

    # --- search -------------------------------------------------------------------------------------------------

    def _walk(self, acc, depth=0):
        if acc is None or depth > 60:
            return
        yield acc
        try:
            n = acc.get_child_count()
        except Exception:  # noqa: BLE001 - widgets come and go while walking
            return
        for i in range(n):
            try:
                c = acc.get_child_at_index(i)
            except Exception:  # noqa: BLE001
                continue
            yield from self._walk(c, depth + 1)

    @staticmethod
    def _showing(acc) -> bool:
        try:
            s = acc.get_state_set()
            return s.contains(Atspi.StateType.SHOWING) and s.contains(Atspi.StateType.VISIBLE)
        except Exception:  # noqa: BLE001
            return False

    def all(self, name: str | None = None, role: str | None = None, contains: bool = False, showing: bool = True):
        out = []
        for a in self._walk(self.acc):
            try:
                n, r = a.get_name() or "", a.get_role_name() or ""
            except Exception:  # noqa: BLE001
                continue
            if name is not None and not ((name in n) if contains else (n == name)):
                continue
            if role is not None and r != role:
                continue
            if showing and not self._showing(a):
                continue
            out.append(a)
        return out

    def find(self, name: str | None = None, role: str | None = None, contains: bool = False, timeout: float = 5,
             showing: bool = True):
        end = time.time() + timeout
        while True:
            hits = self.all(name, role, contains, showing)
            if hits:
                return hits[0]
            if time.time() > end:
                raise NotFound(f"no showing {role or 'widget'} named {name!r}")
            time.sleep(0.25)

    def exists(self, name: str, role: str | None = None, contains: bool = False, showing: bool = True) -> bool:
        return bool(self.all(name, role, contains, showing))

    def readout(self) -> str:
        """The pointer / cursor read-out under the drawing ("X  12.000   Z  -3.000"); GTK does not mark labels as
        showing, so this looks at all of them."""
        return self.find("X ", "label", contains=True, showing=False).get_name()

    def label_of(self, acc) -> str:
        """The accessible name, or the text of the first named descendant (GTK's popover menu items have no name
        of their own, only a label inside)."""
        n = acc.get_name() or ""
        if n:
            return n
        for d in self._walk(acc):
            if d is not acc and (d.get_name() or ""):
                return d.get_name()
        return ""

    def labelled(self, text: str, role: str | None = None, timeout: float = 5):
        """A widget whose own name, or first named descendant, is `text` (buttons and list rows built from a box of
        labels have no name of their own)."""
        end = time.time() + timeout
        while True:
            for a in self.all(role=role):
                try:
                    if self.label_of(a) == text:
                        return a
                except Exception:  # noqa: BLE001
                    continue
            if time.time() > end:
                raise NotFound(f"no {role or 'widget'} labelled {text!r}")
            time.sleep(0.25)

    def menu_items(self, timeout: float = 5) -> list:
        """The open menu's items in order. GTK 4's popover menu items expose no name (their label is not in the
        accessibility tree), so tests pick them by position or by shortcut."""
        end = time.time() + timeout
        while True:
            items = [a for a in self.all(showing=False) if "menu item" in (a.get_role_name() or "")]
            if items or time.time() > end:
                return items
            time.sleep(0.25)

    @staticmethod
    def shortcut(acc) -> str:
        return (acc.get_attributes() or {}).get("keyshortcuts", "")

    # --- actions (GTK's own activation, as a screen reader's "click") ------------------------------------------------
    # Pointer coordinates come from AT-SPI's window extents, which GTK 4 reports reliably for the drawing and the
    # bottom bars but not for header bar widgets (offset by the header's padding), so widgets are pressed through
    # their accessible action; real pointer and touch events go to the drawing.

    def press(self, name: str | None = None, role: str | None = None, contains: bool = False, acc=None) -> None:
        acc = acc or self.find(name, role, contains)
        act = acc.get_action_iface()
        if act is None or act.get_n_actions() == 0:
            raise NotFound(f"{name!r} has no action")
        act.do_action(0)
        time.sleep(0.4)

    def focus(self, name: str | None = None, role: str | None = None, contains: bool = False, acc=None) -> None:
        acc = acc or self.find(name, role, contains)
        acc.get_component_iface().grab_focus()
        time.sleep(0.2)

    # --- geometry ------------------------------------------------------------------------------------------------

    def rect(self, acc) -> tuple[int, int, int, int]:
        e = acc.get_extents(Atspi.CoordType.WINDOW)
        return e.x + self.origin[0], e.y + self.origin[1], e.width, e.height

    def center(self, name: str | None = None, role: str | None = None, acc=None, contains: bool = False):
        acc = acc or self.find(name, role, contains)
        x, y, w, h = self.rect(acc)
        return x + w / 2, y + h / 2

    # --- state ---------------------------------------------------------------------------------------------------

    @staticmethod
    def text(acc) -> str:
        t = acc.get_text_iface() if hasattr(acc, "get_text_iface") else None
        if t is not None:
            return Atspi.Text.get_text(acc, 0, -1)
        return acc.get_name() or ""

    @staticmethod
    def checked(acc) -> bool:
        s = acc.get_state_set()
        return s.contains(Atspi.StateType.CHECKED) or s.contains(Atspi.StateType.PRESSED)

    @staticmethod
    def focused(acc) -> bool:
        return acc.get_state_set().contains(Atspi.StateType.FOCUSED)

    def dump(self, acc=None, depth=0, out=None) -> str:
        """The showing tree, for debugging a test."""
        out = [] if out is None else out
        acc = acc or self.acc
        try:
            if depth == 0 or self._showing(acc):
                out.append("  " * depth + f"{acc.get_role_name()}: {acc.get_name()!r}")
                for i in range(acc.get_child_count()):
                    self.dump(acc.get_child_at_index(i), depth + 1, out)
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(out)
