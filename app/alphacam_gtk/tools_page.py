"""Tools page (ALPHA-T's ATTOOLS): the tool library the posts use, saved per user.

The numbers are the lathe's tool memory slots (manual turret with tool memory, DESIGN.md C8). The nose radius
changes the toolpath (imaginary tool tip), so every edit re-posts the open part. Import reads ALPHA-T's
ATOOLIB.TL1, with the tool pictures from the ATOOLIB.TL2 next to it; Reset goes back to ALPHA-T's standard 32 tools."""

from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from alphacam.io.toollib import load_tl1, load_tl2, sibling  # noqa: E402
from alphacam.model.tools import (  # noqa: E402
    TOOL_TYPES,
    Tool,
    ToolLibrary,
    library_from_json,
    library_to_json,
    standard_library,
)

from .op_editor import fit_spin  # noqa: E402
from .i18n import N_, _  # noqa: E402

LIBRARY_PATH = Path(GLib.get_user_config_dir()) / "alphalink-cam" / "tools.json"
TYPE_LABELS = {"turn": N_("Turning"), "groove": N_("Grooving / parting"), "thread": N_("Threading"),
               "drill": N_("Drill"), "bore": N_("Boring bar")}
HANDS = [("R", N_("Right hand")), ("L", N_("Left hand")), ("N", N_("Neutral"))]


def load_library(path: Path = LIBRARY_PATH) -> ToolLibrary:
    try:
        return library_from_json(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError):
        return standard_library()


def save_library(lib: ToolLibrary, path: Path = LIBRARY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(library_to_json(lib), indent=1, ensure_ascii=False) + "\n")
    tmp.replace(path)


class ToolsPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.w = window
        self._building = False
        # a wrapping row: the button labels are much longer in Ukrainian and must not set the window's width
        bar = Adw.WrapBox(child_spacing=6, line_spacing=6, margin_start=12, margin_end=12, margin_top=6,
                          margin_bottom=6)
        for label, cb, css in ((_("_Add Tool"), self._add, None), (_("_Delete Tool"), self._delete, "destructive-action"),
                               (_("_Import ATOOLIB.TL1…"), self._import, None),
                               (_("_Reset to ALPHA-T Tools"), self._reset, None)):
            b = Gtk.Button(label=label, use_underline=True)
            if css:
                b.add_css_class(css)
            b.connect("clicked", lambda _b, cb=cb: cb())
            bar.append(b)
        self.append(bar)
        self.note = Gtk.Label(label=_("Tool numbers are the lathe's tool memory slots."), xalign=0, wrap=True,
                              margin_start=12, margin_end=12, margin_bottom=6)
        self.note.add_css_class("dim-label")
        self.append(self.note)
        self.append(Gtk.Separator())
        body = Gtk.Paned(position=360, vexpand=True, shrink_start_child=False, shrink_end_child=False)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", lambda *a: self._show())
        body.set_start_child(Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.editor = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        body.set_end_child(self.editor)
        self.append(body)
        self.fill()

    @property
    def lib(self) -> ToolLibrary:
        return self.w.library

    def fill(self, select: int | None = None) -> None:
        self.listbox.remove_all()
        self.lib.tools.sort(key=lambda t: t.number)
        for t in self.lib.tools:
            row = Adw.ActionRow(title=f"T{t.number}  {t.name}",
                                subtitle=f"R{t.nose_radius:g} · {_(TYPE_LABELS.get(t.type, t.type))} · {t.hand}")
            self.listbox.append(row)
        if self.lib.tools:
            idx = next((i for i, t in enumerate(self.lib.tools) if t.number == select), 0)
            self.listbox.select_row(self.listbox.get_row_at_index(idx))

    def current(self) -> Tool | None:
        row = self.listbox.get_selected_row()
        return None if row is None else self.lib.tools[row.get_index()]

    # --- editor -----------------------------------------------------------------------------------------

    def _show(self) -> None:
        t = self.current()
        if t is None:
            self.editor.set_child(None)
            return
        self._building = True
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title=f"T{t.number}")
        page.add(g)
        name = Adw.EntryRow(title=_("Name"), text=t.name, show_apply_button=True)
        name.connect("apply", lambda r: self._set("name", r.get_text().strip()[:20] or t.name))
        g.add(name)
        types = list(TOOL_TYPES)
        type_row = Adw.ComboRow(title=_("Type"), model=Gtk.StringList.new([_(TYPE_LABELS[k]) for k in types]),
                                selected=types.index(t.type))
        type_row.connect("notify::selected", lambda r, _p: self._set("type", types[r.get_selected()]))
        g.add(type_row)
        hand_row = Adw.ComboRow(title=_("Hand"), model=Gtk.StringList.new([_(lab) for _k, lab in HANDS]),
                                selected=[k for k, _lab in HANDS].index(t.hand))
        hand_row.connect("notify::selected", lambda r, _p: self._set("hand", HANDS[r.get_selected()][0]))
        g.add(hand_row)
        for field, label, sub, lo, hi, step, digits in (
                ("nose_radius", _("Nose radius"), _("mm; changes the toolpath"), 0, 10, 0.1, 3),
                ("front_angle", _("Front clearance angle"), _("degrees from Z; negative = internal"), -180, 180, 1, 0),
                ("rear_angle", _("Rear clearance angle"), _("degrees from Z"), -180, 180, 1, 0),
                ("width", _("Width"), _("grooving tip or drill diameter, mm"), 0, 100, 0.5, 2),
                ("default_feed", _("Default feed"), _("per revolution, for new operations"), 0.001, 5, 0.01, 3),
                ("surface_speed", _("Surface speed"), _("m/min, for the cycle time; 0 = a default for the type"),
                 0, 1000, 10, 0),
                ("max_rpm", _("Spindle limit"), _("rpm for this tool; 0 = the machine's"), 0, 10000, 100, 0)):
            row = Adw.SpinRow.new_with_range(lo, hi, step)
            row.set_title(label)
            row.set_subtitle(sub)
            row.set_digits(digits)
            row.set_value(getattr(t, field))
            fit_spin(row, lo, hi, digits)
            row.connect("notify::value", lambda r, _p, f=field, d=digits: self._set(f, round(r.get_value(), max(d, 0))))
            g.add(row)
        desc = Adw.EntryRow(title=_("Description"), text=t.description, show_apply_button=True)
        desc.connect("apply", lambda r: self._set("description", r.get_text()))
        g.add(desc)
        self.editor.set_child(page)
        self._building = False

    def _set(self, field: str, value) -> None:
        t = self.current()
        if self._building or t is None or getattr(t, field) == value:
            return
        setattr(t, field, value)
        if field == "nose_radius":
            t.nose_radius = float(value)
        self._changed(t.number, rebuild_list=field in ("name", "nose_radius", "type", "hand"))

    def _changed(self, select: int | None, rebuild_list: bool = True) -> None:
        try:
            save_library(self.lib)
        except OSError as e:
            self.w.toast(_("The tool library could not be saved: {error}").format(error=e))
        if rebuild_list:
            self.fill(select)
        if self.w.project is not None:
            self.w.refresh()

    # --- actions ----------------------------------------------------------------------------------------

    def _add(self) -> None:
        used = {t.number for t in self.lib.tools}
        n = next(i for i in range(1, 1000) if i not in used)
        self.lib.tools.append(Tool(n, _("New tool")))
        self._changed(n)

    def _delete(self) -> None:
        t = self.current()
        if t is None:
            return
        alert = Adw.AlertDialog(heading=_("Delete T{n}?").format(n=t.number),
                                body=_("{name} is removed from your tool library. Parts that use it fall back to "
                                       "ALPHA-T's standard tool with that number.").format(name=t.name))
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("delete", _("_Delete"))
        alert.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_close_response("cancel")

        def response(_d, r):
            if r == "delete":
                self.lib.tools.remove(t)
                self._changed(None)

        alert.connect("response", response)
        alert.present(self.w)

    def _import(self) -> None:
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name=_("ALPHA-T tool library"))
        for pat in ("*.TL1", "*.tl1"):
            f.add_pattern(pat)
        filters.append(f)
        dlg = Gtk.FileDialog(title=_("Import Tool Library"), filters=filters)

        def done(d, res):
            try:
                file = d.open_finish(res)
            except GLib.Error:
                return
            try:
                lib = load_tl1(file.get_path())
            except (OSError, ValueError) as e:
                self.w.toast(_("Could not import {name}: {error}").format(name=file.get_basename(), error=e))
                return
            pictures = {}
            tl2 = sibling(file.get_path(), ".TL2")  # ALPHA-T keeps the tool pictures next to the library
            if tl2 is not None:
                try:
                    pictures = load_tl2(tl2)
                except (OSError, ValueError):
                    pictures = {}
            for tool in lib.tools:
                tool.outline = [[list(p) for p in line] for line in pictures.get(tool.number, [])]
            self.lib.tools[:] = lib.tools
            self._changed(None)
            if pictures:
                msg = _("Imported {n} tools and their pictures from {name}")
            else:
                msg = _("Imported {n} tools from {name}")
            self.w.toast(msg.format(n=len(lib.tools), name=file.get_basename()))

        dlg.open(self.w, None, done)

    def _reset(self) -> None:
        self.lib.tools[:] = standard_library().tools
        self._changed(None)
        self.w.toast(_("Tool library reset to ALPHA-T's 32 standard tools"))
