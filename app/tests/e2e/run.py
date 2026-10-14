"""Run the end-to-end tests, each in its own process (one private GNOME session and one accessibility connection
per process; AT-SPI cannot switch buses):

    python3 -m tests.e2e.run                 # all
    python3 -m tests.e2e.run Keyboard Touch  # some classes
"""

import os
import subprocess
import sys
import unittest

from . import test_workflows


def ids(names: list[str]) -> list[str]:
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_workflows)
    out = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                out.append(t.id())

    walk(suite)
    return [i for i in out if not names or any(f".{n}." in i or i.endswith(f".{n}") for n in names)]


def main() -> int:
    env = dict(os.environ, ALPHALINK_E2E="1")
    failed = []
    tests = ids(sys.argv[1:])
    for i, test in enumerate(tests, 1):
        print(f"[{i}/{len(tests)}] {test}", flush=True)
        r = subprocess.run([sys.executable, "-m", "unittest", test], env=env, capture_output=True, text=True)
        tail = r.stderr.strip().splitlines()[-1:] if r.stderr else []
        print("    " + (tail[0] if tail else "?"), flush=True)
        if r.returncode:
            failed.append(test)
            print("\n".join("    " + line for line in r.stderr.splitlines()[-25:]), flush=True)
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
