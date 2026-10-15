"""PRG parser tests against every ALPHA-T sample in samples/alphat_v12."""

import json
import re
import unittest
from pathlib import Path

from alphacam.io.prg_parse import Kind, MoveKind, PrgSyntaxError, parse, parse_file
from alphacam.model import OP_KINDS, MachineProfile
from alphacam.numfmt import DECIMALS, format_number

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "samples" / "alphat_v12"
PRGS = sorted(SAMPLES.glob("*.PRG"))

# Expected operations per sample: (op name, number of passes). Samples from 2026-10-12 cloud run: ARC_*, SLOPE_*,
# TWOOP_*, OFFSET_*, INT_*, TOOLCHG_*.
EXPECTED_OPS = {
    "AREA1_metric": [("area_clear", 26)],
    "DRILL_DWELL_metric": [("drill", 1)],
    "FACE_metric": [("face_clear", 26)],
    "GROOVE_metric": [("groove", 1)],
    "MET2_finish_inch": [("finish", 1)],
    "MET2_finish_metric": [("finish", 1)],
    "MOVE_RAPID_metric": [],
    "THREAD_metric": [("thread", 100)],
    "ARC_finish_metric": [("finish", 1)],
    "ARC_area_metric": [("area_clear", 26)],
    "SLOPE_area_metric": [("area_clear", 26)],
    "TWOOP_area_finish_metric": [("area_clear", 26), ("finish", 1)],
    "OFFSET_finish_metric": [("finish", 1)],
    "OFFSET_area_metric": [("area_clear", 26)],
    "INT_finish_metric": [("finish_internal", 1)],
    "INT_area_metric": [("area_clear_internal", 8)],
    "TOOLCHG_rough1_finish3_metric": [("area_clear", 26), ("finish", 1)],
    "PREFINISH_area_metric": [("area_clear", 26), ("area_prefinish", 1)],
    "THREAD_internal_metric": [("thread", 100)],
    "THREAD_p2_len30_metric": [("thread", 100)],
    "FACE_offset_metric": [("face_clear", 26)],
    "FACE_depth3_start110_metric": [("face_clear", 18)],
    "PECK_groove_metric": [("groove", 1)],
    "PECK_fullret_groove_metric": [("groove", 1)],
    # 2026-10-13 batch (samples/alphat_v12, menu values in README_options_2026-10-13.md)
    "ARC_touching_R5_R3_finish_metric": [("finish", 1)],
    "AREA_from_dia20_metric": [("area_clear", 21)],
    "AREA_front_face_metric": [("face_clear", 26)],
    "DRILL_nopeck_nodwell_metric": [("drill", 1)],
    "FACE_arcs_metric": [("face_clear", 26)],
    "FACE_slope_metric": [("face_clear", 25)],
    "FACE_tool1_metric": [("face_clear", 26)],
    "FINISH_then_move_rapid_dwell_metric": [("finish", 1)],
    "GROOVE_datumright_metric": [("groove", 1)],
    "GROOVE_dwell2_metric": [("groove", 1)],
    "GROOVE_finishmode_metric": [("groove_finish", 1)],
    "GROOVE_zshift10_rep1_metric": [("groove", 2)],
    "INT_area_offset_metric": [("area_clear_internal", 8)],
    "INT_finish_offset_metric": [("finish_internal", 1)],
    "THREAD_leadin2_leadout2_metric": [("thread", 100)],
    "THREAD_spring1_metric": [("thread", 100), ("thread_spring", 1)],
    "THREAD_depth6_metric": [("thread", 36)],
    "THREAD_6cuts_metric": [("thread", 6)],
    # 2026-10-13 evening: the options the app still refused (README_options_2026-10-13.md, second table)
    "AREA_away_tool5_retry_metric": [("area_clear", 26)],
    "AREA_face_internal_metric": [("area_clear_internal", 20)],
    "AREA_prefinish_internal_metric": [("area_clear_internal", 8), ("area_prefinish_internal", 1)],
    "AREA_prefinish_offset_metric": [("area_clear", 26), ("area_prefinish", 1)],
    "FACE_away_tool3_nocheck_metric": [("face_clear", 1)],
    "FACE_away_tool5_metric": [("face_clear", 1)],
    "FACE_prefinish_metric": [("face_clear", 26), ("face_prefinish", 1)],
    "GROOVE_back_metric": [("groove", 1)],
    # German manual list (2026-10-13 night)
    "AREA_follow_contour_metric": [("area_clear", 26)],
    "AREA_retract2_metric": [("area_clear", 26)],
    "DIVE1_metric": [("area_clear", 29)],
    "DIVE2_metric": [("area_clear", 29)],
    "DIVE3_metric": [("area_clear", 34)],
    "FINISH_dive2_metric": [("finish", 1)],
    "AREA_away_from_chuck_end_metric": [("area_clear", 11)],
    "AREA_towards_from_chuck_end_metric": [("area_clear", 10)],
    "FACE_away_from_chuck_end_metric": [("face_clear", 26)],
    "FACE_identified_inside_out_metric": [("face_clear", 26)],
    "FACE_towards_from_chuck_end_metric": [("face_clear", 1)],
    "FINISH_face_metric": [("finish_face", 1)],
    "DRILL_fullret_metric": [("drill", 1)],
    "DRILL_retract1_metric": [("drill", 1)],
    "THREAD_angle30_metric": [("thread", 100)],
    "THREAD_spring2_metric": [("thread", 100), ("thread_spring", 2)],
    "THREAD_away_from_chuck_end_metric": [("thread", 100)],
    "GROOVE_face_metric": [("groove", 1)],
    "GROOVE_finishmode_slope_metric": [("groove_finish", 1)],
    "GROOVE_finishoffset_metric": [("groove", 1)],
    "GROOVE_internal_metric": [("groove", 1)],
    "THREAD_away_metric": [("thread", 100)],
}


def sd1_words(path: Path) -> list[dict[str, str]]:
    """Minimal SD1 reader for cross-checks: each line -> {letter: value}, with G codes collected under 'G'."""
    rows = []
    for line in path.read_text("ascii").splitlines():
        row: dict = {"G": []}
        for letter, value in re.findall(r"([A-Z])(-?[\d.]+)", line):
            if letter == "G":
                row["G"].append(int(value))
            else:
                row.setdefault(letter, value)
        rows.append(row)
    return rows


class EverySample(unittest.TestCase):
    def test_samples_present(self):
        self.assertEqual({p.stem for p in PRGS}, set(EXPECTED_OPS))

    def test_parse_and_round_trip(self):
        for path in PRGS:
            with self.subTest(path.name):
                data = path.read_bytes()
                prog = parse(data)
                self.assertEqual(prog.eol, "\r\n")
                self.assertEqual(prog.to_bytes(), data)
                self.assertEqual(prog.o_number, 2)
                self.assertEqual(prog.program_number, 10)
                self.assertEqual(prog.units, "inch" if "inch" in path.stem else "mm")
                self.assertEqual(prog.blocks[0].op_name, "tool_change")
                self.assertEqual(prog.blocks[-1].op_name, "end")
                self.assertEqual([b.label for b in prog.blocks], [10 * (i + 1) for i in range(len(prog.blocks))])

    def test_header_matches_spec(self):
        spec = json.loads((REPO / "data" / "program_formats.json").read_text())["alphat_v12_prg_observed"]["header"]
        for path in PRGS:
            with self.subTest(path.name):
                prog = parse_file(path)
                lines = [s.text for s in prog.statements[: len(spec)]]
                expected = [t.replace("<n>", "2").replace("<program number>", "10") for t in spec]
                expected[-1] = "M70" if prog.units == "inch" else "M71"  # spec lists the metric M71
                self.assertEqual(lines, expected)

    def test_op_kind(self):
        for path in PRGS:
            with self.subTest(path.name):
                ops = parse_file(path).operations
                self.assertEqual([b.op_name for b in ops], [name for name, _ in EXPECTED_OPS[path.stem]])

    def test_declared_passes_match(self):
        for path in PRGS:
            prog = parse_file(path)
            for b, (_, passes) in zip(prog.operations, EXPECTED_OPS[path.stem]):
                with self.subTest(path.name, block=b.label):
                    self.assertEqual(b.declared_passes, passes)
                    self.assertEqual(len(b.passes), b.declared_passes)
                    self.assertEqual([p.number for p in b.passes], list(range(1, b.declared_passes + 1)))
                    self.assertTrue(b.passes[-1].moves, "last pass has moves")

    def test_numbers_are_canonical(self):
        """C6: every coordinate/feed is written exactly as format_number would write it."""
        for path in PRGS:
            prog = parse_file(path)
            decimals = DECIMALS[prog.units]
            with self.subTest(path.name):
                for s in prog.statements:
                    tokens = []
                    if s.kind is Kind.MOTION:
                        tokens = [v for k, v in s.words.items() if k != "P"]
                    elif s.kind is Kind.ASSIGN and s.number in (123, 124, 125, 126, 130):
                        tokens = [s.value]
                    for tok in tokens:
                        self.assertEqual(format_number(float(tok), decimals), tok, f"line {s.line}: {s.text}")

    def test_registers_match_sd1(self):
        """Unscaled #123/#124, #125/#126 and #130 agree with the SD1 listing (G5 tool change, G5 start, G115, F)."""
        for path in PRGS:
            prog = parse_file(path)
            sd1 = sd1_words(path.with_suffix(".SD1"))
            g5 = [r for r in sd1 if 5 in r["G"]]
            g115 = [r for r in sd1 if 115 in r["G"]]
            tool_feeds = [float(r["F"]) for r in sd1 if "T" in r and "F" in r]  # N10 F.. T.. / G99 F.. T..
            with self.subTest(path.name):
                tc = prog.blocks[0]
                self.assertEqual(tc.start, (float(g5[0]["X"]), float(g5[0]["Z"])))
                tools = [b for b in prog.blocks if b.op_kind == 1]
                self.assertEqual([b.feed for b in tools], tool_feeds)
                i = -1
                for b in prog.operations:
                    i += b.op_name != "thread_spring"  # the spring pass block repeats the thread's limits
                    self.assertEqual(b.limit, (float(g115[i]["X"]), float(g115[i]["Z"])))
                    if b.op_name in ("thread", "thread_spring"):
                        g76 = next(r for r in sd1 if 76 in r["G"])
                        self.assertAlmostEqual(b.feed, float(g76["F"]))  # pitch
                    else:
                        current_tool = [t for t in tools if t.label < b.label][-1]
                        self.assertAlmostEqual(b.feed, current_tool.feed)

    def test_moves_inside_limits(self):
        for path in PRGS:
            if any(75 in r["G"] and float(r.get("R", 0)) > 0 for r in sd1_words(path.with_suffix(".SD1"))):
                continue  # a +Z grooving Z-SHIFT repeats the plunges above the start Z (GROOVE_zshift10_rep1)
            if path.stem in ("THREAD_away_metric", "THREAD_away_from_chuck_end_metric"):
                continue  # ALPHA-T's AWAY thread rapids out to Z15 after each cut (the app refuses AWAY)
            prog = parse_file(path)
            for b in prog.operations:
                with self.subTest(path.name):
                    sx, sz = b.start
                    for m in b.moves:
                        self.assertIsNotNone(m.x)
                        self.assertIsNotNone(m.z)
                        self.assertLessEqual(m.z, sz + 1e-9, f"line {m.line}")

    def test_profile_scaling(self):
        path = SAMPLES / "MET2_finish_metric.PRG"
        raw = parse_file(path, MachineProfile(z_scale=1, f_scale=1))
        scaled = parse_file(path)
        self.assertEqual(raw.blocks[1].start, (120.0, 10.0))
        self.assertEqual(scaled.blocks[1].start, (120.0, 5.0))
        self.assertEqual(raw.blocks[1].feed, 0.3)
        self.assertEqual(scaled.blocks[1].feed, 0.15)


class SampleDetails(unittest.TestCase):
    def test_finish_contour(self):
        b = parse_file(SAMPLES / "MET2_finish_metric.PRG").operations[0]
        feeds = [(m.x, m.z) for m in b.moves if m.kind is MoveKind.FEED]
        self.assertEqual(feeds, [(0, 0), (40, 0), (40, -25), (60, -25), (60, -50), (100, -50), (100.282, -49.859)])
        self.assertEqual((b.moves[-1].kind, b.moves[-1].x, b.moves[-1].z), (MoveKind.RAPID, 120, 5))
        self.assertTrue(all(m.feed == 0.15 for m in b.moves if m.kind is MoveKind.FEED))

    def test_finish_inch(self):
        prog = parse_file(SAMPLES / "MET2_finish_inch.PRG")
        b = prog.operations[0]
        feeds = [(m.x, m.z) for m in b.moves if m.kind is MoveKind.FEED]
        self.assertEqual(feeds[:3], [(0, 0), (1.6, 0), (1.6, -1)])
        self.assertEqual(prog.blocks[0].start, (3.937, 1.9685))

    def test_area_passes(self):
        b = parse_file(SAMPLES / "AREA1_metric.PRG").operations[0]
        first_cut = [p.moves[1].x for p in b.passes]
        self.assertEqual(first_cut[:3], [96, 92, 88])
        self.assertEqual(first_cut[-1], -0.8)
        # Restart: pass n jumps to label 50(n+2) when #140 == n+1.
        self.assertEqual([p.restart for p in b.passes[:2]], [(2, 503), (3, 504)])
        self.assertEqual(b.labels[:2], [503, 504])

    def test_face_passes(self):
        b = parse_file(SAMPLES / "FACE_metric.PRG").operations[0]
        depths = [p.moves[0].z for p in b.passes]
        self.assertEqual(depths[:3], [-2, -4, -6])
        self.assertEqual(depths[-1], -50)

    def test_groove_plunges(self):
        b = parse_file(SAMPLES / "GROOVE_metric.PRG").operations[0]
        plunges = [m for m in b.moves if m.kind is MoveKind.FEED]
        self.assertEqual(len(plunges) % 2, 0)
        bottoms = {m.x for m in plunges[::2]}
        self.assertLessEqual(bottoms, {30.0, 40.0, 60.0})

    def test_thread_passes(self):
        b = parse_file(SAMPLES / "THREAD_metric.PRG").operations[0]
        self.assertEqual(b.feed, 1.5)  # pitch, unscaled
        cuts = [m for m in b.moves if m.kind is MoveKind.THREAD and not m.incremental]
        self.assertEqual(len(cuts), 100)
        self.assertEqual([c.x for c in cuts[:3]], [38, 37.172, 36.536])
        self.assertEqual(cuts[-1].x, 20)
        self.assertTrue(all(c.z == -23.5 and c.feed == 1.5 for c in cuts))
        retracts = [m for m in b.moves if m.incremental]
        self.assertEqual(len(retracts), 100)
        self.assertTrue(all(r.kind is MoveKind.THREAD and r.z == -25 for r in retracts))
        self.assertEqual((retracts[0].x, retracts[-1].x), (59, 41))

    def test_drill_dwell(self):
        prog = parse_file(SAMPLES / "DRILL_DWELL_metric.PRG")
        tool_dwell = prog.blocks[0].moves
        self.assertEqual([(m.kind, m.dwell_revs, m.x, m.z) for m in tool_dwell], [(MoveKind.DWELL, 3, 100, 50)])
        drill = prog.operations[0]
        self.assertEqual([m.z for m in drill.moves if m.kind is MoveKind.FEED], [-5, -3, -15, -13, -25, -23, -30, -28])
        self.assertEqual([m.dwell_revs for m in drill.moves if m.kind is MoveKind.DWELL], [3])

    def test_move_rapid(self):
        prog = parse_file(SAMPLES / "MOVE_RAPID_metric.PRG")
        self.assertEqual(len(prog.blocks), 2)
        moves = [(m.kind, m.x, m.z) for m in prog.blocks[0].moves]
        self.assertEqual(moves, [(MoveKind.FEED, 100, -50), (MoveKind.FEED, 60, -50), (MoveKind.RAPID, 60, -70)])


class Strictness(unittest.TestCase):
    def setUp(self):
        self.data = (SAMPLES / "MET2_finish_metric.PRG").read_bytes()

    def test_lf_line_endings(self):
        prog = parse(self.data.replace(b"\r\n", b"\n"))
        self.assertEqual(prog.eol, "\n")
        self.assertEqual(prog.to_bytes(), self.data.replace(b"\r\n", b"\n"))

    def test_unknown_statement(self):
        bad = self.data.replace(b"G1X40.Z0.F0.3\r\n", b"G1X40.Z0.F0.3\r\nT0303\r\n")
        with self.assertRaises(PrgSyntaxError) as cm:
            parse(bad)
        self.assertEqual(cm.exception.line, 45)

    def test_missing_decimal_point(self):
        with self.assertRaisesRegex(PrgSyntaxError, "decimal point"):
            parse(self.data.replace(b"G1X40.Z0.F0.3", b"G1X40Z0.F0.3"))
        with self.assertRaisesRegex(PrgSyntaxError, "decimal point"):
            parse(self.data.replace(b"#123=120.", b"#123=120"))

    def test_missing_end(self):
        with self.assertRaisesRegex(PrgSyntaxError, "M65"):
            parse(self.data.replace(b"M65\r\n", b""))

    def test_missing_final_newline(self):
        with self.assertRaisesRegex(PrgSyntaxError, "final line ending"):
            parse(self.data[:-2])

    def test_non_ascii(self):
        with self.assertRaisesRegex(PrgSyntaxError, "non-ASCII"):
            parse(self.data.replace(b"M71", b"M7\xb9"))

    def test_block_label_order(self):
        with self.assertRaisesRegex(PrgSyntaxError, "N30"):
            parse(self.data.replace(b"N30\r\n", b"N35\r\n"))


class OpKinds(unittest.TestCase):
    def test_table_matches_program_formats(self):
        spec = json.loads((REPO / "data" / "program_formats.json").read_text())
        text = spec["alphat_v12_prg_observed"]["N20 block (operation)"]["#127"]
        pairs = {int(n): name for n, name in re.findall(r"(\d+) ([a-z -]+?)(?:,|$| \()", text)}
        self.assertIn("(1 = N10 tool block)", text)
        self.assertEqual(set(pairs) | {1}, set(OP_KINDS))
