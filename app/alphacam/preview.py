"""Toolpath preview for the UI: post the project, parse the PRG back, and map its blocks to the operations.

Going through the real post means the preview shows exactly what would be sent (scaling undone by the parser).
If an operation cannot be posted, the preview covers the operations before it and reports the error."""

from __future__ import annotations

from dataclasses import dataclass, field

from .io.prg_parse import Block, Move, Program, parse
from .model.operations import AreaClear, Dwell, MoveTo, ToolChange
from .model.project import MachineProfile, Project
from .post.alphat_prg import post


@dataclass
class OpPreview:
    op_index: int | None  # index into project.operations; None for the implicit first tool block
    blocks: list[Block] = field(default_factory=list)

    @property
    def moves(self) -> list[Move]:
        return [m for b in self.blocks for m in b.moves]

    @property
    def passes(self) -> int:
        return sum(b.declared_passes or 0 for b in self.blocks)


@dataclass
class Preview:
    data: bytes | None = None  # the PRG, when the whole project posts
    program: Program | None = None
    ops: list[OpPreview] = field(default_factory=list)
    error: str | None = None
    error_op: int | None = None  # index of the operation that failed

    def for_op(self, index: int) -> OpPreview | None:
        for p in self.ops:
            if p.op_index == index:
                return p
        return None


def _post_prefix(project: Project, n: int, profile: MachineProfile, library=None) -> bytes:
    trial = Project.from_dict(project.to_dict())
    trial.operations = trial.operations[:n]
    return post(trial, profile, library)


def preview(project: Project, profile: MachineProfile | None = None, library=None) -> Preview:
    profile = profile or MachineProfile()
    result = Preview()
    ops = project.operations
    n_ok = len(ops)
    try:
        data = post(project, profile, library)
        result.data = data
    except (NotImplementedError, ValueError, KeyError) as e:
        # find the first operation that cannot be posted
        n_ok = 0
        while n_ok < len(ops):
            try:
                _post_prefix(project, n_ok + 1, profile, library)
            except (NotImplementedError, ValueError, KeyError) as e2:
                result.error, result.error_op = str(e2) or type(e2).__name__, n_ok
                break
            n_ok += 1
        else:
            result.error = str(e) or type(e).__name__
        data = _post_prefix(project, n_ok, profile, library)
    prog = parse(data, profile)
    result.program = prog

    blocks = [b for b in prog.blocks if b.op_name not in ("empty", "end")]
    it = iter(blocks)
    current_tool: OpPreview | None = None
    if not ops[:1] or not isinstance(ops[0], ToolChange):
        current_tool = OpPreview(None, [next(it)])
        result.ops.append(current_tool)
    for i, op in enumerate(ops[:n_ok]):
        if isinstance(op, (MoveTo, Dwell)):
            if current_tool is not None:
                result.ops.append(OpPreview(i, []))  # shown inside the tool block
            continue
        pv = OpPreview(i, [next(it)])
        if isinstance(op, AreaClear) and op.pre_finish:
            pv.blocks.append(next(it))
        if isinstance(op, ToolChange):
            current_tool = pv
        result.ops.append(pv)
    return result
