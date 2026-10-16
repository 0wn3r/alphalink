"""The interface files: Blueprint sources in alphacam_gtk/ui/*.blp, compiled to the GtkBuilder .ui next to them by
tools/build_ui.py (both are committed, as the translations' .mo are, so the app runs from the source tree and the
Flatpak needs no Blueprint compiler).

Translatable strings in the .ui go through the app's own gettext (`i18n._`), not GtkBuilder's: the Language preference
and ALPHALINK_LANG then apply to them exactly as to the strings in the Python code, whatever the C locale is."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .i18n import _, pgettext  # noqa: E402

UI_DIR = Path(__file__).with_name("ui")


@cache
def ui_string(name: str) -> str:
    """ui/<name>.ui with its translatable strings translated."""
    root = ET.parse(UI_DIR / f"{name}.ui").getroot()
    for el in root.iter():
        if el.get("translatable") in ("yes", "true", "1"):
            ctx = el.get("context")
            text = el.text or ""
            el.text = pgettext(ctx, text) if ctx else _(text) if text else text
            for attr in ("translatable", "context", "comments"):
                el.attrib.pop(attr, None)
    root.attrib.pop("domain", None)  # nothing left for GtkBuilder to translate
    return ET.tostring(root, encoding="unicode")


def template(name: str) -> Gtk.Template:
    """The class decorator for a widget whose layout is ui/<name>.blp."""
    return Gtk.Template(string=ui_string(name))


def builder(name: str) -> Gtk.Builder:
    """The objects of ui/<name>.blp: dialogs and menus that are not a widget class of their own (their signals are
    connected in the code that uses them)."""
    return Gtk.Builder.new_from_string(ui_string(name), -1)
