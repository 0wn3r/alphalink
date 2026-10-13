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
from alphacam.model.operations import Drill, Groove  # noqa: E402
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
    "cuts": (N_("Number of cuts"), N_("0 = from the first cut depth"), 0, 500, 1, 0),
    "lead_in": (N_("Lead in"), None, 0, 50, 0.5, 3),
    "retract": (N_("Retract distance"), None, 0.01, 50, 0.1, 3),
    "lead_out": (N_("Lead out"), None, 0, 50, 0.5, 3),
    "spring_passes": (N_("Spring passes"), N_("extra cuts at the final diameter"), 0, 9, 1, 0),
    "angle": (N_("Infeed angle"), N_("degrees; 0 = straight in"), 0, 60, 1, 1),
    "revs": (N_("Dwell"), N_("revolutions"), 0.1, 100, 1, 1),
}
SWITCHES = {
    "external": (N_("External"), N_("off = internal (boring)")),
    "towards": (N_("Towards the chuck"), None),
    "pre_finish": (N_("Pre-finish pass"), N_("a finish pass with a 1 mm pull-off after the roughing")),
    "full_retract": (N_("Full retract"), None),
    "front": (N_("Front"), N_("off = FACE: across the front face")),
    "follow": (N_("Follow contour"), N_("each pass also cleans up the step it leaves")),
    "face": (N_("Face"), N_("finish a face: pulls off towards the axis")),
    "rapid": (N_("Rapid"), N_("off = feed move")),
}
CHOICES = {"datum": (N_("Datum"), ["left", "right"]), "mode": (N_("Mode"), ["area", "finish"])}
CHOICE_LABELS = {"left": N_("left"), "right": N_("right"), "area": N_("area"), "finish": N_("finish")}
POINTS = {"start": N_("Tool start"), "change_point": N_("Tool change point"), "point": N_("Move to")}


def fit_spin(row: Adw.SpinRow, lo: float, hi: float, digits: int) -> Adw.SpinRow:
    """Reserve room for the widest value, so the number is never clipped when a long (e.g. Ukrainian) title takes
    the row's width: "120.00(" instead of "120.000"."""
    chars = max(len(f"{v:.{max(digits, 0)}f}") for v in (lo, hi))
    stack = [row]
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.SpinButton):
            w.set_width_chars(chars)
            w.set_max_width_chars(chars)
            break
        c = w.get_first_child()
        while c:
            stack.append(c)
            c = c.get_next_sibling()
    return row


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
                    row = self._spin(f"{_(POINTS[name])} {lab}", None, -2000, 2000, 1,
                                     4 if project.units == "inch" else 3, getattr(value, axis))
                    row.connect("notify::value", self._point_changed, name, axis)
                    group.add(row)
            elif name in NUMBERS:
                lab, sub, lo, hi, step, digits = NUMBERS[name]
                if name == "retract" and value is None:  # ALPHA-T's default: area clear 1 mm, drilling 2 mm
                    from alphacam.toolpath.area import RETRACT_MM
                    from alphacam.toolpath.drill import PECK_RETRACT_MM

                    value = round((PECK_RETRACT_MM if isinstance(op, Drill) else RETRACT_MM)
                                  / (25.4 if project.units == "inch" else 1), 4)
                if name == "tool":
                    sub = self.tool_name(int(value)) or sub
                if project.units == "inch" and digits == 3:  # inch values have 4 decimals (0.0787)
                    digits = 4
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
                                   selected=opts.index(value) if value in opts else 0)
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
                group.add(_contour_rows(project, value))
        if isinstance(op, Groove) and not op.front:
            group.set_description(_("Face grooving: plunges along -Z, stepping outwards; the tool reference is the "
                                    "tip's corner nearest the axis."))
        elif isinstance(op, Groove):
            group.set_description(_("Grooving follows the drawn profile; the tool reference is the tip's -Z corner."))
        self._building = False
        self.scroll.set_child(page)

    # --- rows -------------------------------------------------------------------------------------------

    @staticmethod
    def _unchanged(row) -> bool:
        """Only the display rounding moved the value (focus-out re-rounds to the digits shown): not an edit."""
        orig = getattr(row, "_orig", None)
        return orig is not None and abs(row.get_value() - orig) <= 0.5 * 10 ** -row.get_digits() + 1e-12

    @staticmethod
    def _spin(title, subtitle, lo, hi, step, digits, value) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_digits(digits)
        row.set_value(value)
        row._orig = value
        return fit_spin(row, lo, hi, digits)

    def _edit(self, label, fn):
        if not self._building and self.index is not None:
            i = self.index
            self._apply(label, lambda p: fn(p.operations[i]))

    def _number_changed(self, row, _pspec, name, is_int):
        if self._unchanged(row):
            return
        row._orig = None
        v = row.get_value()
        self._edit(_("Change {what}").format(what=row.get_title()), lambda op: setattr(op, name, int(round(v)) if is_int else round(v, 4)))

    def _point_changed(self, row, _pspec, name, axis):
        if self._unchanged(row):
            return
        row._orig = None
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


def _fmt_pt(p) -> str:
    return f"X{p.x:g}  Z{p.z:g}"


def _contour_rows(project, ids: list[str]) -> Adw.ExpanderRow:
    """The picked contour as a list: one row per element in cutting order (kind and number, from → to, radius),
    folded under a summary row with the element count and where the contour starts and ends."""
    from alphacam.toolpath.contour import chain

    by_id = {g.id: g for g in project.geometry}
    elems = [by_id[i] for i in ids if i in by_id]
    try:  # oriented in cutting order, as the post walks it
        segs = chain(elems)
        ends = [(s.a, s.b) for s in segs]
    except (ValueError, KeyError):
        ends = [(g.a, g.b) for g in elems]
    exp = Adw.ExpanderRow(title=ngettext("{n} element", "{n} elements", len(ids)).format(n=len(ids)))
    if ends:
        exp.set_subtitle(_("from {a} to {b}").format(a=_fmt_pt(ends[0][0]), b=_fmt_pt(ends[-1][1])))
    for n, (g, (a, b)) in enumerate(zip(elems, ends), 1):
        kind = _("Arc") if isinstance(g, Arc) else _("Line")
        row = Adw.ActionRow(title=f"{n}. {kind}", subtitle=f"{_fmt_pt(a)}  →  {_fmt_pt(b)}")
        row.add_css_class("property")
        if isinstance(g, Arc):
            r = ((g.a.x / 2 - g.centre.x / 2) ** 2 + (g.a.z - g.centre.z) ** 2) ** 0.5
            row.add_suffix(Gtk.Label(label=f"R{r:g}", css_classes=["dim-label"]))
        row.add_suffix(Gtk.Label(label=g.id, css_classes=["dim-label", "caption"]))
        exp.add_row(row)
    for gid in ids:
        if gid not in by_id:
            exp.add_row(Adw.ActionRow(title=_("{id} (missing)").format(id=gid)))
    return exp


def _element_label(project, gid: str) -> str:
    for g in project.geometry:
        if g.id == gid:
            kind = _("Arc") if isinstance(g, Arc) else _("Line")
            return f"{gid} {kind} X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}"
    return _("{id} (missing)").format(id=gid)
