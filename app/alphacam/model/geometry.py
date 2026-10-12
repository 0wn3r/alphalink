"""2D lathe geometry in the XZ half-plane.

X is a **diameter**, Z is 0 at the finished face and negative into the part (ALPHA-T convention, DESIGN.md 4.1)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    x: float  # diameter
    z: float

    def __post_init__(self):
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "z", float(self.z))

    def to_json(self) -> list[float]:
        return [self.x, self.z]

    @classmethod
    def from_json(cls, data) -> "Point":
        """Accepts [x, z] or {"x": .., "z": ..}."""
        if isinstance(data, dict):
            return cls(data["x"], data["z"])
        x, z = data
        return cls(x, z)

    def distance(self, other: "Point") -> float:
        # X is a diameter, so the radial distance is half the X difference.
        return math.hypot((self.x - other.x) / 2, self.z - other.z)


@dataclass
class Line:
    a: Point
    b: Point
    id: str = ""
    type = "line"

    def length(self) -> float:
        return self.a.distance(self.b)


@dataclass
class Arc:
    a: Point
    b: Point
    centre: Point
    ccw: bool = True
    id: str = ""
    type = "arc"

    def radius(self) -> float:
        return self.a.distance(self.centre)


Element = Line | Arc
ELEMENT_TYPES: dict[str, type] = {"line": Line, "arc": Arc}


@dataclass
class Contour:
    """An ordered chain of geometry element ids (as picked in ALPHA-T)."""

    elements: list[str]

    def resolve(self, geometry: list[Element]) -> list[Element]:
        by_id = {g.id: g for g in geometry}
        missing = [e for e in self.elements if e not in by_id]
        if missing:
            raise KeyError(f"contour references unknown geometry: {', '.join(missing)}")
        return [by_id[e] for e in self.elements]
