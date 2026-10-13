"""Toolpath = passes of moves, in project units, unscaled (the post applies the machine profile's scaling)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


MAX_PASSES = 5000  # more passes or plunges than this is a typing error (depth of cut 0.001), not a part


def too_many(what: str) -> ValueError:
    from ..i18n import tr

    return ValueError(tr("{what}: more than {max} passes; check the depth of cut / stepover", what=what,
                         max=MAX_PASSES))


class MoveKind(str, Enum):
    RAPID = "rapid"  # G0
    FEED = "feed"  # G1
    THREAD = "thread"  # G32 (and its modal U/W retract)
    DWELL = "dwell"  # G4
    ARC_CW = "arc_cw"  # G2
    ARC_CCW = "arc_ccw"  # G3


@dataclass
class Move:
    kind: MoveKind
    x: float | None = None  # diameter
    z: float | None = None
    feed: float | None = None
    dwell_revs: float | None = None
    centre_x: float | None = None  # arcs: absolute centre (diameter, Z)
    centre_z: float | None = None


@dataclass
class Pass:
    moves: list[Move] = field(default_factory=list)


@dataclass
class Toolpath:
    passes: list[Pass] = field(default_factory=list)
    retract: list[Move] = field(default_factory=list)  # after the last pass (back to the tool start)
    plunges: int = 0  # grooving: plunges per pass (SD1 G75 H)

    @property
    def moves(self) -> list[Move]:
        return [m for p in self.passes for m in p.moves] + self.retract
