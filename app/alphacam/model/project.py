"""Project and machine profile; native file *.alcam (JSON, versioned, DESIGN.md 4.1/4.2)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import serde
from .geometry import ELEMENT_TYPES, Contour, Element, Point
from .operations import OP_KINDS, OPERATION_TYPES, Operation
from .tools import Tool

FORMAT = "alcam/1"
UNITS = ("mm", "inch")


@dataclass
class Billet:
    diameter: float = 100.0
    length: float = 100.0
    face_allowance: float = 0.8


@dataclass
class Reference:
    """G92 machine reference (SDG settings block)."""

    g92_x: float = 163.3803
    g92_z: float = 120.0


@dataclass
class MachineProfile:
    """Post settings for one machine. z_scale/f_scale are DESIGN.md C4: x2 as observed in every ALPHA-T sample,
    unverified on the control."""

    id: str = "harrison-alpha-400t"
    post: str = "alphat_prg"
    z_scale: float = 2.0
    f_scale: float = 2.0
    decimals_mm: int = 3
    decimals_inch: int = 4
    o_number: int = 2
    verified_on_machine: bool = False
    op_kinds: dict[int, str] = field(default_factory=lambda: dict(OP_KINDS))

    def decimals(self, units: str) -> int:
        return self.decimals_mm if units == "mm" else self.decimals_inch


@dataclass
class Project:
    units: str = "mm"
    machine: str = "harrison-alpha-400t"
    billet: Billet = field(default_factory=Billet)
    reference: Reference = field(default_factory=Reference)
    tool_change: Point = field(default_factory=lambda: Point(100, 50))
    program_number: int = 10
    first_tool: int = 1  # tool of the N10 block when no ToolChange op comes first (SDG settings[8])
    tools: list[Tool] = field(default_factory=list)
    geometry: list[Element] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)

    def __post_init__(self):
        if self.units not in UNITS:
            raise ValueError(f"units must be one of {UNITS}, not {self.units!r}")
        for i, g in enumerate(self.geometry):
            if not g.id:
                g.id = f"g{i}"

    def contour(self, ids: list[str]) -> list[Element]:
        return Contour(ids).resolve(self.geometry)

    # --- JSON -----------------------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "units": self.units,
            "machine": self.machine,
            "billet": serde.to_json(self.billet),
            "reference": serde.to_json(self.reference),
            "tool_change": {"x": self.tool_change.x, "z": self.tool_change.z},
            "program_number": self.program_number,
            "first_tool": self.first_tool,
            "tools": serde.to_json(self.tools),
            "geometry": serde.to_json(self.geometry),
            "operations": serde.to_json(self.operations),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        fmt = data.get("format")
        if fmt != FORMAT:
            raise ValueError(f"unsupported project format {fmt!r} (expected {FORMAT!r})")
        return cls(
            units=data.get("units", "mm"),
            machine=data.get("machine", "harrison-alpha-400t"),
            billet=serde.from_json(Billet, data.get("billet", {})),
            reference=serde.from_json(Reference, data.get("reference", {})),
            tool_change=Point.from_json(data.get("tool_change", {"x": 100, "z": 50})),
            program_number=int(data.get("program_number", 10)),
            first_tool=int(data.get("first_tool", 1)),
            tools=[serde.from_json(Tool, t) for t in data.get("tools", [])],
            geometry=[serde.from_json(None, g, ELEMENT_TYPES) for g in data.get("geometry", [])],
            operations=[serde.from_json(None, o, OPERATION_TYPES) for o in data.get("operations", [])],
        )

    def dumps(self) -> str:
        text = json.dumps(self.to_dict(), indent=1)
        # keep [x, z] pairs and id lists on one line
        text = re.sub(r"\[\s+([^\[\]{}]*?)\s+\]", lambda m: "[" + re.sub(r",\s+", ", ", m[1]) + "]", text)
        return text + "\n"

    @classmethod
    def loads(cls, text: str) -> "Project":
        return cls.from_dict(json.loads(text))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.loads(Path(path).read_text(encoding="utf-8"))
