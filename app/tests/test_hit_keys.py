"""ALPHA-T's one-letter commands on the Part page (hit_keys.py)."""

import unittest

from alphacam_gtk.hit_keys import HIT_KEYS, hit_action


class HitKeys(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(hit_action("x"), "line")
        self.assertEqual(hit_action("X"), "line")
        self.assertEqual(hit_action("s"), "start")
        self.assertEqual(hit_action("g"), "absolute")
        self.assertEqual(hit_action("h"), "relative")
        self.assertEqual(hit_action("f"), "fillet")
        self.assertEqual(hit_action("b"), "corner")
        self.assertEqual(hit_action("l"), "erase")
        self.assertEqual(hit_action("r"), "fit")
        self.assertIsNone(hit_action("q"))  # erase drawing: Ctrl+N asks first
        self.assertIsNone(hit_action(""))

    def test_numbers_go_to_the_entry(self):
        for c in "0123456789-.@":
            self.assertEqual(hit_action(c), "type", c)

    def test_picking(self):
        self.assertEqual(hit_action("3", picking=True), "pick-done")
        self.assertEqual(hit_action("\r", picking=True), "pick-done")
        self.assertIsNone(hit_action("x", picking=True))
        self.assertIsNone(hit_action("\r"))

    def test_help_lists_every_letter(self):
        from alphacam_gtk.help import HELP_DIR

        for lang in ("en", "uk"):
            text = (HELP_DIR / f"help.{lang}.md").read_text("utf-8")
            for k in HIT_KEYS:
                self.assertIn(f"**{k.upper()}**", text, (lang, k))
