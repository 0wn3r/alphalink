"""On-screen keypad for X,Z entry (TOUCH.md 6): big keys, ABS/INC like a control, no system keyboard.

Returns the same text the command entry understands: `X,Z` or `@dX,dZ`."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

KEYS = [["7", "8", "9", "⌫"], ["4", "5", "6", "±"], ["1", "2", "3", "ABS"], ["0", ".", "→", "✓"]]


class Keypad(Adw.Dialog):
    def __init__(self, on_done, title: str = "Point"):
        super().__init__(title=title, content_width=360)
        self.on_done = on_done
        self.values = {"x": "", "z": ""}
        self.active = "x"
        self.incremental = False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=12, margin_end=12,
                      margin_bottom=12, margin_top=6)
        fields = Gtk.Box(spacing=8, homogeneous=True)
        self.field_buttons = {}
        for key, label in (("x", "X Ø"), ("z", "Z")):
            b = Gtk.Button()
            b.add_css_class("keypad-field")
            b.connect("clicked", lambda _b, k=key: self._activate(k))
            fields.append(b)
            self.field_buttons[key] = (b, label)
        box.append(fields)
        grid = Gtk.Grid(row_spacing=8, column_spacing=8, row_homogeneous=True, column_homogeneous=True)
        self.abs_button = None
        for r, row in enumerate(KEYS):
            for c, k in enumerate(row):
                b = Gtk.Button(label=k)
                b.add_css_class("keypad-key")
                if k == "✓":
                    b.add_css_class("suggested-action")
                if k == "ABS":
                    self.abs_button = b
                b.connect("clicked", lambda _b, k=k: self._key(k))
                grid.attach(b, c, r, 1, 1)
        box.append(grid)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        self.set_child(view)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._hw_key)
        self.add_controller(keys)
        self._refresh()

    def _activate(self, key: str) -> None:
        self.active = key
        self._refresh()

    def _key(self, k: str) -> None:
        v = self.values[self.active]
        if k.isdigit():
            v += k
        elif k == "." and "." not in v:
            v += "."
        elif k == "⌫":
            v = v[:-1]
        elif k == "±":
            v = v[1:] if v.startswith("-") else "-" + v
        elif k == "ABS":
            self.incremental = not self.incremental
            self._refresh()
            return
        elif k == "→":
            self.active = "z" if self.active == "x" else "x"
            self._refresh()
            return
        elif k == "✓":
            self._done()
            return
        self.values[self.active] = v
        self._refresh()

    def _hw_key(self, ctl, keyval, code, state) -> bool:
        ch = chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else ""
        if ch.isdigit() or ch == ".":
            self._key(ch)
        elif ch == "-":
            self._key("±")
        elif keyval == Gdk.KEY_BackSpace:
            self._key("⌫")
        elif keyval == Gdk.KEY_Tab or ch == ",":
            self._key("→")
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._done()
        else:
            return False
        return True

    def text(self) -> str:
        x, z = self.values["x"] or "0", self.values["z"] or "0"
        return f"{'@' if self.incremental else ''}{x},{z}"

    def _done(self) -> None:
        if self.values["x"] in ("", "-", ".") and self.values["z"] in ("", "-", "."):
            return
        self.close()
        self.on_done(self.text())

    def _refresh(self) -> None:
        for key, (b, label) in self.field_buttons.items():
            prefix = "Δ" if self.incremental else ""
            b.set_label(f"{prefix}{label}  {self.values[key] or '—'}")
            if key == self.active:
                b.add_css_class("accent")
            else:
                b.remove_css_class("accent")
        self.abs_button.set_label("INC" if self.incremental else "ABS")
