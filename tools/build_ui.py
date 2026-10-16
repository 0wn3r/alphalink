#!/usr/bin/env python3
"""Compile app/alphacam_gtk/ui/*.blp (Blueprint) to the .ui files next to them, which the app loads (alphacam_gtk/ui.py).
Run after editing a .blp, then tools/update_translations.py when strings changed; commit both files.

Needs blueprint-compiler (`pip install blueprint-compiler`, or the distribution's package) and the GTK 4 and libadwaita
introspection data of the GNOME runtime the app targets; BLUEPRINT_COMPILER names another command to run. Each .ui
records the hash of its .blp, so tests/test_ui.py notices a .blp edited without recompiling, without the compiler.

Run from anywhere: python3 tools/build_ui.py"""

import hashlib
import os
import shlex
import subprocess
import sys
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "alphacam_gtk" / "ui"
STAMP = "<!-- blueprint source sha256: {} -->\n"


def source_hash(blp: Path) -> str:
    return hashlib.sha256(blp.read_bytes()).hexdigest()


def main() -> int:
    cmd = shlex.split(os.environ.get("BLUEPRINT_COMPILER", "blueprint-compiler"))
    failed = []
    for blp in sorted(UI.glob("*.blp")):
        res = subprocess.run(cmd + ["compile", str(blp)], capture_output=True, text=True)
        if res.returncode != 0:
            print(res.stdout + res.stderr, file=sys.stderr)
            failed.append(blp.name)
            continue
        xml = res.stdout
        head, sep, rest = xml.partition("\n")  # after the <?xml ...?> declaration
        blp.with_suffix(".ui").write_text(head + sep + STAMP.format(source_hash(blp)) + rest)
    stale = sorted(ui.name for ui in UI.glob("*.ui") if not ui.with_suffix(".blp").exists())
    if stale:
        print("no .blp for:", *stale, file=sys.stderr)
    if failed:
        print("not compiled:", *failed, file=sys.stderr)
        return 1
    print("interface files up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
