"""Main window (DESIGN.md 6, TOUCH.md 7): operations sidebar, Part view (lathe canvas) and Program view."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from alphacam.io.dxf import load_dxf  # noqa: E402
from alphacam.io.sd1 import load_sd1, sibling, verify  # noqa: E402
from alphacam.io.sdg import load_sdg  # noqa: E402
from alphacam.commands import UndoStack  # noqa: E402
from alphacam.model.operations import OP_KIND_TOOL_CHANGE  # noqa: E402
from alphacam.model.project import Project  # noqa: E402
from alphacam.preview import preview  # noqa: E402

from .canvas.widget import LatheCanvas  # noqa: E402
from .drawing_tools import DrawingTools  # noqa: E402
from .simulation import SimulationBar  # noqa: E402
from .report import SheetPage  # noqa: E402
from .help import MANUALS, HelpDialog, open_manual  # noqa: E402
from .shortcuts import shortcuts_dialog  # noqa: E402
from .tools_page import ToolsPage, load_library  # noqa: E402
from .i18n import N_, _, ngettext  # noqa: E402
from .settings import settings  # noqa: E402
from .input_mode import InputMode  # noqa: E402
from .machine import MachinePage  # noqa: E402
from .op_editor import OpEditor  # noqa: E402
from .op_factory import OP_MENU, default_op, find_tool, tool_for  # noqa: E402

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


def alphat_name(stem: str) -> str:
    """A DOS 8.3 name as ALPHA-T and the control expect: upper case, letters, digits, - and _, 8 at most."""
    import re as _re

    name = _re.sub(r"[^A-Z0-9_-]", "", stem.upper())[:8]
    return name or "PART"


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
        self._toast_count = 0
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
        self.editor.on_duplicate = self._duplicate_selected
        self.editor.tool_name = self._tool_name

        # narrow (half a Surface screen, portrait): the operations list becomes its own page, and the editor pane
        # gets narrower, so nothing is cut off (sidebar 260 + the Part page and its tabs need about 1000: a Surface Pro 7
        # in portrait, 912 logical pixels wide, cut the page tabs to "Prog…" at the old 900, found by tests/e2e)
        # below 700 the editor pane and the drawing do not fit side by side: the editor takes the whole page while it
        # is open (only the last matching breakpoint applies, so the small one repeats the narrow setters)
        self._small = False
        for cond, small in (("max-width: 1000sp", False), ("max-width: 700sp", True)):
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(cond))
            bp.add_setter(self.split, "collapsed", True)
            bp.add_setter(self.editor, "width-request", 280)
            bp.add_setter(self.switcher_bar, "reveal", True)
            bp.add_setter(self.header_switcher, "visible", False)
            # the header must fit a narrow (touch: 48 px buttons) window; these are in the main menu too
            for wdg in (self.open_button, self.save_button, self.undo_box):
                bp.add_setter(wdg, "visible", False)
            if small:
                bp.connect("apply", lambda *_: self._set_small(True))
                bp.connect("unapply", lambda *_: self._set_small(False))
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
        child = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER)
        child.append(btns)
        # first run: the way into the exercises (a flat button, so the two pills stay the actions)
        manual = Gtk.Button(label=_("Open the _Training Manual"), use_underline=True, action_name="win.manual-training",
                            halign=Gtk.Align.CENTER)
        manual.add_css_class("flat")
        child.append(manual)
        page.set_child(child)
        start_menu = Gio.Menu()  # what works without a part: the Machine page (Manual Ops backup), settings, help
        start_menu.append(_("New Part…"), "win.new")
        start_menu.append(_("Open…"), "win.open")
        start_menu.append(_("Open Machine Page"), "win.machine-page")
        start_menu.append_section(None, self._manuals_menu())
        start_menu.append_section(None, self._standard_menu())
        header = Adw.HeaderBar()
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=start_menu, primary=True,
                                       tooltip_text=_("Main Menu")))
        view = Adw.ToolbarView()
        view.add_top_bar(header)
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
        ops_section = Gio.Menu()
        for label, kind in OP_MENU:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.add-op", GLib.Variant.new_string(kind))
            ops_section.append_item(item)
        add_menu.append_section(None, ops_section)
        # job templates (workflow improvements, step 4): a saved list of operations applied to this drawing
        self.template_section = Gio.Menu()
        self._fill_templates()
        add_menu.append_section(_("Templates"), self.template_section)
        save_tpl = Gio.Menu()
        save_tpl.append(_("Save Operations as Template…"), "win.save-template")
        add_menu.append_section(None, save_tpl)
        self.add_op_button = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                            tooltip_text=_("Add Operation (C)"))
        side_header.pack_start(self.add_op_button)
        # setups of the part (setup 2: turned around), shown when there is more than one
        self.side_header = side_header
        self.setup_switch = Adw.ToggleGroup(css_classes=["round"])
        self.setup_switch.connect("notify::active", self._setup_switched)
        self._switching = False
        side.add_top_bar(side_header)
        side.set_content(side_scroll)
        side_page = Adw.NavigationPage(title=_("Operations"), child=side)

        # part view
        self.canvas = LatheCanvas()
        self.canvas.update_property([Gtk.AccessibleProperty.LABEL], [_("Drawing")])  # screen readers, tests/e2e
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
        overlay.add_overlay(self.drawing.chip)  # the value chip on the last drawn segment (touch)
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
        # each block starts with its label (N10, N20 ...): bold, so the blocks stand out while scrolling
        self._block_tag = self.program_view.get_buffer().create_tag("block", weight=700)
        tap = Gtk.GestureClick()
        tap.connect("released", self._program_tapped)
        self.program_view.add_controller(tap)
        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_base", self._font_pt))
        pinch.connect("scale-changed", lambda g, s: self._set_font(self._pinch_base * s))
        self.program_view.add_controller(pinch)
        self.program_info = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)  # never widens the window
        self.program_info.add_css_class("readout")
        self.program_info.add_css_class("dim-label")
        # search, and Go to N-block (TOUCH.md 7.3): Ctrl+F, or the search button for a finger
        self.program_search = Gtk.SearchEntry(placeholder_text=_("Search, or N10 to go to a block"), hexpand=True)
        self.program_search.connect("search-changed", lambda e: self._program_find(e.get_text(), 0))
        self.program_search.connect("activate", lambda e: self._program_find(e.get_text(), 1))
        self.program_search.connect("next-match", lambda e: self._program_find(e.get_text(), 1))
        self.program_search.connect("previous-match", lambda e: self._program_find(e.get_text(), -1))
        self.program_searchbar = Gtk.SearchBar(child=self.program_search, show_close_button=True)
        self.program_searchbar.connect_entry(self.program_search)
        self._found = self.program_view.get_buffer().create_tag("found", background="rgba(246,211,45,0.55)")
        prog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        prog.append(self.program_searchbar)
        prog.append(Gtk.ScrolledWindow(child=self.program_view, vexpand=True))
        prog.append(Gtk.Separator())
        info = Gtk.Box()
        info.append(self.program_info)
        self.program_info.set_hexpand(True)
        find = Gtk.ToggleButton(icon_name="edit-find-symbolic", tooltip_text=_("Search (Ctrl+F)"))
        find.add_css_class("flat")
        find.bind_property("active", self.program_searchbar, "search-mode-enabled",
                           GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        info.append(find)
        prog.append(info)
        self._font_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self._font_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        # size to the visible page only: a wide page (e.g. the Sheet's toolbar in Ukrainian) must not set the width of
        # the Part page with the editor open
        self.stack = Adw.ViewStack(hhomogeneous=False)
        self.stack.connect("notify::visible-child-name", lambda *_: (self._layout_editor(), self._focus_drawing()))
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
        self.open_button = Gtk.Button(icon_name="document-open-symbolic", action_name="win.open",
                                      tooltip_text=_("Open (Ctrl+O)"))
        self.save_button = Gtk.Button(icon_name="document-save-symbolic", action_name="win.save",
                                      tooltip_text=_("Save (Ctrl+S)"))
        header.pack_start(self.open_button)
        header.pack_start(self.save_button)
        undo_box = self.undo_box = Gtk.Box()
        undo_box.append(Gtk.Button(icon_name="edit-undo-symbolic", action_name="win.undo", tooltip_text=_("Undo (Ctrl+Z)")))
        undo_box.append(Gtk.Button(icon_name="edit-redo-symbolic", action_name="win.redo",
                                   tooltip_text=_("Redo (Ctrl+Shift+Z)")))
        header.pack_start(undo_box)
        menu = Gio.Menu()
        files = Gio.Menu()  # also reachable by touch, and the header buttons hide on a narrow window
        files.append(_("New Part…"), "win.new")
        files.append(_("Open…"), "win.open")
        files.append(_("Save"), "win.save")
        files.append(_("Save As…"), "win.save-as")
        files.append(_("Save as ALPHA-T Drawing…"), "win.save-sdg")
        menu.append_section(None, files)
        edit = Gio.Menu()
        edit.append(_("Undo"), "win.undo")
        edit.append(_("Redo"), "win.redo")
        menu.append_section(None, edit)
        view_menu = Gio.Menu()
        view_menu.append(_("Fit Part"), "win.fit")
        view_menu.append(_("Show Grid"), "win.show-grid")
        view_menu.append(_("Part Settings"), "win.part-settings")
        view_menu.append(_("Turn Part Around…"), "win.turn-around")
        view_menu.append(_("Remove This Setup…"), "win.remove-setup")
        view_menu.append(_("Clear Messages"), "win.clear-messages")
        menu.append_section(None, view_menu)
        layout = Gio.Menu()
        for label, target in ((_("Automatic"), "auto"), (_("Pointer"), "pointer"), (_("Touch"), "touch")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.input-layout", GLib.Variant.new_string(target))
            layout.append_item(item)
        menu.append_section(_("Input Layout"), layout)
        menu.append_section(None, self._manuals_menu())
        menu.append_section(None, self._standard_menu())
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text=_("Main Menu")))
        # HIG header bars: icon and label, no suggested style
        export = Gtk.Button(child=Adw.ButtonContent(icon_name="document-send-symbolic", label=_("_Export PRG"),
                                                    use_underline=True),
                            action_name="win.export", tooltip_text=_("Export ALPHA-T Program (Ctrl+E)"))
        header.pack_end(export)
        self.lock_button = Gtk.ToggleButton(icon_name="changes-allow-symbolic",
                                            tooltip_text=_("Touch Lock (Hold to Unlock by Touch)"))
        header.pack_end(self.lock_button)

        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", lambda *_: self._show_finding())
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
        self.editor_reveal.connect("notify::reveal-child", lambda *_: self._layout_editor())
        content.set_content(self.editor_split)
        content.add_bottom_bar(self.switcher_bar)
        self.content_page = Adw.NavigationPage(title=_("Part"), child=content)

        self.split = Adw.NavigationSplitView(sidebar=side_page, content=self.content_page,
                                             min_sidebar_width=260, max_sidebar_width=340)
        # a window that becomes narrow (also at start, before the breakpoint first applies) shows the drawing
        self.split.connect("notify::collapsed", lambda sp, _p: sp.get_collapsed() and self.project is not None
                           and sp.set_show_content(True))
        return self.split

    @staticmethod
    def _manuals_menu() -> Gio.Menu:
        m = Gio.Menu()
        m.append(_("Training Manual"), "win.manual-training")
        m.append(_("Reference Manual"), "win.manual-reference")
        return m

    @staticmethod
    def _standard_menu() -> Gio.Menu:
        """The HIG's last group of a main menu, in its order."""
        m = Gio.Menu()
        m.append(_("Preferences"), "win.preferences")
        m.append(_("Keyboard Shortcuts"), "win.shortcuts")
        m.append(_("Help"), "win.help")
        m.append(_("About AlphaLink CAM"), "app.about")
        return m

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
        add("shortcuts", lambda *_: shortcuts_dialog().present(self), ["<Control>question"])
        add("close", lambda *_: self.close(), ["<Control>w"])
        for kind in MANUALS:
            add(f"manual-{kind}", lambda *_, kind=kind: open_manual(self, kind))
        add("part-settings", lambda *_: self.part_settings_dialog())
        add("save-sdg", lambda *_: self.save_sdg_dialog())
        add("clear-messages", lambda *_: self.dismiss_toasts())
        add("find", lambda *_: self._open_find(), ["<Control>f"])
        grid = Gio.SimpleAction.new_stateful("show-grid", None, GLib.Variant.new_boolean(settings()["show_grid"]))
        grid.connect("change-state", self._show_grid)
        self.add_action(grid)
        self.canvas.show_grid = settings()["show_grid"]
        add("save-as", lambda *_: self.save_dialog(), ["<Control><Shift>s"])
        add("machine-page", lambda *_: self.machine_page())
        self.undo_action = add("undo", lambda *_: self._undo(), ["<Control>z"])
        self.redo_action = add("redo", lambda *_: self._redo(), ["<Control><Shift>z", "<Control>y"])
        add("add-op", lambda a, v: self.add_op(v.get_string()), param=GLib.VariantType.new("s"))
        add("apply-template", lambda a, v: self.apply_template(v.get_string()), param=GLib.VariantType.new("s"))
        add("save-template", lambda *_: self.save_template_dialog())
        add("turn-around", lambda *_: self.turn_around_dialog())
        self.remove_setup_action = add("remove-setup", lambda *_: self.remove_setup_dialog())
        self.remove_setup_action.set_enabled(False)
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
        except Exception as e:  # a damaged file must give a message, whatever the reader trips over
            if not isinstance(e, (OSError, ValueError, KeyError)):
                e = _("the file is damaged or not this kind of file ({error})").format(error=type(e).__name__)
            self.toast(_("Could not open {name}: {error}").format(name=path.name, error=e))
            return
        self.path, self.import_name = path, None
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
        units = Adw.ComboRow(title=_("Units"), model=Gtk.StringList.new([_("mm"), _("inch")]),
                             selected=1 if st["units"] == "inch" else 0)
        half = Adw.SwitchRow(title=_("Upper half only"), active=True,
                             subtitle=_("Drop the mirrored half below the axis"))
        rotate = Adw.ComboRow(title=_("Turn the drawing"), subtitle=_("Anticlockwise, for a part drawn upright"),
                              model=Gtk.StringList.new(["0°", "90°", "180°", "270°"]))
        front = Adw.ComboRow(title=_("Front face is on the"), model=Gtk.StringList.new([_("Right"), _("Left")]))
        axis = Adw.ComboRow(title=_("Spindle axis"), model=Gtk.StringList.new(
            [_("At Y0, as drawn"), _("In the middle (full section)"), _("At the lowest point (half drawing)")]))
        datum = Adw.SwitchRow(title=_("Front face on Z0"), active=True,
                              subtitle=_("Moves the drawing so its front face is the part's Z0"))
        for r in (y_row, units, half, rotate, front, axis, datum):
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
                               units="inch" if units.get_selected() == 1 else "mm", upper_half=half.get_active(),
                               rotate=90 * rotate.get_selected(), front="left" if front.get_selected() else "right",
                               axis=("as drawn", "middle", "lowest")[axis.get_selected()], datum=datum.get_active())
            except (OSError, ValueError) as e:
                self.toast(_("Could not open {name}: {error}").format(name=path.name, error=e))
                return
            res.project.first_tool = int(st["first_tool"])
            self.path = None  # an import is a new part: Save asks for a name (never overwrites NAME.alcam unasked)
            self.import_name = path.stem
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
            except Exception as e:  # NotImplementedError, ValueError, OSError, or a damaged listing
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
        except Exception as e:  # NotImplementedError / ValueError, or a damaged listing
            return _("Operations not rebuilt from {name}: {error}").format(name=sd1.name, error=str(e) or type(e).__name__)
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
        """A part (file) opens: its first setup is shown; each setup keeps its own undo history."""
        self.doc = project
        self.undos = [UndoStack(p, lambda _p: self._schedule_refresh()) for p in [project] + project.setups]
        self.root.set_visible_child_name("main")
        self._update_setups()
        self._activate(0)
        if self.split.get_collapsed():
            self.split.set_show_content(True)  # a narrow window opens on the drawing; Back shows the operations
        GLib.idle_add(self._focus_drawing)

    def setups(self) -> list[Project]:
        return [self.doc] + self.doc.setups

    def _activate(self, index: int) -> None:
        self.setup_index = index
        self.project = self.setups()[index]
        self.undo = self.undos[index]
        self.readout.set_label(" ")  # nothing selected
        self.editor_reveal.set_reveal_child(False)
        if hasattr(self, "drawing"):  # element ids repeat between setups: nothing carries over
            self.drawing.finish_chain()
            self.drawing._select_element(None)
        self.refresh(fit=True)
        self._switching = True
        try:
            if len(self.setups()) > 1:
                self.setup_switch.set_active(index)
        finally:
            self._switching = False
        self.remove_setup_action.set_enabled(index > 0)

    def _update_setups(self) -> None:
        """The Setup 1 / Setup 2 switch above the operations, when the part has more than one setup."""
        self._switching = True
        try:
            self.setup_switch.remove_all()
            for n in range(len(self.setups())):
                self.setup_switch.add(Adw.Toggle(label=_("Setup {n}").format(n=n + 1)))
        finally:
            self._switching = False
        self.side_header.set_title_widget(self.setup_switch if len(self.setups()) > 1 else None)

    def _setup_switched(self, group, _p) -> None:
        if not self._switching and group.get_active() != getattr(self, "setup_index", 0) \
                and group.get_active() < len(self.setups()):
            self.drawing.finish_chain() if hasattr(self, "drawing") else None
            self._activate(group.get_active())

    def modified(self) -> bool:
        """Any setup changed since the part was saved."""
        return any(u.modified for u in getattr(self, "undos", []))

    def _set_small(self, small: bool) -> None:
        self._small = small
        self._layout_editor()

    def _layout_editor(self) -> None:
        """The operation editor belongs to the Part and Program pages; in a small window it takes the whole page
        while it is open (closing it, or picking a contour, brings the drawing back)."""
        if not hasattr(self, "editor_reveal") or not hasattr(self, "drawing"):
            return
        page = self.stack.get_visible_child_name()
        here = page in ("part", "program")
        self.editor_reveal.set_visible(here)
        alone = self._small and here and self.editor_reveal.get_reveal_child() and self.drawing.pick_op is None
        self.stack.set_visible(not alone)
        self.editor_reveal.set_hexpand(alone)

    def _focus_drawing(self) -> bool:
        """The drawing takes the keyboard on the Part page, so ALPHA-T's cursor keys work without a click first."""
        if self.project is not None and self.stack.get_visible_child_name() == "part" and not self.get_visible_dialog():
            self.canvas.grab_focus()
        return False

    def _schedule_refresh(self) -> None:
        if not self._refresh_id:
            self._refresh_id = GLib.idle_add(self._idle_refresh)

    def _idle_refresh(self) -> bool:
        self._refresh_id = 0
        self.refresh()
        return False

    def refresh(self, fit: bool = False) -> None:
        """Re-post and redraw after any change; keeps the view and the selected operation."""
        if self._refresh_id:  # this refresh covers the one the undo stack queued (no second post, no editor rebuild)
            GLib.source_remove(self._refresh_id)
            self._refresh_id = 0
        project = self.project
        selected = self.selected_index()
        self.pv = preview(project, library=self.library)
        from alphacam.safety import check as safety_check

        # the safety check before export: warnings, never a block (alphacam/safety.py)
        self.findings = safety_check(project, self.pv, self.library) if self.pv.data is not None else []
        name = self.path.name if self.path else _("Untitled")
        if len(self.setups()) > 1:
            name += " – " + _("Setup {n}").format(n=self.setup_index + 1)
        dirty = "• " if self.modified() else ""
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
            self.banner.set_button_label(None)
            self.banner.set_revealed(True)
        elif self.findings:
            f = self.findings[0]
            where = "" if f.op_index is None else _("Operation {n}: ").format(n=f.op_index + 1)
            more = len(self.findings) - 1
            self.banner.set_title(ngettext("Safety check: {where}{what} (and {n} more)",
                                           "Safety check: {where}{what} (and {n} more)", more).format(
                where=where, what=f.message, n=more) if more else
                _("Safety check: {where}{what}").format(where=where, what=f.message))
            self.banner.set_button_label(_("_Show"))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        self.canvas.alerts = [(f.x, f.z) for f in self.findings]
        self.export_action.set_enabled(self.pv.data is not None)
        self.undo_action.set_enabled(bool(self.undo and self.undo.can_undo))
        self.redo_action.set_enabled(bool(self.undo and self.undo.can_redo))
        prog = self.pv.program
        tools = sorted({b.tool for b in prog.blocks if b.op_kind == OP_KIND_TOOL_CHANGE}) if prog else []
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
        except (ValueError, KeyError, IndexError, ZeroDivisionError) as e:
            self.toast(f"{label}: {e}")
            return False
        return True

    def apply_edit_from_editor(self, label: str, fn) -> None:
        """Edits from the editor's own rows must not rebuild the editor (that would steal focus mid-typing)."""
        self._editor_edit = True
        try:
            self.apply_edit(label, fn)
            self.refresh()
        finally:
            self._editor_edit = False

    def _undo(self) -> None:
        label = self.undo.undo() if self.undo else None
        if label:
            self.drawing.after_history()
            self.toast(_("Undid {action}").format(action=label))

    def _redo(self) -> None:
        label = self.undo.redo() if self.undo else None
        if label:
            self.drawing.after_history()
            self.toast(_("Redid {action}").format(action=label))

    def add_op(self, kind: str) -> None:
        if self.project is None:
            return
        at = self.selected_index()
        at = len(self.project.operations) if at is None else at + 1
        op = default_op(kind, self.project, at)
        tool = tool_for(kind, self.project, at, self.library)
        if tool is not None:  # the tool in use does not suit this operation: change to one that does, first
            tc = default_op("tool_change", self.project, at)
            tc.tool = tool
            op.tool = tool
            t = find_tool(self.project, tool, self.library)
            if t is not None:
                op.feed = round(t.default_feed / 25.4, 4) if self.project.units == "inch" else t.default_feed
                if kind == "groove" and t.width > 0:
                    op.tip_width = round(t.width / 25.4, 4) if self.project.units == "inch" else t.width

            def add(p):
                p.operations.insert(at, tc)
                p.operations.insert(at + 1, op)
            if self.apply_edit(_("Add {what}").format(what=op_title(op)), add):
                self.refresh()
                self._select_op(at + 1)
                self.toast(_("Tool change to T{n} added: {name}").format(n=tool, name=self._tool_name(tool)))
            return
        if self.apply_edit(_("Add {what}").format(what=op_title(op)), lambda p: p.operations.insert(at, op)):
            self.refresh()
            self._select_op(at)

    def insert_op(self, project, at: int, kind: str, **values) -> int:
        """Add a `kind` operation at `at` inside an edit (project is the edit's copy), with a tool change first when
        the tool in use does not suit it; returns the operation's index."""
        op = default_op(kind, project, at)
        for k, v in values.items():
            setattr(op, k, v)
        tool = tool_for(kind, project, at, self.library)
        if tool is not None:
            tc = default_op("tool_change", project, at)
            tc.tool = op.tool = tool
            project.operations.insert(at, tc)
            at += 1
        project.operations.insert(at, op)
        return at

    def _duplicate_selected(self) -> None:
        import copy

        i = self.selected_index()
        if i is None:
            return
        op = copy.deepcopy(self.project.operations[i])
        if self.apply_edit(_("Duplicate {what}").format(what=op_title(op)), lambda p: p.operations.insert(i + 1, op)):
            self.refresh()
            self._select_op(i + 1)

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

    # --- job templates (workflow improvements, step 4) -----------------------------------------------------------

    def _fill_templates(self) -> None:
        from . import templates as tpl

        self.template_section.remove_all()
        for i, t in enumerate(tpl.builtin()):
            item = Gio.MenuItem.new(t["name"], None)
            item.set_action_and_target_value("win.apply-template", GLib.Variant.new_string(f"builtin:{i}"))
            self.template_section.append_item(item)
        for path, t in tpl.user_templates():
            item = Gio.MenuItem.new(t.get("name") or path.stem, None)
            item.set_action_and_target_value("win.apply-template", GLib.Variant.new_string(str(path)))
            self.template_section.append_item(item)

    def apply_template(self, target: str) -> None:
        from . import templates as tpl

        if self.project is None:
            return
        if target.startswith("builtin:"):
            t = tpl.builtin()[int(target.split(":")[1])]
        else:
            t = next((t for path, t in tpl.user_templates() if str(path) == target), None)
            if t is None:
                self.toast(_("The template is gone or damaged"))
                self._fill_templates()
                return
        added = {}

        def edit(p):
            added["n"] = tpl.apply(p, t, self.library)
        if self.apply_edit(_("Template {name}").format(name=t["name"]), edit):
            self.refresh()
            self.toast(ngettext("Added {n} operation from {name}", "Added {n} operations from {name}",
                                added["n"]).format(n=added["n"], name=t["name"]))

    def save_template_dialog(self) -> None:
        from . import templates as tpl

        if self.project is None or not self.project.operations:
            self.toast(_("Add operations first: a template keeps the operations, not the drawing"))
            return
        name = Adw.EntryRow(title=_("Name"), text=self.path.stem if self.path else "")
        group = Adw.PreferencesGroup()
        group.add(name)
        alert = Adw.AlertDialog(heading=_("Save Operations as Template"),
                                body=_("The operations with their tools and settings, without the drawing. A template "
                                       "picks the contours from the part it is applied to."))
        alert.set_extra_child(group)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("save", _("_Save"))
        alert.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("save")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r != "save":
                return
            label = name.get_text().strip() or _("Template")
            try:
                path = tpl.save_user_template(tpl.from_part(self.project, label))
            except OSError as e:
                self.toast(_("Could not save {name}: {error}").format(name=label, error=e))
                return
            self._fill_templates()
            self.toast(_("Template “{name}” saved; it is in the Add Operation menu").format(name=label))
            del path

        alert.connect("response", response)
        alert.present(self)

    # --- second setup (workflow improvements, step 6) --------------------------------------------------------------

    def turn_around_dialog(self) -> None:
        """Setup 2 of the part, in the same file: the first setup's drawing turned end for end."""
        from alphacam.setups import part_length, turn_around

        if self.project is None:
            return
        first = self.doc
        inch = first.units == "inch"
        length = Adw.SpinRow.new_with_range(0.001, 5000, 1)
        length.set_title(_("Part length"))
        length.set_subtitle(_("Its back face becomes Z0"))
        length.set_digits(4 if inch else 3)
        length.set_value(part_length(first) or first.billet.length)
        from .op_editor import fit_spin

        fit_spin(length, 0.001, 5000, 4 if inch else 3)  # room for "50.000", not "0.000" cut short
        prog = Adw.SpinRow.new_with_range(1, 9999, 1)
        prog.set_title(_("Program number"))
        prog.set_subtitle(_("Of the second setup"))
        prog.set_value(min(first.program_number + 1, 9999))
        group = Adw.PreferencesGroup()
        group.add(length)
        group.add(prog)
        replacing = bool(first.setups)
        body = _("Setup 2 machines the other end: setup 1's drawing turned end for end, its back face on Z0, without "
                 "operations. Both setups are kept in this part's file.")
        if replacing:
            body += "\n\n" + _("This replaces the current setup 2 and its operations.")
        alert = Adw.AlertDialog(heading=_("Turn Part Around"), body=body)
        alert.set_extra_child(group)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("turn", _("_Replace Setup 2") if replacing else _("_Turn Around"))
        alert.set_response_appearance("turn", Adw.ResponseAppearance.DESTRUCTIVE if replacing
                                      else Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("cancel" if replacing else "turn")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r != "turn":
                return
            try:
                new = turn_around(first, length.get_value(), int(prog.get_value()))
            except ValueError as e:
                self.toast(str(e))
                return
            first.setups = [new]
            self.undos[1:] = [UndoStack(new, lambda _p: self._schedule_refresh())]
            self.undos[1].mark_unsaved()
            self._update_setups()
            self._activate(1)
            self.toast(_("Setup 2, program {n}: add its operations, then save the part.").format(
                n=new.program_number))

        alert.connect("response", response)
        alert.present(self)

    def remove_setup_dialog(self) -> None:
        if getattr(self, "setup_index", 0) == 0:
            return
        n = self.setup_index + 1
        alert = Adw.AlertDialog(heading=_("Remove Setup {n}?").format(n=n),
                                body=_("Its drawing and operations are removed from the part. This cannot be undone, "
                                       "but the file on disk keeps it until you save."))
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("remove", _("_Remove"))
        alert.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("cancel")
        alert.set_close_response("cancel")

        def response(_d, r):
            if r != "remove":
                return
            i = self.setup_index
            del self.doc.setups[i - 1]
            del self.undos[i]
            self.undos[0].mark_unsaved()  # the file still has it until saved
            self._update_setups()
            self._activate(0)

        alert.connect("response", response)
        alert.present(self)

    def preferences_dialog(self) -> None:
        """ALPHA-T's ATSETUP: defaults for new parts and the language. The serial settings live on the Machine
        page and are remembered there."""
        st = settings()
        dlg = Adw.PreferencesDialog(title=_("Preferences"))
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        new = Adw.PreferencesGroup(title=_("New parts"))
        units = Adw.ComboRow(title=_("Units"), model=Gtk.StringList.new([_("mm"), _("inch")]),
                             selected=1 if st["units"] == "inch" else 0)
        units.connect("notify::selected", lambda r, _p: st.set("units", "inch" if r.get_selected() else "mm"))
        new.add(units)
        size_rows = {}
        inch = st["units"] == "inch"
        for key, title, lo, hi, step, digits in (("billet_diameter", _("Billet diameter"), 0.01, 1000, 1, 1),
                                                 ("billet_length", _("Billet length"), 0.01, 2000, 1, 1),
                                                 ("first_tool", _("First tool"), 1, 99, 1, 0)):
            row = Adw.SpinRow.new_with_range(lo, hi, step)
            row.set_title(title)
            row.set_digits(4 if inch and digits else digits)
            row.set_value(st[key])
            row.connect("notify::value", lambda r, _p, k=key, d=digits: st.set(k, int(r.get_value()) if d == 0
                                                                              else r.get_value()))
            new.add(row)
            size_rows[key] = row
        self._units_follow(units, size_rows, ("billet_diameter", "billet_length"))
        page.add(new)
        export = Adw.PreferencesGroup(title=_("Export"))
        sdg = Adw.SwitchRow(title=_("Also save the ALPHA-T drawing (.SDG)"),
                            subtitle=_("Next to the program, so DOS ALPHA-T can open the part"),
                            active=bool(st["export_sdg"]))
        sdg.connect("notify::active", lambda r, _p: st.set("export_sdg", r.get_active()))
        export.add(sdg)
        page.add(export)
        cycle = Adw.PreferencesGroup(title=_("Cycle time"),
                                     description=_("For the estimate on the setup sheet; the tools' surface speeds "
                                                   "are on the Tools page"))
        for key, title, sub, lo, hi, step in (
                ("spindle_max", _("Spindle limit"), _("rpm"), 100, 10000, 100),
                ("rapid_rate", _("Rapid rate"), _("mm/min"), 100, 50000, 500),
                ("tool_change_s", _("Tool change"), _("seconds for the operator to change a tool"), 0, 600, 5)):
            row = Adw.SpinRow.new_with_range(lo, hi, step)
            row.set_title(title)
            row.set_subtitle(sub)
            row.set_value(float(st[key]))
            row.connect("notify::value", lambda r, _p, k=key: (st.set(k, r.get_value()), self.sheet.refresh()))
            cycle.add(row)
        page.add(cycle)
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

    @staticmethod
    def _units_follow(units_row, rows, keys=("diameter", "length", "bore")) -> None:
        """Switching mm <-> inch converts the size rows already filled in (50 mm stays 50 mm, shown as 1.9685 in)."""
        state = {"inch": units_row.get_selected() == 1}

        def changed(row, _p):
            inch = row.get_selected() == 1
            if inch == state["inch"]:
                return
            state["inch"] = inch
            for key in keys:
                r = rows[key]
                v = r.get_value()
                r.set_digits(4 if inch else 1)
                r.set_value(round(v / 25.4, 4) if inch else round(v * 25.4, 1))

        units_row.connect("notify::selected", changed)

    def _billet_rows(self, b, inch: bool = False, top: Gtk.Widget | None = None) -> tuple[Adw.PreferencesGroup, dict]:
        """Billet rows shared by New Part and Part Settings: size, bore, jaws, tailstock. Inch parts get 4 decimals
        (a 0.75 in bar must not be rounded or clamped to 1)."""
        group = Adw.PreferencesGroup()
        if top is not None:  # e.g. the units, first, so the sizes below are read in them
            group.add(top)
        rows = {}
        for key, title, sub, lo, hi in (("diameter", _("Billet diameter"), None, 0.01, 1000),
                                       ("length", _("Billet length"), None, 0.01, 2000),
                                       ("bore", _("Bore"), _("Tube billet; 0 = solid bar"), 0, 1000)):
            r = Adw.SpinRow.new_with_range(lo, hi, 1)
            r.set_title(title)
            if sub:
                r.set_subtitle(sub)
            r.set_digits(4 if inch else 1)
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
        units = Adw.ComboRow(title=_("Units"), model=Gtk.StringList.new([_("mm"), _("inch")]),
                             selected=1 if settings()["units"] == "inch" else 0)
        group, rows = self._billet_rows(Billet(diameter=settings()["billet_diameter"],
                                               length=settings()["billet_length"]), settings()["units"] == "inch",
                                        top=units)
        self._units_follow(units, rows)
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
            self.path, self.import_name = None, None
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
        # a dialog with room for the rows (an alert is too narrow: labels broke mid-word, rows scrolled away)
        dlg = Adw.Dialog(title=_("Part Settings"), content_width=520, content_height=640)
        group, rows = self._billet_rows(self.project.billet, self.project.units == "inch")
        group.set_title(_("Billet"))
        group.set_description(_("Billet, chuck jaws and tailstock only change the picture."))
        drawing = Adw.ActionRow(title=_("Drawing units"), subtitle=_("Set when the part was made"))
        drawing.add_suffix(Gtk.Label(label=_("mm") if self.project.units == "mm" else _("inch")))
        program = Adw.PreferencesGroup(title=_("Program"))
        # ALPHA-T asks the 4-digit program number when saving; the control shows it when running (#132)
        number = Adw.SpinRow.new_with_range(1, 9999, 1)
        number.set_title(_("Program number"))
        number.set_subtitle(_("Shown on the control"))
        number.set_value(self.project.program_number)
        program.add(number)
        # ALPHA-T's "Select Units To Store Output In": the program (and .SD1) can be in other units than the drawing
        unit_keys = [None, "mm", "inch"]
        out_units = Adw.ComboRow(title=_("Program units"), subtitle=_("M71 mm / M70 inch on the control"),
                                 model=Gtk.StringList.new([_("As the drawing"), _("mm"), _("inch")]))
        current = self.project.output_units if self.project.output_units != self.project.units else None
        out_units.set_selected(unit_keys.index(current))
        program.add(drawing)
        program.add(out_units)
        page = Adw.PreferencesPage()
        page.add(group)
        page.add(program)

        def apply(*_a):
            billet = self._billet_from(rows)
            billet.face_allowance = self.project.billet.face_allowance
            n = int(number.get_value())
            out = unit_keys[out_units.get_selected()]

            def change(p):
                p.billet = billet
                p.program_number = n
                p.output_units = None if out == p.units else out

            dlg.close()
            if self.apply_edit(_("Part settings"), change):
                self.refresh(fit=True)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=_("_Cancel"), use_underline=True)
        cancel.connect("clicked", lambda *_a: dlg.close())
        ok = Gtk.Button(label=_("_Apply"), use_underline=True)
        ok.add_css_class("suggested-action")
        ok.connect("clicked", apply)
        header.pack_start(cancel)
        header.pack_end(ok)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        dlg.set_child(view)
        dlg.set_default_widget(ok)
        dlg.apply = apply  # for scripts and tests
        dlg.present(self)

    def machine_page(self) -> None:
        """The Machine page without a part (e.g. to back up Manual Ops first): an empty new part behind it."""
        if self.project is None:
            from alphacam.model.project import Billet

            st = settings()
            self.set_project(Project(units=st["units"], billet=Billet(diameter=st["billet_diameter"],
                                                                      length=st["billet_length"]),
                                     first_tool=int(st["first_tool"])))
            self.undo.mark_saved()
        self.stack.set_visible_child_name("machine")

    # --- saving -----------------------------------------------------------------------------------------

    def save_sdg_dialog(self) -> None:
        """Write the drawing as an ALPHA-T .SDG (+ .SDT), so DOS ALPHA-T can open it. Operations are not part of an
        ALPHA-T drawing; Export PRG writes them to the .SD1."""
        if self.project is None:
            return
        stem = (self.path.stem if self.path else getattr(self, "import_name", None) or "PART").upper()[:8]
        dlg = Gtk.FileDialog(title=_("Save as ALPHA-T Drawing"), initial_name=f"{stem}.SDG")

        def done(d, res):
            try:
                path = Path(d.save_finish(res).get_path())
            except GLib.Error:
                return
            from alphacam.io.sdg_write import save_sdg

            try:
                files = save_sdg(self.project, path)
            except (OSError, ValueError) as e:
                self.toast(_("Saving the drawing failed: {error}").format(error=e))
                return
            note = _("Saved {files}.").format(files=", ".join(f.name for f in files))
            if len(files[0].stem) > 8:
                note += " " + _("ALPHA-T needs a name of at most 8 characters.")
            self.toast(note)

        dlg.save(self, None, done)

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
        stem = self.path.stem if self.path else getattr(self, "import_name", None) or "part"
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
            self.doc.save(path)  # every setup, in one file
        except OSError as e:
            self.toast(_("Could not save: {error}").format(error=e))
            return False
        self.path = path
        for u in self.undos:
            u.mark_saved()
        self.refresh()
        self.toast(_("Saved {name}").format(name=path.name))
        return True

    def _close_request(self, *_args) -> bool:
        if not self.modified():
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
                for u in self.undos:
                    u.mark_saved()
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
        stem = self.path.stem if self.path else getattr(self, "import_name", None) or "PART"
        base = alphat_name(stem)
        if self.setup_index > 0:  # setup 2 of NAME: NAME2 (ALPHA-T names have 8 characters at most)
            base = base[:7] + str(self.setup_index + 1)
        dlg = Gtk.FileDialog(title=_("Export Alpha-T Program"), initial_name=f"{base}.PRG")
        last = settings()["export_dir"]
        if last and Path(last).is_dir():  # back to the same USB stick or folder
            dlg.set_initial_folder(Gio.File.new_for_path(last))
        dlg.save(self, None, self._export_done)

    def _export_done(self, dlg, res) -> None:
        try:
            file = dlg.save_finish(res)
        except GLib.Error:
            return
        prg = Path(file.get_path())
        if not str(prg.parent).startswith("/run/user/"):  # a portal's document folder is not a place to go back to
            settings().set("export_dir", str(prg.parent))
        # ALPHA-T writes the .SD1 listing next to the .PRG; so do we (same name, same case of the extension)
        listing = prg.with_suffix(".sd1" if prg.suffix.islower() else ".SD1") if self.pv.sd1 is not None else None
        drawing = []
        try:
            prg.write_bytes(self.pv.data)
            if listing is not None:
                listing.write_bytes(self.pv.sd1)
            if settings()["export_sdg"]:  # Preferences: also the drawing, as ALPHA-T saves it with the program
                from alphacam.io.sdg_write import save_sdg

                drawing = save_sdg(self.project, prg.with_suffix(".sdg" if prg.suffix.islower() else ".SDG"))
        except (OSError, ValueError) as e:
            self.toast(_("Export failed: {error}").format(error=e))
            return
        if drawing:
            self.toast(_("Exported {name} ({size} bytes), {listing} and the drawing {drawing}").format(
                name=prg.name, size=len(self.pv.data), listing=listing.name if listing else "-",
                drawing=drawing[0].name))
        elif listing is not None:
            self.toast(_("Exported {name} ({size} bytes) and {listing}").format(
                name=prg.name, size=len(self.pv.data), listing=listing.name))
        else:
            self.toast(_("Exported {name} ({size} bytes)").format(name=prg.name, size=len(self.pv.data)))

    def toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4 if len(text) < 80 else 8))

    def dismiss_toasts(self) -> bool:
        """Clear every message (Esc, or main menu ▸ Clear Messages); True when there was one."""
        shown = self._toast_count > 0
        self.part_toasts.dismiss_all()
        self.toasts.dismiss_all()
        self._toast_count = 0
        return shown

    def add_toast(self, toast: Adw.Toast) -> None:
        on_part = self.root.get_visible_child_name() == "main" and self.stack.get_visible_child_name() == "part"
        self._toast_count = getattr(self, "_toast_count", 0) + 1
        toast.connect("dismissed", lambda *_: setattr(self, "_toast_count", max(0, self._toast_count - 1)))
        (self.part_toasts if on_part else self.toasts).add_toast(toast)

    # --- operations list --------------------------------------------------------------------------------

    def _fill_ops(self) -> None:
        self._select_guard = True
        self.op_list.remove_all()
        self._select_guard = False
        from alphacam.checks import tool_warnings

        warnings = tool_warnings(self.project, self.library)
        for f in getattr(self, "findings", []):
            if f.op_index is not None:
                warnings.setdefault(f.op_index, []).append(f.message)
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
            elif i in warnings:  # a tool that does not look right for the job: a warning, the program is still written
                icon = Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text="\n".join(warnings[i]))
                icon.add_css_class("warning")
                icon.update_property([Gtk.AccessibleProperty.LABEL], ["; ".join(warnings[i])])
                row.add_suffix(icon)
            row.add_suffix(self._drag_handle(row, i))
            self.op_list.append(row)

    def _drag_handle(self, row: Gtk.Widget, i: int) -> Gtk.Widget:
        """Reorder by dragging the handle only (TOUCH.md 7.1): a drag anywhere else in the list scrolls it. The row
        is the drop target; the operation lands in its place."""
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic", tooltip_text=_("Drag to Reorder"))
        handle.add_css_class("dim-label")
        handle.add_css_class("drag-handle")
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT, i)))
        src.connect("drag-begin", lambda s, drag: Gtk.DragIcon.get_for_drag(drag).set_child(
            Gtk.Label(label=op_title(self.project.operations[i]), css_classes=["drag-label", "card"])))
        handle.add_controller(src)
        drop = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        drop.connect("drop", lambda t, value, x, y: self.move_op(int(value), i))
        row.add_controller(drop)
        return handle

    def move_op(self, i: int, j: int) -> bool:
        """Operation i to place j (dragging its handle); the moved operation stays selected."""
        n = len(self.project.operations)
        if i == j or not (0 <= i < n and 0 <= j < n):
            return False

        def move(p):
            p.operations.insert(j, p.operations.pop(i))
        if self.apply_edit(_("Move operation"), move):
            self.refresh()
            self._select_op(j)
        return True

    def selected_index(self) -> int | None:
        row = self.op_list.get_selected_row()
        return None if row is None else row.get_index()

    def _row_selected(self, listbox, row) -> None:
        if self._select_guard:
            return
        index = None if row is None else row.get_index()
        self.canvas.select(index)
        self.sim.select_op(index)
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
            self.sim.select_op(None)
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
            self.sim.select_op(index)
            self._highlight_program(index)
            return
        if self.op_list.get_selected_row() is row:
            self._row_selected(self.op_list, row)
        else:
            self.op_list.select_row(row)

    def _cursor_moved(self, canvas, x, z) -> None:
        if self.canvas.cursor is not None and self.drawing._key_pointer is not None:
            return  # the keys put the cursor; the read-out shows it until the mouse really moves (drawing_tools)
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
                ok, it = buf.get_iter_at_line(b.line - 1)  # the block's label line (not the N5xx jump targets)
                end = it.copy()
                end.forward_to_line_end()
                buf.apply_tag(self._block_tag, it, end)
            size = len(self.pv.data) if self.pv.data else 0
            self.program_info.set_label(
                _("Program {number} · {units} · {lines} lines · {size} bytes").format(
                    number=prog.program_number, units=self.project.output_units or prog.units,
                    lines=len(prog.statements), size=size)
                + ("" if self.pv.data else " · " + _("incomplete (see banner)")))

    def _open_find(self) -> None:
        self.stack.set_visible_child_name("program")
        self.program_searchbar.set_search_mode(True)
        self.program_search.grab_focus()

    def _show_finding(self) -> None:
        """The banner's Show: the first warning's operation, and the drawing centred on the place."""
        if not getattr(self, "findings", None):
            return
        f = self.findings[0]
        self.stack.set_visible_child_name("part")
        if f.op_index is not None:
            self._select_op(f.op_index)
        sx, sy = self.canvas.view.to_screen(f.x, f.z)
        self.canvas.view.pan(self.canvas.get_width() / 2 - sx, self.canvas.get_height() / 2 - sy)
        self.canvas._user_view_change()

    def _program_find(self, text: str, step: int) -> None:
        """Highlight the next (step 1), previous (-1) or first (0) match from the last one; "N10" or "10" alone goes to
        that block's label line."""
        import re as _re

        buf = self.program_view.get_buffer()
        buf.remove_tag(self._found, buf.get_start_iter(), buf.get_end_iter())
        text = text.strip()
        if not text:
            return
        m = _re.fullmatch(r"[Nn]?(\d+)", text)
        if m:
            lines = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).split("\n")
            hit = next((i for i, line in enumerate(lines) if line.strip() == f"N{m[1]}"), None)
            if hit is not None:
                ok, a = buf.get_iter_at_line(hit)
                b = a.copy()
                b.forward_to_line_end()
                self._show_found(buf, a, b)
                return
        start = getattr(self, "_find_at", None)
        if step == 0 or start is None:
            it = buf.get_start_iter()
        else:
            it = buf.get_iter_at_offset(start + (1 if step > 0 else 0))
        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
        res = it.forward_search(text, flags, None) if step >= 0 else it.backward_search(text, flags, None)
        if not res:  # wrap around
            res = (buf.get_start_iter().forward_search(text, flags, None) if step >= 0
                   else buf.get_end_iter().backward_search(text, flags, None))
        if res:
            self._show_found(buf, res[0], res[1])

    def _show_found(self, buf, a, b) -> None:
        buf.apply_tag(self._found, a, b)
        self._find_at = a.get_offset()
        mark = buf.get_mark("found") or buf.create_mark("found", a, True)
        buf.move_mark(mark, a)
        self.program_view.scroll_to_mark(mark, 0.1, True, 0, 0.3)

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
