"""User preferences (ALPHA-T's ATSETUP), kept in ~/.config/alphalink-cam/settings.json.

Read before the GTK modules build their widgets (the language must be known before the first string is
translated), written whenever a value changes."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "serial_port": "",  # "" = first port found
    "char_delay_ms": 2.0,
    "wait_xon": True,
    "input_layout": "auto",  # auto | pointer | touch
    "units": "mm",
    "billet_diameter": 50.0,
    "billet_length": 80.0,
    "first_tool": 3,
    "language": "",  # "" = follow the desktop; "en"; "uk"
    "show_grid": True,
    "export_sdg": False,  # Export PRG also writes the ALPHA-T drawing (.SDG + .SDT) next to the program
    "export_dir": "",  # where the last program was exported (a USB stick, say): the next export starts there
    "spindle_max": 2500.0,  # rpm, for the cycle time estimate
    "rapid_rate": 6000.0,  # mm/min
    "tool_change_s": 30.0,  # seconds the operator needs to change a tool
    "soft_parity": False,  # Bluetooth adapters with 8-bit framing only: 8N2 on the adapter, parity made by the app
}


def _path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "alphalink-cam" / "settings.json"


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or _path()
        self.values = dict(DEFAULTS)
        try:
            data = json.loads(self.path.read_text())
            self.values.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError, AttributeError):  # missing, damaged, or not an object: the defaults
            pass

    def __getitem__(self, key: str):
        return self.values[key]

    def set(self, key: str, value) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        if self.values.get(key) == value:
            return
        self.values[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, indent=1) + "\n")
            tmp.replace(self.path)
        except OSError:
            pass  # preferences are a convenience; never fail an action because they could not be saved


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
