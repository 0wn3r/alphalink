#!/usr/bin/env python3
"""Export the post cases for the Rust port's tests (rust/alphacam/tests/post.rs).

Each case is a project saved as .alcam plus what the Python post gives for it: the ALPHA-T sample when the output
equals it (most cases), a stored .PRG when it differs on purpose or by a known small amount, or the error class
when the post refuses it. The Rust post must give the same bytes or the same error class.

    python3 tools/export_rust_fixtures.py   # from the repository root; rewrites rust/alphacam/tests/fixtures/post
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "app" / "tests")]

from alphacam.io.sd1 import load_sd1  # noqa: E402
from alphacam.io.sdg import load_sdg  # noqa: E402
from alphacam.model import Project  # noqa: E402
from alphacam.post.alphat_prg import post  # noqa: E402
import test_post as golden  # noqa: E402

OUT = ROOT / "rust" / "alphacam" / "tests" / "fixtures" / "post"


def cases():
    """(case name, project, ALPHA-T's program or None)."""
    samples = ROOT / "samples"
    for name, ops in golden.GOLDEN.items():
        p = load_sdg(samples / "alphat_v12" / f"{name}.SDG")
        p.operations = list(ops)
        yield f"golden_{name}", p, samples / "alphat_v12" / f"{name}.PRG"
    for name, ops in {**golden.GOLDEN_INCH, **golden.INCH_KNOWN_DIFF}.items():
        p = load_sdg(samples / "alphat_v12_inch" / f"{name}.SDG")
        p.operations = list(ops)
        yield f"golden_{name}", p, samples / "alphat_v12_inch" / f"{name}.PRG"
    for folder in ("alphat_v12", "alphat_v12_inch", "alphat_units"):
        for sdg in sorted((samples / folder).glob("*.SDG")):
            if not sdg.with_suffix(".SD1").exists():
                continue
            p = load_sdg(sdg)
            p.operations = load_sd1(sdg.with_suffix(".SD1"), p)
            yield f"sd1_{sdg.stem}", p, sdg.with_suffix(".PRG")
            if folder == "alphat_units":  # the program in the other units, as ALPHA-T stored it
                p = load_sdg(sdg)
                p.operations = load_sd1(sdg.with_suffix(".SD1"), p)
                p.output_units = "inch" if p.units == "mm" else "mm"
                yield f"units_{sdg.stem}", p, sdg.with_suffix(".PRG")
    for example in sorted((ROOT / "app" / "examples").glob("*.alcam")):
        yield f"example_{example.stem}", Project.load(example), None


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    manifest = {}
    for name, project, sample in cases():
        text = project.dumps()
        (OUT / f"{name}.alcam").write_text(text)
        project = Project.loads(text)
        try:
            out = post(project)
        except (ValueError, NotImplementedError, KeyError) as e:
            manifest[name] = {"error": type(e).__name__}
            continue
        if sample is not None and sample.exists() and sample.read_bytes() == out:
            manifest[name] = {"expected": str(sample.relative_to(ROOT))}
        else:
            (OUT / f"{name}.PRG").write_bytes(out)
            manifest[name] = {"expected": str((OUT / f"{name}.PRG").relative_to(ROOT)), "alpha_t": sample is not None}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    stored = sum(1 for v in manifest.values() if "expected" in v and "fixtures" in v["expected"])
    errors = sum(1 for v in manifest.values() if "error" in v)
    print(f"{len(manifest)} cases: {stored} stored programs, {errors} refused")


if __name__ == "__main__":
    main()
