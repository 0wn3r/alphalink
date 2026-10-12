"""Toolpath = passes of moves, in project units, unscaled (the post applies the machine profile's scaling)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MoveKind(str, Enum):
    RAPID = "rapid"  # G0
    FEED = "feed"  # G1
    THREAD = "thread"  # G32 (and its modal U/W retract)
    DWELL = "dwell"  # G4


@dataclass
class Move:
    kind: MoveKind
    x: float | None = None  # diameter
    z: float | None = None
    feed: float | None = None
    dwell_revs: float | None = None


@dataclass
class Pass:
    moves: list[Move] = field(default_factory=list)


@dataclass
class Toolpath:
    passes: list[Pass] = field(default_factory=list)
    retract: list[Move] = field(default_factory=list)  # after the last pass (back to the tool start)

    @property
    def moves(self) -> list[Move]:
        return [m for p in self.passes for m in p.moves] + self.retract
