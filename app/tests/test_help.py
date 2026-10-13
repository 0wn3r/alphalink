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
        """Every Ctrl shortcut the help names is bound in the window."""
        src = (HELP_DIR.parent / "window.py").read_text() + (HELP_DIR.parent / "application.py").read_text()
        text = (HELP_DIR / "help.en.md").read_text("utf-8")
        for key in set(re.findall(r"Ctrl\+([A-Z0-9,])\b", text)):
            name = {",": "comma"}.get(key, key.lower())
            self.assertRegex(src, rf"<Control>(<Shift>)?{re.escape(name)}\"", key)
        self.assertIn('"F1"', src)
