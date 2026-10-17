"""The app's widgets through its own test bus (the Rust app with ALPHALINK_TEST=1, see rust/alphacam-gtk/src/test_bus.rs):
where every mapped widget is on the screen, in logical pixels (the window is fullscreen), with its text.

`BusApp` offers the same calls as `a11y.App`, so the tests run against either, but it never activates anything
itself: `press` is a real click or tap through libei at the widget's centre. The bus is only read."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from gi.repository import Gio, GLib

from .a11y import NotFound

BUS_NAME = "io.github._0wn3r.AlphaLinkCAM.Test"
OBJECT_PATH = "/io/github/_0wn3r/AlphaLinkCAM/Test"

# widget types that play a role whatever their CSS name
ROLE_TYPES = {"button": {"AdwButtonRow"}, "push button": {"AdwButtonRow"}}

# accessible names the tests use that the Rust app gives no text: name -> buildable id
IDS = {"Drawing": "canvas", "Креслення": "canvas"}

# AT-SPI role names the tests use -> GTK CSS node names
ROLES = {
    "button": {"button", "menubutton", "togglebutton"},
    "toggle button": {"button", "togglebutton", "menubutton", "toggle"},
    "push button": {"button"},
    "list item": {"row"},
    "switch": {"switch"},
    "label": {"label"},
    "menu item": {"modelbutton"},
    "check box": {"checkbutton"},
    "radio button": {"checkbutton", "toggle"},
    "entry": {"entry", "text"},
    "text": {"text", "textview", "entry"},
    "spin button": {"spinbutton"},
    "page tab": {"button", "toggle"},
}


def _mnemonic(text: str) -> str:
    """A label as shown: "_Open…" is "Open…" ("__" is a literal underscore)."""
    return re.sub(r"_(.)", r"\1", text) if "_" in text else text


def _inside(a, b) -> bool:
    """`a`'s centre is within `b`."""
    cx, cy = a.x + a.w / 2, a.y + a.h / 2
    return b.x <= cx <= b.x + b.w and b.y <= cy <= b.y + b.h


@dataclass
class Widget:
    """One row of Widgets(), shaped like the bits of an Atspi.Accessible the tests use."""

    id: str
    name: str
    css: str
    text: str
    x: float
    y: float
    w: float
    h: float
    sensitive: bool = True
    active: bool = False
    selected: bool = False
    focus: bool = False
    value: str = ""
    accel: str = ""  # a menu item's shortcut, as AT-SPI writes it ("Control+,")

    def get_name(self) -> str:
        return self.text

    def get_role_name(self) -> str:
        return self.css


class BusApp:
    def __init__(self, session):
        self.s = session
        # this session's own bus (Gio's shared one stays on the first session a process saw)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            session.env["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.origin = (0, 0)  # fullscreen: widget bounds are screen coordinates
        self.touch = False  # the app was started with --touch: press by finger

    @classmethod
    def wait(cls, session, timeout: float = 30) -> "BusApp":
        app = cls(session)
        end = time.time() + timeout
        while time.time() < end:
            try:
                if app.widgets():
                    return app
            except GLib.Error:
                pass
            time.sleep(0.5)
        raise TimeoutError("the app's test bus did not answer (ALPHALINK_TEST=1?)")

    # --- reading the bus ------------------------------------------------------------------------------------------

    def _call(self, method: str, args=None, rtype: str | None = None):
        r = self.bus.call_sync(BUS_NAME, OBJECT_PATH, BUS_NAME, method, args,
                               GLib.VariantType(rtype) if rtype else None, Gio.DBusCallFlags.NONE, 5000, None)
        return r.unpack() if r is not None else None

    def widgets(self) -> list[Widget]:
        try:  # with states, when the app offers them
            rows = self._call("Widgets2", None, "(a(ssssddddbbbbs))")[0]
        except GLib.Error:
            rows = self._call("Widgets", None, "(a(ssssdddd))")[0]
        ws = [Widget(r[0], r[1], r[2], _mnemonic(r[3]), *r[4:]) for r in rows]
        for item in (w for w in ws if w.css == "modelbutton" and not w.text):  # a menu item: its label and shortcut
            inner = [w for w in ws if w is not item and w.w > 0 and _inside(w, item)]
            item.text = next((w.text for w in inner if w.css == "label" and w.text), "")
            item.accel = next((w.text.replace("Ctrl", "Control") for w in inner if w.css == "accelerator"), "")
        return ws

    def screenshot(self, path: str) -> bool:
        try:
            return bool(self._call("Screenshot", GLib.Variant("(s)", (path,)), "(b)")[0])
        except GLib.Error:
            return False

    # --- search (the a11y.App calls) ---------------------------------------------------------------------------------

    @staticmethod
    def _is(w: Widget, role: str) -> bool:
        return w.css in ROLES.get(role, {role}) or w.name in ROLE_TYPES.get(role, ())

    @classmethod
    def _matches(cls, w: Widget, name: str | None, role: str | None, contains: bool) -> bool:
        if role is not None and not cls._is(w, role):
            return False
        if name is None:
            return True
        if IDS.get(name) == w.id:
            return True
        keys = (w.text, w.id, w.name)
        return any(name in k for k in keys if k) if contains else name in keys

    def all(self, name: str | None = None, role: str | None = None, contains: bool = False, showing: bool = True):
        ws = [w for w in self.widgets() if w.w > 0 and w.h > 0]
        hits = [w for w in ws if self._matches(w, name, role, contains)]
        if not hits and name is not None and role is not None:
            named = [w for w in ws if self._matches(w, name, None, contains)]
            # a row named so with the widget inside it (a switch row's switch)
            for row in (w for w in named if w.css == "row"):
                hits += [w for w in ws if self._is(w, role) and _inside(w, row)]
            # a button whose text is a label inside it (view switcher tabs): the smallest one around the label
            for lab in ([] if hits else [w for w in named if w.css == "label"]):
                around = [w for w in ws if self._is(w, role) and _inside(lab, w)]
                if around:
                    hits.append(min(around, key=lambda w: w.w * w.h))
        return list({id(w): w for w in hits}.values())  # once each, in order

    def find(self, name: str | None = None, role: str | None = None, contains: bool = False, timeout: float = 5,
             showing: bool = True) -> Widget:
        end = time.time() + timeout
        while True:
            hits = self.all(name, role, contains)
            if hits:
                return hits[0]
            if time.time() > end:
                raise NotFound(f"no {role or 'widget'} named {name!r}")
            time.sleep(0.25)

    def exists(self, name: str, role: str | None = None, contains: bool = False, showing: bool = True) -> bool:
        return bool(self.all(name, role, contains))

    def labelled(self, text: str, role: str | None = None, timeout: float = 5) -> Widget:
        """The `role` widget that contains a label `text` (a row by its title, a button by its label)."""
        end = time.time() + timeout
        while True:
            direct = self.all(text, role)
            if direct:
                return direct[0]
            ws = self.widgets()
            labels = [w for w in ws if w.text == text]
            for lab in labels:
                cx, cy = lab.x + lab.w / 2, lab.y + lab.h / 2
                around = [w for w in ws if (role is None or self._is(w, role))
                          and w.x <= cx <= w.x + w.w and w.y <= cy <= w.y + w.h]
                if around:
                    return min(around, key=lambda w: w.w * w.h)
            if time.time() > end:
                raise NotFound(f"no {role or 'widget'} labelled {text!r}")
            time.sleep(0.25)

    def menu_items(self, timeout: float = 5) -> list[Widget]:
        """The open menu's items, top to bottom."""
        end = time.time() + timeout
        while True:
            items = sorted((w for w in self.widgets() if w.css == "modelbutton" and w.w > 0), key=lambda w: (w.y, w.x))
            if items or time.time() > end:
                return items
            time.sleep(0.25)

    @staticmethod
    def shortcut(w: Widget) -> str:
        return w.accel

    def readout(self) -> str:
        """The pointer read-out under the drawing ("X ... Z ...")."""
        for w in self.widgets():
            if w.css == "label" and w.text.startswith("X ") and " Z " in w.text:
                return w.text
        return ""

    def _now(self, w: Widget) -> Widget:
        """The widget as it is now (a `Widget` is a snapshot; an Atspi.Accessible is live): the same type, id and
        text nearest to where it was."""
        same = [n for n in self.widgets() if (n.name, n.id, n.css) == (w.name, w.id, w.css)]
        named = [n for n in same if n.text == w.text] or same
        return min(named, key=lambda n: abs(n.x - w.x) + abs(n.y - w.y), default=w)

    def text(self, w: Widget) -> str:
        n = self._now(w)
        return n.value or n.text

    def force_source(self, source: str) -> None:
        """Test-only: every press counts as `source` ("pen", "touch"; "" is off). libei has no pen device."""
        self._call("ForceSource", GLib.Variant("(s)", (source,)), None)

    def state(self) -> dict:
        """The app's own state as the bus reports it (page, view, tool, simulation, operations, program...)."""
        import json

        return json.loads(self._call("State", None, "(s)")[0])

    def title(self) -> str:
        return self._call("Title", None, "(s)")[0]

    def sensitive(self, w: Widget) -> bool:
        return self._now(w).sensitive

    def selected(self, w: Widget) -> bool:
        return self._now(w).selected

    def checked(self, w: Widget) -> bool:
        return self._now(w).active

    def focused(self, w: Widget) -> bool:
        return self._now(w).focus

    def label_of(self, w: Widget) -> str:
        return w.text

    # --- geometry and input (libei only) -----------------------------------------------------------------------------

    def rect(self, w: Widget) -> tuple[int, int, int, int]:
        return round(w.x), round(w.y), round(w.w), round(w.h)

    def center(self, name: str | None = None, role: str | None = None, acc=None, contains: bool = False):
        w = acc or self.find(name, role, contains)
        return w.x + w.w / 2, w.y + w.h / 2

    def press(self, name: str | None = None, role: str | None = None, contains: bool = False, acc=None) -> None:
        """A real click (or, in touch mode, a tap) at the widget's centre."""
        x, y = self.center(name, role, acc, contains)
        if self.touch:
            self.s.tap(x, y)
        else:
            self.s.click(x, y)
        time.sleep(0.3)

    def focus(self, name: str | None = None, role: str | None = None, contains: bool = False, acc=None) -> None:
        self.press(name, role, contains, acc)
