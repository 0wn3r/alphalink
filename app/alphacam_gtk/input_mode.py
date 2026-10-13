"""InputMode (TOUCH.md 3): watches the source of every press on the window and switches the .touch density class.

Automatic switching needs two presses in a row from the same kind of device, so one stray touch (or one mouse click
on a touch screen) does not reflow the window."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk  # noqa: E402


class InputMode(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, window: Gtk.Widget, preference: str = "auto"):
        super().__init__()
        self.window = window
        self.preference = preference  # auto | pointer | touch
        self.mode = "pointer"
        self._last = None
        click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._pressed)
        window.add_controller(click)
        self._apply()

    def _pressed(self, gesture, n, x, y) -> None:
        dev = gesture.get_current_event_device()
        src = dev.get_source() if dev is not None else None
        kind = "touch" if src == Gdk.InputSource.TOUCHSCREEN else "pointer"
        if self.preference == "auto" and kind == self._last and kind != self.mode:
            self.mode = kind
            self._apply()
        self._last = kind

    def set_preference(self, preference: str) -> None:
        self.preference = preference
        if preference != "auto":
            self.mode = preference
        self._apply()

    def _apply(self) -> None:
        if self.mode == "touch":
            self.window.add_css_class("touch")
        else:
            self.window.remove_css_class("touch")
        self.emit("changed", self.mode)
