"""HoldButton (TOUCH.md 7.4): an action that only fires after the button is held for `hold_seconds`.

A progress bar inside the button fills while it is held; letting go early cancels. Keyboard: Space/Enter must be
held as well (key repeat keeps it going), so a stray tap or keypress never starts a transfer."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402
from .i18n import _  # noqa: E402


class HoldButton(Gtk.Button):
    __gsignals__ = {"held": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, label: str, hold_seconds: float = 1.5, **kw):
        super().__init__(**kw)
        self.hold_seconds = hold_seconds
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.label = Gtk.Label(label=label)
        self.hint = Gtk.Label(label=_("Hold to start"))
        self.hint.add_css_class("caption")
        self.hint.add_css_class("dim-label")
        self.bar = Gtk.ProgressBar(fraction=0.0)
        box.append(self.label)
        box.append(self.hint)
        box.append(self.bar)
        self.set_child(box)
        self.add_css_class("hold-button")
        self._t0 = None
        self._tick = 0
        press = Gtk.GestureClick(button=0)
        press.connect("pressed", lambda *_: self._start())
        press.connect("released", lambda *_: self._stop())
        press.connect("cancel", lambda *_: self._stop())
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(press)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        keys.connect("key-released", lambda *_: self._stop())
        self.add_controller(keys)

    def _key_pressed(self, ctl, keyval, code, state):
        from gi.repository import Gdk

        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self._t0 is None:
                self._start()
            return True
        return False

    def _start(self):
        if not self.get_sensitive() or self._t0 is not None:
            return
        self._t0 = time.monotonic()
        self._tick = GLib.timeout_add(30, self._update)

    def _update(self):
        if self._t0 is None:
            return False
        frac = (time.monotonic() - self._t0) / self.hold_seconds
        self.bar.set_fraction(min(frac, 1.0))
        if frac >= 1.0:
            self._t0 = None
            self._tick = 0
            self.bar.set_fraction(0.0)
            self.emit("held")
            return False
        return True

    def _stop(self):
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0
        self._t0 = None
        self.bar.set_fraction(0.0)
