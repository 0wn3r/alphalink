import struct
import tempfile
import unittest
from pathlib import Path

from alphacam.io.toollib import read_tl1
from alphacam.model import Tool, ToolLibrary
from alphacam.model.tools import library_from_json, library_to_json, standard_library
from alphacam.post.alphat_prg import post

from .test_post import SAMPLES, sample_project


def tl1(records: dict[int, tuple]) -> bytes:
    data = bytearray(256 * 33)
    struct.pack_into("<h", data, 0, 32)
    for n, (name, r, front, rear, desc) in records.items():
        rec = name.ljust(20).encode() + struct.pack("<fhh", r, front, rear) + desc.encode()
        data[n * 256:n * 256 + len(rec)] = rec
    return bytes(data)


class ToolLibraries(unittest.TestCase):
    def test_read_tl1(self):
        lib = read_tl1(tl1({1: ("80 Deg Rougher 0.8R", 0.8, 87, 7, "GENERAL PURPOSE"),
                            12: ("55 Boring-Bar 0.4R", 0.4, -87, -31, ""), 24: ("25mm Drill", 0, 0, 0, "")}))
        self.assertEqual([(t.number, t.name, t.nose_radius, t.type) for t in lib.tools],
                         [(1, "80 Deg Rougher 0.8R", 0.8, "turn"), (12, "55 Boring-Bar 0.4R", 0.4, "bore"),
                          (24, "25mm Drill", 0.0, "drill")])
        self.assertEqual((lib.get(1).front_angle, lib.get(1).rear_angle, lib.get(1).description),
                         (87, 7, "GENERAL PURPOSE"))
        self.assertEqual(lib.get(24).width, 25)
        with self.assertRaises(ValueError):
            read_tl1(b"\0" * 100)

    def test_standard_is_alpha_t(self):
        std = standard_library()
        self.assertEqual(len(std.tools), 32)
        self.assertEqual((std.get(3).name, std.get(3).nose_radius, std.get(3).rear_angle), ("55 Deg Finisher 0.4R", 0.4, 31))

    def test_json_round_trip(self):
        lib = standard_library()
        lib.tools[2].nose_radius = 0.8
        self.assertEqual(library_from_json(library_to_json(lib)), lib)

    def test_library_changes_the_toolpath(self):
        p = sample_project("ARC_finish_metric")
        same = post(p, library=standard_library())
        self.assertEqual(same, (SAMPLES / "ARC_finish_metric.PRG").read_bytes())
        lib = ToolLibrary([Tool(3, "55 Deg 0.8R", nose_radius=0.8)])
        other = post(p, library=lib)
        self.assertNotEqual(other, same)  # R5 arc becomes R5.8 with a 0.8 nose
        self.assertIn(b"K-11.6", other)  # convex arc K = -(5 + 0.8) x 2
        p.tools = [Tool(3, "part's own", nose_radius=0.4)]  # the part's own tools win over the library
        self.assertEqual(post(p, library=lib), same)

    def test_gtk_store_round_trip(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from alphacam_gtk.tools_page import load_library, save_library

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tools.json"
            self.assertEqual(len(load_library(path).tools), 32)  # missing file: ALPHA-T standard
            lib = ToolLibrary([Tool(7, "My insert", nose_radius=0.2)])
            save_library(lib, path)
            self.assertEqual(load_library(path), lib)


class Preferences(unittest.TestCase):
    def test_settings_round_trip(self):
        from alphacam_gtk.settings import DEFAULTS, Settings

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            st = Settings(path)
            self.assertEqual(st.values, DEFAULTS)
            st.set("serial_port", "/dev/ttyUSB1")
            st.set("char_delay_ms", 3.5)
            again = Settings(path)
            self.assertEqual((again["serial_port"], again["char_delay_ms"], again["wait_xon"]),
                             ("/dev/ttyUSB1", 3.5, True))
            with self.assertRaises(KeyError):
                st.set("nonsense", 1)
