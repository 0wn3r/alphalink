import json
import re
import unittest
from pathlib import Path

from alphacam.model import (
    AreaClear,
    Arc,
    Drill,
    Dwell,
    FaceClear,
    FinishPass,
    Groove,
    Line,
    MoveTo,
    Point,
    Project,
    Thread,
    Tool,
    ToolChange,
)
from alphacam.numfmt import format_number

DESIGN = Path(__file__).resolve().parents[1] / "DESIGN.md"


def met2_project() -> Project:
    pts = [Point(0, 0), Point(40, 0), Point(40, -25), Point(60, -25), Point(60, -50), Point(100, -50)]
    return Project(
        tools=[Tool(3, "55 Deg Finisher 0.4R", "turn", nose_radius=0.4, angle=55)],
        geometry=[Line(a, b) for a, b in zip(pts, pts[1:])] + [Arc(Point(100, -50), Point(100, -60), Point(100, -55))],
        operations=[
            ToolChange(3, Point(100, 50), 0.15),
            AreaClear(3, Point(120, 5), ["g0", "g1", "g2", "g3", "g4"], depth_of_cut=2),
            FaceClear(3, Point(120, 5), ["g0", "g1"]),
            Groove(3, Point(120, 5), ["g2", "g3"]),
            FinishPass(3, Point(120, 5), ["g0", "g1", "g2", "g3", "g4"]),
            Drill(3, Point(0, 5), depth=30, peck=10, dwell_revs=3),
            Thread(3, Point(120, 5), "g1", "g2", pitch=1.5),
            Dwell(3),
            MoveTo(Point(60, -70)),
        ],
    )


class ProjectJson(unittest.TestCase):
    def test_round_trip(self):
        p = met2_project()
        text = p.dumps()
        q = Project.loads(text)
        self.assertEqual(q, p)
        self.assertEqual(q.dumps(), text)
        d = json.loads(text)
        self.assertEqual(d["format"], "alcam/1")
        self.assertEqual([o["type"] for o in d["operations"]],
                         ["tool_change", "area_clear", "face_clear", "groove", "finish", "drill", "thread", "dwell",
                          "move"])
        self.assertEqual(d["geometry"][0], {"type": "line", "a": [0.0, 0.0], "b": [40.0, 0.0], "id": "g0"})

    def test_design_example_loads(self):
        """The JSON example in DESIGN.md 4.1 is a valid project."""
        block = re.search(r"### 4\.1.*?```json\n(.*?)```", DESIGN.read_text(), re.S)[1]
        p = Project.loads(block)
        self.assertEqual(p.billet.diameter, 100)
        self.assertEqual(p.tools[0].nose_radius, 0.4)
        self.assertEqual(p.operations[0].contour, ["g0", "g1"])
        self.assertEqual(p.contour(["g0"]), [p.geometry[0]])
        self.assertEqual(p.tool_change, Point(100, 50))

    def test_contour_unknown_id(self):
        p = met2_project()
        with self.assertRaises(KeyError):
            p.contour(["g0", "g99"])

    def test_rejects_unknown(self):
        d = met2_project().to_dict()
        with self.assertRaisesRegex(ValueError, "format"):
            Project.from_dict({**d, "format": "alcam/2"})
        d["operations"][0]["spindle"] = 1
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            Project.from_dict(d)
        d = met2_project().to_dict()
        d["operations"][0]["type"] = "knurl"
        with self.assertRaisesRegex(ValueError, "unknown type"):
            Project.from_dict(d)

    def test_validation(self):
        with self.assertRaises(ValueError):
            Project(units="cm")
        with self.assertRaises(ValueError):
            Thread(3, Point(0, 0), "g0", "g1", pitch=0)
        with self.assertRaises(ValueError):
            Tool(1, type="laser")

    def test_save_load(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "part.alcam"
            met2_project().save(path)
            self.assertEqual(Project.load(path), met2_project())


class Geometry(unittest.TestCase):
    def test_diameter_distance(self):
        self.assertEqual(Line(Point(40, 0), Point(60, 0)).length(), 10)
        self.assertEqual(Arc(Point(100, -50), Point(100, -60), Point(100, -55)).radius(), 5)


class NumberFormat(unittest.TestCase):
    def test_c6(self):
        cases = [(100, 3, "100."), (0, 3, "0."), (-0.0, 3, "0."), (0.3, 3, "0.3"), (-0.8, 3, "-0.8"),
                 (2.8284, 3, "2.828"), (100.2828, 3, "100.283"), (3.93700787, 4, "3.937"), (0.11135, 4, "0.1114"),
                 (-101.6, 3, "-101.6"), (0.0004, 3, "0.")]
        for value, decimals, text in cases:
            with self.subTest(value=value, decimals=decimals):
                self.assertEqual(format_number(value, decimals), text)
