"""Main window (DESIGN.md 6, TOUCH.md 7): operations sidebar, Part view (lathe canvas) and Program view."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from alphacam.io.sdg import load_sdg  # noqa: E402
from alphacam.commands import UndoStack  # noqa: E402
from alphacam.model.project import Project  # noqa: E402
from alphacam.preview import preview  # noqa: E402

from .canvas.widget import LatheCanvas  # noqa: E402
from .input_mode import InputMode  # noqa: E402
from .machine import MachinePage  # noqa: E402
from .op_editor import OpEditor  # noqa: E402
from .op_factory import OP_MENU, default_op  # noqa: E402

OP_TITLES = {
    "tool_change": "Tool change", "area_clear": "Area clear", "face_clear": "Face clear", "groove": "Grooving",
    "finish": "Finish pass", "drill": "Drilling", "thread": "Threading", "dwell": "Dwell", "move": "Move / rapid",
}


def op_title(op) -> str:
    title = OP_TITLES.get(op.type, op.type)
    if getattr(op, "external", True) is False:
        title += " (internal)"
    if getattr(op, "pre_finish", False):
        title += " + pre-finish"
    return title


def op_summary(op, pv) -> str:
    parts = []
    if hasattr(op, "tool"):
        parts.append(f"T{op.tool}")
    if op.type == "dwell":
        parts.append(f"{op.revs:g} revs")
    elif op.type == "move":
        parts.append(f"{'rapid' if op.rapid else 'feed'} to X{op.point.x:g} Z{op.point.z:g}")
    elif op.type == "thread":
        parts.append(f"pitch {op.pitch:g}")
    elif hasattr(op, "feed"):
        parts.append(f"F{op.feed:g}")
    if pv is not None and pv.passes > 1:
        parts.append(f"{pv.passes} passes")
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

        self.toasts = Adw.ToastOverlay()
        self.root = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toasts.set_child(self.root)
        self.set_content(self.toasts)

        self.root.add_named(self._build_empty(), "empty")
        self.root.add_named(self._build_main(), "main")
        self.root.set_visible_child_name("empty")
        self.input_mode = InputMode(self)
        self.input_mode.connect("changed", lambda _m, mode: self.zoom_box.set_visible(mode == "touch"))
        self._setup_lock()
        self._install_actions()

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(self.switcher_bar, "reveal", True)
        bp.add_setter(self.header_switcher, "visible", False)
        self.add_breakpoint(bp)

    # --- layout -----------------------------------------------------------------------------------------

    def _build_empty(self) -> Gtk.Widget:
        page = Adw.StatusPage(title="No Part Open", icon_name="document-open-symbolic",
                              description="Open an AlphaLink part (.alcam) or an ALPHA-T drawing (.SDG)")
        btn = Gtk.Button(label="_Open…", use_underline=True, halign=Gtk.Align.CENTER, action_name="win.open")
        btn.add_css_class("pill")
        btn.add_css_class("suggested-action")
        page.set_child(btn)
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
        side_header.pack_start(Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                              tooltip_text="Add Operation"))
        side.add_top_bar(side_header)
        side.set_content(side_scroll)
        side_page = Adw.NavigationPage(title="Operations", child=side)

        # part view
        self.canvas = LatheCanvas()
        self.canvas.connect("cursor-moved", self._cursor_moved)
        self.canvas.connect("op-tapped", self._op_tapped)
        self.zoom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END,
                                valign=Gtk.Align.END)
        self.zoom_box.add_css_class("canvas-zoom")
        for icon, action, tip in (("zoom-in-symbolic", "win.zoom-in", "Zoom In"),
                                  ("zoom-out-symbolic", "win.zoom-out", "Zoom Out"),
                                  ("zoom-fit-best-symbolic", "win.fit", "Fit Part")):
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
        part = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        part.append(overlay)
        part.append(Gtk.Separator())
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

        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(part, "part", "Part", "view-paged-symbolic")
        self.stack.add_titled_with_icon(prog, "program", "Program", "text-x-generic-symbolic")
        self.machine = MachinePage(self)
        self.stack.add_titled_with_icon(self.machine, "machine", "Machine", "network-wired-symbolic")

        header = Adw.HeaderBar()
        self.header_switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.header_switcher)
        header.pack_start(Gtk.Button(icon_name="document-open-symbolic", action_name="win.open",
                                     tooltip_text="Open (Ctrl+O)"))
        header.pack_start(Gtk.Button(icon_name="document-save-symbolic", action_name="win.save",
                                     tooltip_text="Save (Ctrl+S)"))
        undo_box = Gtk.Box()
        undo_box.add_css_class("linked")
        undo_box.append(Gtk.Button(icon_name="edit-undo-symbolic", action_name="win.undo", tooltip_text="Undo (Ctrl+Z)"))
        undo_box.append(Gtk.Button(icon_name="edit-redo-symbolic", action_name="win.redo",
                                   tooltip_text="Redo (Ctrl+Shift+Z)"))
        header.pack_start(undo_box)
        menu = Gio.Menu()
        menu.append("Fit Part", "win.fit")
        layout = Gio.Menu()
        for label, target in (("Automatic", "auto"), ("Pointer", "pointer"), ("Touch", "touch")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.input-layout", GLib.Variant.new_string(target))
            layout.append_item(item)
        menu.append_section("Input Layout", layout)
        about = Gio.Menu()
        about.append("About AlphaLink CAM", "app.about")
        menu.append_section(None, about)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main Menu"))
        export = Gtk.Button(label="_Export PRG", use_underline=True, action_name="win.export",
                            tooltip_text="Export the Alpha-T program (Ctrl+E)")
        export.add_css_class("suggested-action")
        header.pack_end(export)
        self.lock_button = Gtk.ToggleButton(icon_name="changes-allow-symbolic",
                                            tooltip_text="Touch Lock: ignore touches (hold to unlock by touch)")
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
        self.content_page = Adw.NavigationPage(title="Part", child=content)

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
                self.toast("Touch locked. Hold the lock button, or click it with the mouse, to unlock.")

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
        add("save-as", lambda *_: self.save_dialog(), ["<Control><Shift>s"])
        self.undo_action = add("undo", lambda *_: self._undo(), ["<Control>z"])
        self.redo_action = add("redo", lambda *_: self._redo(), ["<Control><Shift>z", "<Control>y"])
        add("add-op", lambda a, v: self.add_op(v.get_string()), param=GLib.VariantType.new("s"))
        self.connect("close-request", self._close_request)
        layout = Gio.SimpleAction.new_stateful("input-layout", GLib.VariantType.new("s"),
                                               GLib.Variant.new_string("auto"))
        layout.connect("change-state", self._input_layout)
        self.add_action(layout)
        self.export_action.set_enabled(False)

    def _input_layout(self, action, value) -> None:
        action.set_state(value)
        self.input_mode.set_preference(value.get_string())

    # --- files ------------------------------------------------------------------------------------------

    def open_dialog(self) -> None:
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name="Parts and drawings")
        for pat in ("*.alcam", "*.sdg", "*.SDG"):
            f.add_pattern(pat)
        filters.append(f)
        dlg = Gtk.FileDialog(title="Open Part", filters=filters)
        dlg.open(self, None, self._opened)

    def _opened(self, dlg, res) -> None:
        try:
            file = dlg.open_finish(res)
        except GLib.Error:
            return
        self.load(Path(file.get_path()))

    def load(self, path: Path) -> None:
        try:
            project = load_sdg(path) if path.suffix.lower() == ".sdg" else Project.load(path)
        except (OSError, ValueError, KeyError) as e:
            self.toast(f"Could not open {path.name}: {e}")
            return
        self.path = path
        self.set_project(project)

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
        self.pv = preview(project)
        name = self.path.name if self.path else "Untitled"
        dirty = "• " if self.undo and self.undo.modified else ""
        self.set_title(f"{dirty}{name} – AlphaLink CAM")
        self.content_page.set_title(dirty + name)
        self._fill_ops()
        self._fill_program()
        if fit:
            self.canvas.set_project(project, self.pv)
        else:
            self.canvas.update(project, self.pv)
        if self.pv.error:
            n = "" if self.pv.error_op is None else f"Operation {self.pv.error_op + 1}: "
            self.banner.set_title(f"{n}{self.pv.error}. Export is disabled.")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        self.export_action.set_enabled(self.pv.data is not None)
        self.undo_action.set_enabled(bool(self.undo and self.undo.can_undo))
        self.redo_action.set_enabled(bool(self.undo and self.undo.can_redo))
        prog = self.pv.program
        tools = sorted({b.tool for b in prog.blocks if b.op_kind == 1}) if prog else []
        self.machine.set_program(self.pv.data, f"Program {project.program_number} · {name} · "
                                               f"tools {', '.join(f'T{t}' for t in tools)}")
        if selected is not None and selected < len(project.operations):
            self._select_op(selected, rebuild_editor=not self._editor_edit)
        else:
            self._select_op(None)

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

    def _undo(self) -> None:
        label = self.undo.undo() if self.undo else None
        if label:
            self.toast(f"Undid {label}")

    def _redo(self) -> None:
        label = self.undo.redo() if self.undo else None
        if label:
            self.toast(f"Redid {label}")

    def add_op(self, kind: str) -> None:
        if self.project is None:
            return
        at = self.selected_index()
        at = len(self.project.operations) if at is None else at + 1
        op = default_op(kind, self.project, at)
        if self.apply_edit(f"Add {op_title(op)}", lambda p: p.operations.insert(at, op)):
            self.refresh()
            self._select_op(at)

    def _delete_selected(self) -> None:
        i = self.selected_index()
        if i is None:
            return
        title = op_title(self.project.operations[i])
        if self.apply_edit(f"Delete {title}", lambda p: p.operations.pop(i)):
            self.refresh()
            self._select_op(min(i, len(self.project.operations) - 1) if self.project.operations else None)
            toast = Adw.Toast(title=f"Deleted {title}", button_label="_Undo", action_name="win.undo", timeout=6)
            self.toasts.add_toast(toast)

    def _move_selected(self, delta: int) -> None:
        i = self.selected_index()
        if i is None or not 0 <= i + delta < len(self.project.operations):
            return

        def move(p):
            ops = p.operations
            ops[i], ops[i + delta] = ops[i + delta], ops[i]
        if self.apply_edit("Move operation", move):
            self.refresh()
            self._select_op(i + delta)

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
        dlg = Gtk.FileDialog(title="Save Part", initial_name=f"{stem}.alcam")

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
            self.toast(f"Could not save: {e}")
            return False
        self.path = path
        self.undo.mark_saved()
        self.refresh()
        self.toast(f"Saved {path.name}")
        return True

    def _close_request(self, *_) -> bool:
        if not (self.undo and self.undo.modified):
            return False
        alert = Adw.AlertDialog(heading="Save changes?",
                                body="The part has changes that are not saved. They are lost if you close without saving.")
        alert.add_response("cancel", "_Cancel")
        alert.add_response("discard", "_Discard")
        alert.add_response("save", "_Save")
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
        dlg = Gtk.FileDialog(title="Export Alpha-T Program", initial_name=f"{stem}.PRG")
        dlg.save(self, None, self._export_done)

    def _export_done(self, dlg, res) -> None:
        try:
            file = dlg.save_finish(res)
        except GLib.Error:
            return
        try:
            Path(file.get_path()).write_bytes(self.pv.data)
        except OSError as e:
            self.toast(f"Export failed: {e}")
            return
        self.toast(f"Exported {Path(file.get_path()).name} ({len(self.pv.data)} bytes)")

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

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
                f"Program {prog.program_number} · {prog.units} · {len(prog.statements)} lines · {size} bytes"
                + ("" if self.pv.data else " · incomplete (see banner)"))

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

    def _set_font(self, pt: float) -> None:
        self._font_pt = min(max(pt, 7.0), 32.0)
        self._font_css.load_from_string(f"textview.program-text {{ font-size: {self._font_pt:.1f}pt; }}")
