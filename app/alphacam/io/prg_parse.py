"""Parse ALPHA-T .PRG programs (DESIGN.md section 5, data/program_formats.json).

Two layers:
  * lex: every line becomes a Statement that keeps its exact text, so `Program.to_bytes()` reproduces the file;
  * structure: header, blocks (N10, N20, ...), passes (#121=n) and moves, with values converted back to drawing
    units (Z, F and thread W divided by the profile's z_scale/f_scale; X is 1:1).

The parser is strict: any line it does not recognise raises PrgSyntaxError with its line number, so a new
ALPHA-T construct shows up as a test failure instead of being silently dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..model.operations import OP_KINDS
from ..model.project import MachineProfile
from ..toolpath.toolpath import MoveKind


class PrgSyntaxError(ValueError):
    def __init__(self, line: int, text: str, reason: str):
        super().__init__(f"line {line}: {reason}: {text!r}")
        self.line = line
        self.text = text


# --- lexer -----------------------------------------------------------------------------------------------

NUM = r"-?(?:\d+\.?\d*|\.\d+)"
RE_PROGRAM = re.compile(r"O(\d+)")
RE_ASSIGN = re.compile(r"#(\d+)=(.+)")
RE_RESTART = re.compile(r"IF\[#140EQ(\d+)\] GOTO (\d+)")
RE_IF = re.compile(r"IF\[.+\]GOTO\[.+\]")
RE_MCODE = re.compile(r"M(\d+)")
RE_LABEL = re.compile(r"N(\d+)")
RE_MOTION = re.compile(rf"(?:G(\d+))?((?:[XZUWIKFP]{NUM})*)")
RE_WORD = re.compile(rf"([XZUWIKFP])({NUM})")


class Kind(str, Enum):
    PERCENT = "percent"
    PROGRAM = "program"  # O<n>
    ASSIGN = "assign"  # #nnn=<value or expression>
    RESTART = "restart"  # IF[#140EQn] GOTO <label>
    IF = "if"  # header restart logic, kept verbatim
    MCODE = "mcode"
    LABEL = "label"  # N<n>
    MOTION = "motion"  # G0/G1/G2/G3/G4/G32 words, or modal words only (U21.W-3.)


@dataclass
class Statement:
    line: int  # 1-based
    text: str
    kind: Kind
    number: int | None = None  # O / M / N number, register, G code, or restart #140 value
    value: str | None = None  # assignment right-hand side
    target: int | None = None  # restart label
    words: dict[str, str] = field(default_factory=dict)  # motion words, raw text


def lex_line(line: int, text: str) -> Statement:
    if text == "%":
        return Statement(line, text, Kind.PERCENT)
    if m := RE_PROGRAM.fullmatch(text):
        return Statement(line, text, Kind.PROGRAM, number=int(m[1]))
    if m := RE_ASSIGN.fullmatch(text):
        return Statement(line, text, Kind.ASSIGN, number=int(m[1]), value=m[2])
    if m := RE_RESTART.fullmatch(text):
        return Statement(line, text, Kind.RESTART, number=int(m[1]), target=int(m[2]))
    if RE_IF.fullmatch(text):
        return Statement(line, text, Kind.IF)
    if m := RE_MCODE.fullmatch(text):
        return Statement(line, text, Kind.MCODE, number=int(m[1]))
    if m := RE_LABEL.fullmatch(text):
        return Statement(line, text, Kind.LABEL, number=int(m[1]))
    m = RE_MOTION.fullmatch(text)
    if m and text and (m[1] is not None or m[2]):
        words = dict(RE_WORD.findall(m[2]))
        if len(words) != len(RE_WORD.findall(m[2])):
            raise PrgSyntaxError(line, text, "repeated word")
        return Statement(line, text, Kind.MOTION, number=None if m[1] is None else int(m[1]), words=words)
    raise PrgSyntaxError(line, text, "unrecognised statement")


def lex(data: bytes) -> tuple[list[Statement], str]:
    """Split into statements. Returns (statements, line ending). The file must be 7-bit ASCII."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as e:
        raise PrgSyntaxError(data.count(b"\n", 0, e.start) + 1, "", f"non-ASCII byte {data[e.start]:#04x}") from e
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    if lines and lines[-1] == "":
        lines.pop()
    else:
        raise PrgSyntaxError(len(lines), lines[-1] if lines else "", "missing final line ending")
    stmts = []
    for i, line in enumerate(lines, 1):
        if "\r" in line or "\n" in line:
            raise PrgSyntaxError(i, line, "mixed line endings")
        stmts.append(lex_line(i, line))
    return stmts, eol


# --- structure -------------------------------------------------------------------------------------------


MOTION_G = {0: MoveKind.RAPID, 1: MoveKind.FEED, 2: MoveKind.ARC_CW, 3: MoveKind.ARC_CCW, 4: MoveKind.DWELL,
            32: MoveKind.THREAD}

FLOAT_REGISTERS = {123, 124, 125, 126, 130}  # written with a decimal point
INT_REGISTERS = {120, 121, 122, 127, 128, 129, 132, 140}


@dataclass
class Move:
    """One motion, in drawing units. x/z are the absolute end point; before the first absolute move of a block
    the position is taken from the block's #123/#124 (see parse). `incremental` is set for U/W moves."""

    kind: MoveKind
    line: int
    x: float | None = None
    z: float | None = None
    feed: float | None = None  # mm/rev (or inch), unscaled; thread pitch for THREAD
    dwell_revs: float | None = None
    incremental: bool = False
    centre_x: float | None = None  # arcs: absolute centre. I is a diameter increment, K a (scaled) Z increment
    centre_z: float | None = None


@dataclass
class Pass:
    number: int  # #121
    line: int
    moves: list[Move] = field(default_factory=list)
    restart: tuple[int, int] | None = None  # (#140 value, label) from IF[#140EQn] GOTO label


@dataclass
class Block:
    """A block N<k*10>. Block 1 is the tool change (#127=1); the last one only holds M65."""

    label: int
    line: int
    registers: dict[int, str] = field(default_factory=dict)  # assignments before the first M66, raw text
    passes: list[Pass] = field(default_factory=list)
    m_codes: list[int] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)  # restart labels (N5nn) inside the block
    z_scale: float = 2.0
    f_scale: float = 2.0

    @property
    def index(self) -> int:
        return self.label // 10

    def reg(self, n: int) -> float | None:
        v = self.registers.get(n)
        return None if v is None else float(v)

    @property
    def op_kind(self) -> int | None:
        v = self.reg(127)
        return None if v is None else int(v)

    @property
    def op_name(self) -> str:
        if self.op_kind is None:
            return "end" if 65 in self.m_codes else "empty"
        return OP_KINDS.get(self.op_kind, f"unknown_{self.op_kind}")

    @property
    def tool(self) -> int | None:
        v = self.reg(122)
        return None if v is None else int(v)

    @property
    def declared_passes(self) -> int | None:
        v = self.reg(120)
        return None if v is None else int(v)

    @property
    def start(self) -> tuple[float, float] | None:
        """#123/#124: tool start (op blocks) or tool change point (block 1), unscaled."""
        if 123 not in self.registers:
            return None
        return self.reg(123), self.reg(124) / self.z_scale

    @property
    def limit(self) -> tuple[float, float] | None:
        """#125/#126: billet or op limits (SD1 G115), unscaled."""
        if 125 not in self.registers:
            return None
        return self.reg(125), self.reg(126) / self.z_scale

    @property
    def feed(self) -> float | None:
        """#130 unscaled: feed, or thread pitch for threading."""
        v = self.reg(130)
        return None if v is None else v / self.f_scale

    @property
    def moves(self) -> list[Move]:
        return [m for p in self.passes for m in p.moves]


@dataclass
class Program:
    statements: list[Statement]
    eol: str
    o_number: int
    units: str  # "mm" (M71) or "inch" (M70)
    header: list[Statement]
    blocks: list[Block]
    z_scale: float
    f_scale: float

    @property
    def program_number(self) -> int:
        for s in self.header:
            if s.kind is Kind.ASSIGN and s.number == 132:
                return int(s.value)
        raise KeyError("#132")

    @property
    def operations(self) -> list[Block]:
        return [b for b in self.blocks if b.op_kind is not None and b.op_kind != 1]

    @property
    def moves(self) -> list[Move]:
        return [m for b in self.blocks for m in b.moves]

    def to_bytes(self) -> bytes:
        return "".join(s.text + self.eol for s in self.statements).encode("ascii")


UNITS_MCODE = {71: "mm", 70: "inch"}


def parse(data: bytes, profile: MachineProfile | None = None) -> Program:
    profile = profile or MachineProfile()
    zs, fs = profile.z_scale, profile.f_scale
    stmts, eol = lex(data)

    if len(stmts) < 2 or stmts[0].kind is not Kind.PERCENT or stmts[-1].kind is not Kind.PERCENT:
        raise PrgSyntaxError(1, stmts[0].text if stmts else "", "program must start and end with %")
    if stmts[1].kind is not Kind.PROGRAM:
        raise PrgSyntaxError(2, stmts[1].text, "expected O<number>")
    o_number = stmts[1].number

    restart_targets = {s.target for s in stmts if s.kind is Kind.RESTART}

    # Header: everything up to the units M-code (M71 mm / M70 inch).
    i = 2
    while i < len(stmts) - 1 and not (stmts[i].kind is Kind.MCODE and stmts[i].number in UNITS_MCODE):
        s = stmts[i]
        if s.kind not in (Kind.ASSIGN, Kind.IF):
            raise PrgSyntaxError(s.line, s.text, "unexpected statement in header")
        i += 1
    if i >= len(stmts) - 1:
        raise PrgSyntaxError(stmts[-1].line, stmts[-1].text, "no units M-code (M70/M71) in header")
    header = stmts[1 : i + 1]
    units = UNITS_MCODE[stmts[i].number]
    i += 1

    blocks: list[Block] = []
    block: Block | None = None
    current: Pass | None = None
    in_registers = False
    modal_g: int | None = None
    pos_x: float | None = None
    pos_z: float | None = None

    for s in stmts[i:-1]:
        if s.kind is Kind.LABEL and s.number not in restart_targets:
            expected = 10 * (len(blocks) + 1)
            if s.number != expected:
                raise PrgSyntaxError(s.line, s.text, f"expected block label N{expected}")
            block = Block(label=s.number, line=s.line, z_scale=zs, f_scale=fs)
            blocks.append(block)
            current, in_registers = None, True
            continue
        if block is None:
            raise PrgSyntaxError(s.line, s.text, "statement before the first block label")

        if s.kind is Kind.LABEL:
            block.labels.append(s.number)
        elif s.kind is Kind.ASSIGN:
            if s.number == 121:
                n = int(s.value)
                if current is None or n == current.number + 1:
                    current = Pass(number=n, line=s.line)
                    block.passes.append(current)
                elif n != current.number:
                    raise PrgSyntaxError(s.line, s.text, f"pass {n} after pass {current.number}")
            if in_registers:
                if s.number in block.registers:
                    raise PrgSyntaxError(s.line, s.text, "register set twice in block header")
                if s.number in FLOAT_REGISTERS or s.number in INT_REGISTERS:
                    _check_register(s)
                block.registers[s.number] = s.value
            elif s.number != 121:
                raise PrgSyntaxError(s.line, s.text, "assignment after the block header")
        elif s.kind is Kind.MCODE:
            if s.number == 66 and in_registers:
                in_registers = False
                # Assumed: after the M66/M63 macros the tool sits at #123/#124 (tool change point in block 1,
                # tool start in op blocks). Every op sample starts with an absolute move, so only moves
                # appended to the tool block (dwell, move/rapid) depend on this.
                if block.start is not None:
                    pos_x, pos_z = block.start
            block.m_codes.append(s.number)
        elif s.kind is Kind.RESTART:
            if current is not None:
                current.restart = (s.number, s.target)
        elif s.kind is Kind.MOTION:
            if current is None:
                # e.g. the rapid to the tool change point in the block before a mid-program tool change
                current = Pass(number=0, line=s.line)
                block.passes.append(current)
            g = s.number if s.number is not None else modal_g
            if g not in MOTION_G:
                raise PrgSyntaxError(s.line, s.text, f"unsupported motion G{g}")
            if s.number is not None and g != 4:
                modal_g = g
            move, pos_x, pos_z = _make_move(s, MOTION_G[g], pos_x, pos_z, zs, fs)
            current.moves.append(move)
        else:
            raise PrgSyntaxError(s.line, s.text, "unexpected statement in block")

    if not blocks or 65 not in blocks[-1].m_codes:
        raise PrgSyntaxError(stmts[-1].line, stmts[-1].text, "program does not end with an M65 block")

    return Program(stmts, eol, o_number, units, header, blocks, zs, fs)


def _check_register(s: Statement) -> None:
    v = s.value
    if not re.fullmatch(NUM, v):
        raise PrgSyntaxError(s.line, s.text, "register value is not a number")
    if s.number in FLOAT_REGISTERS and "." not in v:
        raise PrgSyntaxError(s.line, s.text, "coordinate/feed register without a decimal point")
    if s.number in INT_REGISTERS and "." in v:
        raise PrgSyntaxError(s.line, s.text, "integer register with a decimal point")


def _make_move(s: Statement, kind: MoveKind, x, z, zs, fs) -> tuple[Move, float | None, float | None]:
    w = {k: float(v) for k, v in s.words.items()}
    for k, v in s.words.items():
        if k in "XZUWIKF" and "." not in v:
            raise PrgSyntaxError(s.line, s.text, f"{k} value without a decimal point")
    if kind is MoveKind.DWELL:
        if set(w) != {"P"}:
            raise PrgSyntaxError(s.line, s.text, "dwell needs exactly P")
        return Move(kind, s.line, x, z, dwell_revs=w["P"] / 1000), x, z
    if "P" in w:
        raise PrgSyntaxError(s.line, s.text, "P word on a motion")
    arc = kind in (MoveKind.ARC_CW, MoveKind.ARC_CCW)
    if arc != ("I" in w or "K" in w):
        raise PrgSyntaxError(s.line, s.text, "I/K only (and always) on G2/G3")
    if arc:
        if x is None or z is None or "U" in w or "W" in w:
            raise PrgSyntaxError(s.line, s.text, "arc needs a known start and absolute end")
        cx, cz = x + w.get("I", 0.0), z + w.get("K", 0.0) / zs
    absolute = "X" in w or "Z" in w
    incremental = "U" in w or "W" in w
    if absolute and incremental:
        raise PrgSyntaxError(s.line, s.text, "mixed absolute and incremental words")
    if not (absolute or incremental):
        raise PrgSyntaxError(s.line, s.text, "motion without an axis word")
    if absolute:
        x = w.get("X", x)
        z = w["Z"] / zs if "Z" in w else z
    else:
        x = None if x is None else x + w.get("U", 0.0)
        z = None if z is None else z + w.get("W", 0.0) / zs
    feed = w["F"] / fs if "F" in w else None
    move = Move(kind, s.line, x, z, feed=feed, incremental=incremental)
    if arc:
        move.centre_x, move.centre_z = cx, cz
    return move, x, z


def parse_file(path: str | Path, profile: MachineProfile | None = None) -> Program:
    return parse(Path(path).read_bytes(), profile)
