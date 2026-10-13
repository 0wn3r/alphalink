"""Cutting tools and the tool library."""

from __future__ import annotations

from dataclasses import dataclass, field

TOOL_TYPES = ("turn", "groove", "thread", "drill", "bore")


@dataclass
class Tool:
    number: int
    name: str = ""
    type: str = "turn"
    nose_radius: float = 0.0
    angle: float = 0.0
    width: float = 0.0  # grooving tip width
    hand: str = "R"  # R, L or N
    default_feed: float = 0.15
    front_angle: float = 0.0  # clearance angles from the Z axis, as in ATTOOLS (tip angle = front - rear;
    rear_angle: float = 0.0  # negative = internal / boring)
    description: str = ""

    def __post_init__(self):
        if self.type not in TOOL_TYPES:
            raise ValueError(f"unknown tool type {self.type!r}")
        if self.hand not in ("R", "L", "N"):
            raise ValueError(f"tool hand must be R, L or N, not {self.hand!r}")


@dataclass
class ToolLibrary:
    tools: list[Tool] = field(default_factory=list)

    def get(self, number: int) -> Tool:
        for tool in self.tools:
            if tool.number == number:
                return tool
        raise KeyError(f"no tool {number}")


# ALPHA-T / ALPHACAM standard library ATOOLIB (data/atoolib_v181_tools.csv): number, name, nose radius in mm.
_ATOOLIB = [  # ALPHA-T's ATOOLIB.TL1 (vendor/AlphaT): number, name, nose radius (mm), front / rear clearance angles
    (1, "80 Deg Rougher 0.8R", 0.8, 87, 7), (2, "60 Deg Rougher 0.8R", 0.8, 87, 27),
    (3, "55 Deg Finisher 0.4R", 0.4, 87, 31), (4, "35 Deg Finisher 0.4R", 0.4, 87, 51),
    (5, "80 L.H. Rougher 0.8R", 0.8, 93, 173), (6, "55 L.H.Finisher 0.4R", 0.4, 93, 149),
    (7, "35 L.H.Finisher 0.4R", 0.4, 93, 129), (8, "60 Deg Neutral Tool", 0.8, 120, 60),
    (9, "55 Deg Neutral tool", 0.4, 118, 62), (10, "35 Deg Neutral Tool", 0.0, 108, 72),
    (11, "80 Boring-Bar 0.8R", 0.8, -87, -7), (12, "55 Boring-Bar 0.4R", 0.4, -87, -31),
    (13, "35 Boring-Bar 0.4R", 0.4, -72, -36), (14, "2mm Internal Groove", 0.0, 0, 0),
    (15, "3mm Internal Groove", 0.0, 0, 0), (16, "2mm Groove/Parting", 0.0, 0, 0),
    (17, "4mm Groove/Parting", 0.0, 0, 0), (18, "5mm Groove/Parting", 0.0, 0, 0),
    (19, "55 Deg Face Neutral", 0.4, 28, -28), (20, "35 Deg Face Neutral", 0.4, 18, -18),
    (21, "3mm Face Grooving.", 0.2, 0, 0), (22, "5mm Ball-End Facing", 2.5, 0, 0),
    (23, "5mm Ball-End Profile", 2.5, 0, 0), (24, "25mm Drill", 0.0, 0, 0), (25, "20mm Drill", 0.0, 0, 0),
    (26, "15mm Drill", 0.0, 0, 0), (27, "10mm Drill", 0.0, 0, 0), (28, "5mm Drill", 0.0, 0, 0),
    (29, "4mm Drill", 0.0, 0, 0), (30, "2mm Drill", 0.0, 0, 0), (31, "Threading Tool- Ext.", 0.0, 0, 0),
    (32, "Threading Tool- Int.", 0.0, 0, 0),
]


def _tool_from_name(number: int, name: str, nose_radius: float, front: float = 0.0, rear: float = 0.0,
                    description: str = "") -> Tool:
    low = name.lower()
    ttype = ("groove" if "groov" in low or "parting" in low else "bore" if "boring" in low
             else "drill" if "drill" in low else "thread" if "thread" in low else "turn")
    if ttype == "turn" and front < 0:
        ttype = "bore"
    hand = "L" if "l.h." in low else "N" if "neutral" in low else "R"
    angle = next((float(w) for w in name.split() if w.isdigit()), 0.0)
    digits = name.split("mm")[0]
    width = float(digits) if ttype in ("groove", "drill") and "mm" in name and digits.isdigit() else 0.0
    return Tool(number, name, ttype, nose_radius=nose_radius, angle=angle, width=width, hand=hand,
                front_angle=front, rear_angle=rear, description=description)


def tool_from_record(number: int, name: str, nose_radius: float, front: float, rear: float,
                     description: str = "") -> Tool:
    """A tool from an ATTOOLS record (name, radius, clearance angles): type and hand guessed from the name."""
    return _tool_from_name(number, name, nose_radius, front, rear, description)


def standard_library() -> ToolLibrary:
    """The ATOOLIB tools ALPHA-T ships with. Nose radii are in mm."""
    return ToolLibrary([_tool_from_name(*t) for t in _ATOOLIB])


def library_to_json(lib: ToolLibrary) -> dict:
    from .serde import to_json

    return {"format": "alcam-tools/1", "tools": [to_json(t) for t in sorted(lib.tools, key=lambda t: t.number)]}


def library_from_json(data: dict) -> ToolLibrary:
    from .serde import from_json

    if data.get("format") != "alcam-tools/1":
        raise ValueError(f"unsupported tool library format {data.get('format')!r}")
    return ToolLibrary([from_json(Tool, t) for t in data.get("tools", [])])
