"""The F1 help: both languages present, same structure, renders as valid Pango markup."""

import re
import unittest

from alphacam_gtk.help import HELP_DIR, blocks, help_path


class Help(unittest.TestCase):
    def test_languages_and_fallback(self):
        self.assertEqual(help_path("uk").name, "help.uk.md")
        self.assertEqual(help_path("uk_UA").name, "help.uk.md")
        self.assertEqual(help_path("de").name, "help.en.md")

    def test_same_structure(self):
        """The Ukrainian help is a full translation: same headings and list items, in the same order."""
        shape = {}
        for lang in ("en", "uk"):
            shape[lang] = [(k, lvl) for k, lvl, _m in blocks((HELP_DIR / f"help.{lang}.md").read_text("utf-8"))]
        self.assertEqual(shape["en"], shape["uk"])

    def test_markup_is_valid(self):
        from gi.repository import Pango

        for lang in ("en", "uk"):
            for kind, _lvl, markup in blocks((HELP_DIR / f"help.{lang}.md").read_text("utf-8")):
                with self.subTest(lang=lang, text=markup[:40]):
                    Pango.parse_markup(markup, -1, "\0")
                    self.assertNotIn("**", markup)

    def test_shortcuts_documented_exist(self):
        """Every Ctrl shortcut the help names is bound in the window: as an accelerator, or (the Rust app, which
        shares this help) in a key handler with Ctrl held."""
        src = (HELP_DIR.parent / "window.py").read_text() + (HELP_DIR.parent / "application.py").read_text()
        rust = HELP_DIR.parents[2] / "rust/alphacam-gtk/src"
        if rust.is_dir():
            src += "".join(f.read_text() for f in rust.rglob("*.rs"))
        text = (HELP_DIR / "help.en.md").read_text("utf-8")
        for key in set(re.findall(r"Ctrl\+([A-Z0-9,])\b", text)):
            name = {",": "comma"}.get(key, key.lower())
            self.assertRegex(src, rf"<Control>(<Shift>)?{re.escape(name)}\"|gdk::Key::{re.escape(name)}\b[^\n]*\bctrl\b", key)
        self.assertIn('"F1"', src)


class CoreMessages(unittest.TestCase):
    def test_core_errors_are_translated(self):
        """Errors from the core (shown in the banner and toasts) go through the app's catalog."""
        import gettext

        from alphacam import i18n
        from alphacam.toolpath.contour import chain
        from alphacam_gtk.i18n import DOMAIN, LOCALE_DIR

        uk = gettext.translation(DOMAIN, LOCALE_DIR, languages=["uk"])
        i18n.set_translator(uk.gettext)
        try:
            with self.assertRaises(ValueError) as cm:
                chain([])
            self.assertEqual(str(cm.exception), "порожній контур")
        finally:
            from alphacam_gtk.i18n import _

            i18n.set_translator(_)


class Manuals(unittest.TestCase):
    def test_both_manuals_in_both_languages(self):
        from alphacam_gtk.help import MANUALS, manual_path

        for kind in MANUALS:
            for lang in ("en", "uk"):
                p = manual_path(kind, lang)
                self.assertEqual(p.name, f"{kind}.{lang}.pdf")
                self.assertEqual(p.read_bytes()[:5], b"%PDF-")
            self.assertEqual(manual_path(kind, "de").name, f"{kind}.en.pdf")

    def test_menu_actions(self):
        src = (HELP_DIR.parent / "ui" / "window.blp").read_text()
        self.assertIn('"win.manual-training"', src)
        self.assertIn('"win.manual-reference"', src)
