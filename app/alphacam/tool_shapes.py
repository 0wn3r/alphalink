"""The tool's shape for the simulation: ALPHA-T's own picture when the library was imported with its ATOOLIB.TL2,
otherwise a simple shape from the tool's type, clearance angles, width and nose radius. Polylines of (Z, radius)
points in mm with the tip at (0, 0), the way ALPHA-T draws them: +Z right, the radius up. Turning tools hang down from
above the part, boring bars and face tools reach in along +Z."""

from __future__ import annotations

import math


def _circle(cz: float, cr: float, r: float, steps: int = 24) -> list[tuple[float, float]]:
    return [(cz + r * math.cos(2 * math.pi * k / steps), cr + r * math.sin(2 * math.pi * k / steps))
            for k in range(steps + 1)]


def _bar(z0: float, r0: float, r1: float, length: float = 50.0) -> list[tuple[float, float]]:
    """A bar along +Z from z0, between the radii r0 and r1."""
    return [(z0, r0), (z0 + length, r0), (z0 + length, r1), (z0, r1), (z0, r0)]


def _shank(z0: float, z1: float, r0: float, height: float = 35.0) -> list[tuple[float, float]]:
    """A shank going up from the radius r0, between z0 and z1."""
    return [(z0, r0), (z0, r0 + height), (z1, r0 + height), (z1, r0)]


def internal(tool) -> bool:
    name = tool.name.lower()
    return tool.type == "bore" or tool.front_angle < 0 or "internal" in name or "int." in name


def outline(tool) -> list[list[tuple[float, float]]]:
    if tool is None:
        return []
    if getattr(tool, "outline", None):
        return [[(float(z), float(x)) for z, x in line] for line in tool.outline]
    inside = internal(tool)
    if tool.type == "drill":
        r = (tool.width or 10.0) / 2
        tip = r * math.tan(math.radians(31))  # 118 degree point
        return [[(0.0, 0.0), (tip, r), (60.0, r), (60.0, -r), (tip, -r), (0.0, 0.0)]]
    if tool.type == "groove":
        w = tool.width or 3.0
        if inside:
            return [[(0.0, 0.0), (0.0, -6.0), (w, -6.0), (w, 0.0), (0.0, 0.0)], _bar(0.0, -6.0, -16.0)]
        return [[(0.0, 0.0), (0.0, 20.0), (w, 20.0), (w, 0.0), (0.0, 0.0)], _shank(-4.0, w + 8.0, 20.0)]
    if tool.type == "thread":
        s = -1.0 if inside else 1.0
        tip = [(-1.73, s * 3.0), (0.0, 0.0), (1.73, s * 3.0)]
        if inside:
            return [tip + [(-1.73, -3.0)], _bar(-1.73, -3.0, -14.0)]
        return [tip + [(-1.73, 3.0)], _shank(-6.0, 6.0, 3.0)]
    front, rear = tool.front_angle, tool.rear_angle
    if front == rear:  # ball-ends and anything without angles: the nose
        r = tool.nose_radius or 2.5
        return [_circle(0.0, 0.0, r), _bar(0.0, -r, r)]
    length = 10.0
    f = (length * math.cos(math.radians(front)), length * math.sin(math.radians(front)))
    b = (length * math.cos(math.radians(rear)), length * math.sin(math.radians(rear)))
    insert = [(0.0, 0.0), f, (f[0] + b[0], f[1] + b[1]), b, (0.0, 0.0)]
    cz, cr = (f[0] + b[0]) / 2, (f[1] + b[1]) / 2
    if inside or (f[0] > 0 and b[0] > 0 and f[1] * b[1] < 0):  # boring bars, face tools
        return [insert, _bar(cz, cr - 4.0, cr + 4.0)]
    return [insert, _shank(cz - 6.0, cz + 6.0, cr)]
