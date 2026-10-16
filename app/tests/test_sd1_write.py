"""The .SD1 listing writer against ALPHA-T's own .SD1 files."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from alphacam.io.sd1 import load_sd1
from alphacam.io.sdg import load_sdg
from alphacam.post.alphat_sd1 import sd1
from tests.test_post import DIFFERS_ON_PURPOSE, GOLDEN, REFUSED, SAMPLES, sample_project

APP = Path(__file__).resolve().parents[1]
# ALPHA-T computes in single precision; on these the listing differs from the app's in the last digit (0.0001)
SINGLE_PRECISION = {"JOB_shaft_metric"}


def _same_listing(test, got: bytes, want: bytes, close: bool) -> None:
    if not close:
        test.assertEqual(got, want)
        return
    import re

    a, b = got.decode().splitlines(), want.decode().splitlines()
    test.assertEqual(len(a), len(b))
    for x, y in zip(a, b):
        nx, ny = re.findall(r"-?[\d.]+", x), re.findall(r"-?[\d.]+", y)
        test.assertEqual(re.sub(r"-?[\d.]+", "#", x), re.sub(r"-?[\d.]+", "#", y))
        for u, v in zip(nx, ny):
            test.assertAlmostEqual(float(u), float(v), delta=0.00011)


class Sd1Writer(unittest.TestCase):
    def test_every_sample_listing_byte_identical(self):
        """SDG + SD1 import -> SD1 writer gives ALPHA-T's bytes back, for every sample with an .SD1."""
        sdgs = [p for p in sorted(SAMPLES.glob("*.SDG")) if p.with_suffix(".SD1").exists() and p.stem not in REFUSED | DIFFERS_ON_PURPOSE]
        self.assertGreaterEqual(len(sdgs), 40)
        for sdg in sdgs:
            with self.subTest(sdg.stem):
                project = load_sdg(sdg)
                project.operations = load_sd1(sdg.with_suffix(".SD1"), project)
                _same_listing(self, sd1(project), sdg.with_suffix(".SD1").read_bytes(), sdg.stem in SINGLE_PRECISION)

    def test_golden_operations(self):
        """The hand-written operation lists give the same listings."""
        for name in GOLDEN:
            ref = SAMPLES / f"{name}.SD1"
            if not ref.exists():
                continue
            with self.subTest(name):
                _same_listing(self, sd1(sample_project(name)), ref.read_bytes(), name in SINGLE_PRECISION)

    def test_examples(self):
        from alphacam.model.project import Project
        from tests.test_preview import EXAMPLE_SAMPLE, EXAMPLES

        for stem, sample in EXAMPLE_SAMPLE.items():
            with self.subTest(stem):
                self.assertEqual(sd1(Project.load(EXAMPLES / f"{stem}.alcam")),
                                 (SAMPLES / f"{sample}.SD1").read_bytes())

    def test_number_of_cuts_mode(self):
        """NO. OF CUTS 6 on a depth-10 thread: first cut 10 / sqrt 6 = 4.0825, 6 passes (THREAD_6cuts)."""
        from alphacam.post.alphat_prg import post

        sdg = SAMPLES / "THREAD_6cuts_metric.SDG"
        project = load_sdg(sdg)
        project.operations = load_sd1(sdg.with_suffix(".SD1"), project)
        project.operations[-1].cut_depth, project.operations[-1].cuts = 1.0, 6
        self.assertEqual(post(project), sdg.with_suffix(".PRG").read_bytes())
        self.assertEqual(sd1(project), sdg.with_suffix(".SD1").read_bytes())

    def test_cli_writes_sd1(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "P.SD1"
            subprocess.run([sys.executable, "-m", "alphacam", "post", str(APP / "examples" / "met2_finish.alcam"),
                            "-o", str(Path(d) / "P.PRG"), "--sd1", str(out)], cwd=APP, check=True)
            self.assertTrue(out.read_bytes().startswith(b"N10F"))
            self.assertTrue(out.read_bytes().endswith(b"M30\r\n"))
