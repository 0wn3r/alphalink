"""CLI:
    python -m alphacam dump FILE.PRG [--moves]
    python -m alphacam post PROJECT.alcam [-o FILE.PRG] [--sd1 FILE.SD1]"""

from __future__ import annotations

import argparse
import sys

from .io.prg_parse import PrgSyntaxError, parse_file
from .model.project import MachineProfile, Project
from .post.alphat_prg import post
from .post.alphat_sd1 import sd1


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:g}"


def dump(path: str, show_moves: bool, profile: MachineProfile) -> None:
    prog = parse_file(path, profile)
    print(f"{path}: O{prog.o_number} program #{prog.program_number}, {prog.units}, "
          f"{len(prog.statements)} lines, Z x{prog.z_scale:g} F x{prog.f_scale:g} (values below unscaled)")
    for b in prog.blocks:
        parts = [f"N{b.label} {b.op_name}"]
        if b.tool is not None:
            parts.append(f"T{b.tool}")
        if b.start is not None:
            parts.append(("change" if b.op_kind == 1 else "start") + f" X{_fmt(b.start[0])} Z{_fmt(b.start[1])}")
        if b.limit is not None:
            parts.append(f"limit X{_fmt(b.limit[0])} Z{_fmt(b.limit[1])}")
        if b.feed is not None:
            parts.append(("pitch " if b.op_name == "thread" else "F") + _fmt(b.feed))
        if b.declared_passes is not None:
            parts.append(f"{len(b.passes)}/{b.declared_passes} passes")
        moves = b.moves
        if moves:
            xs = [m.x for m in moves if m.x is not None]
            zs = [m.z for m in moves if m.z is not None]
            extent = f", X {min(xs):g}..{max(xs):g} Z {min(zs):g}..{max(zs):g}" if xs and zs else ""
            parts.append(f"{len(moves)} moves{extent}")
        print("  " + ", ".join(parts))
        if show_moves:
            for p in b.passes:
                for m in p.moves:
                    extra = f" F{_fmt(m.feed)}" if m.feed is not None else ""
                    if m.dwell_revs is not None:
                        extra = f" {m.dwell_revs:g} revs"
                    inc = " (inc)" if m.incremental else ""
                    print(f"    pass {p.number:3} L{m.line:<5} {m.kind.value:6} X{_fmt(m.x)} Z{_fmt(m.z)}{extra}{inc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alphacam")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="parse ALPHA-T .PRG files and print their structure")
    d.add_argument("files", nargs="+")
    d.add_argument("--moves", action="store_true", help="list every move")
    d.add_argument("--z-scale", type=float, default=2.0)
    d.add_argument("--f-scale", type=float, default=2.0)
    po = sub.add_parser("post", help="post an .alcam project to an ALPHA-T .PRG")
    po.add_argument("project")
    po.add_argument("-o", "--output", help="output file (default: stdout)")
    po.add_argument("--sd1", help="also write ALPHA-T's .SD1 listing to this file")
    po.add_argument("--z-scale", type=float, default=2.0)
    po.add_argument("--f-scale", type=float, default=2.0)
    args = ap.parse_args(argv)
    profile = MachineProfile(z_scale=args.z_scale, f_scale=args.f_scale)
    if args.cmd == "post":
        project = Project.load(args.project)
        data = post(project, profile)
        if args.sd1:
            with open(args.sd1, "wb") as fh:
                fh.write(sd1(project))
        if args.output:
            with open(args.output, "wb") as fh:
                fh.write(data)
        else:
            sys.stdout.buffer.write(data)
        return 0
    status = 0
    for f in args.files:
        try:
            dump(f, args.moves, profile)
        except (OSError, PrgSyntaxError) as e:
            print(f"{f}: {e}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
