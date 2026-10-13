"""One dataclass per operation; parameters mirror the ALPHA-T menus (DESIGN.md 4.3).

Values are in drawing units (unscaled). The PRG post applies the machine profile's Z/feed scaling."""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Point

# PRG #127 operation kind (data/program_formats.json, "N20 block (operation)").
OP_KIND_TOOL_CHANGE = 1
OP_KINDS: dict[int, str] = {
    1: "tool_change",
    6: "area_clear",
    7: "area_clear_internal",
    8: "face_clear",
    9: "groove",
    10: "area_prefinish",
    14: "finish",
    15: "finish_internal",
    17: "groove_finish",
    18: "drill",
    20: "thread",
    21: "thread_spring",
}


@dataclass
class ToolChange:
    tool: int
    change_point: Point = field(default_factory=lambda: Point(100, 50))
    feed: float = 0.15
    type = "tool_change"
    op_kind = 1


@dataclass
class AreaClear:
    """offset_x is a radius (per side) allowance, like FinishPass."""

    tool: int
    start: Point
    contour: list[str]
    depth_of_cut: float = 2.0
    offset_x: float = 0.0
    offset_z: float = 0.0
    feed: float = 0.15
    towards: bool = True
    pre_finish: bool = False
    external: bool = True
    front: bool = True  # ALPHA-T FRONT/FACE toggle; FACE (SD1 E101) cuts radial passes like face clear, #127=8
    type = "area_clear"
    op_kind = 6  # 7 when internal


@dataclass
class FaceClear:
    tool: int
    start: Point
    contour: list[str]
    depth_of_cut: float = 2.0
    offset_x: float = 0.0
    offset_z: float = 0.0
    feed: float = 0.15
    towards: bool = True
    pre_finish: bool = False
    type = "face_clear"
    op_kind = 8


@dataclass
class Groove:
    tool: int
    start: Point
    contour: list[str]
    datum: str = "left"  # left | right
    finish_offset_x: float = 0.0
    finish_offset_z: float = 0.0
    peck_depth: float = 0.0
    stepover: float = 2.0
    tip_width: float = 3.0
    feed: float = 0.15
    full_retract: bool = False  # ALPHA-T default FULL RET NO (SD1 L110; L111 = yes)
    external: bool = True
    front: bool = True
    z_shift: float = 0.0
    repeat: int = 0
    mode: str = "area"  # area | finish
    dwell_revs: float = 0.0
    type = "groove"
    op_kind = 9


@dataclass
class FinishPass:
    tool: int
    start: Point
    contour: list[str]
    feed: float = 0.15
    offset_x: float = 0.0  # radius (per side): X moves by 2 * offset_x
    offset_z: float = 0.0
    external: bool = True
    type = "finish"
    op_kind = 14  # 15 when internal


@dataclass
class Drill:
    """`depth` is the absolute Z of the hole bottom (ALPHA-T asks for the Z, e.g. -30)."""

    tool: int
    start: Point
    depth: float
    peck: float = 0.0
    feed: float = 0.15
    dwell_revs: float = 0.0
    type = "drill"
    op_kind = 18


@dataclass
class Thread:
    tool: int
    start: Point
    outer_line: str
    inner_line: str
    pitch: float  # > 0 (V1.21)
    cut_depth: float = 1.0  # first cut (radius); cut n goes to first * sqrt(n), constant chip area
    lead_in: float = 5.0  # ALPHA-T default: passes start this far in front of the thread (+Z)
    lead_out: float = 0.0  # the thread runs this far past the drawn end
    angle: float = 60.0
    external: bool = True
    towards: bool = True
    spring_pass: bool = False
    type = "thread"
    op_kind = 20

    def __post_init__(self):
        if self.pitch <= 0:
            raise ValueError("thread pitch must be > 0")


@dataclass
class Dwell:
    revs: float
    type = "dwell"
    op_kind = None


@dataclass
class MoveTo:
    """Move or rapid to a point; appended to the current tool block in the PRG."""

    point: Point
    rapid: bool = True
    feed: float = 0.15
    type = "move"
    op_kind = None


Operation = ToolChange | AreaClear | FaceClear | Groove | FinishPass | Drill | Thread | Dwell | MoveTo
OPERATION_TYPES: dict[str, type] = {
    cls.type: cls
    for cls in (ToolChange, AreaClear, FaceClear, Groove, FinishPass, Drill, Thread, Dwell, MoveTo)
}
