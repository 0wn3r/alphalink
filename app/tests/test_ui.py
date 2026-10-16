"""The interface files: each Blueprint source has its compiled .ui (tools/build_ui.py) and it is up to date."""

import hashlib
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "alphacam_gtk" / "ui"
GTK = UI.parent


class InterfaceFiles(unittest.TestCase):
    def test_every_blueprint_is_compiled(self):
        for blp in sorted(UI.glob("*.blp")):
            with self.subTest(blp.name):
                ui = blp.with_suffix(".ui")
                self.assertTrue(ui.exists(), f"run tools/build_ui.py: {ui.name} is missing")
                stamp = re.search(r"<!-- blueprint source sha256: (\w+) -->", ui.read_text())
                self.assertIsNotNone(stamp, f"{ui.name} was not written by tools/build_ui.py")
                self.assertEqual(stamp[1], hashlib.sha256(blp.read_bytes()).hexdigest(),
                                 f"{blp.name} changed: run tools/build_ui.py")
                ET.parse(ui)

    def test_no_ui_without_blueprint(self):
        self.assertEqual([ui.name for ui in UI.glob("*.ui") if not ui.with_suffix(".blp").exists()], [])

    def test_every_file_is_used(self):
        """Each .ui is loaded by name somewhere in the code (template("x") or builder("x"))."""
        src = "".join(p.read_text() for p in GTK.rglob("*.py"))
        for ui in sorted(UI.glob("*.ui")):
            with self.subTest(ui.name):
                self.assertRegex(src, rf"(template|builder)\(\"{re.escape(ui.stem)}\"\)")

    def test_strings_are_translated(self):
        """Every translatable string in the .ui files is in the Ukrainian catalog (tools/update_translations.py)."""
        import gettext

        from alphacam_gtk import i18n

        uk = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=["uk"])
        missing = []
        for ui in sorted(UI.glob("*.ui")):
            for el in ET.parse(ui).getroot().iter():
                if el.get("translatable") == "yes" and el.text and uk.gettext(el.text) == el.text:
                    missing.append(f"{ui.name}: {el.text}")
        self.assertEqual(missing, [])
