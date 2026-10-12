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
