"""The Keyboard Shortcuts dialog (Ctrl+?), as the GNOME HIG asks for: the Ctrl shortcuts, then ALPHA-T's one-letter
keys on the Part page (hit_keys.py)."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from .i18n import N_, _  # noqa: E402

SECTIONS = [
    (N_("General"), [(N_("Main Menu"), "F10"), (N_("Preferences"), "<Control>comma"),
                     (N_("Keyboard Shortcuts"), "<Control>question"), (N_("Help"), "F1"),
                     (N_("Close Window"), "<Control>w"), (N_("Quit"), "<Control>q")]),
    (N_("Files"), [(N_("New Part"), "<Control>n"), (N_("Open"), "<Control>o"), (N_("Save"), "<Control>s"),
                   (N_("Save As"), "<Shift><Control>s"), (N_("Export PRG"), "<Control>e")]),
    (N_("Editing"), [(N_("Undo"), "<Control>z"), (N_("Redo"), "<Shift><Control>z"),
                     (N_("Add Operation"), "c"), (N_("Edit Element"), "e"), (N_("Delete Element"), "Delete"),
                     (N_("Deselect or Cancel"), "Escape")]),
    (N_("View"), [(N_("Zoom In"), "<Control>plus"), (N_("Zoom Out"), "<Control>minus"),
                  (N_("Fit Part"), "<Control>0"), (N_("Search the Program"), "<Control>f"), (N_("Zoom In at the Cursor"), "z"), (N_("Redraw"), "r"),
                  (N_("Recall View"), "v")]),
    (N_("Cursor (Drawing Has the Focus)"), [
        (N_("Move the Cursor by the Step"), "Left Right Up Down"), (N_("Step ×10"), "Page_Up"),
        (N_("Step ÷10"), "Page_Down"), (N_("Cursor to the Middle of the View"), "Home"),
        (N_("Cursor to X0 Z0"), "End"), (N_("Use the Cursor as the Point"), "Return")]),
    (N_("Drawing Keys (Part Page)"), [
        (N_("Find Nearest Line"), "l"), (N_("Find Nearest Arc"), "a"), (N_("Find Nearest Point"), "p"),
        (N_("Find Nearest Intersection"), "i"), (N_("Draw Line by X,Z"), "x"),
        (N_("Draw Line by Length and Angle"), "d"), (N_("Draw Line by Taps"), "plus"),
        (N_("Start at the Cursor"), "s"), (N_("Arc"), "w"), (N_("Radius at the Nearest Corner"), "f"),
        (N_("Chamfer"), "b"), (N_("Blend Arc"), "o"), (N_("Trim Corner"), "k"),
        (N_("Move Cursor to X,Z"), "g"), (N_("Move Cursor by dX,dZ"), "h"),
        (N_("Move Cursor by Length and Angle"), "y"), (N_("Measure by X,Z"), "m"),
        (N_("Measure by Length and Angle"), "n"), (N_("Add Text"), "t"), (N_("Erase Text"), "u"),
        (N_("Erase the Whole Drawing"), "q")]),
]


def shortcuts_dialog() -> Adw.ShortcutsDialog:
    dlg = Adw.ShortcutsDialog()
    for title, items in SECTIONS:
        section = Adw.ShortcutsSection(title=_(title))
        for label, accel in items:
            section.add(Adw.ShortcutsItem.new(_(label), accel))
        dlg.add(section)
    return dlg
