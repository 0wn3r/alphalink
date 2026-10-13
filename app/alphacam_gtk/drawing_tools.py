"""Drawing tools on the Part canvas (TOUCH.md 5): Select, Line, Fillet, Chamfer, and contour picking.

Rough placement by tap or click is snapped (end points, axis, 1 mm grid) and direction-locked (Z, X, 45 degrees);
exact values come from the command entry under the canvas: `X,Z` absolute or `@dX,dZ` relative to the last point,
as in ALPHA-T. Every change is one undoable step through the window."""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from alphacam import drawing  # noqa: E402
from alphacam.model.geometry import Line, Point  # noqa: E402
from .i18n import N_, _, ngettext  # noqa: E402

TOOLS = [("select", N_("Select")), ("line", N_("Line")), ("arc", N_("Arc")), ("fillet", N_("Fillet")),
         ("chamfer", N_("Chamfer")), ("corner", N_("Corner"))]
FILLET_CHIPS = (0.5, 1, 2, 3, 5)
CHAMFER_CHIPS = (0.5, 1, 2, 3)
RE_COORD = re.compile(r"\s*(@)?\s*(-?\d*\.?\d+)\s*[,; ]\s*(-?\d*\.?\d+)\s*$")


class DrawingTools:
    def __init__(self, window, canvas):
        self.w, self.c = window, canvas
        self.points: list[Point] = []  # line tool: the chain being drawn
        self.selected_element: str | None = None
        self.pick_op: int | None = None
        self.arc_pts: list[Point] = []  # arc tool: start, through, end
        self.corner_first: str | None = None  # corner tool: first line

        # palette (a bottom bar: thumb reach in touch mode)
        self.palette = Adw.ToggleGroup()
        for name, label in TOOLS:
            self.palette.add(Adw.Toggle(name=name, label=_(label)))
        self.palette.set_active_name("select")
        self.palette.connect("notify::active-name", lambda *_: self.set_tool(self.palette.get_active_name()))

        self.entry = Gtk.Entry(hexpand=True, placeholder_text=_("X,Z  or  @dX,dZ  then Enter (line tool)"))
        self.entry.add_css_class("command-entry")
        self.entry.connect("activate", self._entry_activate)
        # shown while Line/Arc is the tool, so it is always where the user expects it; usable once there is a chain
        self.done = Gtk.Button(label=_("_Done"), use_underline=True, visible=False, sensitive=False,
                               tooltip_text=_("Finish the line (Esc)"))
        self.done.add_css_class("suggested-action")
        self.done.connect("clicked", lambda *_: self.finish_chain())
        self.delete_el = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Delete Element (Del)"),
                                    visible=False)
        self.delete_el.connect("clicked", lambda *_: self.delete_selected_element())

        self.keypad_button = Gtk.Button(icon_name="input-dialpad-symbolic", tooltip_text=_("Keypad"), visible=False)
        self.keypad_button.connect("clicked", lambda *_: self.open_keypad())
        self.bar = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        for wdg in (self.palette, self.keypad_button, self.entry, self.done, self.delete_el):
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

    # --- tools ------------------------------------------------------------------------------------------

    def set_tool(self, name: str) -> None:
        if self.palette.get_active_name() != name:
            self.palette.set_active_name(name)
            return
        self.finish_chain()
        self.c.tool = name
        self.done.set_visible(name in ("line", "arc"))
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
        if self.c.tool not in ("line", "arc") or self.w.project is None:
            return
        x, z = self.c.view.to_world(sx, sy)
        tol = 10 / self.c.view.scale
        pt, kind = drawing.snap(self.w.project, Point(x, z), tol, grid=1.0 if self.w.project.units == "mm" else 0.05)
        if self.c.tool == "line" and self.points and kind != "end":
            pt = drawing.lock_direction(self.points[-1], pt)
        self.c.marker = (pt, kind)
        self.c.rubber = (self.points[-1], pt) if self.c.tool == "line" and self.points else None
        self.c.queue_draw()

    def _window_esc(self, ctl, keyval, code, state) -> bool:
        if keyval != Gdk.KEY_Escape or self.w.get_visible_dialog() is not None:
            return False
        if self.pick_op is not None:
            self.cancel_pick()
            return True
        if self.points or self.arc_pts or self.corner_first:
            self.finish_chain()
            self.entry.set_text("")
            return True
        return False

    def _key(self, ctl, keyval, code, state) -> bool:
        if keyval == Gdk.KEY_Escape and (self.points or self.pick_op is not None):
            self.cancel_pick() if self.pick_op is not None else self.finish_chain()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected_element:
            self.delete_selected_element()
            return True
        return False

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
        self.done.set_sensitive(True)
        self.c.marker = (pt, "end")
        self.c.queue_draw()

    def _arc_point(self, pt: Point) -> None:
        """Arc tool: start, a point on the arc, end (TOUCH.md 5.2). Exact points can be typed as for lines."""
        self.arc_pts.append(pt)
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

    def finish_chain(self) -> None:
        self.arc_pts = []
        self.corner_first = None
        self.points.clear()
        self.done.set_sensitive(False)
        self.c.rubber = None
        self.c.marker = None
        self.c.queue_draw()

    def _entry_activate(self, entry) -> None:
        m = RE_COORD.match(entry.get_text())
        if not m:
            self.w.toast(_("Type X,Z (diameter, Z) or @dX,dZ"))
            return
        x, z = float(m[2]), float(m[3])
        if m[1]:
            last = self.arc_pts[-1] if self.c.tool == "arc" and self.arc_pts else (self.points[-1] if self.points
                                                                                     else None)
            if last is None:
                self.w.toast(_("@ needs a previous point"))
                return
            prev = last
            x, z = prev.x + x, prev.z + z
        if self.c.tool == "arc":
            self._arc_point(Point(x, z))
            entry.set_text("")
            return
        if self.c.tool != "line":
            self.set_tool("line")
        # typed values are exact: no snapping, no direction lock
        if self.points:
            prev = self.points[-1]
            pt = Point(x, z)
            if pt == prev:
                return
            if not self.w.apply_edit(_("Draw line"), lambda p: drawing.add_line(p, prev, pt)):
                return
            self.w.refresh()
            self.points.append(pt)
        else:
            self.points.append(Point(x, z))
        self.done.set_sensitive(True)
        self.c.marker = (self.points[-1], "end")
        self.c.queue_draw()
        entry.set_text("")

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
            self.w.toasts.add_toast(Adw.Toast(title=_("Deleted {what}").format(what=gid), button_label=_("_Undo"),
                                              action_name="win.undo", timeout=6))

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
        for v in (FILLET_CHIPS if tool == "fillet" else CHAMFER_CHIPS):
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
        spin.set_value(1)
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
        self.c.queue_draw()

    def _toggle_pick(self, gid: str) -> None:
        if not gid:
            return
        if gid in self.c.picked:
            self.c.picked.remove(gid)
        else:
            self.c.picked.append(gid)
        self.pick_banner.set_title(f"{len(self.c.picked)} picked — tap again to remove, then Done")
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
        self.pick_banner.set_title(_("Tap the contour elements in cutting order"))
        self.c.queue_draw()
