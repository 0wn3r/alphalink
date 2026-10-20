"""Parts for the end-to-end tests without the old Python app: write an .alcam the Rust app opens, read back what it
saved, and get the program it must send from the Rust command line tool (`alphacam-cli post`).

The .alcam format is JSON ("format": "alcam/1"); only what the tests use is modelled here. Saved files are read back
as plain objects whose fields are the JSON keys (`op.depth_of_cut`, `g.a.x`), with the type names the tests compare
(`AreaClear`, `FinishPass`, ...)."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Point:
    x: float
    z: float

    def distance(self, other: "Point") -> float:
        """Radial distance: X is a diameter, so half the X difference."""
        return math.hypot((self.x - other.x) / 2, self.z - other.z)


@dataclass
class Line:
    a: Point
    b: Point
    id: str = ""

    def to_json(self) -> dict:
        return {"type": "line", "a": [self.a.x, self.a.z], "b": [self.b.x, self.b.z], "id": self.id}


@dataclass
class Arc:
    a: Point
    b: Point
    centre: Point
    ccw: bool = True
    id: str = ""

    def radius(self) -> float:
        return self.a.distance(self.centre)

    def to_json(self) -> dict:
        return {"type": "arc", "a": [self.a.x, self.a.z], "b": [self.b.x, self.b.z],
                "centre": [self.centre.x, self.centre.z], "ccw": self.ccw, "id": self.id}


@dataclass
class Billet:
    diameter: float = 100.0
    length: float = 100.0


# the operations' JSON "type" -> the class names the tests compare
OP_TYPES = {"area_clear": "AreaClear", "finish": "FinishPass", "face_clear": "FaceClear", "groove": "Groove",
            "thread": "Thread", "drill": "Drill", "tool_change": "ToolChange", "dwell": "Dwell", "move": "MoveTo"}
# the + menu's operations, in order (the app's OP_MENU)
OP_MENU = list(OP_TYPES)


class Op:
    """An operation as JSON, read as attributes; `type(op).__name__` is its class name (see `op()`)."""

    def __init__(self, data: dict):
        self.__dict__.update(data)

    def to_json(self) -> dict:
        return dict(self.__dict__)


def op(data: dict) -> Op:
    return type(OP_TYPES.get(data["type"], "Op"), (Op,), {})(data)


def _geometry(g: dict):
    pt = lambda v: Point(*v)
    if g["type"] == "arc":
        return Arc(pt(g["a"]), pt(g["b"]), pt(g["centre"]), g.get("ccw", True), g.get("id", ""))
    return Line(pt(g["a"]), pt(g["b"]), g.get("id", ""))


@dataclass
class Project:
    billet: Billet = field(default_factory=Billet)
    first_tool: int = 3
    units: str = "mm"  # "mm" or "inch"
    program_number: int = 10
    geometry: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    setups: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # every other key of a loaded file, written back unchanged

    def to_json(self) -> dict:
        data = {"format": "alcam/1", "units": self.units, "machine": "harrison-alpha-400t",
                "billet": {"diameter": float(self.billet.diameter), "length": float(self.billet.length),
                           "face_allowance": 0.8, "bore": 0.0, "jaws": "external", "tailstock": False},
                "reference": {"g92_x": 163.3803, "g92_z": 120.0}, "tool_change": {"x": 100.0, "z": 50.0},
                "program_number": self.program_number, "first_tool": self.first_tool, "tools": []}
        data.update(self.extra)
        data["geometry"] = [g.to_json() for g in self.geometry]
        data["operations"] = [o.to_json() for o in self.operations]
        if self.setups:
            data["setups"] = [s.to_json() for s in self.setups]
        return data

    def save(self, path: str | Path) -> Path:
        Path(path).write_text(json.dumps(self.to_json(), indent=1))
        return Path(path)

    @classmethod
    def from_json(cls, data: dict) -> "Project":
        known = {"billet", "first_tool", "units", "program_number", "geometry", "operations", "setups"}
        b = data.get("billet", {})
        return cls(billet=Billet(b.get("diameter", 100.0), b.get("length", 60.0)),
                   first_tool=data.get("first_tool", 3), units=data.get("units", "mm"), program_number=data.get("program_number", 10),
                   geometry=[_geometry(g) for g in data.get("geometry", [])],
                   operations=[op(o) for o in data.get("operations", [])],
                   setups=[cls.from_json(s) for s in data.get("setups", [])],
                   extra={k: v for k, v in data.items() if k not in known})

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_json(json.loads(Path(path).read_text()))


def default_op(kind: str, p: Project, index: int = 0) -> Op:
    """A new operation of `kind` as the app makes it (the + menu), on the part's whole profile (the test parts are
    one outer profile drawn front to back)."""
    contour = [g.id for g in p.geometry]
    start = [float(p.billet.diameter) + 20.0, 5.0]
    t = p.first_tool
    shapes = {
        "area_clear": {"tool": t, "start": start, "contour": contour, "depth_of_cut": 2.0, "offset_x": 0.0,
                       "offset_z": 0.0, "feed": 0.15, "towards": True, "pre_finish": False, "external": True,
                       "front": True, "retract": None, "follow": False},
        "finish": {"tool": t, "start": start, "contour": contour, "feed": 0.15, "offset_x": 0.0, "offset_z": 0.0,
                   "external": True, "face": False},
        "face_clear": {"tool": t, "start": start, "contour": contour, "depth_of_cut": 2.0, "offset_x": 0.0,
                       "offset_z": 0.0, "feed": 0.15, "towards": True, "pre_finish": False},
        "tool_change": {"tool": t, "change_point": [100.0, 50.0], "feed": 0.15},
    }
    return op({"type": kind, **shapes[kind]})


APP_ID = "io.github._0wn3r.AlphaLinkCAM"


def cli() -> list[str]:
    """The Rust command line tool (binary `alphacam`, crate alphacam-cli): $ALPHALINK_CLI, `alphacam` on the PATH, the
    repo's cargo build (built when missing), or the installed Flatpak's."""
    found = os.environ.get("ALPHALINK_CLI") or shutil.which("alphacam")
    if found:
        return [found]
    rust = Path(__file__).resolve().parents[3] / "rust"
    exe = rust / "target/release/alphacam"
    if not exe.exists() and (rust / "Cargo.toml").exists() and shutil.which("cargo"):
        subprocess.run(["cargo", "build", "--release", "-q", "-p", "alphacam-cli"], cwd=rust, check=True)
    if exe.exists():
        return [str(exe)]
    # the installed Flatpak's binary, run on the host: it needs only libc, and `flatpak run` would not see the
    # host's /tmp, where the tests keep their files
    where = subprocess.run(["flatpak", "info", "--show-location", APP_ID], capture_output=True, text=True).stdout.strip()
    exe = Path(where) / "files/bin/alphacam"
    if where and subprocess.run([str(exe), "--help"], capture_output=True).returncode == 0:
        return [str(exe)]
    raise FileNotFoundError("no alphacam: set ALPHALINK_CLI, build rust/ with cargo or install the Flatpak")


def post(p: Project) -> bytes:
    """The ALPHA-T program for the part, as the Rust core writes it."""
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "part.alcam", Path(d) / "PART.PRG"
        p.save(src)
        subprocess.run([*cli(), "post", str(src), "-o", str(out)], check=True, capture_output=True)
        return out.read_bytes()


def has_sdg() -> bool:
    """The command line tool converts ALPHA-T drawings (`alphacam sdg`)."""
    try:
        return subprocess.run([*cli(), "sdg", "--help"], capture_output=True).returncode == 0
    except OSError:
        return False


def sdg(src: str | Path, out: str | Path) -> Path:
    """`alphacam sdg`: .SDG to .alcam, .alcam to .SDG, or .SDG to .SDG through the app's own reader and writer (an
    .SDG comes with its .SDT)."""
    subprocess.run([*cli(), "sdg", str(src), "-o", str(out)], check=True, capture_output=True)
    return Path(out)


def manops_load_bytes(data: bytes) -> bytes:
    """What a Manual Ops restore sends (ALPHA-T's ATMANOP): even parity in bit 7, bytes above 0x7F become 0."""
    def even(b: int) -> int:
        if b > 0x7F:
            return 0
        return b | 0x80 if bin(b).count("1") % 2 else b
    return bytes(even(b) for b in data)
