import tempfile
import unittest
from pathlib import Path

import ezdxf

from alphacam.drawing import pick_order
from alphacam.io.dxf import load_dxf, read_dxf
from alphacam.io.sdg import load_sdg
from alphacam.model import Arc, FinishPass, Point
from alphacam.post.alphat_prg import post

from .test_post import SAMPLES


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


# MET2 in a lathe DXF: X = Z axis, Y = radius
MET2_LINES = [line(0, 0, 0, 20), line(0, 20, -25, 20), line(-25, 20, -25, 30), line(-25, 30, -50, 30),
              line(-50, 30, -50, 50)]


class Dxf(unittest.TestCase):
    def test_lines_post_like_alpha_t(self):
        res = read_dxf(dxf(*MET2_LINES, line(-50, -50, -50, -30)))  # the lower half is dropped
        p = res.project
        self.assertEqual(len(p.geometry), 5)
        ref = load_sdg(SAMPLES / "MET2_finish_metric.SDG")
        p.billet, p.first_tool = ref.billet, 3
        p.operations = [FinishPass(3, Point(120, 5), pick_order(p, [g.id for g in p.geometry]))]
        self.assertEqual(post(p), (SAMPLES / "MET2_finish_metric.PRG").read_bytes())

    def test_polyline_with_bulges_and_arcs(self):
        # R5 convex corner as an ARC entity: centre (Z-30, r25), 0 -> 90 degrees in DXF; this is exactly the
        # ALPHA-T ARC sample's arc: CCW X50 Z-25 -> X60 Z-30, centre X50 Z-30
        res = read_dxf(dxf(lwpoly((0, 0, 0), (0, 20, 0), (-25, 20, 0), (-25, 25, 0)), arc(-30, 25, 5, 0, 90),
                           version="AC1015"))
        arcs = [g for g in res.project.geometry if isinstance(g, Arc)]
        self.assertEqual(len(arcs), 1)
        a = arcs[0]
        self.assertEqual((a.a, a.b, a.centre, a.ccw), (Point(50, -25), Point(60, -30), Point(50, -30), True))
        self.assertEqual(len(res.project.geometry), 4)

    def test_diameter_mode_and_skips(self):
        res = read_dxf(dxf(line(0, 0, -10, 0), line(0, 40, -10, 40), "0\nTEXT\n8\n0\n1\nhello\n"),
                       y_is="diameter")
        self.assertEqual([(g.a, g.b) for g in res.project.geometry][1], (Point(40, 0), Point(40, -10)))
        self.assertEqual(res.skipped, {"TEXT": 1})

    def test_files_other_programs_write(self):
        """ezdxf reads what CAD programs save: a modern ASCII DXF, a binary DXF, and arcs with a flipped extrusion."""
        doc = ezdxf.new("R2018")
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 20))
        msp.add_lwpolyline([(0, 20, 0, 0, 0), (-25, 20, 0, 0, 0), (-25, 25)])
        # the R5 arc of test_polyline_with_bulges_and_arcs written mirrored (OCS x = -WCS x): the same arc, clockwise
        msp.add_arc((30, 25), 5, 90, 180, dxfattribs={"extrusion": (0, 0, -1)})
        msp.add_blockref(doc.blocks.new("TITLE").name, (0, -100))
        msp.add_text("hello")
        with tempfile.TemporaryDirectory() as tmp:
            for name, save in (("ascii.dxf", doc.saveas), ("binary.dxf", lambda f: doc.saveas(f, fmt="bin"))):
                with self.subTest(name):
                    save(Path(tmp, name))
                    res = load_dxf(Path(tmp, name))
                    arcs = [g for g in res.project.geometry if isinstance(g, Arc)]
                    self.assertEqual([(a.a, a.b, a.centre, a.ccw) for a in arcs],
                                     [(Point(60, -30), Point(50, -25), Point(50, -30), False)])
                    self.assertEqual(len(res.project.geometry), 4)
                    self.assertEqual(res.skipped, {"INSERT": 1, "TEXT": 1})

    def test_not_a_dxf(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "x.dxf").write_bytes(b"%PDF-1.4 not a drawing")
            with self.assertRaises(ValueError):
                load_dxf(Path(tmp, "x.dxf"))
