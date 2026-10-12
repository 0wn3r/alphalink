"""Contour helpers: chain picked geometry into an ordered point list."""

from __future__ import annotations

import math

from ..model.geometry import Arc, Element, Line, Point

COS45 = math.cos(math.radians(45))
MM_PER_INCH = 25.4


def chain(elements: list[Element]) -> list[Point]:
    """Points of a picked contour, start to end. Elements may be stored in either direction. Lines only for now
    (no ALPHA-T PRG sample with an arc yet)."""
    if not elements:
        raise ValueError("empty contour")
    for e in elements:
        if isinstance(e, Arc):
            raise NotImplementedError("arcs in a contour are not supported by the post yet")
    first = elements[0]
    if len(elements) > 1 and first.a in (elements[1].a, elements[1].b):
        pts = [first.b, first.a]
    else:
        pts = [first.a, first.b]
    for e in elements[1:]:
        if e.a == pts[-1]:
            pts.append(e.b)
        elif e.b == pts[-1]:
            pts.append(e.a)
        else:
            raise ValueError(f"contour is not connected at X{pts[-1].x:g} Z{pts[-1].z:g} ({e.id})")
    return pts


def mm(value_mm: float, units: str) -> float:
    """A length given in mm (tool data, ALPHA-T constants) in project units."""
    return value_mm / MM_PER_INCH if units == "inch" else value_mm


def leg45(length: float, decimals: int) -> float:
    """Radial (= Z) component of a 45-degree move of `length`, rounded to the output decimals first. ALPHA-T
    rounds this component before adding it (MET2: lead-out 0.2 -> 0.141, so X100.282 Z-49.859, not 100.283)."""
    return round(length * COS45, decimals)


def limits(points: list[Point], nose_radius: float) -> tuple[float, float]:
    """#125/#126 (SD1 G115): contour minimum X - 4r (diameter) and minimum Z - 2r (MET2, AREA1, V1.81 MET2)."""
    return min(p.x for p in points) - 4 * nose_radius, min(p.z for p in points) - 2 * nose_radius
