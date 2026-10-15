import math
import struct
import tempfile
import unittest
from pathlib import Path

from alphacam.io.toollib import TL2_RECORD, read_tl2, sibling
from alphacam.model.tools import library_from_json, library_to_json, standard_library
from alphacam.tool_shapes import internal, outline


def record(segments, points) -> bytes:
    """A TL2 record: segments as (start, end, centre) point indices from 1 (centre 0 = a line, <0 = clockwise)."""
    rec = bytearray(TL2_RECORD)
    words = [len(segments), len(points)]
    for s, e, c in segments:
        words += [-s, -e, c]
    struct.pack_into(f"<{len(words)}h", rec, 0, *words)
    struct.pack_into(f"<{len(points)}f", rec, 0x104, *[p[0] for p in points])
    struct.pack_into(f"<{len(points)}f", rec, 0x208, *[p[1] for p in points])
    return bytes(rec)


class TL2(unittest.TestCase):
    def test_lines_and_arcs(self):
        # tool 1: a triangle; tool 2: empty; tool 3: a quarter arc anticlockwise from (5, 0) to (0, 5) about (0, 0)
        data = record([(1, 2, 0), (2, 3, 0), (3, 1, 0)], [(0, 0), (10, 1), (1, 10)]) + bytes(TL2_RECORD) \
            + record([(1, 2, 3)], [(5, 0), (0, 5), (0, 0)])
        pics = read_tl2(data)
        self.assertEqual(sorted(pics), [1, 3])
        self.assertEqual(pics[1][0], [(0.0, 0.0), (10.0, 1.0)])
        self.assertEqual(len(pics[1]), 3)
        arc = pics[3][0]
        self.assertEqual(arc[0], (5.0, 0.0))
        self.assertAlmostEqual(arc[-1][0], 0.0, places=3)
        self.assertAlmostEqual(arc[-1][1], 5.0, places=3)
        mid = arc[len(arc) // 2]
        self.assertAlmostEqual(math.hypot(*mid), 5.0, places=3)
        self.assertGreater(mid[0], 0)  # anticlockwise: through the first quadrant
        self.assertGreater(mid[1], 0)

    def test_clockwise_arc_goes_the_other_way(self):
        pics = read_tl2(record([(1, 2, -3)], [(5, 0), (0, 5), (0, 0)]))
        mid = pics[1][0][len(pics[1][0]) // 2]
        self.assertLess(mid[0], 0)  # the long way round, through the other quadrants

    def test_deleted_segments_are_skipped(self):
        rec = bytearray(record([(1, 2, 0), (1, 2, 0)], [(0, 0), (3, 3)]))
        struct.pack_into("<3h", rec, 10, 0, 0, 0)  # the second triple zeroed: deleted
        self.assertEqual(len(read_tl2(bytes(rec))[1]), 1)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            read_tl2(b"x" * 100)

    def test_sibling_any_case(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ATOOLIB.TL1").write_bytes(b"")
            (Path(d) / "atoolib.tl2").write_bytes(b"")
            self.assertEqual(sibling(Path(d) / "ATOOLIB.TL1", ".TL2").name, "atoolib.tl2")
            self.assertIsNone(sibling(Path(d) / "ATOOLIB.TL1", ".TL3"))


class Shapes(unittest.TestCase):
    def test_every_standard_tool_has_a_shape_with_its_tip_at_the_origin(self):
        for tool in standard_library().tools:
            lines = outline(tool)
            self.assertTrue(lines, tool.name)
            pts = [p for line in lines for p in line]
            self.assertLess(min(math.hypot(*p) for p in pts), (tool.nose_radius or 2.5) + 1e-6, tool.name)

    def test_turning_tools_hang_down_and_boring_bars_reach_in(self):
        lib = standard_library()
        rougher, bar = outline(lib.get(1)), outline(lib.get(11))
        self.assertGreater(max(p[1] for line in rougher for p in line), 20)  # the shank above the tip
        self.assertTrue(internal(lib.get(11)))
        self.assertLessEqual(max(p[1] for line in bar for p in line), 1e-6)  # all of it inside the bore
        self.assertGreater(max(p[0] for line in bar for p in line), 40)  # the bar along +Z

    def test_imported_picture_wins_and_survives_the_library_file(self):
        lib = standard_library()
        lib.get(1).outline = [[[0.0, 0.0], [5.0, 5.0]]]
        again = library_from_json(library_to_json(lib))
        self.assertEqual(outline(again.get(1)), [[(0.0, 0.0), (5.0, 5.0)]])


if __name__ == "__main__":
    unittest.main()
