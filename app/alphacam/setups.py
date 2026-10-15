"""Second setup (workflow improvements, step 6): the part turned around in the chuck to machine its other end.

The drawing is mirrored about the middle of the part's length, so the back face becomes the new front face at Z0 and
the old front face lands at Z-length. X (diameters) stays. Arcs change direction with the mirror. The new setup has
no operations (its own roughing and finishing are added to it) and the next program number, so both programs can sit
in the control side by side. The app keeps it in the same .alcam as setup 2 (Project.setups)."""

from __future__ import annotations

import copy

from .i18n import tr
from .model.geometry import Arc, Point


def part_length(project) -> float:
    """The drawn part's length: from Z0 to its deepest point (the profile's back face)."""
    zs = [p.z for g in project.geometry for p in (g.a, g.b)]
    return round(-min(min(zs), 0.0), 6) if zs else 0.0


def turn_around(project, length: float | None = None, program_number: int | None = None):
    """A new project for the second setup: geometry and texts mirrored so that Z0 is the old back face (z' = -length
    - z), the billet as long as the part, no operations, the next program number."""
    length = part_length(project) if length is None else length
    if length <= 0:
        raise ValueError(tr("the part has no length to turn around"))
    new = copy.deepcopy(project)

    def flip(p: Point) -> Point:
        return Point(p.x, round(-length - p.z, 6))

    for g in new.geometry:
        g.a, g.b = flip(g.a), flip(g.b)
        if isinstance(g, Arc):
            g.centre = flip(g.centre)
            g.ccw = not g.ccw  # a mirror reverses the direction of travel
    for t in new.texts:
        t.p = flip(t.p)
    new.operations = []
    new.setups = []
    new.billet.length = round(length, 6)
    new.program_number = (project.program_number + 1) if program_number is None else program_number
    return new
