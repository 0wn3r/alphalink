# Keep the tests away from the user's real preferences, tool library and desktop.
import atexit
import os
import shutil
import subprocess
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="alphalink-test-config-")
os.environ.pop("ALPHALINK_LANG", None)


def _headless_display() -> None:
    """The GTK tests open real windows. Run them on a private Xvfb display so nothing appears on the user's desktop
    (and focus is not stolen). ALPHALINK_TEST_DISPLAY=1 keeps the current display, e.g. to watch the tests."""
    if os.environ.get("ALPHALINK_TEST_DISPLAY") == "1" or not shutil.which("Xvfb"):
        return
    r, w = os.pipe()
    proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", "1400x900x24", "-nolisten", "tcp"],
                            pass_fds=(w,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    with os.fdopen(r) as f:
        number = f.readline().strip()  # Xvfb writes the display number once it is ready
    if not number:
        proc.kill()
        return
    atexit.register(proc.terminate)
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ["DISPLAY"] = f":{number}"
    os.environ["GDK_BACKEND"] = "x11"
    os.environ.setdefault("GSK_RENDERER", "cairo")  # no GL on Xvfb (avoids libEGL DRI3 warnings)


_headless_display()
