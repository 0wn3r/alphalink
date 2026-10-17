"""Emulated input through libei (https://gitlab.freedesktop.org/libinput/libei), the protocol GNOME and other
Wayland compositors use for input emulation: Mutter's remote desktop session hands out an EIS socket
(ConnectToEIS), and this client gets real virtual devices from it (a keyboard that sends evdev key codes, an
absolute pointer, a multi-touch touchscreen).

ctypes over the system's libei.so.1 (no headers or Python bindings needed). Only what the tests use."""

from __future__ import annotations

import ctypes as C
import select
import time

from gi.repository import Gio, GLib

_lib = C.CDLL("libei.so.1")
P = C.c_void_p

# enum ei_event_type (libei 1.x, ei.h)
CONNECT, DISCONNECT, SEAT_ADDED, SEAT_REMOVED, DEVICE_ADDED, DEVICE_REMOVED, DEVICE_PAUSED, DEVICE_RESUMED = range(1, 9)
# enum ei_device_capability
CAP_POINTER, CAP_POINTER_ABSOLUTE, CAP_KEYBOARD, CAP_TOUCH, CAP_SCROLL, CAP_BUTTON = (1 << i for i in range(6))
BTN_LEFT = 0x110

for name, res, args in (
        ("ei_new_sender", P, [P]), ("ei_configure_name", None, [P, C.c_char_p]), ("ei_setup_backend_fd", C.c_int, [P, C.c_int]),
        ("ei_get_fd", C.c_int, [P]), ("ei_dispatch", None, [P]), ("ei_get_event", P, [P]), ("ei_event_get_type", C.c_int, [P]),
        ("ei_event_get_seat", P, [P]), ("ei_event_get_device", P, [P]), ("ei_event_unref", P, [P]), ("ei_now", C.c_uint64, [P]),
        ("ei_seat_ref", P, [P]), ("ei_device_ref", P, [P]), ("ei_device_has_capability", C.c_bool, [P, C.c_int]),
        ("ei_device_start_emulating", None, [P, C.c_uint32]), ("ei_device_frame", None, [P, C.c_uint64]),
        ("ei_device_keyboard_key", None, [P, C.c_uint32, C.c_bool]),
        ("ei_device_pointer_motion_absolute", None, [P, C.c_double, C.c_double]),
        ("ei_device_button_button", None, [P, C.c_uint32, C.c_bool]),
        ("ei_device_scroll_discrete", None, [P, C.c_int32, C.c_int32]),
        ("ei_device_touch_new", P, [P]), ("ei_touch_down", None, [P, C.c_double, C.c_double]),
        ("ei_touch_motion", None, [P, C.c_double, C.c_double]), ("ei_touch_up", None, [P]), ("ei_touch_unref", P, [P]),
        ("ei_unref", P, [P])):
    f = getattr(_lib, name)
    f.restype, f.argtypes = res, args
_lib.ei_seat_bind_capabilities.restype = None  # varargs: capabilities, then NULL

# US keyboard: character -> (evdev key code, shift)
KEY = {"Escape": 1, "BackSpace": 14, "Tab": 15, "Return": 28, "Control_L": 29, "Shift_L": 42, "Alt_L": 56, "space": 57,
       "F1": 59, "Home": 102, "Up": 103, "Page_Up": 104, "Left": 105, "Right": 106, "End": 107, "Down": 108,
       "Page_Down": 109, "Delete": 111, "Super_L": 125, "Menu": 127,
       **{f"F{n}": 58 + n for n in range(2, 11)}, "F11": 87, "F12": 88}
for row, start in (("1234567890-=", 2), ("qwertyuiop[]", 16), ("asdfghjkl;'", 30), ("zxcvbnm,./", 44)):
    for i, ch in enumerate(row):
        KEY[ch] = start + i
KEY["`"] = 41
KEY["\\"] = 43
SHIFTED = dict(zip('!@#$%^&*()_+{}:"<>?|~', '1234567890-=[];\',./\\`'))
MODS = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L"}
NAMES = {"plus": "+", "minus": "-", "comma": ",", "question": "?", "period": ".", "equal": "="}


class EI:
    """A libei sender connected to the remote desktop session `session_path` on `bus`."""

    def __init__(self, bus: Gio.DBusConnection, session_path: str):
        ret, fds = bus.call_with_unix_fd_list_sync(
            "org.gnome.Mutter.RemoteDesktop", session_path, "org.gnome.Mutter.RemoteDesktop.Session", "ConnectToEIS",
            GLib.Variant("(a{sv})", ({},)), GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
        fd = fds.get(ret.unpack()[0])
        self.ei = _lib.ei_new_sender(None)
        _lib.ei_configure_name(self.ei, b"alphalink-e2e")
        if _lib.ei_setup_backend_fd(self.ei, fd) != 0:
            raise RuntimeError("libei: could not use the EIS socket")
        self.devices: dict[int, P] = {}
        self.sequence = 0
        self.touch_ids = 0
        self._pump(lambda: all(self.devices.get(c) for c in (CAP_KEYBOARD, CAP_POINTER_ABSOLUTE, CAP_TOUCH)), 10)

    # --- the event loop --------------------------------------------------------------------------------------------

    def _pump(self, done=lambda: False, timeout: float = 0.05) -> None:
        end = time.time() + timeout
        fd = _lib.ei_get_fd(self.ei)
        while True:
            select.select([fd], [], [], max(0.0, min(0.02, end - time.time())))
            _lib.ei_dispatch(self.ei)
            while True:
                ev = _lib.ei_get_event(self.ei)
                if not ev:
                    break
                t = _lib.ei_event_get_type(ev)
                if t == SEAT_ADDED:
                    seat = _lib.ei_event_get_seat(ev)
                    _lib.ei_seat_bind_capabilities(
                        P(seat), C.c_int(CAP_POINTER_ABSOLUTE), C.c_int(CAP_BUTTON), C.c_int(CAP_KEYBOARD),
                        C.c_int(CAP_TOUCH), C.c_int(CAP_SCROLL), P(None))
                elif t == DEVICE_RESUMED:
                    dev = _lib.ei_device_ref(_lib.ei_event_get_device(ev))
                    self.sequence += 1
                    _lib.ei_device_start_emulating(dev, self.sequence)
                    for cap in (CAP_KEYBOARD, CAP_POINTER_ABSOLUTE, CAP_TOUCH, CAP_BUTTON, CAP_SCROLL):
                        if _lib.ei_device_has_capability(dev, cap):
                            self.devices[cap] = dev
                elif t in (DEVICE_REMOVED, DEVICE_PAUSED):  # e.g. the monitor's scale changed: new devices follow
                    dev = _lib.ei_event_get_device(ev)
                    for cap in [c for c, d in self.devices.items() if d and _lib.ei_device_has_capability(dev, c)]:
                        self.devices.pop(cap, None)
                elif t == DISCONNECT:
                    raise RuntimeError("libei: the compositor disconnected")
                _lib.ei_event_unref(ev)
            if done() or time.time() > end:
                return

    def _device(self, cap: int):
        """The device for `cap`, waiting for it after the compositor replaced its devices."""
        if not self.devices.get(cap):
            self._pump(lambda: bool(self.devices.get(cap)), 5)
        return self.devices[cap]

    def _frame(self, dev, wait: float = 0.05) -> None:
        _lib.ei_device_frame(dev, _lib.ei_now(self.ei))
        self._pump(timeout=wait)

    # --- devices ---------------------------------------------------------------------------------------------------

    def key(self, name: str, pressed: bool) -> None:
        dev = self._device(CAP_KEYBOARD)
        _lib.ei_device_keyboard_key(dev, KEY[name], pressed)
        self._frame(dev)

    def combo(self, combo: str) -> None:
        parts = combo.split("+") if combo != "+" else ["plus"]
        mods = [MODS[m.lower()] for m in parts[:-1]]
        name = NAMES.get(parts[-1], parts[-1])
        if name in SHIFTED:  # "ctrl+?" is ctrl+shift+/
            mods.append("Shift_L")
            name = SHIFTED[name]
        name = name.lower() if len(name) == 1 else name
        for m in mods:
            self.key(m, True)
        self.key(name, True)
        self.key(name, False)
        for m in reversed(mods):
            self.key(m, False)

    def char(self, ch: str) -> None:
        if ch == "\n":
            return self.combo("Return")
        shift = ch in SHIFTED or ch.isupper()
        base = SHIFTED.get(ch, ch.lower())
        if shift:
            self.key("Shift_L", True)
        self.key(base if base != " " else "space", True)
        self.key(base if base != " " else "space", False)
        if shift:
            self.key("Shift_L", False)

    def pointer_to(self, x: float, y: float) -> None:
        dev = self._device(CAP_POINTER_ABSOLUTE)
        _lib.ei_device_pointer_motion_absolute(dev, x, y)
        self._frame(dev)

    def button(self, button: int, pressed: bool) -> None:
        dev = self.devices.get(CAP_BUTTON) or self._device(CAP_POINTER_ABSOLUTE)
        _lib.ei_device_button_button(dev, button, pressed)
        self._frame(dev)

    def scroll(self, clicks: int) -> None:
        """Wheel clicks at the pointer: positive scrolls down (120 per click, as libei counts)."""
        dev = self.devices.get(CAP_SCROLL) or self._device(CAP_POINTER_ABSOLUTE)
        _lib.ei_device_scroll_discrete(dev, 0, 120 * clicks)
        self._frame(dev)

    def touches(self, start: list[tuple[float, float]], end: list[tuple[float, float]] | None = None,
                steps: int = 10, hold: float = 0.06) -> None:
        """Fingers down at `start`, moved together to `end` in `steps` frames (a pinch or a drag), then up."""
        dev = self._device(CAP_TOUCH)
        ts = [_lib.ei_device_touch_new(dev) for _ in start]
        for t, (x, y) in zip(ts, start):
            _lib.ei_touch_down(t, x, y)
        self._frame(dev)
        if end:
            for i in range(1, steps + 1):
                for t, (x0, y0), (x1, y1) in zip(ts, start, end):
                    _lib.ei_touch_motion(t, x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
                self._frame(dev, wait=0)  # a frame every 16 ms, as a finger gives (a pumped frame took 50 ms more)
                time.sleep(0.016)
        time.sleep(hold)
        for t in ts:
            _lib.ei_touch_up(t)
        self._frame(dev)
        for t in ts:
            _lib.ei_touch_unref(t)
