"""Operation editor (TOUCH.md 7.1): rows generated from the operation's dataclass fields.

Every change goes through the window's UndoStack as one undoable step, and the preview regenerates."""

from __future__ import annotations

import dataclasses
import typing

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Graphene, Gtk  # noqa: E402

from alphacam.model.geometry import Arc, Point  # noqa: E402
from alphacam.model.operations import Drill, Groove  # noqa: E402
from .i18n import N_, _, ngettext  # noqa: E402
from .ui import template  # noqa: E402

# field -> (label, subtitle, lower, upper, step, digits) for numbers
NUMBERS = {
    "tool": (N_("Tool"), N_("Tool memory slot on the control"), 1, 99, 1, 0),
    "feed": (N_("Feed"), N_("Per revolution"), 0.001, 5, 0.01, 3),
    "depth_of_cut": (N_("Depth of cut"), N_("Per pass, radius"), 0.01, 50, 0.1, 3),
    "offset_x": (N_("Offset X"), N_("Finish allowance, radius"), -10, 10, 0.05, 3),
    "offset_z": (N_("Offset Z"), N_("Finish allowance"), -10, 10, 0.05, 3),
    "finish_offset_x": (N_("Finish offset X"), None, -10, 10, 0.05, 3),
    "finish_offset_z": (N_("Finish offset Z"), None, -10, 10, 0.05, 3),
    "peck_depth": (N_("Peck depth"), N_("0 = plunge in one go"), 0, 50, 0.5, 3),
    "stepover": (N_("Stepover"), None, 0.01, 50, 0.1, 3),
    "tip_width": (N_("Tip width"), None, 0.01, 50, 0.1, 3),
    "z_shift": (N_("Z shift"), None, -500, 500, 1, 3),
    "repeat": (N_("Repeats"), None, 0, 50, 1, 0),
    "dwell_revs": (N_("Dwell"), N_("Revolutions"), 0, 100, 1, 1),
    "depth": (N_("Hole bottom Z"), N_("Absolute"), -1000, 0, 1, 3),
    "peck": (N_("Peck"), N_("0 = no pecking"), 0, 100, 1, 3),
    "pitch": (N_("Pitch"), None, 0.01, 20, 0.05, 3),
    "cut_depth": (N_("First cut depth"), N_("Radius; cut n goes to first × √n"), 0.01, 5, 0.05, 3),
    "cuts": (N_("Number of cuts"), N_("0 = from the first cut depth"), 0, 500, 1, 0),
    "lead_in": (N_("Lead in"), None, 0, 50, 0.5, 3),
    "retract": (N_("Retract distance"), None, 0.01, 50, 0.1, 3),
    "lead_out": (N_("Lead out"), None, 0, 50, 0.5, 3),
    "spring_passes": (N_("Spring passes"), N_("Extra cuts at the final diameter"), 0, 9, 1, 0),
    "angle": (N_("Infeed angle"), N_("Degrees; 0 = straight in"), 0, 60, 1, 1),
    "revs": (N_("Dwell"), N_("Revolutions"), 0.1, 100, 1, 1),
}
SWITCHES = {
    "external": (N_("External"), N_("Off = internal (boring)")),
    "towards": (N_("Towards the chuck"), None),
    "pre_finish": (N_("Pre-finish pass"), N_("A finish pass after the roughing, pulling off by the retract distance")),
    "full_retract": (N_("Full retract"), None),
    "front": (N_("Front"), N_("Off = FACE: across the front face")),
    "follow": (N_("Follow contour"), N_("Each pass also cleans up the step it leaves")),
    "face": (N_("Face"), N_("Finish a face: pulls off towards the axis")),
    "rapid": (N_("Rapid"), N_("Off = feed move")),
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


TAP_SLOP_PX = 12  # a finger that travels further than this is scrolling
_fling = {"scroller": None, "id": 0}  # the editor's fling after a swipe from a number


def _stop_fling() -> None:
    if _fling["id"]:
        _fling["scroller"].remove_tick_callback(_fling["id"])
    _fling.update(scroller=None, id=0)


def _start_fling(scroller: Gtk.ScrolledWindow, speed: float) -> None:
    """A swipe from a number goes on after the lift like one from a title, which GTK's ScrolledWindow flings: the
    canvas's decay on the editor's scrolling (Eduard's recorded swipes, 2026-10-19). `speed` is px/s, down the
    content."""
    from .canvas.input import FLING_DECAY, FLING_MIN

    _stop_fling()
    if abs(speed) < FLING_MIN:
        return
    adj = scroller.get_vadjustment()
    state = {"v": speed, "t": None, "mine": adj.get_value()}

    def tick(widget, clock):
        now = clock.get_frame_time()
        if abs(adj.get_value() - state["mine"]) > 0.5:  # something else scrolled the editor
            _fling.update(scroller=None, id=0)
            return False
        if state["t"] is not None:
            dt = (now - state["t"]) / 1e6
            top = adj.get_upper() - adj.get_page_size()
            value = min(max(adj.get_value() + state["v"] * dt, adj.get_lower()), top)
            adj.set_value(value)
            state["mine"] = adj.get_value()
            state["v"] *= FLING_DECAY ** (dt / 0.016)
            if abs(state["v"]) < FLING_MIN or value <= adj.get_lower() or value >= top:
                _fling.update(scroller=None, id=0)
                return False
        state["t"] = now
        return True

    _fling.update(scroller=scroller, id=scroller.add_tick_callback(tick))


def _finger_speed(samples: list) -> float:
    """px/s over the shortest stretch of at least 16 ms that ends at the last move, `samples` being (event time ms,
    y) with the press first; the whole drag when it is shorter (as the canvas does in the Rust app)."""
    if not samples:
        return 0.0
    t1, y1 = samples[-1]
    t0, y0 = next((s for s in reversed(samples) if t1 - s[0] >= 16), samples[0])
    return (y1 - y0) / ((t1 - t0) / 1000) if t1 > t0 else 0.0


def keypad_on_touch(row: Adw.SpinRow) -> Adw.SpinRow:
    """A finger on the number opens the AlphaLink keypad (TOUCH.md 6) instead of the system keyboard, which covers
    half the screen; a finger on − or + steps the value. Both only for a still tap, on release: a finger that moves
    scrolls the editor instead (Eduard 2026-10-16: swiping through the parameters kept opening the keypad and
    changing values). The touch is taken on press (no focus, so no system keyboard), so the scrolling is done here.
    The mouse and keyboard work as before."""
    from .keypad import Keypad

    drag = Gtk.GestureDrag(touch_only=True, propagation_phase=Gtk.PropagationPhase.CAPTURE)
    state = {"what": None, "moved": False, "start": 0.0, "adj": None, "scroller": None, "y0": 0.0, "samples": []}

    def finger_y(gesture, scroller):
        """The finger's height in the scroller's coordinates: the drag's offsets are in the row's, and the row moves
        as the editor scrolls under the finger, which fed back (half the finger's travel, in jumps)."""
        ok, x, y = gesture.get_point(gesture.get_current_sequence())
        if not ok:
            return None
        ok, p = row.compute_point(scroller, Graphene.Point().init(x, y))
        return p.y if ok else None

    def begin(gesture, x, y):
        w = row.pick(x, y, Gtk.PickFlags.DEFAULT)
        what = None
        while w is not None and w is not row:
            if isinstance(w, Gtk.Button):
                what = "up" if w.has_css_class("up") else "down" if w.has_css_class("down") else None
                break
            if isinstance(w, Gtk.Text):
                what = "number"
                break
            w = w.get_parent()
        if what is None:
            return
        scroller = row.get_ancestor(Gtk.ScrolledWindow)
        adj = scroller.get_vadjustment() if scroller is not None else None
        y0 = finger_y(gesture, scroller) if scroller is not None else None
        _stop_fling()  # a finger that lands on the editor holds it, as on a title
        state.update(what=what, moved=False, adj=adj, start=adj.get_value() if adj else 0.0, scroller=scroller,
                     y0=y0 or 0.0, samples=[(gesture.get_current_event_time(), y0 or 0.0)])
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def update(gesture, dx, dy):
        if state["what"] is None:
            return
        if not state["moved"] and abs(dx) + abs(dy) > TAP_SLOP_PX:
            state["moved"] = True
        adj, scroller = state["adj"], state["scroller"]
        y = finger_y(gesture, scroller) if scroller is not None else None
        if y is None:
            return
        if state["moved"]:
            top = adj.get_upper() - adj.get_page_size()
            adj.set_value(min(max(state["start"] + state["y0"] - y, adj.get_lower()), top))
        state["samples"] = (state["samples"][:1] + state["samples"][1:][-15:]) + [(gesture.get_current_event_time(), y)]

    def end(gesture, dx, dy):
        what, state["what"] = state["what"], None
        if what is not None and state["moved"] and state["scroller"] is not None:
            samples, t = state["samples"], gesture.get_current_event_time()
            if not samples or t - samples[-1][0] <= 50:  # a finger that rested before the lift flings nothing
                _start_fling(state["scroller"], -_finger_speed(samples))
        if what is None or state["moved"]:
            return
        if what in ("up", "down"):
            step = row.get_adjustment().get_step_increment()
            row.set_value(row.get_value() + (step if what == "up" else -step))
            return

        def done(text):
            try:
                row.set_value(float(text))
            except ValueError:
                pass

        Keypad(done, title=row.get_title(), values={"v": f"{row.get_value():.{row.get_digits()}f}"},
               fields=[("v", row.get_title())]).present(row)

    drag.connect("drag-begin", begin)
    drag.connect("drag-update", update)
    drag.connect("drag-end", end)
    row.add_controller(drag)
    return row


@template("op-editor")
class OpEditor(Gtk.Box):
    """Shows one operation; the window sets `apply(label, fn)`, its undoable edit entry point, and the header's
    callbacks (ui/op-editor.blp)."""

    __gtype_name__ = "AlphaLinkOpEditor"

    title = Gtk.Template.Child()
    scroll = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.index, self._building = None, False
        self._apply = lambda label, fn: None  # set by the window, with the callbacks below
        self.on_move = lambda delta: None
        self.on_delete = lambda: None
        self.on_close = lambda: None
        self.on_pick = None  # set by the window: start contour picking for operation n
        self.on_duplicate = None  # set by the window: copy the operation below itself
        self.tool_name = lambda n: ""  # set by the window: name of tool n in the library

    @Gtk.Template.Callback()
    def _move_up(self, *_a) -> None:
        self.on_move(-1)

    @Gtk.Template.Callback()
    def _move_down(self, *_a) -> None:
        self.on_move(1)

    @Gtk.Template.Callback()
    def _close(self, *_a) -> None:
        self.on_close()

    def _delete(self, *_a) -> None:  # the Rust app moved Delete Operation out of the header
        self.on_delete()

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
                if name == "feed":
                    self._feed_row = row
                    row.set_sensitive(not getattr(op, "rapid", False))  # a rapid move has no feed
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
                labels = [_line_label(project, i) for i in ids]  # short: the coordinates tell the lines apart
                row = Adw.ComboRow(title=_("Outer diameter line") if name == "outer_line" else _("Inner diameter line"),
                                   model=Gtk.StringList.new(labels or [_("(no lines)")]),
                                   selected=ids.index(value) if value in ids else 0)
                row.connect("notify::selected", self._line_changed, name, ids)
                group.add(row)
            elif name == "contour":
                row = Adw.EntryRow(title=_("Contour, in cutting order"), text=" ".join(value),
                                   show_apply_button=True, tooltip_text=_("Element IDs, for example g0 g1 g2"))
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
        if self.on_duplicate is not None:  # copy it (a second finish pass, the same groove elsewhere) instead of retyping
            more = Adw.PreferencesGroup()
            dup = Adw.ButtonRow(title=_("_Duplicate Operation"), use_underline=True, start_icon_name="edit-copy-symbolic")
            dup.connect("activated", lambda *_: self.on_duplicate())
            more.add(dup)
            page.add(more)
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
        keypad_on_touch(row)
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
        if name == "rapid" and getattr(self, "_feed_row", None) is not None:
            self._feed_row.set_sensitive(not v)
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
        exp.set_subtitle(_("From {a} to {b}").format(a=_fmt_pt(ends[0][0]), b=_fmt_pt(ends[-1][1])))
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


def _line_label(project, gid: str) -> str:
    g = next((g for g in project.geometry if g.id == gid), None)
    if g is None:
        return _("{id} (missing)").format(id=gid)
    if g.a.x == g.b.x:  # a diameter line: X and its Z range
        return f"X{g.a.x:g}  Z{max(g.a.z, g.b.z):g}…{min(g.a.z, g.b.z):g}  ({gid})"
    return f"X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}  ({gid})"


def _element_label(project, gid: str) -> str:
    for g in project.geometry:
        if g.id == gid:
            kind = _("Arc") if isinstance(g, Arc) else _("Line")
            return f"{gid} {kind} X{g.a.x:g} Z{g.a.z:g} → X{g.b.x:g} Z{g.b.z:g}"
    return _("{id} (missing)").format(id=gid)
