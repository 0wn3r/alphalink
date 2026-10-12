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
_ATOOLIB = [
    (1, "80 Deg Rougher 0.8R", 0.8), (2, "60 Deg Rougher 0.8R", 0.8), (3, "55 Deg Finisher 0.4R", 0.4),
    (4, "35 Deg Finisher 0.4R", 0.4), (5, "80 L.H. Rougher 0.8R", 0.8), (6, "55 L.H.Finisher 0.4R", 0.4),
    (7, "35 L.H.Finisher 0.4R", 0.4), (8, "60 Deg Neutral Tool", 0.8), (9, "55 Deg Neutral tool", 0.4),
    (10, "35 Deg Neutral Tool", 0.0), (11, "80 Boring-Bar 0.8R", 0.8), (12, "55 Boring-Bar 0.4R", 0.4),
    (13, "35 Boring-Bar 0.4R", 0.4), (14, "2mm Internal Groove", 0.0), (15, "3mm Internal Groove", 0.0),
    (16, "2mm Groove/Parting", 0.0), (17, "4mm Groove/Parting", 0.0), (18, "5mm Groove/Parting", 0.0),
    (19, "55 Deg Face Neutral", 0.4), (20, "35 Deg Face Neutral", 0.4), (21, "3mm Face Grooving.", 0.0),
    (22, "5mm Ball-End Facing", 2.5), (23, "5mm Ball-End Profile", 2.5),
]


def _tool_from_name(number: int, name: str, nose_radius: float) -> Tool:
    low = name.lower()
    ttype = "groove" if "groov" in low or "parting" in low else "bore" if "boring" in low else "turn"
    hand = "L" if "l.h." in low else "N" if "neutral" in low else "R"
    angle = next((float(w) for w in name.split() if w.isdigit()), 0.0)
    width = float(name[0]) if ttype == "groove" and name[0].isdigit() else 0.0
    return Tool(number, name, ttype, nose_radius=nose_radius, angle=angle, width=width, hand=hand)


def standard_library() -> ToolLibrary:
    """The ATOOLIB tools ALPHA-T ships with. Nose radii are in mm."""
    return ToolLibrary([_tool_from_name(*t) for t in _ATOOLIB])
