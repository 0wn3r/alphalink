"""Preview (post + parse back), example parts, and the pure-Python canvas pieces (view, paths, hit test)."""

import os
import unittest
from pathlib import Path

from alphacam.model import FinishPass, Point, Project
from alphacam.post.alphat_prg import post
from alphacam.preview import preview
from alphacam_gtk.canvas.hittest import hit
from alphacam_gtk.canvas.paths import segments
from alphacam_gtk.canvas.view import ViewTransform

from .test_post import SAMPLES, sample_project

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
EXAMPLE_SAMPLE = {
    "met2_finish": "MET2_finish_metric", "area_clear": "AREA1_metric", "arc_finish": "ARC_finish_metric",
    "rough_and_finish": "TOOLCHG_rough1_finish3_metric", "bore_area": "INT_area_metric", "thread": "THREAD_metric",
    "groove": "GROOVE_metric", "face_clear": "FACE_metric", "drill": "DRILL_DWELL_metric",
}


class Examples(unittest.TestCase):
    def test_examples_post_like_alpha_t(self):
        self.assertEqual({p.stem for p in EXAMPLES.glob("*.alcam")}, set(EXAMPLE_SAMPLE))
        for stem, sample in EXAMPLE_SAMPLE.items():
            with self.subTest(stem):
                self.assertEqual(post(Project.load(EXAMPLES / f"{stem}.alcam")),
                                 (SAMPLES / f"{sample}.PRG").read_bytes())


class Preview(unittest.TestCase):
    def test_ops_map_to_blocks(self):
        pv = preview(sample_project("TOOLCHG_rough1_finish3_metric"))
        self.assertIsNone(pv.error)
        self.assertEqual([(o.op_index, [b.op_name for b in o.blocks]) for o in pv.ops],
                         [(0, ["tool_change"]), (1, ["area_clear"]), (2, ["tool_change"]), (3, ["finish"])])
        self.assertEqual(pv.for_op(1).passes, 26)

    def test_prefinish_and_implicit_tool(self):
        pv = preview(sample_project("PREFINISH_area_metric"))
        self.assertEqual([(o.op_index, len(o.blocks)) for o in pv.ops], [(None, 1), (0, 2)])

    def test_error_stops_at_failing_op(self):
        p = sample_project("TWOOP_area_finish_metric")
        p.operations.insert(1, FinishPass(3, Point(120, 5), ["g0", "g1"], offset_x=1, external=False))
        pv = preview(p)
        self.assertIsNone(pv.data)
        self.assertEqual(pv.error_op, 1)
        self.assertIn("internal finish", pv.error)
        self.assertEqual([o.op_index for o in pv.ops], [None, 0])  # the area clear before it still previews


class Canvas(unittest.TestCase):
    def test_view_round_trip_and_zoom_at(self):
        v = ViewTransform(scale=4, ox=500, oy=300)
        self.assertEqual(v.to_world(*v.to_screen(40, -25)), (40, -25))
        sx, sy = v.to_screen(60, -50)
        v.zoom_at(sx, sy, 2.5)
        self.assertEqual(v.to_screen(60, -50), (sx, sy))  # the point under the fingers stays put
        v.zoom_at(0, 0, 1e9)
        self.assertEqual(v.scale, 100.0)  # clamped (0.01 mm per pixel)

    def test_fit(self):
        v = ViewTransform()
        v.fit((-120, 10, -8, 76), 1000, 600)
        x0, _ = v.to_screen(0, -120)
        x1, _ = v.to_screen(0, 10)
        self.assertAlmostEqual((x0 + x1) / 2, 500)

    def test_segments_and_hit(self):
        pv = preview(sample_project("ARC_finish_metric"))
        segs = segments(pv)
        arcs = [s for s in segs if s.centre is not None]
        self.assertEqual(len(arcs), 2)
        pts = arcs[0].polyline()  # convex R5.4 corner: from (49.2, -25) to (60, -30.4)
        self.assertEqual((round(pts[0][0], 6), round(pts[0][1], 6)), (49.2, -25))
        self.assertEqual((round(pts[-1][0], 6), round(pts[-1][1], 6)), (60, -30.4))
        mid = pts[len(pts) // 2]
        self.assertGreater(mid[1], -30.4)  # bulges towards +Z, i.e. the outside of the corner
        v = ViewTransform(scale=5, ox=600, oy=400)
        sx, sy = v.to_screen(40, -12)  # on the X40 diameter of the finish pass
        self.assertEqual(hit(segs, v, sx + 3, sy, 6).op_index, 0)
        self.assertIsNone(hit(segs, v, sx + 40, sy - 40, 6))
        self.assertIsNotNone(hit(segs, v, sx + 20, sy, 24))  # a finger reaches further than a mouse


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GtkSmoke(unittest.TestCase):
    def test_window_loads_example(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib

        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        state = {}

        def run():
            w = MainWindow(app)
            w.load(EXAMPLES / "rough_and_finish.alcam")
            rows = 0
            while w.op_list.get_row_at_index(rows) is not None:
                rows += 1
            state["rows"] = rows
            state["export"] = w.export_action.get_enabled()
            w._select_op(1)
            state["selected"] = w.canvas.selected
            state["text"] = w.program_view.get_buffer().props.text.count("\n") + 1
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(state, {"rows": 4, "export": True, "selected": 1, "text": 460})


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GtkEditing(unittest.TestCase):
    def test_add_edit_delete_undo(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib

        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.load(EXAMPLES / "area_clear.alcam")
            w._select_op(0)
            w.add_op("finish")
            out["ops"] = [o.type for o in w.project.operations]
            w.apply_edit_from_editor("Change Feed", lambda p: setattr(p.operations[1], "feed", 0.1))
            out["feed_in_prg"] = b"G1X40.Z0.F0.2\r\n" in w.pv.data
            out["dirty"] = w.undo.modified
            w._delete_selected()
            out["after_delete"] = len(w.project.operations)
            w._undo()
            out["after_undo"] = len(w.project.operations)
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(out, {"ops": ["area_clear", "finish"], "feed_in_prg": True, "dirty": True,
                               "after_delete": 1, "after_undo": 2})
