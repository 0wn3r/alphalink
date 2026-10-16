"""The billet rows of New Part and Part Settings (ui/billet-group.blp)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from alphacam.model.project import Billet  # noqa: E402

from .ui import template  # noqa: E402


def units_follow(units_row: Adw.ComboRow, rows: list[Adw.SpinRow]) -> None:
    """Switching mm <-> inch converts the size rows already filled in (50 mm stays 50 mm, shown as 1.9685 in)."""
    state = {"inch": units_row.get_selected() == 1}

    def changed(row, _p):
        inch = row.get_selected() == 1
        if inch == state["inch"]:
            return
        state["inch"] = inch
        for r in rows:
            v = r.get_value()
            r.set_digits(4 if inch else 1)
            r.set_value(round(v / 25.4, 4) if inch else round(v * 25.4, 1))

    units_row.connect("notify::selected", changed)


@template("billet-group")
class BilletGroup(Adw.PreferencesGroup):
    __gtype_name__ = "AlphaLinkBilletGroup"

    units_row = Gtk.Template.Child()
    diameter_row = Gtk.Template.Child()
    length_row = Gtk.Template.Child()
    bore_row = Gtk.Template.Child()
    jaws_row = Gtk.Template.Child()
    tailstock_row = Gtk.Template.Child()

    def set_billet(self, b: Billet, inch: bool = False, units: bool = False) -> None:
        """Show `b`; inch parts get 4 decimals (a 0.75 in bar must not be rounded or clamped to 1). `units`: the units
        row too, first, and switching it converts the sizes."""
        for key, row in (("diameter", self.diameter_row), ("length", self.length_row), ("bore", self.bore_row)):
            row.set_digits(4 if inch else 1)
            row.set_value(getattr(b, key))
        self.jaws_row.set_selected(1 if b.jaws == "internal" else 0)
        self.tailstock_row.set_active(b.tailstock)
        if units:
            self.units_row.set_selected(1 if inch else 0)
            self.units_row.set_visible(True)
            units_follow(self.units_row, [self.diameter_row, self.length_row, self.bore_row])

    def inch(self) -> bool:
        return self.units_row.get_selected() == 1

    def billet(self) -> Billet:
        return Billet(diameter=self.diameter_row.get_value(), length=self.length_row.get_value(),
                      bore=self.bore_row.get_value(), jaws="internal" if self.jaws_row.get_selected() == 1 else "external",
                      tailstock=self.tailstock_row.get_active())
