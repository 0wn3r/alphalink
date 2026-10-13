"""Main window (DESIGN.md 6, TOUCH.md 7): operations sidebar, Part view (lathe canvas) and Program view."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from alphacam.io.dxf import load_dxf  # noqa: E402
from alphacam.io.sd1 import load_sd1, sibling, verify  # noqa: E402
from alphacam.io.sdg import load_sdg  # noqa: E402
from alphacam.commands import UndoStack  # noqa: E402
from alphacam.model.project import Project  # noqa: E402
from alphacam.preview import preview  # noqa: E402

from .canvas.widget import LatheCanvas  # noqa: E402
from .drawing_tools import DrawingTools  # noqa: E402
from .simulation import SimulationBar  # noqa: E402
from .report import SheetPage  # noqa: E402
from .help import HelpDialog  # noqa: E402
from .tools_page import ToolsPage, load_library  # noqa: E402
from .i18n import N_, _, ngettext  # noqa: E402
from .settings import settings  # noqa: E402
from .input_mode import InputMode  # noqa: E402
from .machine import MachinePage  # noqa: E402
from .op_editor import OpEditor  # noqa: E402
from .op_factory import OP_MENU, default_op  # noqa: E402

OP_TITLES = {
    "tool_change": N_("Tool change"), "area_clear": N_("Area clear"), "face_clear": N_("Face clear"),
    "groove": N_("Grooving"), "finish": N_("Finish pass"), "drill": N_("Drilling"), "thread": N_("Threading"),
    "dwell": N_("Dwell"), "move": N_("Move / rapid"),
}


def op_title(op) -> str:
    title = _(OP_TITLES.get(op.type, op.type))
    if getattr(op, "external", True) is False:
        title = _("{title} (internal)").format(title=title)
    if getattr(op, "pre_finish", False):
        title = _("{title} + pre-finish").format(title=title)
    if op.type == "groove" and op.mode == "finish":
        title = _("{title} (finish)").format(title=title)
    if op.type == "area_clear" and not op.front:
        title = _("{title} (face direction)").format(title=title)
    return title


def op_summary(op, pv) -> str:
    parts = []
    if hasattr(op, "tool"):
        parts.append(f"T{op.tool}")
    if op.type == "dwell":
        parts.append(_("{revs} revs").format(revs=f"{op.revs:g}"))
    elif op.type == "move":
        parts.append((_("rapid to {point}") if op.rapid else _("feed to {point}")).format(
            point=f"X{op.point.x:g} Z{op.point.z:g}"))
    elif op.type == "thread":
        parts.append(_("pitch {pitch}").format(pitch=f"{op.pitch:g}"))
    elif hasattr(op, "feed"):
        parts.append(f"F{op.feed:g}")
    if pv is not None and pv.passes > 1:
        parts.append(ngettext("{n} pass", "{n} passes", pv.passes).format(n=pv.passes))
    return " · ".join(parts)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="AlphaLink CAM", default_width=1280, default_height=820)
        self.project: Project | None = None
        self.path: Path | None = None
        self.pv = None
        self._block_lines: list[tuple[int, int, int | None]] = []  # (first line, last line, op index)
        self._font_pt = 11.0
        self._select_guard = False
        self.undo: UndoStack | None = None
        self._refresh_id = 0
        self._editor_edit = False
        self.library = load_library()

        self.toasts = Adw.ToastOverlay()
        self.root = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.root)
        self.set_content(self.toasts)

        self.root.add_named(self._build_empty(), "empty")
        self.root.add_named(self._build_main(), "main")
        self.root.set_visible_child_name("empty")
        self.input_mode = InputMode(self, settings()["input_layout"])
        self.input_mode.connect("changed", lambda _m, mode: (self.zoom_box.set_visible(mode == "touch"),
                                                             self.drawing.set_touch(mode == "touch")))
        self._setup_lock()
        self._install_actions()
        self.editor.on_pick = self.drawing.start_pick
        self.editor.tool_name = self._tool_name

        # narrow (half a Surface screen, portrait): the operations list becomes its own page, and the editor pane
        # gets narrower, so nothing is cut off (sidebar 260 + Part page ~600 do not fit below 900)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(self.editor, "width-request", 280)
        bp.add_setter(self.switcher_bar, "reveal", True)
        bp.add_setter(self.header_switcher, "visible", False)
        self.add_breakpoint(bp)

    # --- layout -----------------------------------------------------------------------------------------

    def _build_empty(self) -> Gtk.Widget:
        page = Adw.StatusPage(title=_("No Part Open"), icon_name="document-open-symbolic",
                              description=_("Open an AlphaLink part (.alcam), an ALPHA-T drawing (.SDG) or a DXF"))
        btns = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        new = Gtk.Button(label=_("_New Part…"), use_underline=True, action_name="win.new")
        new.add_css_class("pill")
        btn = Gtk.Button(label=_("_Open…"), use_underline=True, action_name="win.open")
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        btns.append(new)
        btns.append(btn)
        page.set_child(btns)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        return view

    def _build_main(self) -> Gtk.Widget:
        # sidebar: operations
        self.op_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.op_list.add_css_class("navigation-sidebar")
        self.op_list.add_css_class("op-list")
        self.op_list.connect("row-selected", self._row_selected)
        side_scroll = Gtk.ScrolledWindow(child=self.op_list, vexpand=True)
        side = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        add_menu = Gio.Menu()
        for label, kind in OP_MENU:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.add-op", GLib.Variant.new_string(kind))
            add_menu.append_item(item)
        self.add_op_button = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                            tooltip_text=_("Add Operation (C)"))
        side_header.pack_start(self.add_op_button)
        side.add_top_bar(side_header)
        side.set_content(side_scroll)
        side_page = Adw.NavigationPage(title=_("Operations"), child=side)

        # part view
        self.canvas = LatheCanvas()
        self.canvas.connect("cursor-moved", self._cursor_moved)
        self.zoom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END,
                                valign=Gtk.Align.END)
        self.zoom_box.add_css_class("canvas-zoom")
        for icon, action, tip in (("zoom-in-symbolic", "win.zoom-in", _("Zoom In")),
                                  ("zoom-out-symbolic", "win.zoom-out", _("Zoom Out")),
                                  ("zoom-fit-best-symbolic", "win.fit", _("Fit Part"))):
            b = Gtk.Button(icon_name=icon, action_name=action, tooltip_text=tip)
            b.add_css_class("osd")
            b.add_css_class("circular")
            self.zoom_box.append(b)
        self.zoom_box.set_visible(False)  # shown in touch mode (T1)
        overlay = Gtk.Overlay(child=self.canvas)
        overlay.add_overlay(self.zoom_box)
        self.readout = Gtk.Label(xalign=0, label=" ")
        self.readout.add_css_class("readout")
        self.readout.add_css_class("dim-label")
        self.drawing = DrawingTools(self, self.canvas)
        part = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        part.append(self.drawing.pick_banner)
        part.append(self.drawing.construct_banner)
        # Part page messages show over the drawing, above the simulation and tool bars, so they never cover the
        # tool buttons or the coordinate entry (a window-wide toast sits on the bottom bar)
        self.part_toasts = Adw.ToastOverlay(child=overlay, vexpand=True)
        part.append(self.part_toasts)
        self.sim = SimulationBar(self.canvas, self._sim_line)
        part.append(self.sim)
        part.append(Gtk.Separator())
        part.append(self.drawing.bar)
        part.append(self.readout)

        # program view
        self.program_view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                         top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        self.program_view.add_css_class("program-text")
        self._hl = self.program_view.get_buffer().create_tag("hl", paragraph_background="rgba(53,132,228,0.18)")
        tap = Gtk.GestureClick()
        tap.connect("released", self._program_tapped)
        self.program_view.add_controller(tap)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_base", self._font_pt))
        pinch.connect("scale-changed", lambda g, s: self._set_font(self._pinch_base * s))
        self.program_view.add_controller(pinch)
        self.program_info = Gtk.Label(xalign=0)
        self.program_info.add_css_class("readout")
        self.program_info.add_css_class("dim-label")
        prog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        prog.append(Gtk.ScrolledWindow(child=self.program_view, vexpand=True))
        prog.append(Gtk.Separator())
        prog.append(self.program_info)
        self._font_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self._font_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        # size to the visible page only: a wide page (e.g. the Sheet's toolbar in Ukrainian) must not set the width of
        # the Part page with the editor open
        self.stack = Adw.ViewStack(hhomogeneous=False)
        self.stack.add_titled_with_icon(part, "part", _("Part"), "view-paged-symbolic")
        self.stack.add_titled_with_icon(prog, "program", _("Program"), "text-x-generic-symbolic")
        self.sheet = SheetPage(self)
        self.stack.add_titled_with_icon(self.sheet, "sheet", _("Sheet"), "x-office-document-symbolic")
        self.tools_page = ToolsPage(self)
        self.stack.add_titled_with_icon(self.tools_page, "tools", _("Tools"), "applications-engineering-symbolic")
        self.machine = MachinePage(self)
        self.stack.add_titled_with_icon(self.machine, "machine", _("Machine"), "network-wired-symbolic")

        header = Adw.HeaderBar()
        self.header_switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.NARROW)  # 5 pages: icon over label fits
        header.set_title_widget(self.header_switcher)
        header.pack_start(Gtk.Button(icon_name="document-open-symbolic", action_name="win.open",
                                     tooltip_text=_("Open (Ctrl+O)")))
        header.pack_start(Gtk.Button(icon_name="document-save-symbolic", action_name="win.save",
                                     tooltip_text=_("Save (Ctrl+S)")))
        undo_box = Gtk.Box()
        undo_box.add_css_class("linked")
        undo_box.append(Gtk.Button(icon_name="edit-undo-symbolic", action_name="win.undo", tooltip_text=_("Undo (Ctrl+Z)")))
        undo_box.append(Gtk.Button(icon_name="edit-redo-symbolic", action_name="win.redo",
                                   tooltip_text=_("Redo (Ctrl+Shift+Z)")))
        header.pack_start(undo_box)
        menu = Gio.Menu()
        menu.append(_("Fit Part"), "win.fit")
        menu.append(_("Show Grid"), "win.show-grid")
        menu.append(_("Part Settings…"), "win.part-settings")
        layout = Gio.Menu()
        for label, target in ((_("Automatic"), "auto"), (_("Pointer"), "pointer"), (_("Touch"), "touch")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.input-layout", GLib.Variant.new_string(target))
            layout.append_item(item)
        menu.append_section(_("Input Layout"), layout)
        about = Gio.Menu()
        about.append(_("Preferences"), "win.preferences")
        about.append(_("Help"), "win.help")
        about.append(_("About AlphaLink CAM"), "app.about")
        menu.append_section(None, about)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text=_("Main Menu")))
        export = Gtk.Button(label=_("_Export PRG"), use_underline=True, action_name="win.export",
                            tooltip_text=_("Export the Alpha-T program (Ctrl+E)"))
        export.add_css_class("suggested-action")
        header.pack_end(export)
        self.lock_button = Gtk.ToggleButton(icon_name="changes-allow-symbolic",
                                            tooltip_text=_("Touch Lock: ignore touches (hold to unlock by touch)"))
        header.pack_end(self.lock_button)

        self.banner = Adw.Banner()
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        content = Adw.ToolbarView()
        content.add_top_bar(header)
        content.add_top_bar(self.banner)
        self.editor = OpEditor(self.apply_edit_from_editor, self._move_selected, self._delete_selected,
                               lambda: self._select_op(None))
        # editor pane on the right; a Revealer instead of AdwOverlaySplitView (whose END sidebar warned about
        # negative allocations during its slide animation here)
        self.editor.set_size_request(340, -1)
        self.editor.add_css_class("editor-pane")
        self.editor_reveal = Gtk.Revealer(child=self.editor, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                          reveal_child=False)
        self.editor_split = Gtk.Box()
        self.stack.set_hexpand(True)
        self.editor_split.append(self.stack)
        self.editor_split.append(self.editor_reveal)
        content.set_content(self.editor_split)
        content.add_bottom_bar(self.switcher_bar)
        self.content_page = Adw.NavigationPage(title=_("Part"), child=content)

        self.split = Adw.NavigationSplitView(sidebar=side_page, content=self.content_page,
                                             min_sidebar_width=260, max_sidebar_width=340)
        return self.split

    # --- touch lock (TOUCH.md 7.4) -----------------------------------------------------------------------

    def _setup_lock(self) -> None:
        im = self.input_mode
        im.lock_exempt.append(self.lock_button)
        self._lock_guard = False

        def toggled(btn):
            if self._lock_guard:
                return
            im.set_locked(btn.get_active())

        def lock_changed(_im, locked):
            self._lock_guard = True
            self.lock_button.set_active(locked)
            self._lock_guard = False
            self.lock_button.set_icon_name("changes-prevent-symbolic" if locked else "changes-allow-symbolic")
            if locked:
                self.toast(_("Touch locked. Hold the lock button, or click it with the mouse, to unlock."))

        self.lock_button.connect("toggled", toggled)
        im.connect("lock-changed", lock_changed)
        # by touch, unlocking needs a long press; a plain tap on a locked button is ignored
        tap = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE, touch_only=True)
        tap.connect("pressed", lambda g, *a: im.locked and g.set_state(Gtk.EventSequenceState.CLAIMED))
        self.lock_button.add_controller(tap)
        hold = Gtk.GestureLongPress(touch_only=True, delay_factor=2.0)
        hold.connect("pressed", lambda *_: im.locked and im.set_locked(False))
        self.lock_button.add_controller(hold)

    # --- actions ----------------------------------------------------------------------------------------

    def _install_actions(self) -> None:
        def add(name, cb, accels=(), param=None):
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", list(accels))
            return a

        add("open", lambda *_: self.open_dialog(), ["<Control>o"])
        self.export_action = add("export", lambda *_: self.export_dialog(), ["<Control>e"])
        add("fit", lambda *_: self.canvas.fit(), ["<Control>0"])
        add("zoom-in", lambda *_: self.canvas.zoom(1.25), ["<Control>plus", "<Control>equal"])
        add("zoom-out", lambda *_: self.canvas.zoom(0.8), ["<Control>minus"])
        add("deselect", lambda *_: self._select_op(None), ["Escape"])
        add("save", lambda *_: self.save(), ["<Control>s"])
        add("new", lambda *_: self.new_dialog(), ["<Control>n"])
        add("preferences", lambda *_: self.preferences_dialog(), ["<Control>comma"])
        add("help", lambda *_: HelpDialog().present(self), ["F1"])
        add("part-settings", lambda *_: self.part_settings_dialog())
        grid = Gio.SimpleAction.new_stateful("show-grid", None, GLib.Variant.new_boolean(settings()["show_grid"]))
        grid.connect("change-state", self._show_grid)
        self.add_action(grid)
        self.canvas.show_grid = settings()["show_grid"]
        add("save-as", lambda *_: self.save_dialog(), ["<Control><Shift>s"])
        self.undo_action = add("undo", lambda *_: self._undo(), ["<Control>z"])
        self.redo_action = add("redo", lambda *_: self._redo(), ["<Control><Shift>z", "<Control>y"])
        add("add-op", lambda a, v: self.add_op(v.get_string()), param=GLib.VariantType.new("s"))
        self.connect("close-request", self._close_request)
        layout = Gio.SimpleAction.new_stateful("input-layout", GLib.VariantType.new("s"),
                                               GLib.Variant.new_string(settings()["input_layout"]))
        layout.connect("change-state", self._input_layout)
        self.add_action(layout)
        self.export_action.set_enabled(False)

    def _input_layout(self, action, value) -> None:
        action.set_state(value)
        self.input_mode.set_preference(value.get_string())
        settings().set("input_layout", value.get_string())

    # --- files ------------------------------------------------------------------------------------------

    def open_dialog(self) -> None:
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name=_("Parts and drawings"))
        for pat in ("*.alcam", "*.sdg", "*.SDG", "*.dxf", "*.DXF"):
            f.add_pattern(pat)
        filters.append(f)
        dlg = Gtk.FileDialog(title=_("Open Part"), filters=filters)
        dlg.open(self, None, self._opened)

    def _opened(self, dlg, res) -> None:
        try:
            file = dlg.open_finish(res)
        except GLib.Error:
            return
        self.load(Path(file.get_path()))

    def load(self, path: Path) -> None:
        if path.suffix.lower() == ".dxf":
            self._dxf_dialog(path)
            return
        note = None
        try:
            if path.suffix.lower() == ".sdg":
                project = load_sdg(path)
                note = self._ops_from_sd1(path, project)
            else:
                project = Project.load(path)
        except (OSError, ValueError, KeyError) as e:
            self.toast(_("Could not open {name}: {error}").format(name=path.name, error=e))
            return
        self.path = path
        self.set_project(project)
        if note:
            self.toast(note)

    def _dxf_dialog(self, path: Path) -> None:
        """ATDXF: a DXF becomes a new part. Lathe drawings usually give Y as a radius; units are the DXF's."""
        st = settings()
        alert = Adw.AlertDialog(heading=_("Import DXF"),
                                body=_("{name}: the DXF X axis becomes Z. How is Y drawn?").format(name=path.name))
        group = Adw.PreferencesGroup()
        y_row = Adw.ComboRow(title=_("DXF Y is"), model=Gtk.StringList.new([_("Radius"), _("Diameter")]))
        units = Adw.ComboRow(title=_("Units"), model=Gtk.StringList.new(["mm", "inch"]),
                             selected=1 if st["units"] == "inch" else 0)
        half = Adw.SwitchRow(title=_("Upper half only"), active=True,
                             subtitle=_("Drop the mirrored half below the axis"))
        for r in (y_row, units, half):
            group.add(r)
        alert.set_extra_child(group)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("import", _("_Import"))
        alert.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("import")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r != "import":
                return
            try:
                res = load_dxf(path, y_is="radius" if y_row.get_selected() == 0 else "diameter",
                               units="inch" if units.get_selected() == 1 else "mm", upper_half=half.get_active())
            except (OSError, ValueError) as e:
                self.toast(_("Could not open {name}: {error}").format(name=path.name, error=e))
                return
            res.project.first_tool = int(st["first_tool"])
            self.path = path.with_suffix(".alcam")
            self.set_project(res.project)
            self.undo.mark_unsaved()
            self.refresh()
            skipped = ", ".join(f"{k} ×{n}" for k, n in sorted(res.skipped.items()))
            msg = ngettext("Imported {n} element from {name}.", "Imported {n} elements from {name}.",
                           len(res.project.geometry)).format(n=len(res.project.geometry), name=path.name)
            if skipped:
                msg += " " + _("Skipped: {what}.").format(what=skipped)
            self.toast(msg)

        alert.connect("response", response)
        alert.present(self)

    def _ask_for_sd1(self, path: Path) -> bool:
        """No .SD1 visible next to the drawing (missing, or hidden by a sandbox): offer to pick it."""
        toast = Adw.Toast(title=GLib.markup_escape_text(
            _("{name}: drawing only, no .SD1 found next to it, so no operations.").format(name=path.name)),
            button_label=_("_Choose .SD1…"), timeout=10)
        toast.connect("button-clicked", lambda *_a: self._choose_sd1(path))
        self.add_toast(toast)
        return False

    def _choose_sd1(self, sdg: Path) -> None:
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name=_("ALPHA-T program listing (.SD1)"))
        for pat in ("*.SD1", "*.sd1"):
            f.add_pattern(pat)
        filters.append(f)
        dlg = Gtk.FileDialog(title=_("Choose the .SD1 for {name}").format(name=sdg.name), filters=filters)

        def done(d, res):
            try:
                sd1 = Path(d.open_finish(res).get_path())
            except GLib.Error:
                return
            try:
                ops = load_sd1(sd1, self.project)
            except (NotImplementedError, ValueError, KeyError, OSError) as e:
                self.toast(_("Operations not rebuilt from {name}: {error}").format(name=sd1.name, error=e))
                return
            if self.apply_edit(_("Operations from {name}").format(name=sd1.name),
                               lambda p: setattr(p, "operations", ops)):
                self.refresh()
                msg = ngettext("Rebuilt {n} operation from {name}.", "Rebuilt {n} operations from {name}.",
                               len(ops)).format(n=len(ops), name=sd1.name)
                prg = sibling(sd1, ".PRG")
                if prg is not None:
                    ok = verify(self.project, prg)
                    msg += " " + (_("The program matches ALPHA-T's {name} byte for byte.") if ok else
                                  _("The program differs from ALPHA-T's {name}: check before sending.")).format(
                                      name=prg.name)
                self.toast(msg)

        dlg.open(self, None, done)

    def _ops_from_sd1(self, path: Path, project: Project) -> str:
        """ALPHA-T keeps the operations only in the programs it writes: rebuild them from the .SD1 next to the
        drawing, and check the result against its .PRG when that is there too."""
        sd1 = sibling(path, ".SD1")
        if sd1 is None:
            GLib.idle_add(self._ask_for_sd1, path)
            return ""
        try:
            project.operations = load_sd1(sd1, project)
        except (NotImplementedError, ValueError, KeyError) as e:
            return _("Operations not rebuilt from {name}: {error}").format(name=sd1.name, error=e)
        n = len(project.operations)
        prg = sibling(path, ".PRG")
        if prg is None:
            check = ""
        elif verify(project, prg):
            check = " " + _("The program matches ALPHA-T's {name} byte for byte.").format(name=prg.name)
        else:
            check = " " + _("The program differs from ALPHA-T's {name}: check before sending.").format(name=prg.name)
        return ngettext("Rebuilt {n} operation from {name}.", "Rebuilt {n} operations from {name}.", n).format(
            n=n, name=sd1.name) + check

    def set_project(self, project: Project) -> None:
        self.project = project
        self.undo = UndoStack(project, lambda p: self._schedule_refresh())
        self.root.set_visible_child_name("main")
        self.editor_reveal.set_reveal_child(False)
        self.refresh(fit=True)

    def _schedule_refresh(self) -> None:
        if not self._refresh_id:
            self._refresh_id = GLib.idle_add(self._idle_refresh)

    def _idle_refresh(self) -> bool:
        self._refresh_id = 0
        self.refresh()
        return False

    def refresh(self, fit: bool = False) -> None:
        """Re-post and redraw after any change; keeps the view and the selected operation."""
        project = self.project
        selected = self.selected_index()
        self.pv = preview(project, library=self.library)
        name = self.path.name if self.path else _("Untitled")
        dirty = "• " if self.undo and self.undo.modified else ""
        self.set_title(f"{dirty}{name} – AlphaLink CAM")
        self.content_page.set_title(dirty + name)
        self._fill_ops()
        self._fill_program()
        self.sheet.refresh()
        if fit:
            self.canvas.set_project(project, self.pv)
        else:
            self.canvas.update(project, self.pv)
        self.sim.reset()
        if self.pv.error:
            n = "" if self.pv.error_op is None else _("Operation {n}: ").format(n=self.pv.error_op + 1)
            self.banner.set_title(_("{where}{error}. Export is disabled.").format(where=n, error=self.pv.error))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        self.export_action.set_enabled(self.pv.data is not None)
        self.undo_action.set_enabled(bool(self.undo and self.undo.can_undo))
        self.redo_action.set_enabled(bool(self.undo and self.undo.can_redo))
        prog = self.pv.program
        tools = sorted({b.tool for b in prog.blocks if b.op_kind == 1}) if prog else []
        self.machine.set_program(self.pv.data, _("Program {number} · {name} · tools {tools}").format(
            number=project.program_number, name=name, tools=", ".join(f"T{t}" for t in tools)))
        if selected is not None and selected < len(project.operations):
            self._select_op(selected, rebuild_editor=not self._editor_edit)
        else:
            self._select_op(None)

    def _tool_name(self, number: int) -> str:
        from .report import _tool_name

        return _tool_name(self.project, number, self.library) if self.project else ""

    # --- editing ----------------------------------------------------------------------------------------

    def apply_edit(self, label: str, fn) -> bool:
        try:
            self.undo.apply(label, fn)
        except (ValueError, KeyError, IndexError) as e:
            self.toast(f"{label}: {e}")
            return False
        return True

    def apply_edit_from_editor(self, label: str, fn) -> None:
        """Edits from the editor's own rows must not rebuild the editor (that would steal focus mid-typing)."""
        self._editor_edit = True
        self.apply_edit(label, fn)
        self.refresh()
        self._editor_edit = False
        self.library = load_library()

    def _undo(self) -> None:
        label = self.undo.undo() if self.undo else None
        if label:
            self.toast(_("Undid {action}").format(action=label))

    def _redo(self) -> None:
        label = self.undo.redo() if self.undo else None
        if label:
            self.toast(_("Redid {action}").format(action=label))

    def add_op(self, kind: str) -> None:
        if self.project is None:
            return
        at = self.selected_index()
        at = len(self.project.operations) if at is None else at + 1
        op = default_op(kind, self.project, at)
        if self.apply_edit(_("Add {what}").format(what=op_title(op)), lambda p: p.operations.insert(at, op)):
            self.refresh()
            self._select_op(at)

    def _delete_selected(self) -> None:
        i = self.selected_index()
        if i is None:
            return
        title = op_title(self.project.operations[i])
        if self.apply_edit(_("Delete {what}").format(what=title), lambda p: p.operations.pop(i)):
            self.refresh()
            self._select_op(min(i, len(self.project.operations) - 1) if self.project.operations else None)
            toast = Adw.Toast(title=_("Deleted {what}").format(what=title), button_label=_("_Undo"), action_name="win.undo", timeout=6)
            self.add_toast(toast)

    def _move_selected(self, delta: int) -> None:
        i = self.selected_index()
        if i is None or not 0 <= i + delta < len(self.project.operations):
            return

        def move(p):
            ops = p.operations
            ops[i], ops[i + delta] = ops[i + delta], ops[i]
        if self.apply_edit(_("Move operation"), move):
            self.refresh()
            self._select_op(i + delta)

    def preferences_dialog(self) -> None:
        """ALPHA-T's ATSETUP: defaults for new parts and the language. The serial settings live on the Machine
        page and are remembered there."""
        st = settings()
        dlg = Adw.PreferencesDialog(title=_("Preferences"))
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        new = Adw.PreferencesGroup(title=_("New parts"))
        units = Adw.ComboRow(title=_("Units"), model=Gtk.StringList.new(["mm", "inch"]),
                             selected=1 if st["units"] == "inch" else 0)
        units.connect("notify::selected", lambda r, _p: st.set("units", "inch" if r.get_selected() else "mm"))
        new.add(units)
        for key, title, lo, hi, step, digits in (("billet_diameter", _("Billet diameter"), 1, 1000, 1, 1),
                                                 ("billet_length", _("Billet length"), 1, 2000, 1, 1),
                                                 ("first_tool", _("First tool"), 1, 99, 1, 0)):
            row = Adw.SpinRow.new_with_range(lo, hi, step)
            row.set_title(title)
            row.set_digits(digits)
            row.set_value(st[key])
            row.connect("notify::value", lambda r, _p, k=key, d=digits: st.set(k, int(r.get_value()) if d == 0
                                                                              else r.get_value()))
            new.add(row)
        page.add(new)
        lang = Adw.PreferencesGroup(title=_("Language"), description=_("Takes effect after restarting the app."))
        codes = ["", "en", "uk"]
        names = [_("Same as the desktop"), "English", "Українська"]
        row = Adw.ComboRow(title=_("Language"), model=Gtk.StringList.new(names),
                           selected=codes.index(st["language"]) if st["language"] in codes else 0)
        row.connect("notify::selected", lambda r, _p: st.set("language", codes[r.get_selected()]))
        lang.add(row)
        page.add(lang)
        dlg.add(page)
        dlg.present(self)

    def _billet_rows(self, b) -> tuple[Adw.PreferencesGroup, dict]:
        """Billet rows shared by New Part and Part Settings: size, bore, jaws, tailstock."""
        group = Adw.PreferencesGroup()
        rows = {}
        for key, title, sub, lo, hi in (("diameter", _("Billet diameter"), None, 1, 1000),
                                       ("length", _("Billet length"), None, 1, 2000),
                                       ("bore", _("Bore"), _("tube billet; 0 = solid bar"), 0, 1000)):
            r = Adw.SpinRow.new_with_range(lo, hi, 1)
            r.set_title(title)
            if sub:
                r.set_subtitle(sub)
            r.set_digits(1)
            r.set_value(getattr(b, key))
            rows[key] = r
            group.add(r)
        rows["jaws"] = Adw.ComboRow(title=_("Chuck jaws"),
                                    model=Gtk.StringList.new([_("On the outside"), _("In the bore")]),
                                    selected=1 if b.jaws == "internal" else 0)
        rows["tailstock"] = Adw.SwitchRow(title=_("Show tailstock"), active=b.tailstock)
        group.add(rows["jaws"])
        group.add(rows["tailstock"])
        return group, rows

    @staticmethod
    def _billet_from(rows):
        from alphacam.model.project import Billet

        return Billet(diameter=rows["diameter"].get_value(), length=rows["length"].get_value(),
                      bore=rows["bore"].get_value(), jaws="internal" if rows["jaws"].get_selected() == 1 else "external",
                      tailstock=rows["tailstock"].get_active())

    def new_dialog(self) -> None:
        """New part: units and billet; the drawing starts empty (draw with the Line tool or type X,Z)."""
        from alphacam.model.project import Billet

        alert = Adw.AlertDialog(heading=_("New Part"), body=_("Billet size and units. The drawing starts empty."))
        units = Adw.ComboRow(title=_("Units"), model=Gtk.StringList.new(["mm", "inch"]),
                             selected=1 if settings()["units"] == "inch" else 0)
        group, rows = self._billet_rows(Billet(diameter=settings()["billet_diameter"],
                                               length=settings()["billet_length"]))
        group.add(units)
        alert.set_extra_child(group)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("create", _("_Create"))
        alert.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("create")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r != "create":
                return
            from alphacam.model.geometry import Point

            inch = units.get_selected() == 1
            settings().set("units", "inch" if inch else "mm")
            settings().set("billet_diameter", rows["diameter"].get_value())
            settings().set("billet_length", rows["length"].get_value())
            k = 25.4 if inch else 1.0
            project = Project(units="inch" if inch else "mm", billet=self._billet_from(rows),
                              tool_change=Point(round(100 / k, 4), round(50 / k, 4)),
                              first_tool=int(settings()["first_tool"]))
            self.path = None
            self.set_project(project)
            self.drawing.set_tool("line")
            self.toast(_("Draw the profile: tap points, or type X,Z and Enter"))

        alert.connect("response", response)
        alert.present(self)

    def _show_grid(self, action, value) -> None:
        action.set_state(value)
        self.canvas.show_grid = value.get_boolean()
        settings().set("show_grid", value.get_boolean())
        self.canvas.queue_draw()

    def part_settings_dialog(self) -> None:
        """Billet, bore, chuck jaws and tailstock of the open part (one undoable change)."""
        if self.project is None:
            return
        alert = Adw.AlertDialog(heading=_("Part Settings"),
                                body=_("Billet, chuck jaws, tailstock and program number of this part."))
        group, rows = self._billet_rows(self.project.billet)
        # ALPHA-T asks the 4-digit program number when saving; the control shows it when running (#132)
        number = Adw.SpinRow.new_with_range(1, 9999, 1)
        number.set_title(_("Program number"))
        number.set_subtitle(_("shown on the control"))
        number.set_value(self.project.program_number)
        group.add(number)
        alert.set_extra_child(group)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("apply", _("_Apply"))
        alert.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("apply")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r == "apply":
                billet = self._billet_from(rows)
                billet.face_allowance = self.project.billet.face_allowance
                n = int(number.get_value())

                def change(p):
                    p.billet = billet
                    p.program_number = n

                if self.apply_edit(_("Part settings"), change):
                    self.refresh(fit=True)

        alert.connect("response", response)
        alert.present(self)

    # --- saving -----------------------------------------------------------------------------------------

    def save(self) -> None:
        if self.project is None:
            return
        if self.path is None or self.path.suffix.lower() != ".alcam":
            self.save_dialog()
            return
        self._write(self.path)

    def save_dialog(self, then=None) -> None:
        if self.project is None:
            return
        stem = self.path.stem if self.path else "part"
        dlg = Gtk.FileDialog(title=_("Save Part"), initial_name=f"{stem}.alcam")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            path = Path(f.get_path())
            if path.suffix.lower() != ".alcam":
                path = path.with_suffix(".alcam")
            if self._write(path) and then:
                then()

        dlg.save(self, None, done)

    def _write(self, path: Path) -> bool:
        try:
            self.project.save(path)
        except OSError as e:
            self.toast(_("Could not save: {error}").format(error=e))
            return False
        self.path = path
        self.undo.mark_saved()
        self.refresh()
        self.toast(_("Saved {name}").format(name=path.name))
        return True

    def _close_request(self, *_args) -> bool:
        if not (self.undo and self.undo.modified):
            return False
        alert = Adw.AlertDialog(heading=_("Save changes?"),
                                body=_("The part has changes that are not saved. They are lost if you close without saving."))
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("discard", _("_Discard"))
        alert.add_response("save", _("_Save"))
        alert.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("save")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r == "discard":
                self.undo.mark_saved()
                self.close()
            elif r == "save":
                if self.path is not None and self.path.suffix.lower() == ".alcam":
                    self._write(self.path) and self.close()
                else:
                    self.save_dialog(then=self.close)

        alert.connect("response", response)
        alert.present(self)
        return True

    def export_dialog(self) -> None:
        if self.pv is None or self.pv.data is None:
            return
        stem = self.path.stem if self.path else "PART"
        dlg = Gtk.FileDialog(title=_("Export Alpha-T Program"), initial_name=f"{stem}.PRG")
        dlg.save(self, None, self._export_done)

    def _export_done(self, dlg, res) -> None:
        try:
            file = dlg.save_finish(res)
        except GLib.Error:
            return
        prg = Path(file.get_path())
        # ALPHA-T writes the .SD1 listing next to the .PRG; so do we (same name, same case of the extension)
        listing = prg.with_suffix(".sd1" if prg.suffix.islower() else ".SD1") if self.pv.sd1 is not None else None
        try:
            prg.write_bytes(self.pv.data)
            if listing is not None:
                listing.write_bytes(self.pv.sd1)
        except OSError as e:
            self.toast(_("Export failed: {error}").format(error=e))
            return
        if listing is not None:
            self.toast(_("Exported {name} ({size} bytes) and {listing}").format(
                name=prg.name, size=len(self.pv.data), listing=listing.name))
        else:
            self.toast(_("Exported {name} ({size} bytes)").format(name=prg.name, size=len(self.pv.data)))

    def toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4 if len(text) < 80 else 8))

    def add_toast(self, toast: Adw.Toast) -> None:
        on_part = self.root.get_visible_child_name() == "main" and self.stack.get_visible_child_name() == "part"
        (self.part_toasts if on_part else self.toasts).add_toast(toast)

    # --- operations list --------------------------------------------------------------------------------

    def _fill_ops(self) -> None:
        self._select_guard = True
        self.op_list.remove_all()
        self._select_guard = False
        for i, op in enumerate(self.project.operations):
            pv = self.pv.for_op(i)
            row = Adw.ActionRow(title=op_title(op), subtitle=op_summary(op, pv), activatable=True)
            badge = Gtk.Label(label=str(i + 1))
            badge.add_css_class("op-badge")
            badge.add_css_class("dim-label")
            row.add_prefix(badge)
            if self.pv.error_op is not None and i >= self.pv.error_op:
                icon = Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text=self.pv.error or "")
                icon.add_css_class("warning")
                row.add_suffix(icon)
                if i == self.pv.error_op:
                    row.set_subtitle(self.pv.error or "")
            self.op_list.append(row)

    def selected_index(self) -> int | None:
        row = self.op_list.get_selected_row()
        return None if row is None else row.get_index()

    def _row_selected(self, listbox, row) -> None:
        if self._select_guard:
            return
        index = None if row is None else row.get_index()
        self.canvas.select(index)
        self._highlight_program(index)
        if index is None:
            self.editor_reveal.set_reveal_child(False)
        else:
            self.editor.show_op(self.project, index, op_title(self.project.operations[index]))
            self.editor_reveal.set_reveal_child(True)
            if self.split.get_collapsed():
                self.split.set_show_content(True)

    def _select_op(self, index: int | None, rebuild_editor: bool = True) -> None:
        if index is None:
            self.op_list.unselect_all()
            self.canvas.select(None)
            self.editor_reveal.set_reveal_child(False)
            return
        row = self.op_list.get_row_at_index(index)
        if row is None:
            return
        if not rebuild_editor:
            self._select_guard = True
            self.op_list.select_row(row)
            self._select_guard = False
            self.canvas.select(index)
            self._highlight_program(index)
            return
        if self.op_list.get_selected_row() is row:
            self._row_selected(self.op_list, row)
        else:
            self.op_list.select_row(row)

    def _op_tapped(self, canvas, index: int) -> None:
        self._select_op(None if index < 0 else index)

    def _cursor_moved(self, canvas, x, z) -> None:
        self.readout.set_label(f"X {x:9.3f}   Z {z:9.3f}")

    # --- program view -----------------------------------------------------------------------------------

    def _fill_program(self) -> None:
        buf = self.program_view.get_buffer()
        prog = self.pv.program
        if self.pv.data is not None:
            text = self.pv.data.decode("ascii").replace("\r\n", "\n")
        else:
            text = prog.to_bytes().decode("ascii").replace("\r\n", "\n") if prog else ""
        buf.set_text(text.rstrip("\n"))
        self._block_lines = []
        if prog is not None:
            owner = {b.label: op.op_index for op in self.pv.ops for b in op.blocks}
            blocks = prog.blocks
            for b, nxt in zip(blocks, blocks[1:] + [None]):
                last = (nxt.line - 1) if nxt is not None else len(prog.statements)
                self._block_lines.append((b.line, last, owner.get(b.label)))
            size = len(self.pv.data) if self.pv.data else 0
            self.program_info.set_label(
                _("Program {number} · {units} · {lines} lines · {size} bytes").format(
                    number=prog.program_number, units=prog.units, lines=len(prog.statements), size=size)
                + ("" if self.pv.data else " · " + _("incomplete (see banner)")))

    def _highlight_program(self, index: int | None) -> None:
        buf = self.program_view.get_buffer()
        buf.remove_tag(self._hl, buf.get_start_iter(), buf.get_end_iter())
        if index is None:
            return
        spans = [(a, b) for a, b, op in self._block_lines if op == index]
        for a, b in spans:
            ok_a, it_a = buf.get_iter_at_line(a - 1)
            ok_b, it_b = buf.get_iter_at_line(b - 1)
            it_b.forward_to_line_end()
            buf.apply_tag(self._hl, it_a, it_b)
        if spans:
            ok, it = buf.get_iter_at_line(spans[0][0] - 1)
            mark = buf.get_mark("op-start") or buf.create_mark("op-start", it, True)
            buf.move_mark(mark, it)
            GLib.idle_add(lambda: self.program_view.scroll_to_mark(mark, 0.1, True, 0, 0) and False)

    def _program_tapped(self, gesture, n, x, y) -> None:
        bx, by = self.program_view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.program_view.get_iter_at_location(bx, by)
        line = it.get_line() + 1
        for a, b, op in self._block_lines:
            if a <= line <= b and op is not None:
                self._select_op(op)
                return

    def _sim_line(self, line: int) -> None:
        """Simulation position -> the matching line on the Program page (TOUCH.md 7.2/7.3)."""
        buf = self.program_view.get_buffer()
        ok, it = buf.get_iter_at_line(line - 1)
        if not ok:
            return
        end = it.copy()
        end.forward_to_line_end()
        buf.select_range(it, end)
        mark = buf.get_mark("sim") or buf.create_mark("sim", it, True)
        buf.move_mark(mark, it)
        self.program_view.scroll_to_mark(mark, 0.2, False, 0, 0)

    def _set_font(self, pt: float) -> None:
        self._font_pt = min(max(pt, 7.0), 32.0)
        self._font_css.load_from_string(f"textview.program-text {{ font-size: {self._font_pt:.1f}pt; }}")
