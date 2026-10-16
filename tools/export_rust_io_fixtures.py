#!/usr/bin/env python3
"""Export the file format cases for the Rust port's tests (rust/alphacam/tests/io.rs).

For every sample under samples/ this records what the Python app gives: the .SDG read as a part (.alcam text), the
operations rebuilt from the .SD1, the .SD1 listing written for every post case, the .SDG/.SDT written back, the
.PRG parse (a digest of every statement, block, pass and move), and for the tool library and DXF readers the inputs
the Python tests build and what they read from them. Expected output that equals a file already in the repository
(an ALPHA-T sample, a post fixture) is referenced by its path instead of being copied; the written .SDG files are
pinned by their SHA-256 (28 KB each). The Rust tests need no Python.

    python3 tools/export_rust_io_fixtures.py   # from the repository root; rewrites rust/alphacam/tests/fixtures/io
"""

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "app" / "tests")]

import ezdxf  # noqa: E402

from alphacam.io.dxf import load_dxf  # noqa: E402
from alphacam.io.prg_parse import PrgSyntaxError, parse  # noqa: E402
from alphacam.io.sd1 import load_sd1  # noqa: E402
from alphacam.io.sdg import load_sdg, read_sdg  # noqa: E402
from alphacam.io.sdg_write import write_sdg, write_sdt  # noqa: E402
from alphacam.io.toollib import TL2_RECORD, read_tl1, read_tl2  # noqa: E402
from alphacam.model import MachineProfile, Project  # noqa: E402
from alphacam.model.tools import library_to_json, standard_library  # noqa: E402
from alphacam.post.alphat_sd1 import sd1  # noqa: E402
from tests.test_preview import EXAMPLE_SAMPLE  # noqa: E402

FIXTURES = ROOT / "rust" / "alphacam" / "tests" / "fixtures"
OUT = FIXTURES / "io"
POST = FIXTURES / "post"
SAMPLES = ROOT / "samples"
ERRORS = (ValueError, NotImplementedError, KeyError, IndexError, struct.error)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def store(path: Path, data: bytes | str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.write_text if isinstance(data, str) else path.write_bytes)(data)
    return rel(path)


def error(e: Exception) -> dict:
    return {"error": "ValueError" if isinstance(e, (KeyError, IndexError, struct.error)) else type(e).__name__,
            "message": str(e)}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def samples(pattern: str) -> list[Path]:
    return sorted(SAMPLES.rglob(pattern))


# --- SDG / SD1 ---------------------------------------------------------------------------------------------

def sdg_cases(sd1_import: dict) -> dict:
    """The drawing as a part; when the .SD1 import of the same sample stored it with its operations, that file
    without them."""
    out = {}
    for path in samples("*.SDG"):
        try:
            text = load_sdg(path).dumps()
        except ERRORS as e:
            out[rel(path)] = error(e)
            continue
        imported = sd1_import.get(rel(path.with_suffix(".SD1")), {}).get("expected")
        if imported:
            p = Project.load(ROOT / imported)
            p.operations = []
            if p.dumps() == text:
                out[rel(path)] = {"expected": imported, "without_operations": True}
                continue
        out[rel(path)] = {"expected": store(OUT / "sdg" / path.parent.name / f"{path.stem}.alcam", text)}
    return out


def sd1_import_cases() -> dict:
    out = {}
    for path in samples("*.SD1"):
        sdg = path.with_suffix(".SDG")
        if not sdg.exists():
            continue
        project = load_sdg(sdg)
        try:
            project.operations = load_sd1(path, project)
        except ERRORS as e:
            out[rel(path)] = {"sdg": rel(sdg), **error(e)}
            continue
        text = project.dumps()
        post = POST / f"sd1_{path.stem}.alcam"  # what tools/export_rust_fixtures.py stored for the post tests
        if post.exists() and post.read_text() == text:
            expected = rel(post)
        else:
            expected = store(OUT / "sd1" / path.parent.name / f"{path.stem}.alcam", text)
        out[rel(path)] = {"sdg": rel(sdg), "expected": expected}
    return out


def sample_listing(case: str) -> Path | None:
    """The ALPHA-T listing a post case was made from."""
    kind, _, stem = case.partition("_")
    if kind == "example":
        stem = EXAMPLE_SAMPLE.get(stem, "")
    found = [SAMPLES / f / f"{stem}.SD1" for f in ("alphat_v12", "alphat_v12_inch", "alphat_units")]
    return next((p for p in found if stem and p.exists()), None)


def sd1_write_cases() -> dict:
    """The listing for every post case (golden operations, every sample rebuilt from its .SD1, the examples)."""
    out = {}
    projects = {p.stem: p for p in sorted(POST.glob("*.alcam"))}
    # NO. OF CUTS 6 on a depth-10 thread: first cut 10 / sqrt 6 (tests/test_sd1_write.py)
    p = load_sdg(SAMPLES / "alphat_v12" / "THREAD_6cuts_metric.SDG")
    p.operations = load_sd1(SAMPLES / "alphat_v12" / "THREAD_6cuts_metric.SD1", p)
    p.operations[-1].cut_depth, p.operations[-1].cuts = 1.0, 6
    projects["cuts6_THREAD_6cuts_metric"] = Path(store(OUT / "sd1_write" / "cuts6_THREAD_6cuts_metric.alcam",
                                                       p.dumps()))
    for case, path in projects.items():
        path = ROOT / path
        try:
            data = sd1(Project.load(path))
        except ERRORS as e:
            out[case] = {"project": rel(path), **error(e)}
            continue
        sample = sample_listing(case)
        if sample is not None and sample.read_bytes() == data:
            expected = rel(sample)
        else:
            expected = store(OUT / "sd1_write" / f"{case}.SD1", data)
        out[case] = {"project": rel(path), "expected": expected, "alpha_t": sample is not None}
    return out


def sdg_write_cases() -> dict:
    out = {}
    inputs = [(rel(p), lambda p=p: read_sdg(p.read_bytes()), p) for p in samples("*.SDG")]
    inputs += [(rel(p), lambda p=p: Project.load(p), None) for p in sorted((ROOT / "app" / "examples").glob("*.alcam"))]
    for name, load, sample in inputs:
        try:
            project = load()
        except ERRORS:
            continue
        case = {}
        try:
            data = write_sdg(project)
            case["sdg"] = rel(sample) if sample and sample.read_bytes() == data else sha(data)
        except ERRORS as e:
            case.update(error(e))
        sdt, ref = write_sdt(project), sample and sample.with_suffix(".SDT")
        case["sdt"] = rel(ref) if ref and ref.exists() and ref.read_bytes() == sdt else sha(sdt)
        out[name] = case
    return out


# --- PRG ---------------------------------------------------------------------------------------------------

def prg_digest(prog) -> str:
    """Every statement, block, pass and move as text (floats as repr); the Rust test writes the same."""
    lines = [f"program {prog.o_number} {prog.units} {prog.eol!r} {len(prog.header)} {float(prog.z_scale)!r} {float(prog.f_scale)!r}"]
    for s in prog.statements:
        words = "".join(f"{k}={v};" for k, v in s.words.items())
        lines.append(f"stmt {s.line} {s.kind.value} {s.number} {s.value} {s.target} {words}")
    for b in prog.blocks:
        regs = ";".join(f"{k}={v}" for k, v in b.registers.items())
        lines.append(f"block {b.label} {b.line} {b.op_name} {regs} {b.m_codes} {b.labels}")
        for p in b.passes:
            lines.append(f"pass {p.number} {p.line} {p.restart}")
            for m in p.moves:
                lines.append(f"move {m.kind.value} {m.line} {m.x!r} {m.z!r} {m.feed!r} {m.dwell_revs!r} "
                             f"{m.incremental} {m.centre_x!r} {m.centre_z!r}")
    return "\n".join(lines) + "\n"


def prg_summary(prog) -> dict:
    return {"sha256": sha(prg_digest(prog).encode()), "units": prog.units, "blocks": len(prog.blocks),
            "ops": [[b.op_name, b.declared_passes, len(b.passes)] for b in prog.operations]}


# Broken programs (tests/test_prg_parse.py Strictness): the base sample with byte replacements, LF line endings or
# the last bytes cut off, and the error the parser gives.
MET2 = "samples/alphat_v12/MET2_finish_metric.PRG"
BROKEN = {
    "lf": {"replace": [["\r\n", "\n"]]},
    "unknown_statement": {"replace": [["G1X40.Z0.F0.3\r\n", "G1X40.Z0.F0.3\r\nT0303\r\n"]]},
    "missing_decimal_point": {"replace": [["G1X40.Z0.F0.3", "G1X40Z0.F0.3"]]},
    "register_without_point": {"replace": [["#123=120.", "#123=120"]]},
    "missing_end": {"replace": [["M65\r\n", ""]]},
    "missing_final_newline": {"cut": 2},
    "non_ascii": {"replace": [["M71", "M7\xb9"]]},
    "block_label_order": {"replace": [["N30\r\n", "N35\r\n"]]},
    "repeated_word": {"replace": [["G1X40.Z0.F0.3", "G1X40.X40.Z0.F0.3"]]},
    "mixed_line_endings": {"replace": [["N30\r\n", "N30\n\r\n"]]},
    "pass_order": {"replace": [["#121=1\r\n", "#121=3\r\n"]]},
    "no_percent": {"replace": [["%\r\n", ""]]},
    "dwell_with_x": {"replace": [["G1X40.Z0.F0.3", "G4X1.P3000"]]},
    "arc_without_ik": {"replace": [["G1X40.Z0.F0.3", "G2X40.Z0.F0.3"]]},
}


def broken(base: bytes, case: dict) -> bytes:
    data = base
    for a, b in case.get("replace", []):
        data = data.replace(a.encode("latin-1"), b.encode("latin-1"))
    return data[:-case["cut"]] if "cut" in case else data


def prg_cases() -> dict:
    out = {"samples": {}, "broken": {}}
    for path in samples("*.PRG"):
        try:
            out["samples"][rel(path)] = prg_summary(parse(path.read_bytes()))
        except ERRORS as e:
            out["samples"][rel(path)] = error(e)
    raw = parse((ROOT / MET2).read_bytes(), MachineProfile(z_scale=1, f_scale=1))
    out["unscaled"] = {"sample": MET2, **prg_summary(raw)}
    base = (ROOT / MET2).read_bytes()
    for name, case in BROKEN.items():
        try:
            prog = parse(broken(base, case))
            result = prg_summary(prog)
        except PrgSyntaxError as e:
            result = {"line": e.line, "message": str(e)}
        out["broken"][name] = {"base": MET2, **case, **result}
    return out


# --- tool libraries ----------------------------------------------------------------------------------------

def tl1(records: dict[int, tuple], slots: int = 32) -> bytes:
    """A TL1 file (tests/test_tools.py)."""
    data = bytearray(256 * (slots + 1))
    struct.pack_into("<h", data, 0, slots)
    for n, (name, r, front, rear, desc) in records.items():
        rec = name.ljust(20).encode("latin-1") + struct.pack("<fhh", r, front, rear) + desc.encode("latin-1")
        data[n * 256:n * 256 + len(rec)] = rec
    return bytes(data)


def tl2_record(segments, points) -> bytes:
    """A TL2 record (tests/test_tool_shapes.py): segments as (start, end, centre) point indices from 1."""
    rec = bytearray(TL2_RECORD)
    words = [len(segments), len(points)]
    for s, e, c in segments:
        words += [-s, -e, c]
    struct.pack_into(f"<{len(words)}h", rec, 0, *words)
    struct.pack_into(f"<{len(points)}f", rec, 0x104, *[p[0] for p in points])
    struct.pack_into(f"<{len(points)}f", rec, 0x208, *[p[1] for p in points])
    return bytes(rec)


def toollib_cases() -> dict:
    deleted = bytearray(tl2_record([(1, 2, 0), (1, 2, 0)], [(0, 0), (3, 3)]))
    struct.pack_into("<3h", deleted, 10, 0, 0, 0)  # the second triple zeroed: deleted
    files = {
        "test.TL1": tl1({1: ("80 Deg Rougher 0.8R", 0.8, 87, 7, "GENERAL PURPOSE"),
                         12: ("55 Boring-Bar 0.4R", 0.4, -87, -31, ""), 24: ("25mm Drill", 0, 0, 0, ""),
                         30: ("\0Odd \xe9 name\0", 0.15, 0, 0, "\0 tabs\tand\x1fseparators \xa0")}),
        "atoolib.TL1": tl1({t.number: (t.name, t.nose_radius, int(t.front_angle), int(t.rear_angle), t.description)
                            for t in standard_library().tools}),
        "short.TL1": b"\0" * 100,
        "test.TL2": tl2_record([(1, 2, 0), (2, 3, 0), (3, 1, 0)], [(0, 0), (10, 1), (1, 10)]) + bytes(TL2_RECORD)
        + tl2_record([(1, 2, 3)], [(5, 0), (0, 5), (0, 0)]),
        "clockwise.TL2": tl2_record([(1, 2, -3)], [(5, 0), (0, 5), (0, 0)]),
        "deleted.TL2": bytes(deleted),
        "arcs.TL2": tl2_record([(1, 2, 3), (2, 4, -3), (4, 5, 0)], [(2.5, 0), (0, 2.5), (0, 0), (-2.5, 0), (-9, 3.3)]),
        "short.TL2": b"x" * 100,
    }
    out = {}
    for name, data in files.items():
        path = store(OUT / "toollib" / name, data)
        try:
            if name.endswith(".TL1"):
                result = {"library": library_to_json(read_tl1(data))}
            else:
                result = {"pictures": {str(k): v for k, v in read_tl2(data).items()}}
        except ERRORS as e:
            result = error(e)
        out[path] = result
    return out


# --- DXF ---------------------------------------------------------------------------------------------------

def dxf(*entities: str, version: str = "AC1009") -> str:  # R12 unless given; LWPOLYLINE needs R2000 (AC1015)
    return (f"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\n{version}\n0\nENDSEC\n" if version != "AC1009" else "") + \
        "0\nSECTION\n2\nENTITIES\n" + "".join(entities) + "0\nENDSEC\n0\nEOF\n"


def line(x1, y1, x2, y2):
    return f"0\nLINE\n8\n0\n10\n{x1}\n20\n{y1}\n11\n{x2}\n21\n{y2}\n"


def arc(cx, cy, r, a0, a1):
    return f"0\nARC\n8\n0\n10\n{cx}\n20\n{cy}\n40\n{r}\n50\n{a0}\n51\n{a1}\n"


def lwpoly(*verts):  # (x, y, bulge)
    body = "".join(f"10\n{x}\n20\n{y}\n" + (f"42\n{b}\n" if b else "") for x, y, b in verts)
    return f"0\nLWPOLYLINE\n100\nAcDbEntity\n8\n0\n100\nAcDbPolyline\n90\n{len(verts)}\n70\n0\n{body}"


MET2_LINES = [line(0, 0, 0, 20), line(0, 20, -25, 20), line(-25, 20, -25, 30), line(-25, 30, -50, 30),
              line(-50, 30, -50, 50)]


def cad_doc():
    """What CAD programs save (tests/test_dxf.py): an R2018 drawing with a mirrored arc, a block and a text."""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (0, 20))
    msp.add_lwpolyline([(0, 20, 0, 0, 0), (-25, 20, 0, 0, 0), (-25, 25)])
    msp.add_arc((30, 25), 5, 90, 180, dxfattribs={"extrusion": (0, 0, -1)})
    msp.add_blockref(doc.blocks.new("TITLE").name, (0, -100))
    msp.add_text("hello")
    return doc


def shapes_doc():
    """Circles, 2D polylines with bulges both ways (open and closed), a 3D polyline and mirrored entities."""
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    msp.add_circle((-40, 0), 6)
    msp.add_circle((-70, 12), 3, dxfattribs={"extrusion": (0, 0, -1)})
    msp.add_polyline2d([(0, 0), (0, 10), (-5, 15), (-30, 15), (-35, 10)], format="xy",
                       dxfattribs={"flags": 0}).vertices[1].dxf.bulge = 0.41421356
    poly = msp.add_polyline2d([(-35, 10), (-45, 10), (-50, 20), (-60, 20)], format="xy")
    poly.vertices[0].dxf.bulge = -0.3
    poly.vertices[2].dxf.bulge = 1.5
    msp.add_lwpolyline([(-60, 20, 0, 0, 0.2), (-70, 25, 0, 0, -2.0), (-80, 25, 0, 0, 0)], close=True)
    msp.add_lwpolyline([(60, 25, 0, 0, 0.5), (80, 30)], dxfattribs={"extrusion": (0, 0, -1)})
    msp.add_polyline3d([(-80, 25, 0), (-90, 30, 1), (-95, 30, 2)], close=True)
    msp.add_arc((-100, 30), 5, 200, 20)
    msp.add_arc((-110, 30), 4, 30, 150, dxfattribs={"extrusion": (0.3, 0.2, 0.9)})
    msp.add_spline([(0, 0), (1, 2), (3, 1)])
    msp.add_ellipse((0, 0), (10, 0), 0.5)
    msp.add_point((1, 1))
    msp.add_text("upstairs").dxf.paperspace = 0
    doc.layouts.get("Layout1").add_line((0, 0), (500, 500))  # paper space: not part of the drawing
    return doc


OPTIONS = {
    "rotate90": {"rotate": 90}, "rotate180": {"rotate": 180}, "rotate270": {"rotate": 270}, "rotate-90": {"rotate": -90},
    "front_left": {"front": "left"}, "axis_middle": {"axis": "middle", "upper_half": False},
    "axis_lowest": {"axis": "lowest"}, "datum": {"datum": True}, "both_halves": {"upper_half": False},
    "inch": {"units": "inch"}, "diameter": {"y_is": "diameter"},
    "everything": {"rotate": 270, "front": "left", "axis": "middle", "datum": True, "units": "inch",
                   "y_is": "diameter"},
}


def dxf_cases() -> dict:
    d = OUT / "dxf"
    d.mkdir(parents=True, exist_ok=True)
    files = {
        "met2_lines.dxf": dxf(*MET2_LINES, line(-50, -50, -50, -30)),  # the lower half is dropped
        "lwpoly_arc.dxf": dxf(lwpoly((0, 0, 0), (0, 20, 0), (-25, 20, 0), (-25, 25, 0)), arc(-30, 25, 5, 0, 90),
                              version="AC1015"),
        "diameter_text.dxf": dxf(line(0, 0, -10, 0), line(0, 40, -10, 40), "0\nTEXT\n8\n0\n1\nhello\n"),
        "section.dxf": dxf(*MET2_LINES, line(0, 0, 0, -20), line(0, -20, -25, -20), line(-25, -20, -25, -30),
                           line(-25, -30, -50, -30), line(-50, -30, -50, -50), line(-50, 50, -50, -50),
                           arc(-60, 10, 5, 90, 270)),
        "empty.dxf": dxf(),
        "not_a_dxf.dxf": "%PDF-1.4 not a drawing",
    }
    for name, text in files.items():
        (d / name).write_text(text)
    cad_doc().saveas(d / "cad_ascii.dxf")
    cad_doc().saveas(d / "cad_binary.dxf", fmt="bin")
    shapes_doc().saveas(d / "shapes.dxf")
    runs = [(name, "default", {}) for name in [*files, "cad_ascii.dxf", "cad_binary.dxf", "shapes.dxf"]]
    runs[2] = ("diameter_text.dxf", "diameter", {"y_is": "diameter"})
    runs += [(name, opt, kw) for name in ("section.dxf", "shapes.dxf") for opt, kw in OPTIONS.items()]
    out = {}
    for name, opt, kw in runs:
        try:
            res = load_dxf(d / name, **kw)
            for g in res.project.geometry:  # LWPOLYLINE bulges come as numpy floats: the app cannot save ccw
                if hasattr(g, "ccw"):  # numpy.bool (TypeError in json), see the Rust port's notes
                    g.ccw = bool(g.ccw)
            result = {"project": res.project.dumps(), "skipped": res.skipped}
        except ERRORS as e:
            result = error(e)
        out[f"{name} {opt}"] = {"file": rel(d / name), "options": kw, **result}
    return out


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    sd1_import = sd1_import_cases()
    manifest = {"sdg": sdg_cases(sd1_import), "sd1_import": sd1_import, "sd1_write": sd1_write_cases(),
                "sdg_write": sdg_write_cases(), "prg": prg_cases(), "toollib": toollib_cases(), "dxf": dxf_cases()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    for k, v in manifest.items():
        n = sum(len(x) for x in v.values()) if k == "prg" else len(v)
        print(f"{k}: {n} cases")


if __name__ == "__main__":
    main()
