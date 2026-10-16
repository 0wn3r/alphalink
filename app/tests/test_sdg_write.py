"""The .SDG writer against ALPHA-T's own drawings: read a sample, write it back, compare the bytes."""

import struct
import unittest

from alphacam.io.sdg import read_sdg
from alphacam.io.sdg_write import write_sdg, write_sdt
from tests.test_post import SAMPLES

VIEW = (0, 1, 2, 11)


def _close(test, a: bytes, b: bytes, float_from: int):
    """Equal bytes up to float_from, then float32 arrays equal to 1e-4 mm (inch drawings: ALPHA-T computes in
    single precision, so the last bit of a value converted from inch can differ)."""
    test.assertEqual(a[:float_from], b[:float_from])
    n = (len(a) - float_from) // 4
    fa, fb = struct.unpack_from(f"<{n}f", a, float_from), struct.unpack_from(f"<{n}f", b, float_from)
    for x, y in zip(fa, fb):
        test.assertAlmostEqual(x, y, delta=1e-4)  # screen scale and pan: a sample that was zoomed or panned keeps its own


ULP = {"THREAD_internal_m24_d03_metric"}  # X22.376 typed into ALPHA-T: its float32 radius is 1 ulp off


class SdgWriter(unittest.TestCase):
    def test_every_sample_round_trips(self):
        sdgs = sorted(SAMPLES.glob("*.SDG")) + sorted((SAMPLES.parent / "alphat_v12_inch").glob("*.SDG"))
        self.assertGreaterEqual(len(sdgs), 60)
        for path in sdgs:
            with self.subTest(path.stem):
                ref = path.read_bytes()
                project = read_sdg(ref)
                out = write_sdg(project)
                self.assertEqual(len(out), len(ref))
                again = read_sdg(out)
                self.assertEqual([(g.type, g.a, g.b, getattr(g, "centre", None), getattr(g, "ccw", None))
                                  for g in again.geometry],
                                 [(g.type, g.a, g.b, getattr(g, "centre", None), getattr(g, "ccw", None))
                                  for g in project.geometry])
                inch = project.units == "inch"
                if not any(g.type == "arc" for g in project.geometry):
                    # drawn as chains: ALPHA-T's own point numbering (arcs come from corner edits, whose leftover
                    # points only ALPHA-T's edit history explains)
                    self.assertEqual(out[:0xC00], ref[:0xC00])
                    check = _close if inch or path.stem in ULP else lambda t, a, b, f: t.assertEqual(a, b)
                    check(self, out[0xC00:0x2400], ref[0xC00:0x2400], 0)
                # billet and jaws layer; after its 18 entries ALPHA-T's files hold leftover screen memory (0x2770 ..
                # 0x3300 and after the empty third layer's header at 0x4B00), written as zeros
                self.assertEqual(out[0x2400:0x2770], ref[0x2400:0x2770])
                if inch:
                    _close(self, out[0x3300:0x4B00], ref[0x3300:0x4B00], 0)
                else:
                    self.assertEqual(out[0x3300:0x4B00], ref[0x3300:0x4B00])
                self.assertEqual(out[0x4B00:0x4B02], ref[0x4B00:0x4B02])
                a, b = struct.unpack_from("<16f", out, 0x5700), struct.unpack_from("<16f", ref, 0x5700)
                for k in range(16):
                    if k not in VIEW:
                        self.assertAlmostEqual(a[k], b[k], places=3, msg=k)
                self.assertEqual(out[0x5740:], ref[0x5740:])

    def test_fresh_view_matches_alpha_t(self):
        ref = (SAMPLES / "AREA1_metric.SDG").read_bytes()
        a = struct.unpack_from("<16f", write_sdg(read_sdg(ref)), 0x5700)
        b = struct.unpack_from("<16f", ref, 0x5700)
        for k in VIEW:
            self.assertAlmostEqual(a[k], b[k], places=3)

    def test_sdt(self):
        for name in ("AREA1_metric", "GROOVE_metric", "DRILL_DWELL_metric", "THREAD_metric"):
            with self.subTest(name):
                project = read_sdg((SAMPLES / f"{name}.SDG").read_bytes())
                self.assertEqual(write_sdt(project), (SAMPLES / f"{name}.SDT").read_bytes())
