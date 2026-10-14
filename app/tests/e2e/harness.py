"""End-to-end UI harness: the installed Flatpak in a private, headless GNOME session, driven with real input.

    with GnomeSession(1368, 912) as s:            # Surface Pro 7 landscape at 200 % (logical pixels)
        app = s.launch_app(lang="en")             # flatpak run, waits for the window in the accessibility tree
        s.click(*app.center("Line"))              # real pointer events (Mutter remote desktop API)
        s.tap(400, 300)                           # real touch events
        s.type("40,0\\n")                          # real key events
        s.screenshot("shot.png")                  # the screen through PipeWire (Mutter screen cast API)

Nothing touches the user's session: the run has its own HOME, XDG dirs, D-Bus session bus, PipeWire and GNOME Shell
(`--headless --virtual-monitor`), so the app starts with default settings every time and the user's desktop, keyring
and app settings are left alone. Only the Flatpak installation is shared (read only, FLATPAK_USER_DIR).

Coordinates are screen pixels. Widgets are found by their accessible name (AT-SPI), which GTK takes from the label or
the tooltip, so a test also checks that every control has one."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

APP_ID = "io.github._0wn3r.AlphaLinkCAM"
REAL_FLATPAK = Path.home() / ".local/share/flatpak"
BTN_LEFT = 0x110
# services the private session must not start: the keyring would ask for a password
MASKED_SERVICES = ("org.freedesktop.secrets", "org.gnome.keyring", "org.freedesktop.impl.portal.Secret")


class GnomeSession:
    def __init__(self, width: int, height: int, keep: bool = False):
        self.width, self.height, self.keep = width, height, keep
        self.procs: list[subprocess.Popen] = []
        self.root = Path(tempfile.mkdtemp(prefix="alphalink-e2e-"))
        self.env: dict[str, str] = {}
        self.top = 0  # the top bar's height: a maximized window starts below it

    # --- session -----------------------------------------------------------------------------------------------

    def __enter__(self) -> "GnomeSession":
        r = self.root
        run = r / "run"
        for d in ("home", "config", "data", "cache", "state", "run"):
            (r / d).mkdir(mode=0o700)
        services = r / "data/dbus-1/services"
        services.mkdir(parents=True)
        for name in MASKED_SERVICES:
            (services / f"{name}.service").write_text(f"[D-BUS Service]\nName={name}\nExec=/bin/false\n")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("DBUS_", "WAYLAND_", "DISPLAY", "XDG_", "PIPEWIRE", "GNOME_", "SESSION_"))}
        env.update(HOME=str(r / "home"), XDG_CONFIG_HOME=str(r / "config"), XDG_DATA_HOME=str(r / "data"),
                   XDG_CACHE_HOME=str(r / "cache"), XDG_STATE_HOME=str(r / "state"), XDG_RUNTIME_DIR=str(run),
                   FLATPAK_USER_DIR=str(REAL_FLATPAK), XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="GNOME",
                   GSETTINGS_BACKEND="dconf", NO_AT_BRIDGE="0", GTK_A11Y="atspi")
        # the bus and every service it starts write to a log, not to the test's output
        addr = r / "bus-address"
        with open(r / "dbus.log", "w") as log, open(addr, "w") as out:
            bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"], env=env,
                                   stdout=out, stderr=log, start_new_session=True)
        self.procs.append(bus)
        end = time.time() + 10
        while not addr.read_text().strip() and time.time() < end:
            time.sleep(0.05)
        env["DBUS_SESSION_BUS_ADDRESS"] = addr.read_text().strip()
        self.env = env
        # quiet, predictable desktop: no animations, US keys, default (light) style
        for schema, key, value in (("org.gnome.desktop.interface", "enable-animations", "false"),
                                   ("org.gnome.desktop.interface", "enable-hot-corners", "false"),  # pointer at 0,0
                                   ("org.gnome.desktop.input-sources", "sources", "[('xkb', 'us')]"),
                                   ("org.gnome.desktop.screensaver", "lock-enabled", "false"),
                                   ("org.gnome.desktop.session", "idle-delay", "uint32 0")):
            subprocess.run(["gsettings", "set", schema, key, value], env=env, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for cmd in (["pipewire"], ["wireplumber"]):
            self._spawn(cmd)
            time.sleep(0.5)
        self._spawn(["gnome-shell", "--headless", "--wayland", "--no-x11", "--wayland-display", "alphalink-e2e",
                     "--virtual-monitor", f"{self.width}x{self.height}"], log="shell.log")
        env["WAYLAND_DISPLAY"] = "alphalink-e2e"
        self._apply_env()
        self._wait_name("org.gnome.Mutter.RemoteDesktop", 30)
        from .remote import Remote  # noqa: needs the session's bus in os.environ first

        self.remote = Remote()
        time.sleep(1.0)
        self.key("Escape")  # GNOME starts in the Activities overview
        time.sleep(0.5)
        # the first touch event makes Mutter create its virtual touchscreen and is lost: spend it on the desktop
        self.remote.touch_down(0, self.width / 2, self.height / 2)
        time.sleep(0.1)
        self.remote.touch_up(0)
        time.sleep(0.5)
        return self

    def __exit__(self, *exc) -> None:
        for p in reversed(self.procs):
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)

    def _spawn(self, cmd: list[str], log: str | None = None) -> subprocess.Popen:
        out = open(self.root / (log or f"{cmd[0]}.log"), "w")
        p = subprocess.Popen(cmd, env=self.env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(p)
        return p

    def _apply_env(self) -> None:
        """This process talks to the private session from now on (D-Bus, AT-SPI, PipeWire)."""
        for k in [k for k in os.environ if k.startswith(("DBUS_", "WAYLAND_", "XDG_", "PIPEWIRE"))]:
            del os.environ[k]
        for k in ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"):
            os.environ[k] = self.env[k]

    def _wait_name(self, name: str, timeout: float) -> None:
        end = time.time() + timeout
        while time.time() < end:
            r = subprocess.run(["busctl", "--user", "status", name], env=self.env, capture_output=True)
            if r.returncode == 0:
                return
            time.sleep(0.3)
        raise TimeoutError(f"{name} did not appear; see {self.root / 'shell.log'}")

    # --- the app ------------------------------------------------------------------------------------------------

    def launch_app(self, lang: str = "en", args: tuple[str, ...] = (), maximize: bool = True,
                   env: dict[str, str] | None = None):
        from .a11y import App

        extra = [f"--env={k}={v}" for k, v in (env or {}).items()]
        cmd = ["flatpak", "run", f"--env=ALPHALINK_LANG={lang}", *extra, APP_ID, *args]
        self._spawn(cmd, log=f"app-{lang}.log")
        app = App.wait(timeout=30)
        if maximize:
            self.key("super+Up")
            time.sleep(0.8)
        self.top = self._find_top()
        app.origin = (0, self.top)
        return app

    def _find_top(self) -> int:
        """Height of the top bar: the first row whose colour differs from the bar's (an empty spot of it)."""
        img = self.grab()
        x = int(self.width * 0.3)
        bar = img.getpixel((x, 2))[:3]
        for y in range(3, min(80, img.height)):
            px = img.getpixel((x, y))[:3]
            if sum(abs(a - b) for a, b in zip(px, bar)) > 40:
                return y
        return 0

    # --- input (real events) -------------------------------------------------------------------------------------

    def move(self, x: float, y: float) -> None:
        self.remote.pointer_to(x, y)

    def click(self, x: float, y: float, button: int = BTN_LEFT) -> None:
        # approach in two steps: GTK takes the first event over a widget as entering it, not as a motion
        self.remote.pointer_to(x + 4, y + 4)
        time.sleep(0.08)
        self.remote.pointer_to(x, y)
        time.sleep(0.08)
        self.remote.button(button, True)
        time.sleep(0.05)
        self.remote.button(button, False)
        time.sleep(0.25)

    def tap(self, x: float, y: float) -> None:
        self.remote.touch_down(0, x, y)
        time.sleep(0.06)
        self.remote.touch_up(0)
        time.sleep(0.3)

    def hold(self, x: float, y: float, seconds: float, touch: bool = False) -> None:
        if touch:
            self.remote.touch_down(0, x, y)
            time.sleep(seconds)
            self.remote.touch_up(0)
        else:
            self.remote.pointer_to(x + 4, y + 4)
            time.sleep(0.08)
            self.remote.pointer_to(x, y)
            time.sleep(0.08)
            self.remote.button(BTN_LEFT, True)
            time.sleep(seconds)
            self.remote.button(BTN_LEFT, False)
        time.sleep(0.3)

    def hold_key(self, combo: str, seconds: float) -> None:
        """Hold a key down (a hold-to-start button by keyboard)."""
        from gi.repository import Gdk

        sym = Gdk.keyval_from_name(combo)
        self.remote.keysym(sym, True)
        time.sleep(seconds)
        self.remote.keysym(sym, False)
        time.sleep(0.3)

    def key(self, combo: str) -> None:
        """A key or a combination: "Return", "ctrl+z", "super+Up", "shift+ctrl+s"."""
        self.remote.combo(combo)
        time.sleep(0.15)

    def type(self, text: str) -> None:
        for ch in text:
            self.remote.char(ch)
            time.sleep(0.03)
        time.sleep(0.2)

    # --- output -----------------------------------------------------------------------------------------------------

    def grab(self):
        return self.remote.grab()

    def screenshot(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.grab().save(path)
        return path
