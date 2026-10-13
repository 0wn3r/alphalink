"""Project and machine profile; native file *.alcam (JSON, versioned, DESIGN.md 4.1/4.2)."""

from __future__ import annotations

from ..i18n import tr
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import serde
from .geometry import ELEMENT_TYPES, Contour, Element, Point, Text
from .operations import OP_KINDS, OPERATION_TYPES, Operation
from .tools import Tool

FORMAT = "alcam/1"
UNITS = ("mm", "inch")


@dataclass
class Billet:
    diameter: float = 100.0
    length: float = 100.0
    face_allowance: float = 0.8
    bore: float = 0.0  # inner diameter of a tube billet; 0 = solid bar
    jaws: str = "external"  # external (gripping the bar, as ALPHA-T draws) | internal (expanding in the bore)
    tailstock: bool = False  # show the tailstock centre at the front face (ALPHA-T "Display tailstock")


@dataclass
class Reference:
    """G92 machine reference (SDG settings block)."""

    g92_x: float = 163.3803
    g92_z: float = 120.0


@dataclass
class MachineProfile:
    """Post settings for one machine. z_scale/f_scale are DESIGN.md C4: x2 as in every ALPHA-T sample; the T macros
    halve Z and feed (confirmed on the control, RE_NOTES Progress 29)."""

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
    output_units: str | None = None  # units the program is written in; None = the drawing's (ALPHA-T asks on save)
    first_tool: int = 1  # tool of the N10 block when no ToolChange op comes first (SDG settings[8])
    tools: list[Tool] = field(default_factory=list)
    geometry: list[Element] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)

    def __post_init__(self):
        if self.units not in UNITS:
            raise ValueError(tr("units must be one of {units}, not {units2}", units=repr(UNITS), units2=repr(self.units)))
        if self.output_units is not None and self.output_units not in UNITS:
            raise ValueError(tr("units must be one of {units}, not {units2}", units=repr(UNITS),
                                units2=repr(self.output_units)))
        for i, g in enumerate(self.geometry):
            if not g.id:
                g.id = f"g{i}"

    def for_output(self) -> "Project":
        """The project in its output units (itself when they are the drawing's)."""
        if self.output_units in (None, self.units):
            return self
        from .units import convert_units

        return convert_units(self, self.output_units)

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
        } | ({"texts": serde.to_json(self.texts)} if self.texts else {}) \
            | ({"output_units": self.output_units} if self.output_units not in (None, self.units) else {})

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
            output_units=data.get("output_units"),
            first_tool=int(data.get("first_tool", 1)),
            tools=[serde.from_json(Tool, t) for t in data.get("tools", [])],
            geometry=[serde.from_json(None, g, ELEMENT_TYPES) for g in data.get("geometry", [])],
            operations=[serde.from_json(None, _legacy_op(o), OPERATION_TYPES) for o in data.get("operations", [])],
            texts=[serde.from_json(Text, t) for t in data.get("texts", [])],
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


def _legacy_op(o: dict) -> dict:
    """Parts saved before 2026-10-13 have a thread "angle" of 60 (the profile angle, which the post never used); the
    field is now ALPHA-T's infeed angle, where 0 is what those parts posted. Their "spring_pass" yes/no is now a
    count."""
    if o.get("type") == "thread" and o.get("angle") == 60.0:
        o = dict(o, angle=0.0)
    if o.get("type") == "thread" and "spring_pass" in o:  # before 2026-10-13: one spring pass or none
        o = dict(o)
        o["spring_passes"] = int(bool(o.pop("spring_pass")))
    return o
