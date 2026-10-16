"""The Keyboard Shortcuts dialog (Ctrl+?): ui/shortcuts.blp."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from .ui import builder  # noqa: E402


def shortcuts_dialog() -> Adw.ShortcutsDialog:
    return builder("shortcuts").get_object("dialog")
