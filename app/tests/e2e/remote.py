"""Real input and screen grabs through Mutter's remote desktop and screen cast D-Bus APIs (what GNOME's own remote
desktop uses), on the private session's bus."""

from __future__ import annotations

import os

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, Gio, GLib, Gst  # noqa: E402

RD, SC = "org.gnome.Mutter.RemoteDesktop", "org.gnome.Mutter.ScreenCast"
MODIFIERS = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L"}
# characters typed with Shift on a US keyboard
SHIFTED = set('~!@#$%^&*()_+{}|:"<>?') | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class Remote:
    def __init__(self):
        # this session's bus by address (Gio's shared session bus would be whatever this process saw first)
        self.bus = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.rd = self._call(RD, "/org/gnome/Mutter/RemoteDesktop", RD, "CreateSession", None, "(o)")[0]
        sid = self._call(RD, self.rd, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (RD + ".Session", "SessionId")))[0]
        self.sc = self._call(SC, "/org/gnome/Mutter/ScreenCast", SC, "CreateSession",
                             GLib.Variant("(a{sv})", ({"remote-desktop-session-id": GLib.Variant("s", sid)},)))[0]
        self.stream = self._call(SC, self.sc, SC + ".Session", "RecordMonitor",
                                 GLib.Variant("(sa{sv})", ("", {"cursor-mode": GLib.Variant("u", int(os.environ.get("ALPHALINK_E2E_CURSOR", "0")))})))[0]
        node = []
        loop = GLib.MainLoop()
        self.bus.signal_subscribe(SC, SC + ".Stream", "PipeWireStreamAdded", self.stream, None, 0,
                                  lambda *a: (node.append(a[5].unpack()[0]), loop.quit()))
        self._call(RD, self.rd, RD + ".Session", "Start")
        GLib.timeout_add(10000, loop.quit)
        loop.run()
        if not node:
            raise TimeoutError("no screen cast stream")
        self.node = node[0]
        Gst.init(None)

    def _call(self, name, path, iface, method, args=None, rtype=None):
        r = self.bus.call_sync(name, path, iface, method, args, GLib.VariantType(rtype) if rtype else None,
                               Gio.DBusCallFlags.NONE, -1, None)
        return r.unpack() if r is not None else None

    def _rd(self, method: str, sig: str, *args) -> None:
        self._call(RD, self.rd, RD + ".Session", method, GLib.Variant(sig, args))

    # --- pointer, touch, keys ---------------------------------------------------------------------------------------

    def pointer_to(self, x: float, y: float) -> None:
        self._rd("NotifyPointerMotionAbsolute", "(sdd)", self.stream, float(x), float(y))

    def button(self, button: int, pressed: bool) -> None:
        self._rd("NotifyPointerButton", "(ib)", button, pressed)

    def touch_down(self, slot: int, x: float, y: float) -> None:
        self._rd("NotifyTouchDown", "(sudd)", self.stream, slot, float(x), float(y))

    def touch_move(self, slot: int, x: float, y: float) -> None:
        self._rd("NotifyTouchMotion", "(sudd)", self.stream, slot, float(x), float(y))

    def touch_up(self, slot: int) -> None:
        self._rd("NotifyTouchUp", "(u)", slot)

    def touches(self, start, end=None, steps: int = 10, hold: float = 0.06) -> None:
        import time

        for i, (x, y) in enumerate(start):
            self.touch_down(i, x, y)
        if end:
            for k in range(1, steps + 1):
                for i, ((x0, y0), (x1, y1)) in enumerate(zip(start, end)):
                    self.touch_move(i, x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)
                time.sleep(0.016)
        time.sleep(hold)
        for i in range(len(start)):
            self.touch_up(i)

    def key(self, name: str, pressed: bool) -> None:
        self.keysym(Gdk.keyval_from_name(name), pressed)

    def keysym(self, sym: int, pressed: bool) -> None:
        self._rd("NotifyKeyboardKeysym", "(ub)", sym, pressed)

    def combo(self, combo: str) -> None:
        parts = combo.split("+") if combo != "+" else ["plus"]
        mods = [Gdk.keyval_from_name(MODIFIERS[m.lower()]) for m in parts[:-1]]
        name = parts[-1]
        sym = Gdk.keyval_from_name(name)
        if sym in (0, 0xFFFFFF) and len(name) == 1:
            sym = Gdk.unicode_to_keyval(ord(name))
        for m in mods:
            self.keysym(m, True)
        self.keysym(sym, True)
        self.keysym(sym, False)
        for m in reversed(mods):
            self.keysym(m, False)

    def char(self, ch: str) -> None:
        if ch == "\n":
            return self.combo("Return")
        sym = Gdk.unicode_to_keyval(ord(ch))
        shift = ch in SHIFTED
        if shift:
            self.keysym(Gdk.KEY_Shift_L, True)
        self.keysym(sym, True)
        self.keysym(sym, False)
        if shift:
            self.keysym(Gdk.KEY_Shift_L, False)

    # --- screen ---------------------------------------------------------------------------------------------------------

    def grab(self):
        """One frame of the monitor as a PIL image."""
        from PIL import Image

        os.environ.setdefault("PIPEWIRE_RUNTIME_DIR", os.environ["XDG_RUNTIME_DIR"])
        p = Gst.parse_launch(f"pipewiresrc path={self.node} always-copy=true num-buffers=2 ! videoconvert ! "
                             "video/x-raw,format=RGB ! appsink name=sink sync=false")
        sink = p.get_by_name("sink")
        p.set_state(Gst.State.PLAYING)
        sample = None
        for _ in range(2):
            s = sink.emit("try-pull-sample", 5 * Gst.SECOND)
            sample = s or sample
        p.set_state(Gst.State.NULL)
        if sample is None:
            raise TimeoutError("no frame from the screen cast")
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        try:
            stride = len(info.data) // h
            return Image.frombytes("RGB", (w, h), bytes(info.data), "raw", "RGB", stride)
        finally:
            buf.unmap(info)
