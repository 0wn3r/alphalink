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

TOOLS = [("select", "Select"), ("line", "Line"), ("fillet", "Fillet"), ("chamfer", "Chamfer")]
FILLET_CHIPS = (0.5, 1, 2, 3, 5)
CHAMFER_CHIPS = (0.5, 1, 2, 3)
RE_COORD = re.compile(r"\s*(@)?\s*(-?\d*\.?\d+)\s*[,; ]\s*(-?\d*\.?\d+)\s*$")


class DrawingTools:
    def __init__(self, window, canvas):
        self.w, self.c = window, canvas
        self.points: list[Point] = []  # line tool: the chain being drawn
        self.selected_element: str | None = None
        self.pick_op: int | None = None

        # palette (a bottom bar: thumb reach in touch mode)
        self.palette = Adw.ToggleGroup()
        for name, label in TOOLS:
            self.palette.add(Adw.Toggle(name=name, label=label))
        self.palette.set_active_name("select")
        self.palette.connect("notify::active-name", lambda *_: self.set_tool(self.palette.get_active_name()))

        self.entry = Gtk.Entry(hexpand=True, placeholder_text="X,Z  or  @dX,dZ  then Enter (line tool)")
        self.entry.add_css_class("command-entry")
        self.entry.connect("activate", self._entry_activate)
        self.done = Gtk.Button(label="_Done", use_underline=True, visible=False)
        self.done.connect("clicked", lambda *_: self.finish_chain())
        self.delete_el = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete Element (Del)",
                                    visible=False)
        self.delete_el.connect("clicked", lambda *_: self.delete_selected_element())

        self.bar = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        for wdg in (self.palette, self.entry, self.done, self.delete_el):
            self.bar.append(wdg)

        # contour picking banner
        self.pick_banner = Adw.Banner(title="Tap the contour elements in cutting order", button_label="_Done")
        self.pick_banner.connect("button-clicked", lambda *_: self.finish_pick())

        canvas.connect("canvas-tapped", self._tapped)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        canvas.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        canvas.add_controller(keys)

    # --- tools ------------------------------------------------------------------------------------------

    def set_tool(self, name: str) -> None:
        if self.palette.get_active_name() != name:
            self.palette.set_active_name(name)
            return
        self.finish_chain()
        self.c.tool = name
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
            pt, _ = drawing.snap(project, Point(x, z), tol, grid=1.0 if project.units == "mm" else 0.05)
            self.add_point(pt)
        elif tool in ("fillet", "chamfer"):
            corner = self._corner_near(Point(x, z), tol)
            if corner is None:
                self.w.toast("Tap a corner between two lines")
                return
            self._size_popover(tool, corner)

    def _motion(self, ctl, sx, sy) -> None:
        if self.c.tool != "line" or self.w.project is None:
            return
        x, z = self.c.view.to_world(sx, sy)
        tol = 10 / self.c.view.scale
        pt, kind = drawing.snap(self.w.project, Point(x, z), tol, grid=1.0 if self.w.project.units == "mm" else 0.05)
        if self.points and kind != "end":
            pt = drawing.lock_direction(self.points[-1], pt)
        self.c.marker = (pt, kind)
        self.c.rubber = (self.points[-1], pt) if self.points else None
        self.c.queue_draw()

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
            if not self.w.apply_edit("Draw line", lambda p: drawing.add_line(p, prev, pt)):
                return
            self.w.refresh()
        self.points.append(pt)
        self.done.set_visible(True)
        self.c.marker = (pt, "end")
        self.c.queue_draw()

    def finish_chain(self) -> None:
        self.points.clear()
        self.done.set_visible(False)
        self.c.rubber = None
        self.c.marker = None
        self.c.queue_draw()

    def _entry_activate(self, entry) -> None:
        m = RE_COORD.match(entry.get_text())
        if not m:
            self.w.toast("Type X,Z (diameter, Z) or @dX,dZ")
            return
        x, z = float(m[2]), float(m[3])
        if m[1]:
            if not self.points:
                self.w.toast("@ needs a previous point")
                return
            prev = self.points[-1]
            x, z = prev.x + x, prev.z + z
        if self.c.tool != "line":
            self.set_tool("line")
        # typed values are exact: no snapping, no direction lock
        if self.points:
            prev = self.points[-1]
            pt = Point(x, z)
            if pt == prev:
                return
            if not self.w.apply_edit("Draw line", lambda p: drawing.add_line(p, prev, pt)):
                return
            self.w.refresh()
            self.points.append(pt)
        else:
            self.points.append(Point(x, z))
        self.done.set_visible(True)
        self.c.marker = (self.points[-1], "end")
        self.c.queue_draw()
        entry.set_text("")

    # --- select / delete ---------------------------------------------------------------------------------

    def _select_element(self, gid: str | None) -> None:
        self.selected_element = gid
        self.c.highlight = {gid} if gid else set()
        self.delete_el.set_visible(gid is not None)
        if gid:
            g = next(g for g in self.w.project.geometry if g.id == gid)
            kind = "Line" if isinstance(g, Line) else "Arc"
            self.w.readout.set_label(f"{gid} {kind}  X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}")
            self.c.grab_focus()
        self.c.queue_draw()

    def delete_selected_element(self) -> None:
        gid = self.selected_element
        if gid and self.w.apply_edit(f"Delete {gid}", lambda p: drawing.delete_element(p, gid)):
            self._select_element(None)
            self.w.refresh()
            self.w.toasts.add_toast(Adw.Toast(title=f"Deleted {gid}", button_label="_Undo",
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
        title = Gtk.Label(label=("Radius" if tool == "fillet" else "Chamfer") + f" at X{corner.x:g} Z{corner.z:g}")
        title.add_css_class("heading")
        box.append(title)
        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5, column_spacing=6,
                            row_spacing=6)
        for v in (FILLET_CHIPS if tool == "fillet" else CHAMFER_CHIPS):
            b = Gtk.Button(label=f"R{v:g}" if tool == "fillet" else f"{v:g}×45°")
            b.add_css_class("pill")
            b.connect("clicked", lambda _b, v=v: (pop.popdown(), self._apply_corner(tool, corner, v)))
            chips.append(b)
        box.append(chips)
        other = Gtk.Box(spacing=6)
        spin = Gtk.SpinButton.new_with_range(0.01, 100, 0.1)
        spin.set_digits(3)
        spin.set_value(1)
        ok = Gtk.Button(label="_Apply", use_underline=True)
        ok.add_css_class("suggested-action")
        ok.connect("clicked", lambda *_: (pop.popdown(), self._apply_corner(tool, corner, spin.get_value())))
        other.append(spin)
        other.append(ok)
        box.append(other)
        pop.set_child(box)
        pop.connect("closed", lambda p: p.unparent())
        pop.popup()

    def _apply_corner(self, tool: str, corner: Point, size: float) -> None:
        fn = drawing.fillet if tool == "fillet" else drawing.chamfer
        label = f"{'Fillet R' if tool == 'fillet' else 'Chamfer '}{size:g}"
        if self.w.apply_edit(label, lambda p: fn(p, corner, size)):
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
        if self.w.apply_edit("Pick contour", lambda p: setattr(p.operations[i], "contour", order)):
            self.w.refresh()
            self.w._select_op(i)

    def cancel_pick(self) -> None:
        self.pick_op = None
        self.c.picked = []
        self.pick_banner.set_revealed(False)
        self.pick_banner.set_title("Tap the contour elements in cutting order")
        self.c.queue_draw()
