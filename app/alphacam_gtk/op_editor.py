"""Operation editor (TOUCH.md 7.1): rows generated from the operation's dataclass fields.

Every change goes through the window's UndoStack as one undoable step, and the preview regenerates."""

from __future__ import annotations

import dataclasses
import typing

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from alphacam.model.geometry import Arc, Point  # noqa: E402
from alphacam.model.operations import Groove  # noqa: E402

# field -> (label, subtitle, lower, upper, step, digits) for numbers
NUMBERS = {
    "tool": ("Tool", "Tool memory slot on the control", 1, 99, 1, 0),
    "feed": ("Feed", "per revolution", 0.001, 5, 0.01, 3),
    "depth_of_cut": ("Depth of cut", "per pass, radius", 0.01, 50, 0.1, 3),
    "offset_x": ("Offset X", "finish allowance, radius", -10, 10, 0.05, 3),
    "offset_z": ("Offset Z", "finish allowance", -10, 10, 0.05, 3),
    "finish_offset_x": ("Finish offset X", None, -10, 10, 0.05, 3),
    "finish_offset_z": ("Finish offset Z", None, -10, 10, 0.05, 3),
    "peck_depth": ("Peck depth", "0 = plunge in one go", 0, 50, 0.5, 3),
    "stepover": ("Stepover", None, 0.01, 50, 0.1, 3),
    "tip_width": ("Tip width", None, 0.01, 50, 0.1, 3),
    "z_shift": ("Z shift", None, -500, 500, 1, 3),
    "repeat": ("Repeats", None, 0, 50, 1, 0),
    "dwell_revs": ("Dwell", "revolutions", 0, 100, 1, 1),
    "depth": ("Hole bottom Z", "absolute", -1000, 0, 1, 3),
    "peck": ("Peck", "0 = no pecking", 0, 100, 1, 3),
    "pitch": ("Pitch", None, 0.01, 20, 0.05, 3),
    "cut_depth": ("First cut depth", "radius; cut n goes to first × √n", 0.01, 5, 0.05, 3),
    "lead_in": ("Lead in", None, 0, 50, 0.5, 3),
    "lead_out": ("Lead out", None, 0, 50, 0.5, 3),
    "angle": ("Thread angle", "degrees", 1, 120, 1, 1),
    "revs": ("Dwell", "revolutions", 0.1, 100, 1, 1),
}
SWITCHES = {
    "external": ("External", "off = internal (boring)"),
    "towards": ("Towards the chuck", None),
    "pre_finish": ("Pre-finish pass", "a finish pass with a 1 mm pull-off after the roughing"),
    "full_retract": ("Full retract", None),
    "front": ("Front", None),
    "spring_pass": ("Spring pass", None),
    "rapid": ("Rapid", "off = feed move"),
}
CHOICES = {"datum": ("Datum", ["left", "right"]), "mode": ("Mode", ["area", "finish"])}
POINTS = {"start": "Tool start", "change_point": "Tool change point", "point": "Move to"}


class OpEditor(Gtk.Box):
    """Shows one operation; `apply(label, fn)` is the window's undoable edit entry point."""

    def __init__(self, apply, on_move, on_delete, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._apply, self.index, self._building = apply, None, False
        self.on_pick = None  # set by the window: start contour picking for operation n
        self.title = Adw.WindowTitle(title="Operation")
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.set_title_widget(self.title)
        for icon, tip, cb in (("go-up-symbolic", "Move Up", lambda *_: on_move(-1)),
                              ("go-down-symbolic", "Move Down", lambda *_: on_move(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", cb)
            header.pack_start(b)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close Editor")
        close.connect("clicked", lambda *_: on_close())
        header.pack_end(close)
        delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete Operation")
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda *_: on_delete())
        header.pack_end(delete)
        self.append(header)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroll)

    def show_op(self, project, index: int, title: str) -> None:
        self.index = index
        op = project.operations[index]
        self.title.set_title(title)
        self.title.set_subtitle(f"Operation {index + 1}")
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)
        self._building = True
        hints = typing.get_type_hints(type(op))
        for f in dataclasses.fields(op):
            name, value = f.name, getattr(op, f.name)
            if name in POINTS:
                for axis, lab in (("x", "X (Ø)"), ("z", "Z")):
                    row = self._spin(f"{POINTS[name]} {lab}", None, -2000, 2000, 1, 3, getattr(value, axis))
                    row.connect("notify::value", self._point_changed, name, axis)
                    group.add(row)
            elif name in NUMBERS:
                lab, sub, lo, hi, step, digits = NUMBERS[name]
                row = self._spin(lab, sub, lo, hi, step, digits, value)
                row.connect("notify::value", self._number_changed, name, hints[name] is int)
                group.add(row)
            elif name in SWITCHES:
                lab, sub = SWITCHES[name]
                row = Adw.SwitchRow(title=lab, active=bool(value))
                if sub:
                    row.set_subtitle(sub)
                row.connect("notify::active", self._switch_changed, name)
                group.add(row)
            elif name in CHOICES:
                lab, opts = CHOICES[name]
                row = Adw.ComboRow(title=lab, model=Gtk.StringList.new(opts), selected=opts.index(value))
                row.connect("notify::selected", self._choice_changed, name, opts)
                group.add(row)
            elif name in ("outer_line", "inner_line"):
                ids = [g.id for g in project.geometry if not isinstance(g, Arc)]
                labels = [_element_label(project, i) for i in ids]
                row = Adw.ComboRow(title="Outer diameter line" if name == "outer_line" else "Inner diameter line",
                                   model=Gtk.StringList.new(labels or ["(no lines)"]),
                                   selected=ids.index(value) if value in ids else 0)
                row.connect("notify::selected", self._line_changed, name, ids)
                group.add(row)
            elif name == "contour":
                row = Adw.EntryRow(title="Contour (element ids, in cutting order)", text=" ".join(value),
                                   show_apply_button=True)
                row.connect("apply", self._contour_applied)
                group.add(row)
                pick = Adw.ButtonRow(title="Pick Contour on the Drawing", start_icon_name="find-location-symbolic")
                pick.connect("activated", lambda *_: self.on_pick and self.on_pick(self.index))
                group.add(pick)
                desc = Adw.ActionRow(title=f"{len(value)} elements",
                                     subtitle=" → ".join(_element_label(project, i) for i in value[:4])
                                     + (" …" if len(value) > 4 else ""))
                desc.add_css_class("property")
                group.add(desc)
        if isinstance(op, Groove):
            group.set_description("Grooving follows the drawn profile; the tool reference is the tip's -Z corner.")
        self._building = False
        self.scroll.set_child(page)

    # --- rows -------------------------------------------------------------------------------------------

    @staticmethod
    def _spin(title, subtitle, lo, hi, step, digits, value) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_digits(digits)
        row.set_value(value)
        return row

    def _edit(self, label, fn):
        if not self._building and self.index is not None:
            i = self.index
            self._apply(label, lambda p: fn(p.operations[i]))

    def _number_changed(self, row, _pspec, name, is_int):
        v = row.get_value()
        self._edit(f"Change {row.get_title()}", lambda op: setattr(op, name, int(round(v)) if is_int else round(v, 4)))

    def _point_changed(self, row, _pspec, name, axis):
        v = round(row.get_value(), 4)

        def fn(op):
            p = getattr(op, name)
            setattr(op, name, Point(v, p.z) if axis == "x" else Point(p.x, v))
        self._edit(f"Change {row.get_title()}", fn)

    def _switch_changed(self, row, _pspec, name):
        v = row.get_active()
        self._edit(f"Change {row.get_title()}", lambda op: setattr(op, name, v))

    def _choice_changed(self, row, _pspec, name, opts):
        v = opts[row.get_selected()]
        self._edit(f"Change {row.get_title()}", lambda op: setattr(op, name, v))

    def _line_changed(self, row, _pspec, name, ids):
        if ids:
            v = ids[row.get_selected()]
            self._edit(f"Change {row.get_title()}", lambda op: setattr(op, name, v))

    def _contour_applied(self, row):
        ids = row.get_text().replace(",", " ").split()
        self._edit("Change contour", lambda op: setattr(op, "contour", ids))


def _element_label(project, gid: str) -> str:
    for g in project.geometry:
        if g.id == gid:
            kind = "Arc" if isinstance(g, Arc) else "Line"
            return f"{gid} {kind} X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}"
    return f"{gid} (missing)"
