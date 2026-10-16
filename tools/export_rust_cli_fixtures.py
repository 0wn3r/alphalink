#!/usr/bin/env python3
"""Export the command line cases for the Rust port's tests (rust/alphacam-cli/tests/cli.rs).

Runs `python -m alphacam` (app/alphacam/__main__.py) from the repository root on a few samples and records its exit
status, stdout, stderr and the files it writes: `dump` of every ALPHA-T sample .PRG, `dump --moves` (also with other
scales) of a few, `dump` of a file that is not a program and of one that does not exist, and `post -o --sd1` of a few
post fixtures. In the arguments `{out}` stands for a scratch directory. The Rust tests need no Python.

    python3 tools/export_rust_cli_fixtures.py   # from the repository root; rewrites rust/alphacam-cli/tests/fixtures
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rust" / "alphacam-cli" / "tests" / "fixtures"
POST = "rust/alphacam/tests/fixtures/post"


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def cases() -> dict[str, list[str]]:
    prgs = [rel(p) for p in sorted((ROOT / "samples").rglob("*")) if p.suffix.upper() == ".PRG"]
    few = ["samples/alphat_v12/THREAD_spring1_metric.PRG", "samples/alphat_v12/DRILL_fullret_metric.PRG",
           "samples/alphat_v12/MET2_finish_inch.PRG", "samples/alphat_v12/FINISH_relief_groove_metric.PRG"]
    out = {
        "dump_all_samples": ["dump", *prgs],
        "dump_moves": ["dump", "--moves", *few],
        "dump_moves_scale_1": ["dump", "--moves", "--z-scale", "1", "--f-scale", "1", few[0]],
        "dump_errors": ["dump", few[1], "samples/alphat_v12/no_such_file.PRG",
                        "rust/alphacam/tests/fixtures/post/example_area_clear.alcam", few[2]],
    }
    for name in ("example_area_clear", "example_thread", "example_groove", "example_drill"):
        out[f"post_sd1_{name}"] = ["post", f"{POST}/{name}.alcam", "-o", "{out}/OUT.PRG", "--sd1", "{out}/OUT.SD1"]
    out["post_sd1_stdout_z1"] = ["post", f"{POST}/example_rough_and_finish.alcam", "--sd1", "{out}/OUT.SD1",
                                 "--z-scale", "1", "--f-scale", "1"]
    return out


def run(args: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        argv = [a.replace("{out}", tmp) for a in args]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "app"))
        p = subprocess.run([sys.executable, "-m", "alphacam", *argv], cwd=ROOT, env=env, capture_output=True)
        assert b"Traceback" not in p.stderr, p.stderr.decode()
        files = {f.name: f.read_bytes().decode("ascii") for f in sorted(Path(tmp).iterdir())}
    return {"args": args, "status": p.returncode, "stdout": p.stdout.decode("ascii"),
            "stderr": p.stderr.decode("ascii"), "files": files}


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    out = {name: run(args) for name, args in cases().items()}
    (OUT / "cases.json").write_text(json.dumps(out, indent=1) + "\n")
    print("cli cases", len(out))


if __name__ == "__main__":
    main()
