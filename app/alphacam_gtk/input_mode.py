"""InputMode (TOUCH.md 3): watches the source of every press on the window and switches the .touch density class.

Automatic switching needs two presses in a row from the same kind of device, so one stray touch (or one mouse click
on a touch screen) does not reflow the window."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk  # noqa: E402


class InputMode(GObject.Object):
    """Also owns the touch lock (TOUCH.md 7.4): while locked, touch events are swallowed except on the widgets in
    `lock_exempt` (the unlock button); mouse, pen and keyboard keep working."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "lock-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, window: Gtk.Widget, preference: str = "auto"):
        super().__init__()
        self.window = window
        self.preference = preference  # auto | pointer | touch
        self.mode = preference if preference != "auto" else "pointer"
        self._last = None
        click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._pressed)
        window.add_controller(click)
        self.locked = False
        self.lock_exempt: list[Gtk.Widget] = []
        guard = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        guard.connect("event", self._guard)
        window.add_controller(guard)
        self._apply()

    def set_locked(self, locked: bool) -> None:
        if locked != self.locked:
            self.locked = locked
            self.emit("lock-changed", locked)

    def _guard(self, ctl, event) -> bool:
        if not self.locked or event.get_event_type() not in (
                Gdk.EventType.TOUCH_BEGIN, Gdk.EventType.TOUCH_UPDATE, Gdk.EventType.TOUCH_END,
                Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.MOTION_NOTIFY):
            return False
        dev = event.get_device()
        if dev is None or dev.get_source() != Gdk.InputSource.TOUCHSCREEN:
            return False
        ok, x, y = event.get_position()
        target = self.window.pick(x, y, Gtk.PickFlags.DEFAULT) if ok else None
        while target is not None:
            if target in self.lock_exempt:
                return False
            target = target.get_parent()
        return True  # swallow the touch

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
