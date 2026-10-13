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
from .i18n import N_, _, ngettext  # noqa: E402

# field -> (label, subtitle, lower, upper, step, digits) for numbers
NUMBERS = {
    "tool": (N_("Tool"), N_("Tool memory slot on the control"), 1, 99, 1, 0),
    "feed": (N_("Feed"), N_("per revolution"), 0.001, 5, 0.01, 3),
    "depth_of_cut": (N_("Depth of cut"), N_("per pass, radius"), 0.01, 50, 0.1, 3),
    "offset_x": (N_("Offset X"), N_("finish allowance, radius"), -10, 10, 0.05, 3),
    "offset_z": (N_("Offset Z"), N_("finish allowance"), -10, 10, 0.05, 3),
    "finish_offset_x": (N_("Finish offset X"), None, -10, 10, 0.05, 3),
    "finish_offset_z": (N_("Finish offset Z"), None, -10, 10, 0.05, 3),
    "peck_depth": (N_("Peck depth"), N_("0 = plunge in one go"), 0, 50, 0.5, 3),
    "stepover": (N_("Stepover"), None, 0.01, 50, 0.1, 3),
    "tip_width": (N_("Tip width"), None, 0.01, 50, 0.1, 3),
    "z_shift": (N_("Z shift"), None, -500, 500, 1, 3),
    "repeat": (N_("Repeats"), None, 0, 50, 1, 0),
    "dwell_revs": (N_("Dwell"), N_("revolutions"), 0, 100, 1, 1),
    "depth": (N_("Hole bottom Z"), N_("absolute"), -1000, 0, 1, 3),
    "peck": (N_("Peck"), N_("0 = no pecking"), 0, 100, 1, 3),
    "pitch": (N_("Pitch"), None, 0.01, 20, 0.05, 3),
    "cut_depth": (N_("First cut depth"), N_("radius; cut n goes to first × √n"), 0.01, 5, 0.05, 3),
    "lead_in": (N_("Lead in"), None, 0, 50, 0.5, 3),
    "lead_out": (N_("Lead out"), None, 0, 50, 0.5, 3),
    "angle": (N_("Thread angle"), N_("degrees"), 1, 120, 1, 1),
    "revs": (N_("Dwell"), N_("revolutions"), 0.1, 100, 1, 1),
}
SWITCHES = {
    "external": (N_("External"), N_("off = internal (boring)")),
    "towards": (N_("Towards the chuck"), None),
    "pre_finish": (N_("Pre-finish pass"), N_("a finish pass with a 1 mm pull-off after the roughing")),
    "full_retract": (N_("Full retract"), None),
    "front": (N_("Front"), None),
    "spring_pass": (N_("Spring pass"), None),
    "rapid": (N_("Rapid"), N_("off = feed move")),
}
CHOICES = {"datum": (N_("Datum"), ["left", "right"]), "mode": (N_("Mode"), ["area", "finish"])}
CHOICE_LABELS = {"left": N_("left"), "right": N_("right"), "area": N_("area"), "finish": N_("finish")}
POINTS = {"start": N_("Tool start"), "change_point": N_("Tool change point"), "point": N_("Move to")}


class OpEditor(Gtk.Box):
    """Shows one operation; `apply(label, fn)` is the window's undoable edit entry point."""

    def __init__(self, apply, on_move, on_delete, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._apply, self.index, self._building = apply, None, False
        self.on_pick = None  # set by the window: start contour picking for operation n
        self.tool_name = lambda n: ""  # set by the window: name of tool n in the library
        self.title = Adw.WindowTitle(title=_("Operation"))
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.set_title_widget(self.title)
        for icon, tip, cb in (("go-up-symbolic", _("Move Up"), lambda *a: on_move(-1)),
                              ("go-down-symbolic", _("Move Down"), lambda *a: on_move(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", cb)
            header.pack_start(b)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Close Editor"))
        close.connect("clicked", lambda *a: on_close())
        header.pack_end(close)
        delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Delete Operation"))
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda *a: on_delete())
        header.pack_end(delete)
        self.append(header)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroll)

    def show_op(self, project, index: int, title: str) -> None:
        self.index = index
        op = project.operations[index]
        self.title.set_title(title)
        self.title.set_subtitle(_("Operation {n}").format(n=index + 1))
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        page.add(group)
        self._building = True
        hints = typing.get_type_hints(type(op))
        for f in dataclasses.fields(op):
            name, value = f.name, getattr(op, f.name)
            if name in POINTS:
                for axis, lab in (("x", "X (Ø)"), ("z", "Z")):
                    row = self._spin(f"{_(POINTS[name])} {lab}", None, -2000, 2000, 1, 3, getattr(value, axis))
                    row.connect("notify::value", self._point_changed, name, axis)
                    group.add(row)
            elif name in NUMBERS:
                lab, sub, lo, hi, step, digits = NUMBERS[name]
                if name == "tool":
                    sub = self.tool_name(int(value)) or sub
                row = self._spin(_(lab), sub and _(sub), lo, hi, step, digits, value)
                if name == "tool":
                    row.connect("notify::value", lambda r, _p: r.set_subtitle(self.tool_name(int(r.get_value()))))
                row.connect("notify::value", self._number_changed, name, hints[name] is int)
                group.add(row)
            elif name in SWITCHES:
                lab, sub = SWITCHES[name]
                row = Adw.SwitchRow(title=_(lab), active=bool(value))
                if sub:
                    row.set_subtitle(_(sub))
                row.connect("notify::active", self._switch_changed, name)
                group.add(row)
            elif name in CHOICES:
                lab, opts = CHOICES[name]
                row = Adw.ComboRow(title=_(lab), model=Gtk.StringList.new([_(CHOICE_LABELS[o]) for o in opts]),
                                   selected=opts.index(value))
                row.connect("notify::selected", self._choice_changed, name, opts)
                group.add(row)
            elif name in ("outer_line", "inner_line"):
                ids = [g.id for g in project.geometry if not isinstance(g, Arc)]
                labels = [_element_label(project, i) for i in ids]
                row = Adw.ComboRow(title=_("Outer diameter line") if name == "outer_line" else _("Inner diameter line"),
                                   model=Gtk.StringList.new(labels or [_("(no lines)")]),
                                   selected=ids.index(value) if value in ids else 0)
                row.connect("notify::selected", self._line_changed, name, ids)
                group.add(row)
            elif name == "contour":
                row = Adw.EntryRow(title=_("Contour (element ids, in cutting order)"), text=" ".join(value),
                                   show_apply_button=True)
                row.connect("apply", self._contour_applied)
                group.add(row)
                pick = Adw.ButtonRow(title=_("Pick Contour on the Drawing"), start_icon_name="find-location-symbolic")
                pick.connect("activated", lambda *_: self.on_pick and self.on_pick(self.index))
                group.add(pick)
                desc = Adw.ActionRow(title=ngettext("{n} element", "{n} elements", len(value)).format(n=len(value)),
                                     subtitle=" → ".join(_element_label(project, i) for i in value[:4])
                                     + (" …" if len(value) > 4 else ""))
                desc.add_css_class("property")
                group.add(desc)
        if isinstance(op, Groove):
            group.set_description(_("Grooving follows the drawn profile; the tool reference is the tip's -Z corner."))
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
        self._edit(_("Change {what}").format(what=row.get_title()), lambda op: setattr(op, name, int(round(v)) if is_int else round(v, 4)))

    def _point_changed(self, row, _pspec, name, axis):
        v = round(row.get_value(), 4)

        def fn(op):
            p = getattr(op, name)
            setattr(op, name, Point(v, p.z) if axis == "x" else Point(p.x, v))
        self._edit(_("Change {what}").format(what=row.get_title()), fn)

    def _switch_changed(self, row, _pspec, name):
        v = row.get_active()
        self._edit(_("Change {what}").format(what=row.get_title()), lambda op: setattr(op, name, v))

    def _choice_changed(self, row, _pspec, name, opts):
        v = opts[row.get_selected()]
        self._edit(_("Change {what}").format(what=row.get_title()), lambda op: setattr(op, name, v))

    def _line_changed(self, row, _pspec, name, ids):
        if ids:
            v = ids[row.get_selected()]
            self._edit(_("Change {what}").format(what=row.get_title()), lambda op: setattr(op, name, v))

    def _contour_applied(self, row):
        ids = row.get_text().replace(",", " ").split()
        self._edit(_("Change contour"), lambda op: setattr(op, "contour", ids))


def _element_label(project, gid: str) -> str:
    for g in project.geometry:
        if g.id == gid:
            kind = _("Arc") if isinstance(g, Arc) else _("Line")
            return f"{gid} {kind} X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}"
    return _("{id} (missing)").format(id=gid)
