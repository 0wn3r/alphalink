"""The .SD1 listing writer against ALPHA-T's own .SD1 files."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from alphacam.io.sd1 import load_sd1
from alphacam.io.sdg import load_sdg
from alphacam.post.alphat_sd1 import sd1
from tests.test_post import GOLDEN, SAMPLES, sample_project

APP = Path(__file__).resolve().parents[1]


class Sd1Writer(unittest.TestCase):
    def test_every_sample_listing_byte_identical(self):
        """SDG + SD1 import -> SD1 writer gives ALPHA-T's bytes back, for every sample with an .SD1."""
        sdgs = [p for p in sorted(SAMPLES.glob("*.SDG")) if p.with_suffix(".SD1").exists()]
        self.assertGreaterEqual(len(sdgs), 40)
        for sdg in sdgs:
            with self.subTest(sdg.stem):
                project = load_sdg(sdg)
                project.operations = load_sd1(sdg.with_suffix(".SD1"), project)
                self.assertEqual(sd1(project), sdg.with_suffix(".SD1").read_bytes())

    def test_golden_operations(self):
        """The hand-written operation lists give the same listings."""
        for name in GOLDEN:
            ref = SAMPLES / f"{name}.SD1"
            if not ref.exists():
                continue
            with self.subTest(name):
                self.assertEqual(sd1(sample_project(name)), ref.read_bytes())

    def test_examples(self):
        from alphacam.model.project import Project
        from tests.test_preview import EXAMPLE_SAMPLE, EXAMPLES

        for stem, sample in EXAMPLE_SAMPLE.items():
            with self.subTest(stem):
                self.assertEqual(sd1(Project.load(EXAMPLES / f"{stem}.alcam")),
                                 (SAMPLES / f"{sample}.SD1").read_bytes())

    def test_cli_writes_sd1(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "P.SD1"
            subprocess.run([sys.executable, "-m", "alphacam", "post", str(APP / "examples" / "met2_finish.alcam"),
                            "-o", str(Path(d) / "P.PRG"), "--sd1", str(out)], cwd=APP, check=True)
            self.assertTrue(out.read_bytes().startswith(b"N10F"))
            self.assertTrue(out.read_bytes().endswith(b"M30\r\n"))
