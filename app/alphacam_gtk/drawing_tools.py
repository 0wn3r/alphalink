"""Drawing tools on the Part canvas (TOUCH.md 5): Select, Line, Arc, Fillet, Chamfer, Corner, Edit Element, the More
commands (circle, tangents, parallel, split arc, blend arc, Mirror Image), ALPHA-T's one-letter keys, contour picking.

Rough placement by tap or click is snapped (end points, axis, 1 mm grid) and direction-locked (Z, X, 45 degrees);
exact values come from the command entry under the canvas: `X,Z` absolute or `@dX,dZ` relative to the last point,
as in ALPHA-T. Every change is one undoable step through the window."""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from alphacam import drawing  # noqa: E402
from alphacam.model.geometry import Arc, Line, Point  # noqa: E402
from .hit_keys import CURSOR_KEYS, STEP_KEYS, hit_action, key_char  # noqa: E402
from .i18n import N_, _, ngettext  # noqa: E402

CONSTRUCT_MENU = [(N_("Circle…"), "circle"), (N_("Tangent"), "tangent"), (N_("Tangent at an Angle…"), "tangent-angle"),
                  (N_("Parallel Line…"), "parallel"), (N_("Split Arc"), "split"), (N_("Blend Arc… (Key O)"), "blend"),
                  (N_("Mirror Image"), "mirror")]
TOOLS = [("select", N_("Select")), ("line", N_("Line")), ("arc", N_("Arc")), ("fillet", N_("Fillet")),
         ("chamfer", N_("Chamfer")), ("corner", N_("Corner"))]
# tooltips with the one-letter keys (hit_keys.py)
TOOL_TIPS = {
    "select": N_("Select an element or an operation. Press L or A to select the nearest line or arc"),
    "line": N_("Draw lines. Press X to type X,Z, D to type length and angle, + to draw by taps, S to start at the cursor"),
    "arc": N_("Draw an arc: start, a point on the arc, end (key W)"),
    "fillet": N_("Round a corner. Press F to round the corner nearest the cursor"),
    "chamfer": N_("Chamfer a corner (key B)"),
    "corner": N_("Trim or extend two lines to meet (key K)"),
}
FILLET_CHIPS = (0.5, 1, 2, 3, 5)
CHAMFER_CHIPS = (0.5, 1, 2, 3)
FILLET_CHIPS_INCH = (0.01, 0.02, 0.04, 0.06, 0.1)
CHAMFER_CHIPS_INCH = (0.01, 0.02, 0.04, 0.06)
# what Enter in the command entry does, after the ALPHA-T key that asked for the point (hit_keys.py)
ENTRY_MODES = {
    "point": N_("X,Z  or  @dX,dZ  then Enter (line tool)"),
    "arc": N_("Arc: X,Z  or  @dX,dZ  then Enter: start, a point on the arc, end"),
    "line-la": N_("Draw line: length,angle  then Enter (0° = towards -Z, 90° = +X)"),
    "move-abs": N_("Move the cursor to X,Z  then Enter"),
    "move-inc": N_("Move the cursor by dX,dZ  then Enter"),
    "move-la": N_("Move the cursor by length,angle  then Enter (0° = towards -Z, 90° = +X)"),
}
RE_COORD = re.compile(r"\s*(@)?\s*(-?\d*\.?\d+)\s*[,; ]\s*(-?\d*\.?\d+)\s*$")


class DrawingTools:
    def __init__(self, window, canvas):
        self.w, self.c = window, canvas
        self.points: list[Point] = []  # line tool: the chain being drawn
        self.selected_element: str | None = None
        self.pick_op: int | None = None
        self.arc_pts: list[Point] = []  # arc tool: start, through, end
        self.corner_first: str | None = None  # corner tool: first line
        self.cursor: Point | None = None  # ALPHA-T's cursor: the pointer's last place, or where the keys put it
        self.step = 10.0  # cursor key step (radius / Z)
        self.entry_mode = "point"
        self.measure: tuple[str, Point | None] | None = None
        self._prev_view = None

        # palette (a bottom bar: thumb reach in touch mode)
        self.palette = Adw.ToggleGroup()
        for name, label in TOOLS:
            self.palette.add(Adw.Toggle(name=name, label=_(label), tooltip=_(TOOL_TIPS[name])))
        self.palette.set_active_name("select")
        self.palette.connect("notify::active-name", lambda *_: self.set_tool(self.palette.get_active_name()))

        self.entry = Gtk.Entry(hexpand=True, placeholder_text=_(ENTRY_MODES["point"]))
        self.entry.add_css_class("command-entry")
        self.entry.connect("activate", self._entry_activate)
        entry_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        entry_keys.connect("key-pressed", self._entry_key)
        self.entry.add_controller(entry_keys)
        # shown while Line/Arc is the tool, so it is always where the user expects it; usable once there is a chain
        self.done = Gtk.Button(label=_("_Done"), use_underline=True, visible=False, sensitive=False,
                               tooltip_text=_("Finish Line (Esc)"))
        self.done.add_css_class("suggested-action")
        self.done.connect("clicked", lambda *_: self.finish_chain())
        self.delete_el = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Delete Element (Delete or Minus Key)"),
                                    visible=False)
        self.delete_el.connect("clicked", lambda *_: self.delete_selected_element())
        self.edit_el = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text=_("Edit Element (Key E)"), visible=False)
        self.edit_el.connect("clicked", lambda *_: self.edit_selected_element())

        self.keypad_button = Gtk.Button(icon_name="input-dialpad-symbolic", tooltip_text=_("Keypad"), visible=False)
        self.keypad_button.connect("clicked", lambda *_: self.open_keypad())
        # wraps the entry under the tool names when the Part page is narrow (editor open on half a screen), instead
        # of cutting the tool names short
        self.bar = Adw.WrapBox(child_spacing=6, line_spacing=6, margin_start=6, margin_end=6, margin_top=6,
                               margin_bottom=6, justify=Adw.JustifyMode.FILL, justify_last_line=True)
        # ALPHA-T's other drawing commands (German manual pp. 20-34): circle, tangents, parallel, split, mirror, blend
        more = Gio.Menu()
        for label, kind in CONSTRUCT_MENU:
            item = Gio.MenuItem.new(_(label), None)
            item.set_action_and_target_value("win.construct", GLib.Variant.new_string(kind))
            more.append_item(item)
        self.more = Gtk.MenuButton(label=_("More"), menu_model=more, tooltip_text=_("More Drawing Commands"))
        construct = Gio.SimpleAction.new("construct", GLib.VariantType.new("s"))
        construct.connect("activate", lambda _a, v: self.start_construct(v.get_string()))
        window.add_action(construct)
        self.construct: dict | None = None
        self.construct_banner = Adw.Banner(button_label=_("_Cancel"))
        self.construct_banner.connect("button-clicked", lambda *_: self._banner_button())
        # picking a contour: a way out without applying it (the banner has room for one button only)
        self.cancel_pick_btn = Gtk.Button(label=_("Cancel Picking"), visible=False,
                                          tooltip_text=_("Cancel Picking (Esc)"))
        self.cancel_pick_btn.connect("clicked", lambda *_: self.cancel_pick())
        for wdg in (self.palette, self.more, self.keypad_button, self.entry, self.done, self.cancel_pick_btn,
                    self.edit_el,
                    self.delete_el):
            self.bar.append(wdg)

        # contour picking banner
        self.pick_banner = Adw.Banner(title=_("Tap the contour elements in cutting order"), button_label=_("_Done"))
        self.pick_banner.connect("button-clicked", lambda *_: self.finish_pick())

        canvas.connect("canvas-tapped", self._tapped)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        canvas.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        canvas.add_controller(keys)
        esc = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        esc.connect("key-pressed", self._window_esc)
        window.add_controller(esc)
        # ALPHA-T's one-letter commands (hit_keys.py); bubble phase, so focused text fields get the keys first
        hit = Gtk.EventControllerKey()
        hit.connect("key-pressed", self._hit_key_pressed)
        window.add_controller(hit)

    # --- tools ------------------------------------------------------------------------------------------

    def set_tool(self, name: str) -> None:
        if self.palette.get_active_name() != name:
            self.palette.set_active_name(name)
            return
        self.finish_chain()
        self.c.tool = name
        self.done.set_visible(name in ("line", "arc"))
        if self.entry_mode == "point":
            self.entry.set_placeholder_text(_(ENTRY_MODES["arc" if name == "arc" else "point"]))
        self._select_element(None)
        if name != "select":
            self.w._select_op(None)
        self.c.queue_draw()

    def _tapped(self, canvas, x, z, tol, op_index, element) -> None:
        project = self.w.project
        if project is None:
            return
        if self.pick_op is not None:
            self._toggle_pick(element)
            return
        self.cursor, self.c.cursor = Point(x, z), None
        if self.construct is not None:
            self._construct_tap(Point(x, z), tol, element)
            return
        if self.measure is not None:
            self._measure_point(Point(x, z))
            return
        tool = self.c.tool
        if tool == "select":
            if op_index >= 0:
                self._select_element(None)
                self.w._select_op(op_index)
            elif element:
                self.w._select_op(None)
                self._select_element(element)
            else:
                self.w._select_op(None)
                self._select_element(None)
        elif tool == "line":
            pt, _k = drawing.snap(project, Point(x, z), tol, grid=1.0 if project.units == "mm" else 0.05)
            self.add_point(pt)
        elif tool == "arc":
            pt, _k = drawing.snap(project, Point(x, z), tol, grid=1.0 if project.units == "mm" else 0.05)
            self._arc_point(pt)
        elif tool == "corner":
            self._corner_pick(element)
        elif tool in ("fillet", "chamfer"):
            corner = self._corner_near(Point(x, z), tol)
            if corner is None:
                self.w.toast(_("Tap a corner between two lines"))
                return
            self._size_popover(tool, corner)

    def _motion(self, ctl, sx, sy) -> None:
        if self.w.project is None:
            return
        x, z = self.c.view.to_world(sx, sy)
        self.cursor = Point(x, z)  # the mouse is ALPHA-T's cursor too
        if self.c.cursor is not None:
            self.c.cursor = None
            self.c.queue_draw()
        if self.c.tool not in ("line", "arc"):
            return
        tol = 10 / self.c.view.scale
        pt, kind = drawing.snap(self.w.project, Point(x, z), tol, grid=1.0 if self.w.project.units == "mm" else 0.05)
        if self.c.tool == "line" and self.points and kind != "end":
            pt = drawing.lock_direction(self.points[-1], pt)
        self.c.marker = (pt, kind)
        self.c.rubber = (self.points[-1], pt) if self.c.tool == "line" and self.points else None
        self.c.queue_draw()

    def _window_esc(self, ctl, keyval, code, state) -> bool:
        """Esc ends whatever is going on (a construction, picking, a typed command, a line) and gives the keyboard
        back to the drawing, so ALPHA-T's letter keys work again (after X the entry has the focus)."""
        if keyval != Gdk.KEY_Escape or self.w.get_visible_dialog() is not None:
            return False
        cleared = self.w.dismiss_toasts()
        handled = True
        if self.construct is not None:
            self.cancel_construct()
        elif self.pick_op is not None:
            self.cancel_pick()
        elif self.measure is not None or self.entry_mode != "point":
            self.measure = None
            self._ask_done()
            self.entry.set_text("")
            self.w.readout.set_label(_("Command aborted"))
        elif self.points or self.arc_pts or self.corner_first:
            self.finish_chain()
            self.entry.set_text("")
        else:
            handled = cleared
        focus = self.w.get_focus()
        if focus is not None and (focus is self.entry or focus.is_ancestor(self.entry)):
            self.entry.set_text("")
            self.c.grab_focus()
            handled = True
        return handled

    def _key(self, ctl, keyval, code, state) -> bool:
        if keyval == Gdk.KEY_Escape and (self.points or self.pick_op is not None):
            self.cancel_pick() if self.pick_op is not None else self.finish_chain()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected_element:
            self.delete_selected_element()
            return True
        return False

    def _hit_key_pressed(self, ctl, keyval, code, state) -> bool:
        w = self.w
        if (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
                or w.project is None or w.get_visible_dialog() is not None
                or w.stack.get_visible_child_name() != "part" or isinstance(w.get_focus(), Gtk.Editable)):
            return False
        name = Gdk.keyval_name(keyval) or ""
        on_canvas = w.get_focus() in (None, self.c)
        if name in CURSOR_KEYS or name in STEP_KEYS or name in ("Home", "End", "Return", "KP_Enter"):
            # the cursor keys and Enter keep their usual meaning on buttons and lists
            if not on_canvas:
                return False
            return self.cursor_key(name)
        u = Gdk.keyval_to_unicode(keyval)
        return self.hit_key(key_char(chr(u) if u else "", code))

    def _entry_key(self, ctl, keyval, code, state) -> bool:
        """A coordinate never has letters, so in the command entry a letter is ALPHA-T's one-letter command too
        (typing X,Z then pressing A works as in ALPHA-T, without clicking the drawing first)."""
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        u = Gdk.keyval_to_unicode(keyval)
        char = key_char(chr(u) if u else "", code)
        if not (char.isalpha() and char.isascii()) or self.pick_op is not None:
            return False
        self.hit_key(char)
        return True

    def hit_key(self, char: str) -> bool:
        """Run ALPHA-T's command for a typed character; True when it was one."""
        action = hit_action(char, picking=self.pick_op is not None, selected=self.selected_element is not None)
        if action is None:
            return False
        p, cur = self.w.project, self.cursor
        if action == "pick-done":
            self.finish_pick()
        elif action == "accept":
            return self.accept_cursor()
        elif action == "type":
            self._ask("point", char)
        elif action in ("line", "freehand"):
            self._ask("point") if action == "line" else self._line_tool()
        elif action in ("line-la", "move-abs", "move-inc", "move-la"):
            self._ask(action)
        elif action == "start":
            self.move_cursor(cur or Point(0, 0), start=True)
        elif action in ("find-point", "find-intersection"):
            pt = (drawing.nearest_point if action == "find-point" else drawing.nearest_intersection)(
                p, cur or Point(0, 0))
            if pt is None:
                self.w.toast(_("No point to find") if action == "find-point" else _("No intersection to find"))
            else:
                self.move_cursor(pt, start=self.c.tool == "line" and not self.points)
        elif action in ("find-line", "find-arc"):
            gid = drawing.nearest_element(p, cur or Point(0, 0), Line if action == "find-line" else Arc)
            if gid is None:
                self.w.toast(_("No line to find") if action == "find-line" else _("No arc to find"))
            else:
                self.set_tool("select")
                self._select_element(gid)
                self.w.toast(_("{what} found. Press Delete or minus to erase it").format(what=gid))
        elif action == "erase":
            self.delete_selected_element()
        elif action == "radius":
            corner = self._corner_near(cur, float("inf")) if cur is not None else None
            if corner is None:
                self.set_tool("fillet")
            else:
                self._size_popover("fillet", corner)
        elif action == "blend":
            self.start_construct("blend")  # ALPHA-T's O: 2 PKT RADIUS
        elif action in ("corner", "chamfer", "arc"):
            self.set_tool(action)
        elif action == "edit":
            if self.selected_element:
                self.edit_selected_element()
            else:
                self.w.toast(_("Select a line or arc first, or press L or A to find the nearest"))
        elif action in ("measure-xz", "measure-la"):
            self.start_measure(action[-2:])
        elif action == "text":
            self.text_dialog(cur or Point(0, 0))
        elif action == "erase-text":
            i = drawing.nearest_text(p, cur or Point(0, 0))
            if i is None:
                self.w.toast(_("No text to erase"))
            elif self.w.apply_edit(_("Erase text"), lambda q: q.texts.pop(i)):
                self.w.refresh()
        elif action == "erase-drawing":
            self.erase_drawing_dialog()
        elif action in ("zoom-in", "fit"):
            self._prev_view = self.c.view.copy()
            self.c.zoom(1.25) if action == "zoom-in" else self.c.fit()
        elif action == "recall-view":
            if self._prev_view is not None:
                self._prev_view, self.c.view = self.c.view.copy(), self._prev_view
                self.c._auto_fit = False
                self.c.queue_draw()
        elif action == "cam":
            self.w.add_op_button.popup()
        return True

    # --- ALPHA-T cursor ---------------------------------------------------------------------------------

    def cursor_key(self, name: str) -> bool:
        """Cursor keys move the ALPHA-T cursor by the step (PgUp x10, PgDn :10), Home: middle of the view,
        End: the reference point X0 Z0, Enter accepts the cursor (see accept_cursor)."""
        if name in ("Return", "KP_Enter"):
            return self.hit_key("\r")
        cur = self.cursor or Point(0, 0)
        if name in STEP_KEYS:
            self.step = min(max(self.step * STEP_KEYS[name], 0.001), 1000.0)
            self.w.readout.set_label(_("Cursor step {step}").format(step=f"{self.step:g}"))
            return True
        if name == "Home":
            x, z = self.c.view.to_world(self.c.get_width() / 2, self.c.get_height() / 2)
            pt = Point(round(x, 3), round(z, 3))
        elif name == "End":
            pt = Point(0, 0)
        else:
            dx, dz = CURSOR_KEYS[name]
            pt = Point(round(cur.x + 2 * dx * self.step, 6), round(cur.z + dz * self.step, 6))
        self.move_cursor(pt)
        return True

    def move_cursor(self, pt: Point, start: bool = False) -> None:
        """Put the ALPHA-T cursor on pt; `start`: a line starts there (ALPHA-T S, or a move with the Line tool)."""
        self.cursor = pt
        self.c.cursor = pt
        self.c.marker = (pt, "end")
        self.w.readout.set_label(f"X {pt.x:9.3f}   Z {pt.z:9.3f}")
        if start:
            self._line_tool()
            self.finish_chain()
            self.points.append(pt)
            self.done.set_sensitive(True)
            self.c.marker = (pt, "end")
        self.c.grab_focus()
        self.c.queue_draw()

    def accept_cursor(self) -> bool:
        """Enter on the drawing: the cursor is the next measuring point or the next point of the line."""
        if self.cursor is None:
            return False
        if self.measure is not None:
            self._measure_point(self.cursor)
        elif self.c.tool == "line":
            self.add_point(self.cursor)
        elif self.c.tool == "arc":
            self._arc_point(self.cursor)
        else:
            return False
        return True

    def _line_tool(self) -> None:
        if self.c.tool not in ("line", "arc"):
            self.set_tool("line")

    def _ask(self, mode: str, text: str = "") -> None:
        """Focus the command entry for a typed point; `mode` says what Enter does with it (ENTRY_MODES)."""
        if mode in ("point", "line-la"):
            self._line_tool()
        self.entry_mode = mode
        self.entry.set_placeholder_text(_(ENTRY_MODES[mode]))
        self.entry.grab_focus()
        self.entry.set_text(text)
        self.entry.set_position(-1)

    # --- measure (ALPHA-T M, N) -----------------------------------------------------------------------------

    def start_measure(self, kind: str) -> None:
        self.measure = (kind, None)
        self.w.readout.set_label(_("Measure: tap the 1st point, or move the cursor and press Enter"))

    def _measure_point(self, p: Point) -> None:
        pt = drawing.nearest_point(self.w.project, p) or p  # ALPHA-T: the point nearest the cursor
        kind, first = self.measure
        self.c.marker = (pt, "end")
        self.c.queue_draw()
        if first is None:
            self.measure = (kind, pt)
            self.w.readout.set_label(_("Measure: now the 2nd point"))
            return
        self.measure = None
        if kind == "xz":
            dx, dz = drawing.measure_xz(first, pt)
            text = f"X {dx:g}  Z {dz:g}"
        else:
            length, angle = drawing.measure_la(first, pt)
            text = f"L {length:.4g}  A {angle:.4g}°"
        self.w.readout.set_label(text)
        self.w.toast(text)

    # --- text (ALPHA-T T) and erase drawing (Q) -------------------------------------------------------------

    def text_dialog(self, p: Point) -> None:
        alert = Adw.AlertDialog(heading=_("Text"), body=_("At X{x} Z{z}").format(x=f"{p.x:g}", z=f"{p.z:g}"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        text = Gtk.Entry(activates_default=True)
        size = Adw.SpinRow.new_with_range(0.1, 100, 0.5)
        size.set_title(_("Height"))
        size.set_value(5 if self.w.project.units == "mm" else 0.2)
        size.set_digits(2)
        group = Adw.PreferencesGroup()
        group.add(size)
        box.append(text)
        box.append(group)
        alert.set_extra_child(box)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("add", _("_Add"))
        alert.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("add")
        alert.set_close_response("cancel")

        def done(_d, response):
            if response == "add" and text.get_text().strip():
                t, h = text.get_text(), size.get_value()
                if self.w.apply_edit(_("Text"), lambda q: drawing.add_text(q, p, t, h)):
                    self.w.refresh()

        alert.connect("response", done)
        alert.present(self.w)
        text.grab_focus()

    def erase_drawing_dialog(self) -> None:
        alert = Adw.AlertDialog(heading=_("Erase All of the Drawing?"),
                                body=_("Lines, arcs and text are erased. Operations stay, without their contours. "
                                       "Undo brings the drawing back."))
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("erase", _("_Erase"))
        alert.set_response_appearance("erase", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("erase")  # ALPHA-T: Enter erases, Esc aborts
        alert.set_close_response("cancel")

        def done(_d, response):
            if response == "erase" and self.w.apply_edit(_("Erase drawing"), drawing.erase_drawing):
                self.finish_chain()
                self._select_element(None)
                self.w.refresh()

        alert.connect("response", done)
        alert.present(self.w)

    # --- line tool --------------------------------------------------------------------------------------

    def add_point(self, pt: Point) -> None:
        if self.points:
            prev = self.points[-1]
            if pt not in drawing.endpoints(self.w.project):
                pt = drawing.lock_direction(prev, pt)
            if pt == prev:
                return
            if not self.w.apply_edit(_("Draw line"), lambda p: drawing.add_line(p, prev, pt)):
                return
            self.w.refresh()
        self.points.append(pt)
        self.cursor = pt
        self.done.set_sensitive(True)
        self.c.marker = (pt, "end")
        self.c.queue_draw()

    def _arc_point(self, pt: Point) -> None:
        """Arc tool: start, a point on the arc, end (TOUCH.md 5.2). Exact points can be typed as for lines."""
        self.arc_pts.append(pt)
        self.done.set_sensitive(True)  # Done / Esc drops a half-drawn arc
        self.c.marker = (pt, "end")
        hints = [_("Arc: now a point on the arc"), _("Arc: now the end point")]
        if len(self.arc_pts) < 3:
            self.w.readout.set_label(hints[len(self.arc_pts) - 1])
            self.c.queue_draw()
            return
        a, m, b = self.arc_pts
        self.arc_pts = []
        if self.w.apply_edit(_("Draw arc"), lambda p: drawing.add_arc_3p(p, a, m, b)):
            self.w.refresh()

    def _corner_pick(self, element: str) -> None:
        """Corner tool: tap two lines; both are trimmed or extended to meet."""
        if not element:
            return
        if self.corner_first is None or self.corner_first == element:
            self.corner_first = element
            self.c.highlight = {element}
            self.w.readout.set_label(_("Corner: now tap the second line"))
            self.c.queue_draw()
            return
        first, self.corner_first = self.corner_first, None
        self.c.highlight = set()
        if self.w.apply_edit(_("Corner"), lambda p: drawing.corner(p, first, element)):
            self.w.refresh()

    def after_history(self) -> None:
        """After undo / redo: a line being drawn continues from a point that still exists (Ctrl+Z of the last line
        goes back to its start), and a selected element that is gone is deselected."""
        ends = set(drawing.endpoints(self.w.project))
        while len(self.points) > 1 and self.points[-1] not in ends:
            self.points.pop()
        if self.points and self.points[-1] not in ends and len(self.w.project.geometry):
            self.points.clear()
        if self.points:
            self.cursor = self.points[-1]
            self.c.marker = (self.points[-1], "end")
        self.done.set_sensitive(bool(self.points))
        if self.selected_element and self._element(self.selected_element) is None:
            self._select_element(None)
        self.c.queue_draw()

    def finish_chain(self) -> None:
        self.arc_pts = []
        self.corner_first = None
        self.points.clear()
        self.done.set_sensitive(False)
        self.c.rubber = None
        self.c.marker = None
        self.c.queue_draw()

    def _ask_done(self) -> None:
        self.entry_mode = "point"
        self.entry.set_placeholder_text(_(ENTRY_MODES["arc" if self.c.tool == "arc" else "point"]))

    def _entry_activate(self, entry) -> None:
        mode = self.entry_mode
        m = RE_COORD.match(entry.get_text())
        if not m or (m[1] and mode != "point"):
            self.w.toast(_("Type X,Z (diameter, Z) or @dX,dZ") if mode == "point" else _(ENTRY_MODES[mode]))
            return
        x, z = float(m[2]), float(m[3])
        if mode != "point":
            self._ask_done()
            entry.set_text("")
            if mode == "line-la":  # ALPHA-T D: a line of that length and angle from the last point (or the cursor)
                here = self.points[-1] if self.points else (self.cursor or Point(0, 0))
                self._line_tool()
                if not self.points:
                    self.points.append(here)
                self.add_point_exact(drawing.polar(here, x, z))
                return
            cur = self.cursor or Point(0, 0)
            target = {"move-abs": Point(x, z), "move-inc": Point(round(cur.x + x, 6), round(cur.z + z, 6)),
                      "move-la": drawing.polar(cur, x, z)}[mode]
            self.move_cursor(target, start=self.c.tool == "line")
            return
        if m[1]:
            last = self.arc_pts[-1] if self.c.tool == "arc" and self.arc_pts else (self.points[-1] if self.points
                                                                                     else None)
            if last is None:
                self.w.toast(_("@ needs a previous point"))
                return
            prev = last
            x, z = round(prev.x + x, 6), round(prev.z + z, 6)  # no float noise (-34.800000000000004)
        if self.c.tool == "arc":
            self._arc_point(Point(x, z))
            entry.set_text("")
            return
        if self.c.tool != "line":
            self.set_tool("line")
        self.add_point_exact(Point(x, z))
        entry.set_text("")

    def add_point_exact(self, pt: Point) -> None:
        """Next point of the line, typed: no snapping, no direction lock."""
        if self.points:
            prev = self.points[-1]
            if pt == prev:
                return
            if not self.w.apply_edit(_("Draw line"), lambda p: drawing.add_line(p, prev, pt)):
                return
            self.w.refresh()
        self.points.append(pt)
        self.cursor = pt
        self.done.set_sensitive(True)
        self.c.marker = (pt, "end")
        self.c.queue_draw()

    def open_keypad(self) -> None:
        from .keypad import Keypad

        def done(text):
            self.entry.set_text(text)
            self._entry_activate(self.entry)

        Keypad(done, title=_("Next Point") if self.points else _("Start Point")).present(self.w)

    def set_touch(self, touch: bool) -> None:
        """Touch mode: the keypad replaces the system keyboard for points (TOUCH.md 6)."""
        self.keypad_button.set_visible(touch)

    # --- select / delete ---------------------------------------------------------------------------------

    def _select_element(self, gid: str | None) -> None:
        self.selected_element = gid
        self.c.highlight = {gid} if gid else set()
        self.delete_el.set_visible(gid is not None)
        self.edit_el.set_visible(gid is not None)
        if gid is None:
            self.w.readout.set_label(" ")  # not the last selected element any more
        if gid:
            g = next(g for g in self.w.project.geometry if g.id == gid)
            kind = _("Line") if isinstance(g, Line) else _("Arc")
            self.w.readout.set_label(f"{gid} {kind}  X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}")
            self.c.grab_focus()
        self.c.queue_draw()

    def delete_selected_element(self) -> None:
        gid = self.selected_element
        if gid and self.w.apply_edit(_("Delete {what}").format(what=gid), lambda p: drawing.delete_element(p, gid)):
            self._select_element(None)
            self.w.refresh()
            self.w.add_toast(Adw.Toast(title=_("Deleted {what}").format(what=gid), button_label=_("_Undo"),
                                              action_name="win.undo", timeout=6))

    def edit_selected_element(self) -> None:
        """Change the selected line's or arc's end points (and an arc's radius) by typed values. Ends of other
        elements that met it move too unless switched off, so the profile stays joined."""
        gid = self.selected_element
        g = self._element(gid) if gid else None
        if g is None:
            return
        dlg = Adw.AlertDialog(heading=_("Edit {what}").format(what=gid),
                              body=_("End points as X (diameter), Z."))
        group = Adw.PreferencesGroup()
        rows = []
        fields = [(_("Start X"), g.a.x), (_("Start Z"), g.a.z), (_("End X"), g.b.x), (_("End Z"), g.b.z)]
        if isinstance(g, Arc):
            fields.append((_("Radius"), g.radius()))
        for label, value in fields:
            r = Adw.SpinRow.new_with_range(-100000, 100000, 0.1)
            r.set_title(label)
            r.set_digits(4 if self.w.project.units == "inch" else 3)
            r.set_value(value)
            from .op_editor import fit_spin

            fit_spin(r, -10000, 10000, r.get_digits())
            group.add(r)
            rows.append(r)
        if isinstance(g, Arc):
            rows[-1].get_adjustment().set_lower(0.001)
        follow = Adw.SwitchRow(title=_("Move connected ends too"), subtitle=_("Keeps the profile joined"),
                               active=True)
        group.add(follow)
        dlg.set_extra_child(group)
        dlg.add_response("cancel", _("_Cancel"))
        dlg.add_response("ok", _("_Apply"))
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def response(_d, r):
            if r != "ok":
                return
            v = [row.get_value() for row in rows]
            a, b = Point(v[0], v[1]), Point(v[2], v[3])
            radius = v[4] if len(v) > 4 else None
            if self.w.apply_edit(_("Edit {what}").format(what=gid),
                                 lambda p: drawing.edit_element(p, gid, a, b, radius, follow.get_active())):
                self.w.refresh()
                self._select_element(gid)

        dlg.connect("response", response)
        dlg.present(self.w)

    # --- fillet / chamfer ---------------------------------------------------------------------------------

    def _corner_near(self, p: Point, tol: float) -> Point | None:
        best, best_d = None, max(tol, 1e-9)
        for q in drawing.endpoints(self.w.project):
            lines = [g for g in self.w.project.geometry if isinstance(g, Line) and q in (g.a, g.b)]
            if len(lines) == 2 and p.distance(q) <= best_d:
                best, best_d = q, p.distance(q)
        return best

    def _size_popover(self, tool: str, corner: Point) -> None:
        pop = Gtk.Popover()
        pop.set_parent(self.c)
        sx, sy = self.c.view.to_screen(corner.x, corner.z)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(sx), int(sy), 1, 1
        pop.set_pointing_to(rect)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = Gtk.Label(label=(_("Radius at {point}") if tool == "fillet" else _("Chamfer at {point}")).format(
            point=f"X{corner.x:g} Z{corner.z:g}"))
        title.add_css_class("heading")
        box.append(title)
        angle = None
        if tool == "chamfer":
            # angle between the chamfer and the more axial line (lathe drawings: "2 × 30°", angle from the axis)
            arow = Gtk.Box(spacing=6)
            arow.append(Gtk.Label(label=_("Angle"), xalign=0, hexpand=True))
            angle = Gtk.SpinButton.new_with_range(1, 89, 1)
            angle.set_digits(1)
            angle.set_value(45)
            angle.set_tooltip_text(_("Between the chamfer and the line along Z; the size is measured along that line"))
            for a in (30, 45, 60):
                ab = Gtk.Button(label=f"{a}°")
                ab.connect("clicked", lambda _b, a=a: angle.set_value(a))
                arow.append(ab)
            arow.append(angle)
            box.append(arow)

        def ang() -> float:
            return angle.get_value() if angle is not None else 45.0

        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5, column_spacing=6,
                            row_spacing=6)
        chip_buttons = []
        inch = self.w.project.units == "inch"
        for v in ((FILLET_CHIPS_INCH if inch else FILLET_CHIPS) if tool == "fillet"
                  else (CHAMFER_CHIPS_INCH if inch else CHAMFER_CHIPS)):
            b = Gtk.Button(label=f"R{v:g}" if tool == "fillet" else f"{v:g}×45°")
            b.add_css_class("pill")
            b.connect("clicked", lambda _b, v=v: (pop.popdown(), self._apply_corner(tool, corner, v, ang())))
            chips.append(b)
            chip_buttons.append((b, v))
        if angle is not None:
            angle.connect("value-changed", lambda s: [b.set_label(f"{v:g}×{s.get_value():g}°")
                                                      for b, v in chip_buttons])
        box.append(chips)
        other = Gtk.Box(spacing=6)
        spin = Gtk.SpinButton.new_with_range(0.01, 100, 0.1)
        spin.set_digits(3)
        spin.set_value(0.04 if inch else 1)
        ok = Gtk.Button(label=_("_Apply"), use_underline=True)
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: (pop.popdown(), self._apply_corner(tool, corner, spin.get_value(), ang())))
        other.append(spin)
        other.append(ok)
        box.append(other)
        pop.set_child(box)
        pop.connect("closed", lambda p: p.unparent())
        pop.popup()

    def _apply_corner(self, tool: str, corner: Point, size: float, angle: float = 45.0) -> None:
        if tool == "fillet":
            label = _("Fillet R{size}").format(size=f"{size:g}")
            edit = lambda p: drawing.fillet(p, corner, size)  # noqa: E731
        else:
            label = _("Chamfer {size}").format(size=f"{size:g}×{angle:g}°")
            edit = lambda p: drawing.chamfer(p, corner, size, angle)  # noqa: E731
        if self.w.apply_edit(label, edit):
            self.w.refresh()

    # --- pick contour (TOUCH.md 7.1) -----------------------------------------------------------------------

    def start_pick(self, op_index: int) -> None:
        self.finish_chain()
        self.pick_op = op_index
        self.c.picked = []
        self.w.stack.set_visible_child_name("part")
        self.pick_banner.set_revealed(True)
        self.cancel_pick_btn.set_visible(True)
        self.c.queue_draw()

    def _toggle_pick(self, gid: str) -> None:
        if not gid:
            return
        if gid in self.c.picked:
            self.c.picked.remove(gid)
        else:
            self.c.picked.append(gid)
        n = len(self.c.picked)
        self.pick_banner.set_title(ngettext("{n} picked — tap again to remove, then Done",
                                            "{n} picked — tap again to remove, then Done", n).format(n=n))
        self.c.queue_draw()

    def finish_pick(self) -> None:
        i, picked = self.pick_op, list(self.c.picked)
        self.cancel_pick()
        if i is None or not picked:
            return
        try:
            order = drawing.pick_order(self.w.project, picked)
        except ValueError as e:
            self.w.toast(str(e))
            return
        if self.w.apply_edit(_("Pick contour"), lambda p: setattr(p.operations[i], "contour", order)):
            self.w.refresh()
            self.w._select_op(i)

    def cancel_pick(self) -> None:
        self.pick_op = None
        self.c.picked = []
        self.pick_banner.set_revealed(False)
        self.cancel_pick_btn.set_visible(False)
        self.pick_banner.set_title(_("Tap the contour elements in cutting order"))
        self.c.queue_draw()

    # --- construction commands (ALPHA-T manual pp. 20-34) ----------------------------------------------------
    # A small state machine: each step says what to tap in the banner; numbers come from a dialog; where there
    # are several solutions they are shown dashed and the tap nearest one picks it (ALPHA-T cycles them with Space).

    def start_construct(self, kind: str) -> None:
        if self.w.project is None:
            return
        self.cancel_pick() if self.pick_op is not None else None
        self.finish_chain()
        self.set_tool("select")
        if kind == "circle":
            c = self.cursor or Point(0, 0)
            self._numbers(_("Circle"), [(_("Centre X (Ø)"), c.x, -2000, 2000), (_("Centre Z"), c.z, -2000, 2000),
                                       (_("Radius"), self._mm(10), 0.001, 1000)],
                          lambda v: self._edit(_("Circle"), lambda p: drawing.add_circle(p, Point(v[0], v[1]), v[2])))
            return
        if kind == "mirror":
            self.mirror_image()
            return
        first = {"tangent": _("Tangent: tap a point, or an arc"), "tangent-angle": _("Tangent at an angle: tap the arc"),
                 "parallel": _("Parallel line: tap the line"), "split": _("Split arc: tap the arc where to split it"),
                 "blend": _("Blend arc: tap the 1st point, line or arc")}[kind]
        self.construct = {"kind": kind, "step": 0, "refs": []}
        self._prompt(first)

    def mirror_image(self) -> None:
        """ALPHA-T MIRROR IMAGE: mirror the whole drawing about the axis at once, show it, then ask whether it is
        correct; No takes it back."""
        if not self.w.apply_edit(_("Mirror Image"), drawing.mirror_axis):
            return
        self.w.refresh()
        self.c.fit()
        # ALPHA-T asks "Is this correct?" over the drawing; a banner keeps the mirrored drawing in full view
        self.construct = {"kind": "mirror-confirm", "step": 0, "refs": []}
        self.construct_banner.set_button_label(_("_No, Take It Back"))
        self._prompt(_("Mirrored about the spindle axis. Is this correct? If yes, just carry on."))

    def _banner_button(self) -> None:
        if self.construct and self.construct["kind"] == "mirror-confirm":  # No: take the mirror image back
            self.w.undo.undo()
            self.w.refresh()
            self.c.fit()
        self.cancel_construct()

    def _prompt(self, text: str) -> None:
        self.construct_banner.set_title(text)
        self.construct_banner.set_revealed(True)

    def cancel_construct(self) -> None:
        self.construct = None
        self.construct_banner.set_button_label(_("_Cancel"))
        self.construct_banner.set_revealed(False)
        self.c.previews = []
        self.c.queue_draw()

    def _edit(self, label: str, fn) -> None:
        try:
            if self.w.apply_edit(label, fn):
                self.w.refresh()
        finally:
            self.cancel_construct()

    def _numbers(self, title: str, fields, done) -> None:
        dlg = Adw.AlertDialog(heading=title)
        group = Adw.PreferencesGroup()
        spins = []
        for label, value, lo, hi in fields:
            from .op_editor import fit_spin

            r = Adw.SpinRow.new_with_range(lo, hi, 0.5)
            r.set_title(label)
            digits = 4 if self.w.project.units == "inch" else 3
            r.set_digits(digits)
            r.set_value(value)
            fit_spin(r, lo, hi, digits)  # room for the widest value ("100.000", not "l00.000")
            group.add(r)
            spins.append(r)
        dlg.set_extra_child(group)
        dlg.add_response("cancel", _("_Cancel"))
        dlg.add_response("ok", _("_Apply"))
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def response(_d, r):
            if r == "ok":
                done([s.get_value() for s in spins])
            else:
                self.cancel_construct()

        dlg.connect("response", response)
        dlg.present(self.w)

    def _mm(self, v: float) -> float:
        """A default size given in mm, in the part's units."""
        return round(v / 25.4, 3) if self.w.project.units == "inch" else v

    def _element(self, gid: str):
        return next((g for g in self.w.project.geometry if g.id == gid), None)

    def _ref_at(self, p: Point, tol: float, element: str):
        """What a construction tap means: an arc or line under the finger, else the nearest point (snapped)."""
        g = self._element(element) if element else None
        from alphacam.model.geometry import Arc

        if isinstance(g, Arc):
            return ("arc", g.id)
        if g is not None and isinstance(g, Line) and drawing.distance_to(g, p) <= tol:
            near = min((g.a, g.b), key=p.distance)
            if near.distance(p) > tol:
                return ("line", g.id)
            return ("point", near)
        pt, _k = drawing.snap(self.w.project, p, tol, grid=1.0 if self.w.project.units == "mm" else 0.05)
        return ("point", pt)

    def _choose(self, previews, then) -> None:
        if not previews:
            self.w.toast(_("No solution for these elements"))
            self.cancel_construct()
            return
        if len(previews) == 1:
            then(previews[0])
            return
        self.c.previews = previews
        self.construct["choices"] = (previews, then)
        self._prompt(_("Tap the one you want (dashed)"))
        self.c.queue_draw()

    @staticmethod
    def _dist_to_preview(pv, p: Point) -> float:
        from alphacam.model.geometry import Line as L

        if pv[0] == "line":
            return drawing.distance_to(L(pv[1], pv[2]), p)
        from alphacam_gtk.canvas.widget import arc_polyline

        return min(Point(x, z).distance(p) for x, z in arc_polyline(pv[1], pv[2], pv[3]))

    def _construct_tap(self, p: Point, tol: float, element: str) -> None:
        st = self.construct
        if "choices" in st:
            previews, then = st["choices"]
            then(min(previews, key=lambda pv: self._dist_to_preview(pv, p)))
            return
        if st["kind"] == "mirror-confirm":  # carrying on means yes
            self.cancel_construct()
            return
        kind, ref = st["kind"], self._ref_at(p, tol, element)
        try:
            if kind == "split":
                if ref[0] != "arc":
                    raise ValueError(_("Tap an arc"))
                self._edit(_("Split arc"), lambda pr: drawing.split_arc(pr, ref[1], p))
            elif kind == "parallel":
                if ref[0] == "point" and element and isinstance(self._element(element), Line):
                    ref = ("line", element)
                if ref[0] != "line":
                    raise ValueError(_("Tap a line"))
                self._numbers(_("Parallel Line"), [(_("Distance"), self._mm(1), 0.001, 1000)], lambda v: self._choose(
                    [("line", a, b) for a, b in drawing.parallels(self.w.project, ref[1], v[0])],
                    lambda pv: self._edit(_("Parallel line"), lambda pr: drawing.add_line(pr, pv[1], pv[2]))))
            elif kind == "tangent-angle":
                if ref[0] != "arc":
                    raise ValueError(_("Tap an arc"))
                self._numbers(_("Tangent at an Angle"), [(_("Angle"), 0.0, -360, 360)], lambda v: self._choose(
                    [("line", a, b) for a, b in drawing.tangents_at_angle(self.w.project, ref[1], v[0])],
                    lambda pv: self._edit(_("Tangent"), lambda pr: drawing.add_line(pr, pv[1], pv[2]))))
            elif kind == "tangent":
                if not st["refs"] and ref[0] == "line":
                    raise ValueError(_("Tap a point or an arc"))
                st["refs"].append(ref)
                if len(st["refs"]) == 1:
                    self._prompt(_("Tangent: now tap the arc"))
                    return
                first, second = st["refs"]
                if second[0] != "arc":
                    st["refs"].pop()
                    raise ValueError(_("Tap an arc"))
                lines = (drawing.tangents_arc_arc(self.w.project, first[1], second[1]) if first[0] == "arc"
                         else drawing.tangents_point_arc(self.w.project, first[1], second[1]))
                self._choose([("line", a, b) for a, b in lines],
                             lambda pv: self._edit(_("Tangent"), lambda pr: drawing.add_line(pr, pv[1], pv[2])))
            elif kind == "blend":
                st["refs"].append(ref)
                if len(st["refs"]) == 1:
                    self._prompt(_("Blend arc: now the 2nd point, line or arc"))
                    return
                refs = [(r[0], r[1], cv) if r[0] == "arc" else r for r in st["refs"] for cv in ((True, False)
                        if r[0] == "arc" else (None,))]
                r1 = [r for r in refs if r[1] == st["refs"][0][1]]
                r2 = [r for r in refs if r[1] == st["refs"][1][1]]

                def solve(v):
                    sols = []
                    for a in r1:
                        for b in r2:
                            try:
                                sols += drawing.blends(self.w.project, a, b, v[0])
                            except (ValueError, ZeroDivisionError):
                                pass
                    self._choose([("arc", s, c, e) for s, c, e in sols],
                                 lambda pv: self._edit(_("Blend arc"), lambda pr: drawing.add_blend(pr, pv[1], pv[2], pv[3])))

                self._numbers(_("Blend Arc"), [(_("Radius"), self._mm(5), 0.001, 1000)], solve)
        except ValueError as e:
            self.w.toast(str(e))
        except Exception as e:  # never leave a command half done: say what happened and start over
            self.w.toast(str(e) or type(e).__name__)
            self.cancel_construct()

