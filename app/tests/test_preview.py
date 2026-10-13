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
        from alphacam.model import AreaClear

        p.operations.insert(1, AreaClear(3, Point(120, 5), ["g0", "g1"], towards=False))  # "away": refused
        pv = preview(p)
        self.assertIsNone(pv.data)
        self.assertEqual(pv.error_op, 1)
        self.assertIn("AWAY", pv.error)
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


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GtkDrawing(unittest.TestCase):
    def test_draw_fillet_pick_post(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(120, 100), first_tool=3))
            d = w.drawing
            for t in ["0,0", "40,0", "@0,-25", "60,-25", "60,-50", "100,-50"]:
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            d.finish_chain()
            d._apply_corner("fillet", Point(60, -25), 5)
            d._apply_corner("fillet", Point(60, -50), 3)
            w.add_op("finish")
            d.start_pick(0)
            for gid in ["g4", "g0", "g1", "g5", "g2", "g6", "g3"]:
                d._toggle_pick(gid)
            d.finish_pick()
            out["contour"] = w.project.operations[0].contour
            w.undo.apply("start", lambda p: setattr(p.operations[0], "start", Point(120, 5)))
            w.refresh()
            out["prg"] = w.pv.data
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(out["contour"], ["g0", "g1", "g2", "g5", "g3", "g6", "g4"])
        # drawn in the app = the ALPHA-T arc sample, byte for byte (billet and first tool as in that drawing)
        self.assertEqual(out["prg"], (SAMPLES / "ARC_finish_metric.PRG").read_bytes())


class GtkChamferAngle(unittest.TestCase):
    def test_popup_angle_sets_chips_and_geometry(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def widgets(root, kind):
            found, c = [], root.get_first_child()
            while c:
                if isinstance(c, kind):
                    found.append(c)
                found += widgets(c, kind)
                c = c.get_next_sibling()
            return found

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(120, 100), first_tool=3))
            d = w.drawing
            for t in ["0,0", "40,0", "40,-25"]:
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            d.finish_chain()
            d._size_popover("chamfer", Point(40, 0))
            pop = widgets(w.canvas, Gtk.Popover)[-1]
            widgets(pop, Gtk.SpinButton)[0].set_value(30)  # the angle
            chips = [b for b in widgets(pop, Gtk.Button) if not isinstance(b, Gtk.SpinButton)]
            out["labels"] = [b.get_label() for b in chips]
            next(b for b in chips if b.get_label() == "2×30°").emit("clicked")
            out["chamfer"] = w.project.geometry[-1]
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertIn("2×30°", out["labels"])
        self.assertEqual(out["chamfer"].b, Point(40, -2))
        self.assertAlmostEqual(out["chamfer"].a.x, 40 - 4 * 0.57735, places=3)


class GtkLineTool(unittest.TestCase):
    def test_done_shown_and_esc_ends_line(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, GLib

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(120, 100), first_tool=3))
            d = w.drawing
            d.set_tool("line")
            out["done"] = (d.done.get_visible(), d.done.get_sensitive())
            d.add_point(Point(0, 0))
            out["after_point"] = d.done.get_sensitive()
            d.entry.grab_focus()
            out["esc"] = d._window_esc(None, Gdk.KEY_Escape, 0, 0)
            out["focus_back"] = w.get_focus() is w.canvas
            out["points"] = list(d.points)
            d._tapped(w.canvas, 0, 0, 1, -1, "")  # line tool tap
            d.set_tool("chamfer")
            d._tapped(w.canvas, 500, 500, 1, -1, "")  # no corner there: a toast, not a crash
            out["chamfer_done_hidden"] = not d.done.get_visible()
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(out["done"], (True, False))
        self.assertTrue(out["after_point"])
        self.assertTrue(out["esc"])
        self.assertTrue(out["focus_back"])  # letter keys work again after Esc
        self.assertEqual(out["points"], [])
        self.assertTrue(out["chamfer_done_hidden"])


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GtkHitKeys(unittest.TestCase):
    def test_alpha_t_keys(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib

        from alphacam import drawing
        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def typed(d, key, text):
            d.hit_key(key)
            d.entry.set_text(text)
            d._entry_activate(d.entry)

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(120, 100), first_tool=3))
            d = w.drawing
            d.hit_key("s")  # start point at the cursor (none yet: X0 Z0)
            out["tool"], out["start"] = w.canvas.tool, list(d.points)
            d.hit_key("4")  # a number goes to the entry
            out["typed"] = d.entry.get_text()
            d.entry.set_text("40,0")
            d._entry_activate(d.entry)
            typed(d, "d", "25,0")  # draw line D,A: 25 long at 0 degrees (towards -Z)
            out["lines"] = [(g.a, g.b) for g in w.project.geometry]
            typed(d, "y", "10,90")  # L,A move: 10 up in radius, the next line starts there
            out["moved"], out["chain"] = d.cursor, list(d.points)
            d.hit_key("p")  # find point
            out["found"] = d.cursor
            d.hit_key("m")  # measure X-Z: X0 Z0 to the found point
            d.cursor_key("End")
            d.cursor_key("Return")
            d.move_cursor(Point(41, -24))
            d.cursor_key("Return")
            out["measure"] = w.readout.get_label()
            typed(d, "g", "41,-12")  # absolute move next to the shoulder
            d.hit_key("l")  # find nearest line
            out["found_line"] = d.selected_element
            d.hit_key("-")  # erase it
            out["after_erase"] = len(w.project.geometry)
            w.apply_edit("text", lambda q: drawing.add_text(q, Point(0, 10), "HELLO"))
            d.hit_key("u")
            out["texts"] = len(w.project.texts)
            d.hit_key("q")
            w.get_visible_dialog().emit("response", "erase")
            out["after_q"] = len(w.project.geometry)
            w._undo()
            out["after_undo"] = len(w.project.geometry)
            w.add_op("finish")
            d.start_pick(0)
            out["pick_letter"] = d.hit_key("x")
            d.hit_key("3")  # finish picking
            out["picking"] = d.pick_op
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(out["tool"], "line")
        self.assertEqual(out["start"], [Point(0, 0)])
        self.assertEqual(out["typed"], "4")
        self.assertEqual(out["lines"], [(Point(0, 0), Point(40, 0)), (Point(40, 0), Point(40, -25))])
        self.assertEqual(out["moved"], Point(60, -25))
        self.assertEqual(out["chain"], [Point(60, -25)])
        self.assertEqual(out["found"], Point(40, -25))
        self.assertEqual(out["measure"], "X 40  Z -25")
        self.assertEqual(out["found_line"], "g1")
        self.assertEqual(out["after_erase"], 1)
        self.assertEqual(out["texts"], 0)
        self.assertEqual(out["after_q"], 0)
        self.assertEqual(out["after_undo"], 1)
        self.assertFalse(out["pick_letter"])
        self.assertIsNone(out["picking"])


class Grid(unittest.TestCase):
    def test_nice_steps(self):
        from alphacam_gtk.canvas.widget import LatheCanvas

        self.assertEqual([LatheCanvas._nice_step(v) for v in (0.3, 1, 1.5, 3, 7, 12, 60)], [0.5, 1, 2, 5, 10, 20, 100])


class ShiftScroll(unittest.TestCase):
    def test_shift_wheel_scrolls_sideways(self):
        from alphacam_gtk.canvas.input import shift_scroll

        self.assertEqual(shift_scroll(0, 1, True), (1, 0))
        self.assertEqual(shift_scroll(0, 1, False), (0, 1))
        self.assertEqual(shift_scroll(2, 1, True), (2, 1))  # a touchpad's own sideways scroll is kept


class Simulation(unittest.TestCase):
    def test_pass_starts(self):
        from alphacam_gtk.canvas.paths import pass_starts

        segs = segments(preview(sample_project("AREA1_metric")))
        starts = pass_starts(segs)
        passes = [(segs[i].block, segs[i].pass_no) for i in starts]
        # the tool block has no moves here, so the stops are exactly the 26 area-clear passes
        self.assertEqual(passes, [(20, n) for n in range(1, 27)])
        self.assertEqual(segs[starts[0]].line, 40)  # pass 1 begins with the G0 on PRG line 40


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GtkKeypad(unittest.TestCase):
    def test_keys(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from alphacam_gtk.keypad import Keypad

        got = []
        k = Keypad(got.append)
        for key in ["4", "0", "→", "±", "2", "5", ".", "5", "⌫"]:
            k._key(key)
        self.assertEqual(k.text(), "40,-25.")
        k._key("ABS")
        self.assertEqual(k.text(), "@40,-25.")


class Translations(unittest.TestCase):
    """Every marked UI string has a Ukrainian translation, and the compiled catalog matches uk.po."""

    def test_ukrainian_complete(self):
        import gettext
        import re

        from alphacam_gtk import i18n

        po = (Path(__file__).resolve().parents[1] / "po" / "uk.po").read_text()
        self.assertNotRegex(po, r'msgstr(\[\d\])? ""\n\n')  # no empty translations (the header is first)
        self.assertNotIn("#, fuzzy", po)
        pot = (Path(__file__).resolve().parents[1] / "po" / "alphalink-cam.pot").read_text()
        ids = [m for m in re.findall(r'\nmsgid "(.+)"\n(?:msgstr|msgid_plural)', pot)]
        uk = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=["uk"])
        untranslated = [m for m in ids if uk.gettext(m) == m and m not in ("ABS",)]
        self.assertEqual(untranslated, [])
        self.assertEqual(uk.ngettext("{n} pass", "{n} passes", 21).format(n=21), "21 прохід")
        self.assertEqual(uk.ngettext("{n} pass", "{n} passes", 24).format(n=24), "24 проходи")
        self.assertEqual(uk.ngettext("{n} pass", "{n} passes", 11).format(n=11), "11 проходів")


class Sheet(unittest.TestCase):
    def test_setup_sheet_like_aprint(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from alphacam_gtk.report import sheet_text

        p = sample_project("TOOLCHG_rough1_finish3_metric")
        text = sheet_text(p, preview(p), "TOOLCHG", moves=False)
        self.assertIn("Operation  : AREA CLEARANCE     Tool No. :   1    80 Deg Rougher 0.8R", text)
        self.assertIn("Maximum X :   -3.200", text)  # = #125, as APRINT shows it
        self.assertIn("Total Passes : 26     Depth Of Cut :   2.000", text)
        moves = sheet_text(p, preview(p), "TOOLCHG", moves=True)
        self.assertIn("  Pass No. : 26", moves)
        self.assertIn("RAPID TO   : X   96.000, Z    5.000", moves)


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GtkArcCorner(unittest.TestCase):
    def test_arc_by_typed_points_and_corner(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(120, 100), first_tool=3))
            d = w.drawing
            d.set_tool("arc")
            for t in ["50,-25", "57.071,-26.464", "60,-30"]:  # R5 corner by three points
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            arc = w.project.geometry[0]
            out["arc"] = (arc.centre, arc.ccw)
            d.set_tool("line")
            for t in ["40,1", "40,-20"]:
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            d.finish_chain()
            for t in ["35,-25", "50,-25"]:
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            d.finish_chain()
            d.set_tool("corner")
            d._corner_pick("g1")
            d._corner_pick("g2")
            out["corner"] = (w.project.geometry[1].b, w.project.geometry[2].a)
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(out["arc"][1], True)
        self.assertAlmostEqual(out["arc"][0].x, 50, places=2)
        self.assertAlmostEqual(out["arc"][0].z, -30, places=2)
        self.assertEqual(out["corner"], (Point(40, -25), Point(40, -25)))


class GtkConstruct(unittest.TestCase):
    def test_parallel_line_by_taps(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(100, 60), first_tool=3))
            d = w.drawing
            d.entry.set_text("40,0")
            d._entry_activate(d.entry)
            d.entry.set_text("40,-20")
            d._entry_activate(d.entry)
            d.finish_chain()
            line = w.project.geometry[0].id
            d.start_construct("parallel")
            d._tapped(w.canvas, 40, -10, 1.0, -1, line)
            dlg = w.get_visible_dialog()
            dlg.emit("response", "ok")
            out["previews"] = len(w.canvas.previews)
            d._tapped(w.canvas, 44, -10, 1.0, -1, "")  # the outer one
            out["new"] = w.project.geometry[-1]
            out["banner"] = d.construct_banner.get_revealed()
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertEqual(out["previews"], 2)
        self.assertEqual((out["new"].a.x, out["new"].b.x), (42.0, 42.0))  # distance 1 (radius) = X+2
        self.assertFalse(out["banner"])


class GtkEditElement(unittest.TestCase):
    def test_edit_dialog_moves_line_and_neighbours(self):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(120, 100), first_tool=3))
            d = w.drawing
            for t in ["0,0", "40,0", "40,-25", "60,-25"]:
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            d.finish_chain()
            d.set_tool("select")
            gid = w.project.geometry[1].id
            d._select_element(gid)
            out["button"] = d.edit_el.get_visible()
            d.edit_selected_element()
            dlg = w.get_visible_dialog()
            rows = []
            child = dlg.get_extra_child()
            stack = [child]
            while stack:  # every SpinRow in the dialog, in order
                c = stack.pop(0)
                if isinstance(c, Adw.SpinRow):
                    rows.append(c)
                    continue
                k = c.get_first_child()
                kids = []
                while k:
                    kids.append(k)
                    k = k.get_next_sibling()
                stack = kids + stack
            out["rows"] = len(rows)
            rows[0].set_value(44)
            rows[2].set_value(44)
            dlg.emit("response", "ok")
            dlg.force_close()
            out["geom"] = [(g.a, g.b) for g in w.project.geometry]
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertTrue(out["button"])
        self.assertEqual(out["rows"], 4)
        self.assertEqual(out["geom"][1], (Point(44, 0), Point(44, -25)))
        self.assertEqual(out["geom"][0][1], Point(44, 0))
        self.assertEqual(out["geom"][2][0], Point(44, -25))


class GtkLetterKeys(unittest.TestCase):
    def test_a_from_entry_and_ukrainian_layout(self):
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, GLib

        from alphacam.model.project import Billet
        from alphacam_gtk.application import Application
        from alphacam_gtk.window import MainWindow

        app = Application()
        out = {}

        def run():
            w = MainWindow(app)
            w.set_project(Project(billet=Billet(100, 60), first_tool=3))
            d = w.drawing
            for t in ["0,0", "40,0", "@0,-25", "60,-25", "60,-50", "100,-50"]:
                d.entry.set_text(t)
                d._entry_activate(d.entry)
            d.finish_chain()
            d._apply_corner("fillet", Point(60, -50), 2)
            arc = next(g.id for g in w.project.geometry if g.type == "arc")
            d.entry.set_text("12")
            out["entry"] = d._entry_key(None, Gdk.KEY_a, 38, 0)  # A typed while the entry has the keyboard
            out["entry_sel"], out["entry_text"] = d.selected_element, d.entry.get_text()
            d._select_element(None)
            out["uk"] = d._hit_key_pressed(None, Gdk.KEY_Cyrillic_ef, 38, 0)  # A key, Ukrainian layout
            out["uk_sel"] = d.selected_element
            out["arc"] = arc
            w.undo.mark_saved()
            app.quit()

        app.connect("activate", lambda *_: GLib.idle_add(run))
        app.run([])
        self.assertTrue(out["entry"])
        self.assertEqual(out["entry_sel"], out["arc"])
        self.assertEqual(out["entry_text"], "12")
        self.assertTrue(out["uk"])
        self.assertEqual(out["uk_sel"], out["arc"])
