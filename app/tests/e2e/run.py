"""Run the end-to-end tests, each in its own process (one private GNOME session and one accessibility connection
per process; AT-SPI cannot switch buses):

    python3 -m tests.e2e.run                 # all
    python3 -m tests.e2e.run Keyboard Touch  # some classes
    python3 -m tests.e2e.run -j 1            # one at a time (default: 3 at once)
"""

import os
import subprocess
import sys
import unittest

from . import test_interaction, test_surface_reports, test_workflows


def ids(names: list[str]) -> list[str]:
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m)
                               for m in (test_workflows, test_interaction, test_surface_reports))
    out = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                out.append(t.id())

    walk(suite)
    out = list(dict.fromkeys(out))  # classes imported into a second module are listed once
    return [i for i in out if not names or any(f".{n}." in i or i.endswith(f".{n}") for n in names)]


def main() -> int:
    """`-j N` runs N tests at once (each has its own GNOME session; default 3). A test that fails is run once more on
    its own; if it passes then, it counts as passed but is listed as flaky (a busy machine, not the app)."""
    from concurrent.futures import ThreadPoolExecutor

    args = sys.argv[1:]
    jobs = 3
    if "-j" in args:
        i = args.index("-j")
        jobs = int(args[i + 1])
        del args[i:i + 2]
    env = dict(os.environ, ALPHALINK_E2E="1")
    tests = ids(args)

    def one(test):
        r = subprocess.run([sys.executable, "-m", "unittest", test], env=env, capture_output=True, text=True)
        return test, r

    failed, flaky = [], []
    with ThreadPoolExecutor(jobs) as pool:
        for i, (test, r) in enumerate(pool.map(one, tests), 1):
            if r.returncode:
                _, again = one(test)
                if not again.returncode:
                    flaky.append(test)
                    r = again
            tail = r.stderr.strip().splitlines()[-1:] if r.stderr else []
            print(f"[{i}/{len(tests)}] {test}\n    {tail[0] if tail else '?'}{' (on the 2nd try)' if test in flaky else ''}",
                  flush=True)
            if r.returncode:
                failed.append(test)
                print("\n".join("    " + line for line in r.stderr.splitlines()[-25:]), flush=True)
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed"
          + (f", {len(flaky)} flaky (passed on the 2nd try): " + " ".join(t.split(".", 3)[-1] for t in flaky)
             if flaky else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
