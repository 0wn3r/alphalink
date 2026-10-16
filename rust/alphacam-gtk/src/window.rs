//! Main window (DESIGN.md 6, TOUCH.md 7): operations sidebar, Part view (lathe canvas) and Program view
//! (window.py). The layout is ui/window.blp; the pages and bars built from code are added here.

use std::cell::{Cell, OnceCell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Instant;

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::commands::UndoStack;
use alphacam::model::Project;
use alphacam::model::geometry::Point;
use alphacam::model::operations::{GrooveMode, Operation};
use alphacam::model::project::Billet;
use alphacam::model::tools::ToolLibrary;
use alphacam::preview::{OpPreview, Preview, preview};
use alphacam::safety::Finding;
use gtk::{gdk, gio, glib};

use crate::application::StartOptions;
use crate::billet::{BilletGroup, units_follow};
use crate::canvas::LatheCanvas;
use crate::drawing_tools::DrawingTools;
use crate::help::{HelpDialog, MANUALS, open_manual};
use crate::i18n::{gettext, ngettext, tr};
use crate::input_mode::{InputMode, Mode, Preference};
use crate::machine::MachinePage;
use crate::op_editor::{EditorHooks, OpEditor, fit_spin};
use crate::op_factory::{default_op, op_menu, tool_for};
use crate::report::SheetPage;
use crate::settings::{PROGRAM_FONT_PT, settings, update as update_settings};
use crate::shortcuts::shortcuts_dialog;
use crate::simulation::SimulationBar;
use crate::tools_page::{ToolsPage, load_library};
use crate::ui::{builder, chosen, object};
use crate::templates;

/// a touch this soon after the Operations list scrolled stops its glide; it does not pick an operation
const LIST_GLIDE_S: f64 = 0.3;
/// a touch that travels further than this is a swipe
const TAP_SLOP_PX: f64 = 12.0;
/// operation 1 (tool change) of a PRG block (#127)
const OP_KIND_TOOL_CHANGE: i64 = 1;

/// The pages in the order of the switcher (Alt+1 … Alt+5).
const PAGES: [&str; 5] = ["part", "program", "sheet", "tools", "machine"];

pub fn op_title(op: &Operation) -> String {
    let base = match op {
        Operation::ToolChange(_) => "Tool change",
        Operation::AreaClear(_) => "Area clear",
        Operation::FaceClear(_) => "Face clear",
        Operation::Groove(_) => "Grooving",
        Operation::FinishPass(_) => "Finish pass",
        Operation::Drill(_) => "Drilling",
        Operation::Thread(_) => "Threading",
        Operation::Dwell(_) => "Dwell",
        Operation::MoveTo(_) => "Move / rapid",
    };
    let mut title = gettext(base);
    let external = match op {
        Operation::AreaClear(o) => o.external,
        Operation::Groove(o) => o.external,
        Operation::FinishPass(o) => o.external,
        Operation::Thread(o) => o.external,
        _ => true,
    };
    if !external {
        title = tr("{title} (internal)", &[("title", &title)]);
    }
    let pre_finish = match op {
        Operation::AreaClear(o) => o.pre_finish,
        Operation::FaceClear(o) => o.pre_finish,
        _ => false,
    };
    if pre_finish {
        title = tr("{title} + pre-finish", &[("title", &title)]);
    }
    if let Operation::Groove(g) = op
        && g.mode == GrooveMode::Finish
    {
        title = tr("{title} (finish)", &[("title", &title)]);
    }
    if let Operation::AreaClear(a) = op
        && !a.front
    {
        title = tr("{title} (face direction)", &[("title", &title)]);
    }
    title
}

pub fn op_summary(op: &Operation, pv: Option<&OpPreview>) -> String {
    let g = alphacam::py::fmt_g;
    let mut parts = vec![];
    if let Some(t) = op.tool() {
        parts.push(format!("T{t}"));
    }
    match op {
        Operation::Dwell(d) => parts.push(tr("{revs} revs", &[("revs", &g(d.revs))])),
        Operation::MoveTo(m) => parts.push(tr(
            if m.rapid { "rapid to {point}" } else { "feed to {point}" },
            &[("point", &format!("X{} Z{}", g(m.point.x), g(m.point.z)))],
        )),
        Operation::Thread(t) => parts.push(tr("pitch {pitch}", &[("pitch", &g(t.pitch))])),
        _ => {
            if let Some(f) = op.feed() {
                parts.push(format!("F{}", g(f)));
            }
        }
    }
    if let Some(pv) = pv {
        let n = pv.passes();
        if n > 1 {
            parts.push(crate::i18n::fill(&ngettext("{n} pass", "{n} passes", n as u32), &[("n", &n)]));
        }
    }
    parts.join(" · ")
}

/// A DOS 8.3 name as ALPHA-T and the control expect: upper case, letters, digits, - and _, 8 at most.
pub fn alphat_name(stem: &str) -> String {
    let name: String =
        stem.to_uppercase().chars().filter(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || *c == '_' || *c == '-').take(8).collect();
    if name.is_empty() { "PART".into() } else { name }
}

fn stem(path: &Path) -> String {
    path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
}

fn file_name(path: &Path) -> String {
    path.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
}

fn is_alcam(path: &Path) -> bool {
    path.extension().is_some_and(|e| e.eq_ignore_ascii_case("alcam"))
}

/// What the open part is: its file, its setups (each with its own undo history) and the last post.
#[derive(Default)]
pub struct State {
    pub path: Option<PathBuf>,
    /// the name of the DXF a new part was imported from (Save asks for a name)
    pub import_name: Option<String>,
    /// one per setup; undos[0] is the part (setup 1), the others its further setups
    pub undos: Vec<UndoStack>,
    pub setup_index: usize,
    /// the shown setup as it is now (a copy: readers never hold a borrow of the window)
    pub project: Option<Rc<Project>>,
    pub preview: Option<Rc<Preview>>,
    pub findings: Vec<Finding>,
    /// (first line, last line, op index) of each program block
    pub block_lines: Vec<(usize, usize, Option<usize>)>,
    pub find_at: Option<i32>,
}

/// The parts built from code once the template is up.
pub struct Parts {
    pub drawing: Rc<DrawingTools>,
    pub sim: SimulationBar,
    pub sheet: SheetPage,
    pub tools_page: ToolsPage,
    pub machine: MachinePage,
    pub input_mode: Rc<InputMode>,
    pub setup_switch: adw::ToggleGroup,
    pub hl: gtk::TextTag,
    pub block_tag: gtk::TextTag,
    pub found: gtk::TextTag,
    pub font_css: gtk::CssProvider,
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/window.ui")]
    pub struct MainWindow {
        #[template_child]
        pub toasts: TemplateChild<adw::ToastOverlay>,
        #[template_child]
        pub root: TemplateChild<gtk::Stack>,
        #[template_child]
        pub split: TemplateChild<adw::NavigationSplitView>,
        #[template_child]
        pub side_header: TemplateChild<adw::HeaderBar>,
        #[template_child]
        pub add_op_button: TemplateChild<gtk::MenuButton>,
        #[template_child]
        pub op_list: TemplateChild<gtk::ListBox>,
        #[template_child]
        pub content_page: TemplateChild<adw::NavigationPage>,
        #[template_child]
        pub lock_button: TemplateChild<gtk::ToggleButton>,
        #[template_child]
        pub banner: TemplateChild<adw::Banner>,
        #[template_child]
        pub stack: TemplateChild<adw::ViewStack>,
        #[template_child]
        pub part_page: TemplateChild<gtk::Box>,
        #[template_child]
        pub part_toasts: TemplateChild<adw::ToastOverlay>,
        #[template_child]
        pub part_overlay: TemplateChild<gtk::Overlay>,
        #[template_child]
        pub canvas: TemplateChild<LatheCanvas>,
        #[template_child]
        pub zoom_box: TemplateChild<gtk::Box>,
        #[template_child]
        pub part_separator: TemplateChild<gtk::Separator>,
        #[template_child]
        pub readout: TemplateChild<gtk::Label>,
        #[template_child]
        pub program_searchbar: TemplateChild<gtk::SearchBar>,
        #[template_child]
        pub program_search: TemplateChild<gtk::SearchEntry>,
        #[template_child]
        pub program_view: TemplateChild<gtk::TextView>,
        #[template_child]
        pub program_info: TemplateChild<gtk::Label>,
        #[template_child]
        pub editor_reveal: TemplateChild<gtk::Revealer>,
        #[template_child]
        pub editor: TemplateChild<OpEditor>,
        #[template_child]
        pub ops_section: TemplateChild<gio::Menu>,
        #[template_child]
        pub template_section: TemplateChild<gio::Menu>,

        pub state: RefCell<State>,
        pub library: RefCell<Rc<ToolLibrary>>,
        pub parts: OnceCell<Parts>,
        pub refresh_id: RefCell<Option<glib::SourceId>>,
        pub select_guard: Cell<bool>,
        pub small: Cell<bool>,
        pub editor_edit: Cell<bool>,
        pub switching: Cell<bool>,
        pub toast_count: Cell<u32>,
        pub suppress_select: Cell<bool>,
        pub suppress_restore: Cell<Option<usize>>,
        pub font_pt: Cell<f64>,
        pub pinch_base: Cell<f64>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MainWindow {
        const NAME: &'static str = "AlphaLinkMainWindow";
        type Type = super::MainWindow;
        type ParentType = adw::ApplicationWindow;

        fn class_init(klass: &mut Self::Class) {
            LatheCanvas::ensure_type();
            OpEditor::ensure_type();
            BilletGroup::ensure_type();
            klass.bind_template();
            bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for MainWindow {}
    impl WidgetImpl for MainWindow {}
    impl WindowImpl for MainWindow {}
    impl ApplicationWindowImpl for MainWindow {}
    impl AdwApplicationWindowImpl for MainWindow {}
}

glib::wrapper! {
    pub struct MainWindow(ObjectSubclass<imp::MainWindow>)
        @extends adw::ApplicationWindow, gtk::ApplicationWindow, gtk::Window, gtk::Widget,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget,
            gtk::Native, gtk::Root, gtk::ShortcutManager;
}

pub fn new(app: &adw::Application) -> MainWindow {
    let win: MainWindow = glib::Object::builder().property("application", app).build();
    win.setup();
    win
}

/// A file from the command line, then the debugging options (--select N, --page NAME, --touch).
pub fn open(win: &MainWindow, path: &Path, options: &StartOptions) {
    win.load(path);
    if options.touch {
        win.parts().input_mode.set_preference(Preference::Fixed(Mode::Touch));
    }
    if let Some(page) = &options.page {
        win.imp().stack.set_visible_child_name(page);
    }
    if let Some(n) = options.select_op {
        win.select_op(Some(n));
    }
}

fn arg<T: for<'a> glib::value::FromValue<'a> + 'static>(values: &[glib::Value], i: usize) -> Option<T> {
    values.get(i).and_then(|v| v.get::<T>().ok())
}

/// Binds the handlers ui/window.blp names. The template scope passes (emitter, signal arguments..., template object):
/// the window is the last value, the signal's own values come before it.
fn bind_callbacks(klass: &mut <imp::MainWindow as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&MainWindow, &[glib::Value]) -> Option<glib::Value>);
    let handlers: [Handler; 14] = [
        ("_close_request", |w, _| Some(w.close_request_cb().to_value())),
        ("_split_collapsed", |w, _| {
            w.split_collapsed();
            None
        }),
        ("_page_changed", |w, _| {
            w.page_changed();
            None
        }),
        ("_small_applied", |w, _| {
            w.set_small(true);
            None
        }),
        ("_small_unapplied", |w, _| {
            w.set_small(false);
            None
        }),
        ("_program_search_changed", |w, v| {
            if let Some(e) = arg::<gtk::SearchEntry>(v, 0) {
                w.program_search_changed(&e)
            }
            None
        }),
        ("_program_search_next", |w, v| {
            if let Some(e) = arg::<gtk::SearchEntry>(v, 0) {
                w.program_search_next(&e)
            }
            None
        }),
        ("_program_search_previous", |w, v| {
            if let Some(e) = arg::<gtk::SearchEntry>(v, 0) {
                w.program_search_previous(&e)
            }
            None
        }),
        ("_pinch_begin", |w, _| {
            w.pinch_begin();
            None
        }),
        ("_pinch_scaled", |w, v| {
            if let Some(scale) = arg::<f64>(v, 1) {
                w.pinch_scaled(scale)
            }
            None
        }),
        ("_row_selected", |w, v| {
            if let Some(listbox) = arg::<gtk::ListBox>(v, 0) {
                w.row_selected(&listbox, arg::<gtk::ListBoxRow>(v, 1).as_ref())
            }
            None
        }),
        ("_cursor_moved", |w, v| {
            if let (Some(x), Some(z)) = (arg::<f64>(v, 1), arg::<f64>(v, 2)) {
                w.cursor_moved(x, z)
            }
            None
        }),
        ("_show_finding", |w, _| {
            w.show_finding();
            None
        }),
        ("_program_tapped", |w, v| {
            if let (Some(x), Some(y)) = (arg::<f64>(v, 2), arg::<f64>(v, 3)) {
                w.program_tapped(x, y)
            }
            None
        }),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            let (last, args) = values.split_last()?;
            let win = last.get::<MainWindow>().ok()?;
            handler(&win, args)
        });
    }
}

impl MainWindow {
    fn close_request_cb(&self) -> bool {
        if !self.modified() {
            return false;
        }
        let alert: adw::AlertDialog = object(&builder("save-changes"), "close_alert");
        let win = self.downgrade();
        alert.connect_response(None, move |_, r| {
            let Some(win) = win.upgrade() else { return };
            match r {
                "discard" => {
                    win.imp().state.borrow_mut().undos.iter_mut().for_each(UndoStack::mark_saved);
                    win.close();
                }
                "save" => {
                    let path = win.path().filter(|p| is_alcam(p));
                    match path {
                        Some(p) => {
                            if win.write(&p) {
                                win.close();
                            }
                        }
                        None => {
                            let w = win.downgrade();
                            win.save_dialog(Some(Box::new(move || {
                                if let Some(w) = w.upgrade() {
                                    w.close()
                                }
                            })));
                        }
                    }
                }
                _ => {}
            }
        });
        alert.present(Some(self));
        true
    }

    fn split_collapsed(&self) {
        // a window that becomes narrow (also at start, before the breakpoint first applies) shows the drawing
        let split = &self.imp().split;
        if split.is_collapsed() && self.has_project() {
            split.set_show_content(true);
        }
    }

    fn page_changed(&self) {
        self.layout_editor();
        self.focus_drawing();
    }

    fn set_small(&self, small: bool) {
        self.imp().small.set(small);
        self.layout_editor();
    }

    fn program_search_changed(&self, entry: &gtk::SearchEntry) {
        self.program_find(&entry.text(), 0);
    }

    fn program_search_next(&self, entry: &gtk::SearchEntry) {
        self.program_find(&entry.text(), 1);
    }

    fn program_search_previous(&self, entry: &gtk::SearchEntry) {
        self.program_find(&entry.text(), -1);
    }

    fn pinch_begin(&self) {
        self.imp().pinch_base.set(self.imp().font_pt.get());
    }

    fn pinch_scaled(&self, scale: f64) {
        self.set_font(self.imp().pinch_base.get() * scale);
    }

    fn row_selected(&self, listbox: &gtk::ListBox, row: Option<&gtk::ListBoxRow>) {
        let imp = self.imp();
        if imp.select_guard.get() || imp.parts.get().is_none() {
            return;
        }
        if imp.suppress_select.get() {
            // the touch only stopped the list: put the selection back as it was
            imp.suppress_select.set(false);
            imp.select_guard.set(true);
            match imp.suppress_restore.get().and_then(|i| listbox.row_at_index(i as i32)) {
                Some(r) => listbox.select_row(Some(&r)),
                None => listbox.unselect_all(),
            }
            imp.select_guard.set(false);
            return;
        }
        let index = row.map(|r| r.index() as usize);
        imp.canvas.select(index);
        self.parts().sim.select_op(index);
        self.highlight_program(index);
        match (index, self.project()) {
            (Some(i), Some(project)) if i < project.operations.len() => {
                imp.editor.show_op(&project, i, &op_title(&project.operations[i]));
                imp.editor_reveal.set_reveal_child(true);
                if imp.split.is_collapsed() {
                    imp.split.set_show_content(true);
                }
            }
            _ => imp.editor_reveal.set_reveal_child(false),
        }
    }

    fn cursor_moved(&self, x: f64, z: f64) {
        if self.imp().canvas.state().overlay.cursor.is_some() && self.parts().drawing.key_pointer() {
            return; // the keys put the cursor; the read-out shows it until the mouse really moves (drawing_tools)
        }
        self.imp().readout.set_label(&format!("X {x:9.3}   Z {z:9.3}"));
    }

    /// The banner's Show: the first warning's operation, and the drawing centred on the place.
    fn show_finding(&self) {
        let Some(f) = self.imp().state.borrow().findings.first().cloned() else { return };
        let imp = self.imp();
        imp.stack.set_visible_child_name("part");
        if f.op_index.is_some() {
            self.select_op(f.op_index);
        }
        let (w, h) = (imp.canvas.width() as f64, imp.canvas.height() as f64);
        {
            let mut s = imp.canvas.state_mut();
            let (sx, sy) = s.view.to_screen(f.x, f.z);
            s.view.pan(w / 2.0 - sx, h / 2.0 - sy);
        }
        imp.canvas.user_view_change();
    }

    fn program_tapped(&self, x: f64, y: f64) {
        let view = &self.imp().program_view;
        let (bx, by) = view.window_to_buffer_coords(gtk::TextWindowType::Widget, x as i32, y as i32);
        let Some(it) = view.iter_at_location(bx, by) else { return };
        let op = self.op_at_line(it.line() as usize + 1);
        if op.is_some() {
            self.select_op(op);
        }
    }
}

impl MainWindow {
    // --- what the pages use ---------------------------------------------------------------------------------

    pub fn parts(&self) -> &Parts {
        self.imp().parts.get().expect("the window is set up")
    }

    /// What the end-to-end tests read through the test bus (test_bus.rs State): the page, the view, the
    /// operations and the drawing tools' state.
    pub fn test_state(&self) -> serde_json::Value {
        let imp = self.imp();
        let (view, tool, sim) = {
            let s = imp.canvas.state();
            (s.view, s.tool.clone(), s.sim_index)
        };
        let project = self.project();
        let ops: Vec<String> = project.iter().flat_map(|p| p.operations.iter().map(op_title)).collect();
        let buf = imp.program_view.buffer();
        let caret_line = buf.iter_at_mark(&buf.get_insert()).line() + 1;
        serde_json::json!({
            "page": self.stack().visible_child_name().map(String::from),
            "view": {"scale": view.scale, "ox": view.ox, "oy": view.oy},
            "tool": tool,
            "sim_index": sim,
            "elements": project.as_ref().map_or(0, |p| p.geometry.len()),
            "operations": ops,
            "selected_op": self.selected_index(),
            "readout": self.readout().label().to_string(),
            "program_font_pt": imp.font_pt.get(),
            "program_caret_line": caret_line,
            "drawing": self.parts().drawing.test_state(),
        })
    }

    pub fn has_project(&self) -> bool {
        self.imp().state.borrow().project.is_some()
    }

    /// The shown setup as it is now.
    pub fn project(&self) -> Option<Rc<Project>> {
        self.imp().state.borrow().project.clone()
    }

    /// The last post of the shown setup.
    pub fn preview(&self) -> Option<Rc<Preview>> {
        self.imp().state.borrow().preview.clone()
    }

    pub fn findings(&self) -> Vec<Finding> {
        self.imp().state.borrow().findings.clone()
    }

    pub fn library(&self) -> Rc<ToolLibrary> {
        self.imp().library.borrow().clone()
    }

    /// The Tools page changed the library: every part re-posts with it.
    pub fn set_library(&self, library: ToolLibrary) {
        let lib = Rc::new(library);
        self.imp().canvas.state_mut().library = Some(lib.clone());
        *self.imp().library.borrow_mut() = lib;
    }

    pub fn path(&self) -> Option<PathBuf> {
        self.imp().state.borrow().path.clone()
    }

    pub fn import_name(&self) -> Option<String> {
        self.imp().state.borrow().import_name.clone()
    }

    pub fn setup_count(&self) -> usize {
        self.imp().state.borrow().undos.len()
    }

    pub fn setup_index(&self) -> usize {
        self.imp().state.borrow().setup_index
    }

    pub fn canvas(&self) -> LatheCanvas {
        self.imp().canvas.get()
    }

    pub fn readout(&self) -> gtk::Label {
        self.imp().readout.get()
    }

    pub fn stack(&self) -> adw::ViewStack {
        self.imp().stack.get()
    }

    pub fn program_view(&self) -> gtk::TextView {
        self.imp().program_view.get()
    }

    pub fn add_op_button(&self) -> gtk::MenuButton {
        self.imp().add_op_button.get()
    }

    pub fn part_overlay(&self) -> gtk::Overlay {
        self.imp().part_overlay.get()
    }

    // --- set up -----------------------------------------------------------------------------------------

    fn setup(&self) {
        let imp = self.imp();
        imp.font_pt.set(settings().program_font_pt);
        *imp.library.borrow_mut() = Rc::new(load_library());
        self.build_main();
        let input_mode = self.parts().input_mode.clone();
        let win = self.downgrade();
        let apply = move |mode: Mode| {
            if let Some(win) = win.upgrade() {
                win.imp().zoom_box.set_visible(mode == Mode::Touch);
                win.parts().drawing.set_touch(mode == Mode::Touch);
            }
        };
        // the mode was already chosen (from the saved layout) before this handler existed
        apply(input_mode.mode());
        input_mode.connect_changed(apply);
        self.setup_lock();
        self.guard_list_taps();
        self.setup_op_keys();
        // a finger on any dialog's number opens the keypad (POINTER_KEYBOARD.md 11)
        self.connect_visible_dialog_notify(|win| {
            if let Some(dlg) = win.visible_dialog() {
                crate::op_editor::keypad_on_touch_all(&dlg);
            }
        });
        self.setup_program_keys();
        self.set_font(imp.font_pt.get());
        self.install_actions();
        let w = self.downgrade();
        let hook = move || w.upgrade();
        let (h1, h2, h3, h4, h5, h6, h7) =
            (hook.clone(), hook.clone(), hook.clone(), hook.clone(), hook.clone(), hook.clone(), hook);
        imp.editor.set_hooks(EditorHooks {
            apply: Box::new(move |label, f| {
                if let Some(w) = h1() {
                    w.apply_edit_from_editor(label, f)
                }
            }),
            on_move: Box::new(move |delta| {
                if let Some(w) = h2() {
                    w.move_selected(delta)
                }
            }),
            on_delete: Box::new(move || {
                if let Some(w) = h3() {
                    w.delete_selected()
                }
            }),
            on_close: Box::new(move || {
                if let Some(w) = h4() {
                    w.select_op(None)
                }
            }),
            on_pick: Box::new(move |n| {
                if let Some(w) = h5() {
                    w.parts().drawing.start_pick(n)
                }
            }),
            on_duplicate: Box::new(move || {
                if let Some(w) = h6() {
                    w.duplicate_selected()
                }
            }),
            tool_name: Box::new(move |n| h7().map(|w| w.tool_name(n)).unwrap_or_default()),
        });
    }

    /// What ui/window.blp leaves to the code: the operations and templates in the Add Operation menu, the drawing
    /// tools and the simulation on the Part page, the Sheet, Tools and Machine pages.
    fn build_main(&self) {
        let imp = self.imp();
        for (label, kind) in op_menu() {
            let item = gio::MenuItem::new(Some(&label), None);
            item.set_action_and_target_value(Some("win.add-op"), Some(&kind.to_variant()));
            imp.ops_section.append_item(&item);
        }
        self.fill_templates();
        // setups of the part (setup 2: turned around), shown when there is more than one
        let setup_switch = adw::ToggleGroup::new();
        setup_switch.add_css_class("round");
        let win = self.downgrade();
        setup_switch.connect_active_notify(move |group| {
            if let Some(win) = win.upgrade() {
                win.setup_switched(group);
            }
        });

        // part view: banners above the drawing, the simulation below it, then the drawing tools' bar
        imp.canvas.state_mut().library = Some(self.library());
        let drawing = DrawingTools::new(self, &imp.canvas);
        imp.part_overlay.add_overlay(&drawing.chip);
        imp.part_page.prepend(&drawing.construct_banner);
        imp.part_page.prepend(&drawing.pick_banner);
        let win = self.downgrade();
        let sim = SimulationBar::new(&imp.canvas, move |line| {
            if let Some(win) = win.upgrade() {
                win.sim_line(line);
            }
        });
        imp.part_page.insert_child_after(&sim, Some(&*imp.part_toasts));
        imp.part_page.insert_child_after(&drawing.bar, Some(&*imp.part_separator));

        // program view
        let buf = imp.program_view.buffer();
        let hl = buf.create_tag(Some("hl"), &[("paragraph-background", &"rgba(53,132,228,0.18)")]).expect("tag");
        // each block starts with its label (N10, N20 ...): bold, so the blocks stand out while scrolling
        let block_tag = buf.create_tag(Some("block"), &[("weight", &700i32)]).expect("tag");
        let found = buf.create_tag(Some("found"), &[("background", &"rgba(246,211,45,0.55)")]).expect("tag");
        imp.program_searchbar.connect_entry(&*imp.program_search);
        let font_css = gtk::CssProvider::new();
        gtk::style_context_add_provider_for_display(
            &WidgetExt::display(self),
            &font_css,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        );

        let sheet = SheetPage::new(self);
        let tools_page = ToolsPage::new(self);
        let machine = MachinePage::new(self);
        let input_mode = InputMode::new(self, Preference::parse(&settings().input_layout));
        let parts = Parts {
            drawing,
            sim,
            sheet,
            tools_page,
            machine,
            input_mode,
            setup_switch,
            hl,
            block_tag,
            found,
            font_css,
        };
        let _ = imp.parts.set(parts);
        let p = self.parts();
        imp.stack.add_titled_with_icon(&p.sheet, Some("sheet"), &gettext("Sheet"), "x-office-document-symbolic");
        imp.stack.add_titled_with_icon(
            &p.tools_page,
            Some("tools"),
            &gettext("Tools"),
            "applications-engineering-symbolic",
        );
        imp.stack.add_titled_with_icon(&p.machine, Some("machine"), &gettext("Machine"), "network-wired-symbolic");
    }

    /// The operations by keys, with the list or the editor focused (POINTER_KEYBOARD.md 6): Alt+Up / Alt+Down move
    /// the selected one, Ctrl+D duplicates it, Delete deletes it (with the Undo toast).
    fn setup_op_keys(&self) {
        let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let win = self.downgrade();
        keys.connect_key_pressed(move |_, keyval, _, state| {
            let Some(win) = win.upgrade() else { return glib::Propagation::Proceed };
            let imp = win.imp();
            let Some(focus) = gtk::prelude::GtkWindowExt::focus(&win) else { return glib::Propagation::Proceed };
            let in_list = focus.is_ancestor(&*imp.op_list) || focus == *imp.op_list;
            if !(in_list || focus.is_ancestor(&*imp.editor)) || win.selected_index().is_none() {
                return glib::Propagation::Proceed;
            }
            let mods = state & gtk::accelerator_get_default_mod_mask();
            let (alt, ctrl) = (mods == gdk::ModifierType::ALT_MASK, mods == gdk::ModifierType::CONTROL_MASK);
            match keyval {
                gdk::Key::Up | gdk::Key::KP_Up if alt => win.move_selected(-1),
                gdk::Key::Down | gdk::Key::KP_Down if alt => win.move_selected(1),
                gdk::Key::d | gdk::Key::D if ctrl => win.duplicate_selected(),
                gdk::Key::Delete | gdk::Key::KP_Delete if mods.is_empty() && !focus.is::<gtk::Editable>() => {
                    win.delete_selected()
                }
                _ => return glib::Propagation::Proceed,
            }
            // the keyboard stays on the list, on the operation it acted on
            if in_list && let Some(row) = imp.op_list.selected_row() {
                row.grab_focus();
            }
            glib::Propagation::Stop
        });
        self.add_controller(keys);
    }

    // --- touch lock (TOUCH.md 7.4) -----------------------------------------------------------------------

    /// A touch that lands on the Operations list while it is still gliding after a swipe stops it; it is not a tap
    /// on an operation (Eduard 2026-10-16: swiping through the list kept selecting operations). Neither is a touch
    /// that travels (a swipe the list could not follow, at its end). The list's own selection on such a touch is
    /// undone.
    fn guard_list_taps(&self) {
        #[derive(Default)]
        struct Guard {
            moved: Option<Instant>,
            stop: bool,
            swipe: bool,
            before: Option<usize>,
            at: (f64, f64),
        }
        let imp = self.imp();
        let scrolled = imp.op_list.ancestor(gtk::ScrolledWindow::static_type()).and_downcast::<gtk::ScrolledWindow>();
        let Some(scrolled) = scrolled else { return };
        let state = Rc::new(RefCell::new(Guard::default()));
        let s = state.clone();
        scrolled.vadjustment().connect_value_changed(move |_| s.borrow_mut().moved = Some(Instant::now()));

        let tap = gtk::GestureClick::builder().touch_only(true).propagation_phase(gtk::PropagationPhase::Capture).build();
        let (s, win) = (state.clone(), self.downgrade());
        tap.connect_pressed(move |_, _, _, _| {
            let Some(win) = win.upgrade() else { return };
            let mut g = s.borrow_mut();
            g.before = win.selected_index();
            g.stop = g.moved.is_some_and(|t| t.elapsed().as_secs_f64() < LIST_GLIDE_S);
        });
        let (s, win) = (state.clone(), self.downgrade());
        tap.connect_released(move |_, _, _, _| {
            let Some(win) = win.upgrade() else { return };
            let g = s.borrow();
            if g.stop || g.swipe {
                let imp = win.imp();
                imp.suppress_select.set(true);
                imp.suppress_restore.set(g.before);
                let w = win.downgrade();
                glib::idle_add_local_once(move || {
                    if let Some(w) = w.upgrade() {
                        w.imp().suppress_select.set(false);
                    }
                });
            }
        });
        imp.op_list.add_controller(tap);

        // a swipe that scrolled the list counts as moving it until the finger lifts; the click gesture above is
        // cancelled by such a swipe, so watch the events
        let watch = gtk::EventControllerLegacy::new();
        watch.set_propagation_phase(gtk::PropagationPhase::Capture);
        let s = state;
        watch.connect_event(move |_, event| {
            let kind = event.event_type();
            if !matches!(kind, gdk::EventType::TouchBegin | gdk::EventType::TouchEnd | gdk::EventType::TouchCancel) {
                return glib::Propagation::Proceed;
            }
            let (x, y) = event.position().unwrap_or((0.0, 0.0));
            let mut g = s.borrow_mut();
            if kind == gdk::EventType::TouchBegin {
                g.at = (x, y);
                g.swipe = false;
            } else {
                // the finger travelled: a swipe, however the list took it (it may be at its end)
                g.swipe = (x - g.at.0).abs() + (y - g.at.1).abs() > TAP_SLOP_PX;
                if g.swipe {
                    g.moved = Some(Instant::now());
                }
            }
            glib::Propagation::Proceed
        });
        scrolled.add_controller(watch);
    }

    fn setup_lock(&self) {
        let imp = self.imp();
        let im = self.parts().input_mode.clone();
        im.lock_exempt.borrow_mut().push(imp.lock_button.clone().upcast());
        let guard = Rc::new(Cell::new(false));
        let (g, m) = (guard.clone(), im.clone());
        imp.lock_button.connect_toggled(move |btn| {
            if !g.get() {
                m.set_locked(btn.is_active());
            }
        });
        let (g, win) = (guard, self.downgrade());
        im.connect_lock_changed(move |locked| {
            let Some(win) = win.upgrade() else { return };
            let btn = &win.imp().lock_button;
            g.set(true);
            btn.set_active(locked);
            g.set(false);
            btn.set_icon_name(if locked { "changes-prevent-symbolic" } else { "changes-allow-symbolic" });
            if locked {
                win.toast(&gettext("Touch locked. Hold the lock button, or click it with the mouse, to unlock."));
            }
        });
        // by touch, unlocking needs a long press; a plain tap on a locked button is ignored
        let tap = gtk::GestureClick::builder().touch_only(true).propagation_phase(gtk::PropagationPhase::Capture).build();
        let m = im.clone();
        tap.connect_pressed(move |g, _, _, _| {
            if m.locked() {
                g.set_state(gtk::EventSequenceState::Claimed);
            }
        });
        imp.lock_button.add_controller(tap);
        let hold = gtk::GestureLongPress::builder().touch_only(true).delay_factor(2.0).build();
        hold.connect_pressed(move |_, _, _| {
            if im.locked() {
                im.set_locked(false);
            }
        });
        imp.lock_button.add_controller(hold);
    }

    // --- actions ----------------------------------------------------------------------------------------

    fn install_actions(&self) {
        let app = self.application().expect("the window's application");
        let add = |name: &str, accels: &[&str], f: fn(&MainWindow)| {
            let action = gio::SimpleAction::new(name, None);
            let win = self.downgrade();
            action.connect_activate(move |_, _| {
                if let Some(win) = win.upgrade() {
                    f(&win)
                }
            });
            self.add_action(&action);
            if !accels.is_empty() {
                app.set_accels_for_action(&format!("win.{name}"), accels);
            }
            action
        };
        add("open", &["<Control>o"], |w| w.open_dialog());
        add("export", &["<Control>e"], |w| w.export_dialog()).set_enabled(false);
        add("fit", &["<Control>0"], |w| w.zoom_key(None));
        add("zoom-in", &["<Control>plus", "<Control>equal"], |w| w.zoom_key(Some(1.25)));
        add("zoom-out", &["<Control>minus"], |w| w.zoom_key(Some(0.8)));
        add("deselect", &["Escape"], |w| w.select_op(None));
        add("save", &["<Control>s"], |w| w.save());
        add("new", &["<Control>n"], |w| w.new_dialog());
        add("preferences", &["<Control>comma"], |w| w.preferences_dialog());
        add("help", &["F1"], |w| HelpDialog::new(None).present(Some(w)));
        add("shortcuts", &["<Control>question"], |w| shortcuts_dialog().present(Some(w)));
        add("close", &["<Control>w"], |w| w.close());
        for kind in MANUALS {
            let action = gio::SimpleAction::new(&format!("manual-{kind}"), None);
            let win = self.downgrade();
            action.connect_activate(move |_, _| {
                if let Some(win) = win.upgrade() {
                    open_manual(&win, kind)
                }
            });
            self.add_action(&action);
        }
        add("part-settings", &[], |w| w.part_settings_dialog());
        add("save-sdg", &[], |w| w.save_sdg_dialog());
        add("clear-messages", &[], |w| {
            w.dismiss_toasts();
        });
        add("find", &["<Control>f"], |w| w.open_find());
        // the pages by keys (POINTER_KEYBOARD.md 9): Alt+1 … Alt+5, Ctrl+PgUp / Ctrl+PgDn
        let page = gio::SimpleAction::new("page", Some(glib::VariantTy::STRING));
        let win = self.downgrade();
        page.connect_activate(move |_, v| {
            if let (Some(win), Some(name)) = (win.upgrade(), v.and_then(|v| v.get::<String>()))
                && win.has_project()
            {
                win.imp().stack.set_visible_child_name(&name);
            }
        });
        self.add_action(&page);
        for (i, name) in PAGES.iter().enumerate() {
            app.set_accels_for_action(&format!("win.page::{name}"), &[&format!("<Alt>{}", i + 1)]);
        }
        // the simulation by keys on the Part page (POINTER_KEYBOARD.md 7); F5, as Space belongs to buttons
        let sim_actions = [
            add("sim-play", &["F5"], |w| w.parts().sim.toggle_play()),
            add("sim-previous-pass", &["<Control>bracketleft"], |w| w.parts().sim.step_pass(-1)),
            add("sim-next-pass", &["<Control>bracketright"], |w| w.parts().sim.step_pass(1)),
            add("sim-first", &["<Alt>Home"], |w| w.parts().sim.goto(Some(0))),
            add("sim-all", &["<Alt>End"], |w| w.parts().sim.goto(None)),
        ];
        let on_part = move |stack: &adw::ViewStack| {
            let part = stack.visible_child_name().as_deref() == Some("part");
            sim_actions.iter().for_each(|a| a.set_enabled(part));
        };
        on_part(&self.imp().stack);
        self.imp().stack.connect_visible_child_name_notify(on_part);
        add("page-previous", &["<Control>Page_Up"], |w| w.step_page(-1));
        add("page-next", &["<Control>Page_Down"], |w| w.step_page(1));
        let show_grid = settings().show_grid;
        let grid = gio::SimpleAction::new_stateful("show-grid", None, &show_grid.to_variant());
        let win = self.downgrade();
        grid.connect_change_state(move |action, value| {
            let (Some(win), Some(on)) = (win.upgrade(), value.and_then(|v| v.get::<bool>())) else { return };
            action.set_state(&on.to_variant());
            win.imp().canvas.state_mut().show_grid = on;
            update_settings(|s| s.show_grid = on);
            win.imp().canvas.queue_draw();
        });
        self.add_action(&grid);
        self.imp().canvas.state_mut().show_grid = show_grid;
        add("save-as", &["<Control><Shift>s"], |w| w.save_dialog(None));
        add("machine-page", &[], |w| w.machine_page());
        add("undo", &["<Control>z"], |w| w.undo());
        add("redo", &["<Control><Shift>z", "<Control>y"], |w| w.redo());
        for (name, f) in [("add-op", MainWindow::add_op as fn(&MainWindow, &str)), ("apply-template", MainWindow::apply_template)] {
            let action = gio::SimpleAction::new(name, Some(glib::VariantTy::STRING));
            let win = self.downgrade();
            action.connect_activate(move |_, v| {
                if let (Some(win), Some(s)) = (win.upgrade(), v.and_then(|v| v.get::<String>())) {
                    f(&win, &s)
                }
            });
            self.add_action(&action);
        }
        add("save-template", &[], |w| w.save_template_dialog());
        add("turn-around", &[], |w| w.turn_around_dialog());
        add("remove-setup", &[], |w| w.remove_setup_dialog()).set_enabled(false);
        let layout = gio::SimpleAction::new_stateful(
            "input-layout",
            Some(glib::VariantTy::STRING),
            &settings().input_layout.to_variant(),
        );
        let win = self.downgrade();
        layout.connect_change_state(move |action, value| {
            let (Some(win), Some(v)) = (win.upgrade(), value.and_then(|v| v.get::<String>())) else { return };
            action.set_state(&v.to_variant());
            win.parts().input_mode.set_preference(Preference::parse(&v));
            update_settings(|s| s.input_layout = v.clone());
        });
        self.add_action(&layout);
    }

    fn step_page(&self, delta: i32) {
        let stack = &self.imp().stack;
        let Some(now) = stack.visible_child_name() else { return };
        let Some(i) = PAGES.iter().position(|p| *p == now.as_str()) else { return };
        if self.has_project() {
            let n = PAGES.len() as i32;
            stack.set_visible_child_name(PAGES[(i as i32 + delta).rem_euclid(n) as usize]);
        }
    }

    fn enable(&self, action: &str, on: bool) {
        if let Some(a) = self.lookup_action(action).and_downcast::<gio::SimpleAction>() {
            a.set_enabled(on);
        }
    }

    // --- files ------------------------------------------------------------------------------------------

    fn file_filter(name: &str, patterns: &[&str]) -> gio::ListStore {
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let f = gtk::FileFilter::new();
        f.set_name(Some(name));
        for p in patterns {
            f.add_pattern(p);
        }
        filters.append(&f);
        filters
    }

    pub fn open_dialog(&self) {
        let dlg = gtk::FileDialog::builder()
            .title(gettext("Open Part"))
            .filters(&Self::file_filter(
                &gettext("Parts and drawings"),
                &["*.alcam", "*.sdg", "*.SDG", "*.dxf", "*.DXF"],
            ))
            .build();
        let win = self.downgrade();
        dlg.open(Some(self), gio::Cancellable::NONE, move |res| {
            if let (Some(win), Some(path)) = (win.upgrade(), chosen(res)) {
                win.load(&path);
            }
        });
    }

    pub fn load(&self, path: &Path) {
        let ext = path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
        if ext == "dxf" {
            self.dxf_dialog(path);
            return;
        }
        let mut note = String::new();
        let loaded = if ext == "sdg" {
            alphacam::io::sdg::load_sdg(path).map(|mut project| {
                note = self.ops_from_sd1(path, &mut project);
                project
            })
        } else {
            Project::load(path)
        };
        let project = match loaded {
            Ok(p) => p,
            Err(e) => {
                self.toast(&tr("Could not open {name}: {error}", &[("name", &file_name(path)), ("error", &e)]));
                return;
            }
        };
        {
            let mut s = self.imp().state.borrow_mut();
            s.path = Some(path.to_path_buf());
            s.import_name = None;
        }
        self.set_project(project);
        if !note.is_empty() {
            self.toast(&note);
        }
    }

    /// ATDXF: a DXF becomes a new part. Lathe drawings usually give Y as a radius; units are the DXF's.
    fn dxf_dialog(&self, path: &Path) {
        let st = settings();
        let b = builder("dxf-import");
        let alert: adw::AlertDialog = object(&b, "dxf_alert");
        alert.set_body(&tr("{name}: the DXF X axis becomes Z. How is Y drawn?", &[("name", &file_name(path))]));
        let combo = |k: &str| object::<adw::ComboRow>(&b, &format!("dxf_{k}_row"));
        let switch = |k: &str| object::<adw::SwitchRow>(&b, &format!("dxf_{k}_row"));
        let (y_row, units, half, rotate, front, axis, datum) =
            (combo("y"), combo("units"), switch("half"), combo("rotate"), combo("front"), combo("axis"), switch("datum"));
        units.set_selected(if st.units == "inch" { 1 } else { 0 });
        let (win, path) = (self.downgrade(), path.to_path_buf());
        alert.connect_response(None, move |_, r| {
            let Some(win) = win.upgrade() else { return };
            if r != "import" {
                return;
            }
            let opts = alphacam::io::dxf::DxfOptions {
                y_is: if y_row.selected() == 0 { "radius" } else { "diameter" }.into(),
                units: if units.selected() == 1 { "inch" } else { "mm" }.into(),
                upper_half: half.is_active(),
                rotate: 90 * rotate.selected() as i32,
                front: if front.selected() != 0 { "left" } else { "right" }.into(),
                axis: ["as drawn", "middle", "lowest"][(axis.selected() as usize).min(2)].into(),
                datum: datum.is_active(),
            };
            let mut res = match alphacam::io::dxf::load_dxf(&path, &opts) {
                Ok(r) => r,
                Err(e) => {
                    win.toast(&tr("Could not open {name}: {error}", &[("name", &file_name(&path)), ("error", &e)]));
                    return;
                }
            };
            res.project.first_tool = settings().first_tool;
            {
                // an import is a new part: Save asks for a name (never overwrites NAME.alcam unasked)
                let mut s = win.imp().state.borrow_mut();
                s.path = None;
                s.import_name = Some(stem(&path));
            }
            let n = res.project.geometry.len();
            win.set_project(res.project);
            if let Some(u) = win.imp().state.borrow_mut().undos.first_mut() {
                u.mark_unsaved();
            }
            win.refresh(false);
            let skipped: Vec<String> = {
                let mut v = res.skipped.clone();
                v.sort();
                v.iter().map(|(k, n)| format!("{k} ×{n}")).collect()
            };
            let mut msg = crate::i18n::fill(
                &ngettext("Imported {n} element from {name}.", "Imported {n} elements from {name}.", n as u32),
                &[("n", &n), ("name", &file_name(&path))],
            );
            if !skipped.is_empty() {
                msg += &format!(" {}", tr("Skipped: {what}.", &[("what", &skipped.join(", "))]));
            }
            win.toast(&msg);
        });
        alert.present(Some(self));
    }

    /// No .SD1 visible next to the drawing (missing, or hidden by a sandbox): offer to pick it.
    fn ask_for_sd1(&self, path: &Path) {
        let toast = adw::Toast::builder()
            .title(glib::markup_escape_text(&tr(
                "{name}: drawing only, no .SD1 found next to it, so no operations.",
                &[("name", &file_name(path))],
            )))
            .button_label(gettext("_Choose .SD1…"))
            .timeout(10)
            .build();
        let (win, path) = (self.downgrade(), path.to_path_buf());
        toast.connect_button_clicked(move |_| {
            if let Some(win) = win.upgrade() {
                win.choose_sd1(&path);
            }
        });
        self.add_toast(toast);
    }

    fn choose_sd1(&self, sdg: &Path) {
        let dlg = gtk::FileDialog::builder()
            .title(tr("Choose the .SD1 for {name}", &[("name", &file_name(sdg))]))
            .filters(&Self::file_filter(&gettext("ALPHA-T program listing (.SD1)"), &["*.SD1", "*.sd1"]))
            .build();
        let win = self.downgrade();
        dlg.open(Some(self), gio::Cancellable::NONE, move |res| {
            let (Some(win), Some(sd1)) = (win.upgrade(), chosen(res)) else { return };
            let Some(project) = win.project() else { return };
            let ops = match alphacam::io::sd1::load_sd1(&sd1, &project) {
                Ok(ops) => ops,
                Err(e) => {
                    win.toast(&tr(
                        "Operations not rebuilt from {name}: {error}",
                        &[("name", &file_name(&sd1)), ("error", &e)],
                    ));
                    return;
                }
            };
            let n = ops.len();
            let label = tr("Operations from {name}", &[("name", &file_name(&sd1))]);
            if win.apply_edit(&label, move |p| {
                p.operations = ops;
                Ok(())
            }) {
                win.refresh(false);
                let mut msg = crate::i18n::fill(
                    &ngettext("Rebuilt {n} operation from {name}.", "Rebuilt {n} operations from {name}.", n as u32),
                    &[("n", &n), ("name", &file_name(&sd1))],
                );
                if let (Some(prg), Some(project)) = (alphacam::io::sd1::sibling(&sd1, ".PRG"), win.project()) {
                    let ok = alphacam::io::sd1::verify(&project, &prg);
                    msg += " ";
                    msg += &tr(
                        if ok {
                            "The program matches ALPHA-T's {name} byte for byte."
                        } else {
                            "The program differs from ALPHA-T's {name}: check before sending."
                        },
                        &[("name", &file_name(&prg))],
                    );
                }
                win.toast(&msg);
            }
        });
    }

    /// ALPHA-T keeps the operations only in the programs it writes: rebuild them from the .SD1 next to the
    /// drawing, and check the result against its .PRG when that is there too.
    fn ops_from_sd1(&self, path: &Path, project: &mut Project) -> String {
        let Some(sd1) = alphacam::io::sd1::sibling(path, ".SD1") else {
            let (win, path) = (self.downgrade(), path.to_path_buf());
            glib::idle_add_local_once(move || {
                if let Some(win) = win.upgrade() {
                    win.ask_for_sd1(&path)
                }
            });
            return String::new();
        };
        match alphacam::io::sd1::load_sd1(&sd1, project) {
            Ok(ops) => project.operations = ops,
            Err(e) => {
                return tr("Operations not rebuilt from {name}: {error}", &[("name", &file_name(&sd1)), ("error", &e)]);
            }
        }
        let n = project.operations.len();
        let check = match alphacam::io::sd1::sibling(path, ".PRG") {
            None => String::new(),
            Some(prg) => {
                let text = if alphacam::io::sd1::verify(project, &prg) {
                    "The program matches ALPHA-T's {name} byte for byte."
                } else {
                    "The program differs from ALPHA-T's {name}: check before sending."
                };
                format!(" {}", tr(text, &[("name", &file_name(&prg))]))
            }
        };
        crate::i18n::fill(
            &ngettext("Rebuilt {n} operation from {name}.", "Rebuilt {n} operations from {name}.", n as u32),
            &[("n", &n), ("name", &file_name(&sd1))],
        ) + &check
    }

    /// A part (file) opens: its first setup is shown; each setup keeps its own undo history.
    pub fn set_project(&self, mut project: Project) {
        let imp = self.imp();
        let setups = std::mem::take(&mut project.setups);
        {
            let mut s = imp.state.borrow_mut();
            s.undos = std::iter::once(project).chain(setups).map(|p| UndoStack::new(p, None, 0)).collect();
        }
        imp.root.set_visible_child_name("main");
        self.update_setups();
        self.activate_setup(0);
        if imp.split.is_collapsed() {
            imp.split.set_show_content(true); // a narrow window opens on the drawing; Back shows the operations
        }
        let win = self.downgrade();
        glib::idle_add_local_once(move || {
            if let Some(win) = win.upgrade() {
                win.focus_drawing();
            }
        });
    }

    /// The whole part as it is saved: setup 1 with its further setups.
    pub fn document(&self) -> Option<Project> {
        let s = self.imp().state.borrow();
        let mut doc = s.undos.first()?.project.clone();
        doc.setups = s.undos[1..].iter().map(|u| u.project.clone()).collect();
        Some(doc)
    }

    fn activate_setup(&self, index: usize) {
        let imp = self.imp();
        imp.state.borrow_mut().setup_index = index;
        self.sync_project();
        imp.readout.set_label(" "); // nothing selected
        imp.editor_reveal.set_reveal_child(false);
        if let Some(p) = imp.parts.get() {
            // element ids repeat between setups: nothing carries over
            p.drawing.finish_chain();
            p.drawing.select_element(None);
        }
        self.refresh(true);
        imp.switching.set(true);
        if self.setup_count() > 1 {
            self.parts().setup_switch.set_active(index as u32);
        }
        imp.switching.set(false);
        self.enable("remove-setup", index > 0);
    }

    /// The Setup 1 / Setup 2 switch above the operations, when the part has more than one setup.
    fn update_setups(&self) {
        let imp = self.imp();
        let switch = &self.parts().setup_switch;
        let n = self.setup_count();
        imp.switching.set(true);
        switch.remove_all();
        for i in 0..n {
            switch.add(adw::Toggle::builder().label(tr("Setup {n}", &[("n", &(i + 1))])).build());
        }
        imp.switching.set(false);
        imp.side_header.set_title_widget(if n > 1 { Some(switch) } else { None });
    }

    fn setup_switched(&self, group: &adw::ToggleGroup) {
        let active = group.active() as usize;
        if !self.imp().switching.get() && active != self.setup_index() && active < self.setup_count() {
            self.parts().drawing.finish_chain();
            self.activate_setup(active);
        }
    }

    /// Any setup changed since the part was saved.
    pub fn modified(&self) -> bool {
        self.imp().state.borrow().undos.iter().any(UndoStack::modified)
    }

    /// The operation editor belongs to the Part and Program pages; in a small window it takes the whole page while
    /// it is open (closing it, or picking a contour, brings the drawing back).
    pub fn layout_editor(&self) {
        let imp = self.imp();
        let Some(parts) = imp.parts.get() else { return };
        let page = imp.stack.visible_child_name();
        let here = matches!(page.as_deref(), Some("part") | Some("program"));
        imp.editor_reveal.set_visible(here);
        let alone = imp.small.get() && here && imp.editor_reveal.reveals_child() && parts.drawing.pick_op().is_none();
        imp.stack.set_visible(!alone);
        imp.editor_reveal.set_hexpand(alone);
    }

    /// The drawing takes the keyboard on the Part page, so ALPHA-T's cursor keys work without a click first.
    fn focus_drawing(&self) {
        let imp = self.imp();
        if self.has_project() && imp.stack.visible_child_name().as_deref() == Some("part") && self.visible_dialog().is_none() {
            imp.canvas.grab_focus();
        }
    }

    fn sync_project(&self) {
        let mut s = self.imp().state.borrow_mut();
        let i = s.setup_index;
        s.project = s.undos.get(i).map(|u| Rc::new(u.project.clone()));
    }

    fn schedule_refresh(&self) {
        let imp = self.imp();
        if imp.refresh_id.borrow().is_some() {
            return;
        }
        let win = self.downgrade();
        let id = glib::idle_add_local_once(move || {
            if let Some(win) = win.upgrade() {
                win.imp().refresh_id.borrow_mut().take();
                win.refresh(false);
            }
        });
        *imp.refresh_id.borrow_mut() = Some(id);
    }

    fn display_name(&self) -> String {
        let s = self.imp().state.borrow();
        let mut name = s.path.as_deref().map(file_name).unwrap_or_else(|| gettext("Untitled"));
        if s.undos.len() > 1 {
            name += &format!(" – {}", tr("Setup {n}", &[("n", &(s.setup_index + 1))]));
        }
        name
    }

    /// Re-post and redraw after any change; keeps the view and the selected operation.
    pub fn refresh(&self, fit: bool) {
        let imp = self.imp();
        if let Some(id) = imp.refresh_id.borrow_mut().take() {
            id.remove(); // this refresh covers the one queued (no second post, no editor rebuild)
        }
        self.sync_project();
        let Some(project) = self.project() else { return };
        let selected = self.selected_index();
        let library = self.library();
        let pv = Rc::new(preview(&project, None, Some(&library)).unwrap_or_else(|e| Preview {
            error: Some(e.to_string()),
            ..Default::default()
        }));
        // the safety check before export: warnings, never a block (alphacam/safety.rs)
        let findings =
            if pv.data.is_some() { alphacam::safety::check(&project, &pv, Some(&library)) } else { vec![] };
        {
            let mut s = imp.state.borrow_mut();
            s.preview = Some(pv.clone());
            s.findings = findings.clone();
        }
        let name = self.display_name();
        let dirty = if self.modified() { "• " } else { "" };
        self.set_title(Some(&format!("{dirty}{name} – AlphaLink CAM")));
        imp.content_page.set_title(&format!("{dirty}{name}"));
        self.fill_ops();
        self.fill_program();
        let parts = self.parts();
        parts.sheet.refresh();
        if fit {
            imp.canvas.set_project(project.clone(), pv.clone());
        } else {
            imp.canvas.update(project.clone(), pv.clone());
        }
        parts.sim.reset();
        let banner = &imp.banner;
        let where_ = |op: Option<usize>| op.map(|n| tr("Operation {n}: ", &[("n", &(n + 1))])).unwrap_or_default();
        if let Some(error) = &pv.error {
            banner.set_title(&tr(
                "{where}{error}. Export is disabled.",
                &[("where", &where_(pv.error_op)), ("error", error)],
            ));
            banner.set_button_label(None);
            banner.set_revealed(true);
        } else if let Some(f) = findings.first() {
            let more = findings.len() - 1;
            let title = if more > 0 {
                crate::i18n::fill(
                    &ngettext(
                        "Safety check: {where}{what} (and {n} more)",
                        "Safety check: {where}{what} (and {n} more)",
                        more as u32,
                    ),
                    &[("where", &where_(f.op_index)), ("what", &f.message), ("n", &more)],
                )
            } else {
                tr("Safety check: {where}{what}", &[("where", &where_(f.op_index)), ("what", &f.message)])
            };
            banner.set_title(&title);
            banner.set_button_label(Some(&gettext("_Show")));
            banner.set_revealed(true);
        } else {
            banner.set_revealed(false);
        }
        imp.canvas.overlay(|o| o.alerts = findings.iter().map(|f| (f.x, f.z)).collect());
        self.enable("export", pv.data.is_some());
        let (can_undo, can_redo) = {
            let s = imp.state.borrow();
            s.undos.get(s.setup_index).map_or((false, false), |u| (u.can_undo(), u.can_redo()))
        };
        self.enable("undo", can_undo);
        self.enable("redo", can_redo);
        let mut tools: Vec<i64> = pv
            .program
            .as_ref()
            .map(|prog| {
                prog.blocks.iter().filter(|b| b.op_kind() == Some(OP_KIND_TOOL_CHANGE)).filter_map(|b| b.tool()).collect()
            })
            .unwrap_or_default();
        tools.sort();
        tools.dedup();
        let tools: Vec<String> = tools.iter().map(|t| format!("T{t}")).collect();
        parts.machine.set_program(
            pv.data.clone(),
            &tr(
                "Program {number} · {name} · tools {tools}",
                &[("number", &project.program_number), ("name", &name), ("tools", &tools.join(", "))],
            ),
        );
        match selected {
            Some(i) if i < project.operations.len() => {
                let rebuild = !imp.editor_edit.get();
                self.select_op_with(Some(i), rebuild)
            }
            _ => self.select_op(None),
        }
    }

    pub fn tool_name(&self, number: i32) -> String {
        self.project().map(|p| crate::report::tool_name(&p, number, Some(&self.library()))).unwrap_or_default()
    }

    // --- editing ----------------------------------------------------------------------------------------

    /// One undoable change to the shown setup; a failure is shown as a message. The window refreshes afterwards.
    pub fn apply_edit(
        &self,
        label: &str,
        change: impl FnOnce(&mut Project) -> alphacam::error::Result<()>,
    ) -> bool {
        let result = {
            let mut s = self.imp().state.borrow_mut();
            let i = s.setup_index;
            match s.undos.get_mut(i) {
                Some(u) => u.apply(label, change),
                None => return false,
            }
        };
        match result {
            Ok(()) => {
                self.sync_project();
                self.schedule_refresh();
                true
            }
            Err(e) => {
                self.toast(&format!("{label}: {e}"));
                false
            }
        }
    }

    /// Edits from the editor's own rows must not rebuild the editor (that would steal focus mid-typing).
    fn apply_edit_from_editor(&self, label: &str, change: crate::op_editor::ApplyFn) {
        let imp = self.imp();
        imp.editor_edit.set(true);
        self.apply_edit(label, change);
        self.refresh(false);
        imp.editor_edit.set(false);
    }

    fn history(&self, redo: bool) {
        let label = {
            let mut s = self.imp().state.borrow_mut();
            let i = s.setup_index;
            s.undos.get_mut(i).and_then(|u| if redo { u.redo() } else { u.undo() })
        };
        if let Some(label) = label {
            self.sync_project();
            self.schedule_refresh();
            self.parts().drawing.after_history();
            self.toast(&tr(if redo { "Redid {action}" } else { "Undid {action}" }, &[("action", &label)]));
        }
    }

    pub fn undo(&self) {
        self.history(false);
    }

    pub fn redo(&self) {
        self.history(true);
    }

    pub fn add_op(&self, kind: &str) {
        let Some(project) = self.project() else { return };
        let at = self.selected_index().map_or(project.operations.len(), |i| i + 1);
        let mut op = default_op(kind, &project, at);
        let library = self.library();
        let title = op_title(&op);
        let label = tr("Add {what}", &[("what", &title)]);
        if let Some(tool) = tool_for(kind, &project, at, Some(&library), true) {
            // the tool in use does not suit this operation: change to one that does, first
            let mut tc = default_op("tool_change", &project, at);
            set_tool(&mut tc, tool);
            set_tool(&mut op, tool);
            if let Some(t) = alphacam::checks::tool(&project, tool, Some(&library)) {
                let inch = project.units == "inch";
                let conv = |v: f64| if inch { alphacam::py::round(v / 25.4, 4) } else { v };
                set_feed(&mut op, conv(t.default_feed));
                if let Operation::Groove(g) = &mut op
                    && t.width > 0.0
                {
                    g.tip_width = conv(t.width);
                }
            }
            if self.apply_edit(&label, move |p| {
                p.operations.insert(at, tc);
                p.operations.insert(at + 1, op);
                Ok(())
            }) {
                self.refresh(false);
                self.select_op(Some(at + 1));
                self.toast(&tr(
                    "Tool change to T{n} added: {name}",
                    &[("n", &tool), ("name", &self.tool_name(tool))],
                ));
            }
            return;
        }
        if self.apply_edit(&label, move |p| {
            p.operations.insert(at, op);
            Ok(())
        }) {
            self.refresh(false);
            self.select_op(Some(at));
        }
    }

    /// Add a `kind` operation at `at` inside an edit (project is the edit's copy), with a tool change first when the
    /// tool in use does not suit it; returns the operation's index.
    pub fn insert_op(&self, project: &mut Project, mut at: usize, kind: &str, values: impl FnOnce(&mut Operation)) -> usize {
        let mut op = default_op(kind, project, at);
        values(&mut op);
        if let Some(tool) = tool_for(kind, project, at, Some(&self.library()), true) {
            let mut tc = default_op("tool_change", project, at);
            set_tool(&mut tc, tool);
            set_tool(&mut op, tool);
            project.operations.insert(at, tc);
            at += 1;
        }
        project.operations.insert(at, op);
        at
    }

    fn duplicate_selected(&self) {
        let (Some(i), Some(project)) = (self.selected_index(), self.project()) else { return };
        let op = project.operations[i].clone();
        if self.apply_edit(&tr("Duplicate {what}", &[("what", &op_title(&op))]), move |p| {
            p.operations.insert(i + 1, op);
            Ok(())
        }) {
            self.refresh(false);
            self.select_op(Some(i + 1));
        }
    }

    fn delete_selected(&self) {
        let (Some(i), Some(project)) = (self.selected_index(), self.project()) else { return };
        let title = op_title(&project.operations[i]);
        if self.apply_edit(&tr("Delete {what}", &[("what", &title)]), move |p| {
            p.operations.remove(i);
            Ok(())
        }) {
            self.refresh(false);
            let n = self.project().map_or(0, |p| p.operations.len());
            self.select_op(if n > 0 { Some(i.min(n - 1)) } else { None });
            let toast = adw::Toast::builder()
                .title(glib::markup_escape_text(&tr("Deleted {what}", &[("what", &title)])))
                .button_label(gettext("_Undo"))
                .action_name("win.undo")
                .timeout(6)
                .build();
            self.add_toast(toast);
        }
    }

    fn move_selected(&self, delta: i32) {
        let (Some(i), Some(project)) = (self.selected_index(), self.project()) else { return };
        let j = i as i64 + delta as i64;
        if j < 0 || j as usize >= project.operations.len() {
            return;
        }
        let j = j as usize;
        if self.apply_edit(&gettext("Move operation"), move |p| {
            p.operations.swap(i, j);
            Ok(())
        }) {
            self.refresh(false);
            self.select_op(Some(j));
        }
    }

    // --- job templates (workflow improvements, step 4) -------------------------------------------------

    fn fill_templates(&self) {
        let section = &self.imp().template_section;
        section.remove_all();
        for (i, t) in templates::builtin().iter().enumerate() {
            let item = gio::MenuItem::new(Some(&templates::name(t)), None);
            item.set_action_and_target_value(Some("win.apply-template"), Some(&format!("builtin:{i}").to_variant()));
            section.append_item(&item);
        }
        for (path, t) in templates::user_templates() {
            let name = Some(templates::name(&t)).filter(|n| !n.is_empty()).unwrap_or_else(|| stem(&path));
            let item = gio::MenuItem::new(Some(&name), None);
            item.set_action_and_target_value(Some("win.apply-template"), Some(&path.to_string_lossy().to_variant()));
            section.append_item(&item);
        }
    }

    pub fn apply_template(&self, target: &str) {
        if !self.has_project() {
            return;
        }
        let template = match target.strip_prefix("builtin:") {
            Some(i) => i.parse::<usize>().ok().and_then(|i| templates::builtin().into_iter().nth(i)),
            None => templates::user_templates().into_iter().find(|(p, _)| p.to_string_lossy() == target).map(|t| t.1),
        };
        let Some(template) = template else {
            self.toast(&gettext("The template is gone or damaged"));
            self.fill_templates();
            return;
        };
        let name = templates::name(&template);
        let added = Rc::new(Cell::new(0usize));
        let (a, library) = (added.clone(), self.library());
        if self.apply_edit(&tr("Template {name}", &[("name", &name)]), move |p| {
            a.set(templates::apply(p, &template, Some(&library))?);
            Ok(())
        }) {
            self.refresh(false);
            let n = added.get();
            self.toast(&crate::i18n::fill(
                &ngettext("Added {n} operation from {name}", "Added {n} operations from {name}", n as u32),
                &[("n", &n), ("name", &name)],
            ));
        }
    }

    fn save_template_dialog(&self) {
        let Some(project) = self.project() else { return };
        if project.operations.is_empty() {
            self.toast(&gettext("Add operations first: a template keeps the operations, not the drawing"));
            return;
        }
        let b = builder("save-template");
        let alert: adw::AlertDialog = object(&b, "template_alert");
        let name: adw::EntryRow = object(&b, "template_name_row");
        name.set_text(&self.path().map(|p| stem(&p)).unwrap_or_default());
        let win = self.downgrade();
        alert.connect_response(None, move |_, r| {
            let Some(win) = win.upgrade() else { return };
            if r != "save" {
                return;
            }
            let label = Some(name.text().trim().to_string()).filter(|s| !s.is_empty()).unwrap_or_else(|| gettext("Template"));
            match templates::save_user_template(&templates::from_part(&project, &label)) {
                Ok(_) => {
                    win.fill_templates();
                    win.toast(&tr("Template “{name}” saved; it is in the Add Operation menu", &[("name", &label)]));
                }
                Err(e) => win.toast(&tr("Could not save {name}: {error}", &[("name", &label), ("error", &e)])),
            }
        });
        alert.present(Some(self));
    }

    // --- second setup (workflow improvements, step 6) --------------------------------------------------

    /// Setup 2 of the part, in the same file: the first setup's drawing turned end for end.
    fn turn_around_dialog(&self) {
        let Some(first) = self.imp().state.borrow().undos.first().map(|u| u.project.clone()) else { return };
        let inch = first.units == "inch";
        let b = builder("turn-around");
        let alert: adw::AlertDialog = object(&b, "turn_alert");
        let length: adw::SpinRow = object(&b, "turn_length_row");
        let digits = if inch { 4 } else { 3 };
        length.set_digits(digits);
        let part_length = alphacam::setups::part_length(&first);
        length.set_value(if part_length != 0.0 { part_length } else { first.billet.length });
        fit_spin(&length, 0.001, 5000.0, digits); // room for "50.000", not "0.000" cut short
        let prog: adw::SpinRow = object(&b, "turn_program_row");
        prog.set_value((first.program_number + 1).min(9999) as f64);
        let replacing = self.setup_count() > 1;
        let mut body = gettext(
            "Setup 2 machines the other end: setup 1's drawing turned end for end, its back face on Z0, without \
             operations. Both setups are kept in this part's file.",
        );
        if replacing {
            body += &format!("\n\n{}", gettext("This replaces the current setup 2 and its operations."));
        }
        alert.set_body(&body);
        alert.add_response("turn", &gettext(if replacing { "_Replace Setup 2" } else { "_Turn Around" }));
        alert.set_response_appearance(
            "turn",
            if replacing { adw::ResponseAppearance::Destructive } else { adw::ResponseAppearance::Suggested },
        );
        alert.set_default_response(Some(if replacing { "cancel" } else { "turn" }));
        let win = self.downgrade();
        alert.connect_response(None, move |_, r| {
            let Some(win) = win.upgrade() else { return };
            if r != "turn" {
                return;
            }
            let new = match alphacam::setups::turn_around(&first, Some(length.value()), Some(prog.value() as i64)) {
                Ok(p) => p,
                Err(e) => {
                    win.toast(&e.to_string());
                    return;
                }
            };
            let number = new.program_number;
            {
                let mut s = win.imp().state.borrow_mut();
                s.undos.truncate(1);
                let mut stack = UndoStack::new(new, None, 0);
                stack.mark_unsaved();
                s.undos.push(stack);
            }
            win.update_setups();
            win.activate_setup(1);
            win.toast(&tr("Setup 2, program {n}: add its operations, then save the part.", &[("n", &number)]));
        });
        alert.present(Some(self));
    }

    fn remove_setup_dialog(&self) {
        let i = self.setup_index();
        if i == 0 {
            return;
        }
        let alert: adw::AlertDialog = object(&builder("remove-setup"), "remove_setup_alert");
        alert.set_heading(Some(&tr("Remove Setup {n}?", &[("n", &(i + 1))])));
        let win = self.downgrade();
        alert.connect_response(None, move |_, r| {
            let Some(win) = win.upgrade() else { return };
            if r != "remove" {
                return;
            }
            {
                let mut s = win.imp().state.borrow_mut();
                let i = s.setup_index;
                s.undos.remove(i);
                s.undos[0].mark_unsaved(); // the file still has it until saved
            }
            win.update_setups();
            win.activate_setup(0);
        });
        alert.present(Some(self));
    }

    /// ALPHA-T's ATSETUP: defaults for new parts and the language (ui/preferences.blp). The serial settings live
    /// on the Machine page and are remembered there.
    fn preferences_dialog(&self) {
        let st = settings();
        let b = builder("preferences");
        let units: adw::ComboRow = object(&b, "pref_units_row");
        units.set_selected(if st.units == "inch" { 1 } else { 0 });
        units.connect_selected_notify(|r| {
            let inch = r.selected() == 1;
            update_settings(|s| s.units = if inch { "inch" } else { "mm" }.into());
        });
        let inch = st.units == "inch";
        let diameter: adw::SpinRow = object(&b, "pref_diameter_row");
        let length: adw::SpinRow = object(&b, "pref_length_row");
        let first_tool: adw::SpinRow = object(&b, "pref_first_tool_row");
        for (row, value) in [(&diameter, st.billet_diameter), (&length, st.billet_length)] {
            row.set_digits(if inch { 4 } else { 1 });
            row.set_value(value);
        }
        first_tool.set_digits(0);
        first_tool.set_value(st.first_tool as f64);
        diameter.connect_value_notify(|r| {
            let v = r.value();
            update_settings(|s| s.billet_diameter = v)
        });
        length.connect_value_notify(|r| {
            let v = r.value();
            update_settings(|s| s.billet_length = v)
        });
        first_tool.connect_value_notify(|r| {
            let v = r.value() as i32;
            update_settings(|s| s.first_tool = v)
        });
        units_follow(&units, &[diameter, length]);
        let sdg: adw::SwitchRow = object(&b, "pref_sdg_row");
        sdg.set_active(st.export_sdg);
        sdg.connect_active_notify(|r| {
            let on = r.is_active();
            update_settings(|s| s.export_sdg = on)
        });
        type Field = fn(&mut crate::settings::Settings) -> &mut f64;
        let rows: [(&str, Field); 3] = [
            ("pref_spindle_row", |s| &mut s.spindle_max),
            ("pref_rapid_row", |s| &mut s.rapid_rate),
            ("pref_tool_change_row", |s| &mut s.tool_change_s),
        ];
        for (id, field) in rows {
            let row: adw::SpinRow = object(&b, id);
            row.set_value(*field(&mut settings()));
            let win = self.downgrade();
            row.connect_value_notify(move |r| {
                let v = r.value();
                update_settings(|s| *field(s) = v);
                if let Some(win) = win.upgrade() {
                    win.parts().sheet.refresh();
                }
            });
        }
        const CODES: [&str; 3] = ["", "en", "uk"];
        let language: adw::ComboRow = object(&b, "pref_language_row");
        language.set_selected(CODES.iter().position(|c| *c == st.language).unwrap_or(0) as u32);
        language.connect_selected_notify(|r| {
            let code = CODES[(r.selected() as usize).min(2)];
            update_settings(|s| s.language = code.into());
        });
        object::<adw::Dialog>(&b, "preferences").present(Some(self));
    }

    /// New part: units and billet; the drawing starts empty (draw with the Line tool or type X,Z).
    fn new_dialog(&self) {
        let st = settings();
        let b = builder("new-part");
        let alert: adw::AlertDialog = object(&b, "new_part_alert");
        let billet: BilletGroup = object(&b, "new_part_billet");
        billet.set_billet(
            &Billet { diameter: st.billet_diameter, length: st.billet_length, ..Default::default() },
            st.units == "inch",
            true,
        );
        let win = self.downgrade();
        alert.connect_response(None, move |_, r| {
            let Some(win) = win.upgrade() else { return };
            if r != "create" {
                return;
            }
            let inch = billet.inch();
            let size = billet.billet();
            let units = if inch { "inch" } else { "mm" };
            update_settings(|s| {
                s.units = units.into();
                s.billet_diameter = size.diameter;
                s.billet_length = size.length;
            });
            let k = if inch { 25.4 } else { 1.0 };
            let project = Project {
                units: units.into(),
                billet: size,
                tool_change: Point::new(alphacam::py::round(100.0 / k, 4), alphacam::py::round(50.0 / k, 4)),
                first_tool: settings().first_tool,
                ..Default::default()
            };
            {
                let mut s = win.imp().state.borrow_mut();
                s.path = None;
                s.import_name = None;
            }
            win.set_project(project);
            win.parts().drawing.set_tool("line");
            win.toast(&gettext("Draw the profile: tap points, or type X,Z and Enter"));
        });
        alert.present(Some(self));
    }

    /// Billet, bore, chuck jaws and tailstock of the open part (one undoable change).
    fn part_settings_dialog(&self) {
        let Some(project) = self.project() else { return };
        let b = builder("part-settings");
        let dlg: adw::Dialog = object(&b, "part_settings");
        let group: BilletGroup = object(&b, "part_settings_billet");
        group.set_billet(&project.billet, project.units == "inch", false);
        object::<gtk::Label>(&b, "part_settings_units_label")
            .set_label(&gettext(if project.units == "mm" { "mm" } else { "inch" }));
        let number: adw::SpinRow = object(&b, "part_settings_number_row");
        number.set_value(project.program_number as f64);
        const UNIT_KEYS: [Option<&str>; 3] = [None, Some("mm"), Some("inch")];
        let out_units: adw::ComboRow = object(&b, "part_settings_output_row");
        let current = project.output_units.as_deref().filter(|u| *u != project.units);
        out_units.set_selected(UNIT_KEYS.iter().position(|k| *k == current).unwrap_or(0) as u32);
        let d = dlg.clone();
        object::<gtk::Button>(&b, "part_settings_cancel").connect_clicked(move |_| {
            d.close();
        });
        let (win, d) = (self.downgrade(), dlg.clone());
        object::<gtk::Button>(&b, "part_settings_apply").connect_clicked(move |_| {
            let Some(win) = win.upgrade() else { return };
            let mut billet = group.billet();
            billet.face_allowance = project.billet.face_allowance;
            let n = number.value() as i64;
            let out = UNIT_KEYS[(out_units.selected() as usize).min(2)];
            d.close();
            if win.apply_edit(&gettext("Part settings"), move |p| {
                p.billet = billet;
                p.program_number = n;
                p.output_units = out.filter(|u| *u != p.units).map(str::to_string);
                Ok(())
            }) {
                win.refresh(true);
            }
        });
        dlg.present(Some(self));
    }

    /// The Machine page without a part (e.g. to back up Manual Ops first): an empty new part behind it.
    fn machine_page(&self) {
        if !self.has_project() {
            let st = settings();
            self.set_project(Project {
                units: st.units.clone(),
                billet: Billet { diameter: st.billet_diameter, length: st.billet_length, ..Default::default() },
                first_tool: st.first_tool,
                ..Default::default()
            });
            if let Some(u) = self.imp().state.borrow_mut().undos.first_mut() {
                u.mark_saved();
            }
        }
        self.imp().stack.set_visible_child_name("machine");
    }

    // --- saving -----------------------------------------------------------------------------------------

    fn default_stem(&self, fallback: &str) -> String {
        self.path().map(|p| stem(&p)).or_else(|| self.import_name()).unwrap_or_else(|| fallback.into())
    }

    /// Write the drawing as an ALPHA-T .SDG (+ .SDT), so DOS ALPHA-T can open it. Operations are not part of an
    /// ALPHA-T drawing; Export PRG writes them to the .SD1.
    fn save_sdg_dialog(&self) {
        if !self.has_project() {
            return;
        }
        let name: String = self.default_stem("PART").to_uppercase().chars().take(8).collect();
        let dlg = gtk::FileDialog::builder()
            .title(gettext("Save as ALPHA-T Drawing"))
            .initial_name(format!("{name}.SDG"))
            .build();
        let win = self.downgrade();
        dlg.save(Some(self), gio::Cancellable::NONE, move |res| {
            let (Some(win), Some(path)) = (win.upgrade(), chosen(res)) else { return };
            let Some(project) = win.project() else { return };
            match alphacam::io::sdg_write::save_sdg(&project, &path) {
                Err(e) => win.toast(&tr("Saving the drawing failed: {error}", &[("error", &e)])),
                Ok(files) => {
                    let names: Vec<String> = files.iter().map(|f| file_name(f)).collect();
                    let mut note = tr("Saved {files}.", &[("files", &names.join(", "))]);
                    if files.first().is_some_and(|f| stem(f).chars().count() > 8) {
                        note += &format!(" {}", gettext("ALPHA-T needs a name of at most 8 characters."));
                    }
                    win.toast(&note);
                }
            }
        });
    }

    pub fn save(&self) {
        if !self.has_project() {
            return;
        }
        match self.path().filter(|p| is_alcam(p)) {
            Some(p) => {
                self.write(&p);
            }
            None => self.save_dialog(None),
        }
    }

    pub fn save_dialog(&self, then: Option<Box<dyn FnOnce()>>) {
        if !self.has_project() {
            return;
        }
        let dlg = gtk::FileDialog::builder()
            .title(gettext("Save Part"))
            .initial_name(format!("{}.alcam", self.default_stem("part")))
            .build();
        let win = self.downgrade();
        dlg.save(Some(self), gio::Cancellable::NONE, move |res| {
            let (Some(win), Some(mut path)) = (win.upgrade(), chosen(res)) else { return };
            if !is_alcam(&path) {
                path.set_extension("alcam");
            }
            if win.write(&path)
                && let Some(then) = then
            {
                then();
            }
        });
    }

    fn write(&self, path: &Path) -> bool {
        let Some(doc) = self.document() else { return false };
        if let Err(e) = doc.save(path) {
            // every setup, in one file
            self.toast(&tr("Could not save: {error}", &[("error", &e)]));
            return false;
        }
        {
            let mut s = self.imp().state.borrow_mut();
            s.path = Some(path.to_path_buf());
            s.undos.iter_mut().for_each(UndoStack::mark_saved);
        }
        self.refresh(false);
        self.toast(&tr("Saved {name}", &[("name", &file_name(path))]));
        true
    }

    fn export_dialog(&self) {
        if self.preview().is_none_or(|pv| pv.data.is_none()) {
            return;
        }
        let mut base = alphat_name(&self.default_stem("PART"));
        let index = self.setup_index();
        if index > 0 {
            // setup 2 of NAME: NAME2 (ALPHA-T names have 8 characters at most)
            base = format!("{}{}", base.chars().take(7).collect::<String>(), index + 1);
        }
        let dlg = gtk::FileDialog::builder()
            .title(gettext("Export Alpha-T Program"))
            .initial_name(format!("{base}.PRG"))
            .build();
        let last = settings().export_dir;
        if !last.is_empty() && Path::new(&last).is_dir() {
            dlg.set_initial_folder(Some(&gio::File::for_path(&last))); // back to the same USB stick or folder
        }
        let win = self.downgrade();
        dlg.save(Some(self), gio::Cancellable::NONE, move |res| {
            if let (Some(win), Some(prg)) = (win.upgrade(), chosen(res)) {
                win.export_to(&prg);
            }
        });
    }

    pub fn export_to(&self, prg: &Path) {
        let (Some(pv), Some(project)) = (self.preview(), self.project()) else { return };
        let Some(data) = &pv.data else { return };
        let parent = prg.parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
        if !parent.starts_with("/run/user/") {
            // a portal's document folder is not a place to go back to
            update_settings(|s| s.export_dir = parent);
        }
        // ALPHA-T writes the .SD1 listing next to the .PRG; so do we (same name, same case of the extension)
        let lower = prg.extension().is_some_and(|e| e.to_string_lossy().chars().all(|c| !c.is_ascii_uppercase()));
        let ext = |e: &str| if lower { e.to_lowercase() } else { e.to_string() };
        let listing = pv.sd1.as_ref().map(|_| prg.with_extension(ext("SD1")));
        let result = (|| -> alphacam::error::Result<Vec<PathBuf>> {
            std::fs::write(prg, data)?;
            if let (Some(listing), Some(sd1)) = (&listing, &pv.sd1) {
                std::fs::write(listing, sd1)?;
            }
            if settings().export_sdg {
                // Preferences: also the drawing, as ALPHA-T saves it with the program
                return alphacam::io::sdg_write::save_sdg(&project, prg.with_extension(ext("SDG")));
            }
            Ok(vec![])
        })();
        let drawing = match result {
            Ok(d) => d,
            Err(e) => {
                self.toast(&tr("Export failed: {error}", &[("error", &e)]));
                return;
            }
        };
        let (name, size) = (file_name(prg), data.len());
        let listing_name = listing.as_deref().map(file_name);
        if let Some(d) = drawing.first() {
            self.toast(&tr(
                "Exported {name} ({size} bytes), {listing} and the drawing {drawing}",
                &[
                    ("name", &name),
                    ("size", &size),
                    ("listing", &listing_name.unwrap_or_else(|| "-".into())),
                    ("drawing", &file_name(d)),
                ],
            ));
        } else if let Some(listing) = listing_name {
            self.toast(&tr(
                "Exported {name} ({size} bytes) and {listing}",
                &[("name", &name), ("size", &size), ("listing", &listing)],
            ));
        } else {
            self.toast(&tr("Exported {name} ({size} bytes)", &[("name", &name), ("size", &size)]));
        }
    }

    // --- messages ---------------------------------------------------------------------------------------

    pub fn toast(&self, text: &str) {
        let timeout = if text.chars().count() < 80 { 4 } else { 8 };
        self.add_toast(adw::Toast::builder().title(glib::markup_escape_text(text)).timeout(timeout).build());
    }

    /// Clear every message (Esc, or main menu ▸ Clear Messages); true when there was one.
    pub fn dismiss_toasts(&self) -> bool {
        let imp = self.imp();
        let shown = imp.toast_count.get() > 0;
        imp.part_toasts.dismiss_all();
        imp.toasts.dismiss_all();
        imp.toast_count.set(0);
        shown
    }

    pub fn add_toast(&self, toast: adw::Toast) {
        let imp = self.imp();
        let on_part = imp.root.visible_child_name().as_deref() == Some("main")
            && imp.stack.visible_child_name().as_deref() == Some("part");
        imp.toast_count.set(imp.toast_count.get() + 1);
        let win = self.downgrade();
        toast.connect_dismissed(move |_| {
            if let Some(win) = win.upgrade() {
                let c = &win.imp().toast_count;
                c.set(c.get().saturating_sub(1));
            }
        });
        if on_part { imp.part_toasts.add_toast(toast) } else { imp.toasts.add_toast(toast) }
    }

    // --- operations list --------------------------------------------------------------------------------

    fn fill_ops(&self) {
        let imp = self.imp();
        let (Some(project), Some(pv)) = (self.project(), self.preview()) else { return };
        imp.select_guard.set(true);
        imp.op_list.remove_all();
        imp.select_guard.set(false);
        let mut warnings = alphacam::checks::tool_warnings(&project, Some(&self.library()));
        for f in self.findings() {
            if let Some(i) = f.op_index {
                warnings.entry(i).or_default().push(f.message.clone());
            }
        }
        for (i, op) in project.operations.iter().enumerate() {
            let row = adw::ActionRow::builder()
                .title(glib::markup_escape_text(&op_title(op)))
                .subtitle(glib::markup_escape_text(&op_summary(op, pv.for_op(i))))
                .activatable(true)
                .build();
            let badge = gtk::Label::new(Some(&(i + 1).to_string()));
            badge.add_css_class("op-badge");
            badge.add_css_class("dim-label");
            row.add_prefix(&badge);
            if pv.error_op.is_some_and(|e| i >= e) {
                let icon = gtk::Image::from_icon_name("dialog-warning-symbolic");
                icon.set_tooltip_text(pv.error.as_deref());
                icon.add_css_class("warning");
                row.add_suffix(&icon);
                if pv.error_op == Some(i) {
                    row.set_subtitle(&glib::markup_escape_text(pv.error.as_deref().unwrap_or("")));
                }
            } else if let Some(w) = warnings.get(&i) {
                // a tool that does not look right for the job: a warning, the program is still written
                let icon = gtk::Image::from_icon_name("dialog-warning-symbolic");
                icon.set_tooltip_text(Some(&w.join("\n")));
                icon.add_css_class("warning");
                icon.update_property(&[gtk::accessible::Property::Label(&w.join("; "))]);
                row.add_suffix(&icon);
            }
            row.add_suffix(&self.drag_handle(&row, i, &op_title(op)));
            imp.op_list.append(&row);
        }
    }

    /// Reorder by dragging the handle only (TOUCH.md 7.1): a drag anywhere else in the list scrolls it. The row is
    /// the drop target; the operation lands in its place.
    fn drag_handle(&self, row: &adw::ActionRow, i: usize, title: &str) -> gtk::Image {
        let handle = gtk::Image::from_icon_name("list-drag-handle-symbolic");
        handle.set_tooltip_text(Some(&gettext("Drag to Reorder")));
        handle.add_css_class("dim-label");
        handle.add_css_class("drag-handle");
        let src = gtk::DragSource::new();
        src.set_actions(gdk::DragAction::MOVE);
        src.connect_prepare(move |_, _, _| Some(gdk::ContentProvider::for_value(&(i as i32).to_value())));
        let title = title.to_string();
        src.connect_drag_begin(move |_, drag| {
            let label = gtk::Label::new(Some(&title));
            label.add_css_class("drag-label");
            label.add_css_class("card");
            gtk::DragIcon::for_drag(drag).set_child(Some(&label));
        });
        handle.add_controller(src);
        let drop = gtk::DropTarget::new(i32::static_type(), gdk::DragAction::MOVE);
        let win = self.downgrade();
        drop.connect_drop(move |_, value, _, _| {
            match (win.upgrade(), value.get::<i32>()) {
                (Some(win), Ok(from)) => win.move_op(from as usize, i),
                _ => false,
            }
        });
        row.add_controller(drop);
        handle
    }

    /// Operation i to place j (dragging its handle); the moved operation stays selected.
    pub fn move_op(&self, i: usize, j: usize) -> bool {
        let n = self.project().map_or(0, |p| p.operations.len());
        if i == j || i >= n || j >= n {
            return false;
        }
        if self.apply_edit(&gettext("Move operation"), move |p| {
            let op = p.operations.remove(i);
            p.operations.insert(j, op);
            Ok(())
        }) {
            self.refresh(false);
            self.select_op(Some(j));
        }
        true
    }

    pub fn selected_index(&self) -> Option<usize> {
        self.imp().op_list.selected_row().map(|r| r.index() as usize)
    }

    pub fn select_op(&self, index: Option<usize>) {
        self.select_op_with(index, true);
    }

    fn select_op_with(&self, index: Option<usize>, rebuild_editor: bool) {
        let imp = self.imp();
        let Some(index) = index else {
            imp.op_list.unselect_all();
            imp.canvas.select(None);
            if let Some(p) = imp.parts.get() {
                p.sim.select_op(None);
            }
            imp.editor_reveal.set_reveal_child(false);
            return;
        };
        let Some(row) = imp.op_list.row_at_index(index as i32) else { return };
        if !rebuild_editor {
            imp.select_guard.set(true);
            imp.op_list.select_row(Some(&row));
            imp.select_guard.set(false);
            imp.canvas.select(Some(index));
            self.parts().sim.select_op(Some(index));
            self.highlight_program(Some(index));
            return;
        }
        if imp.op_list.selected_row().as_ref() == Some(&row) {
            self.row_selected(&imp.op_list, Some(&row));
        } else {
            imp.op_list.select_row(Some(&row));
        }
    }

    // --- program view -----------------------------------------------------------------------------------

    fn fill_program(&self) {
        let imp = self.imp();
        let (Some(pv), Some(project)) = (self.preview(), self.project()) else { return };
        let buf = imp.program_view.buffer();
        let text = match (&pv.data, &pv.program) {
            (Some(data), _) => String::from_utf8_lossy(data).replace("\r\n", "\n"),
            (None, Some(prog)) => String::from_utf8_lossy(&prog.to_bytes()).replace("\r\n", "\n"),
            _ => String::new(),
        };
        buf.set_text(text.trim_end_matches('\n'));
        buf.place_cursor(&buf.start_iter()); // the caret starts at the top, where reading starts
        let mut block_lines = vec![];
        if let Some(prog) = &pv.program {
            let owner: std::collections::HashMap<i64, Option<usize>> =
                pv.ops.iter().flat_map(|op| op.blocks.iter().map(move |b| (b.label, op.op_index))).collect();
            let blocks = &prog.blocks;
            for (k, b) in blocks.iter().enumerate() {
                let last = blocks.get(k + 1).map_or(prog.statements.len(), |n| n.line - 1);
                block_lines.push((b.line, last, owner.get(&b.label).copied().flatten()));
                // the block's label line (not the N5xx jump targets)
                if let Some(it) = buf.iter_at_line(b.line as i32 - 1) {
                    let mut end = it;
                    end.forward_to_line_end();
                    buf.apply_tag(&self.parts().block_tag, &it, &end);
                }
            }
            let size = pv.data.as_ref().map_or(0, Vec::len);
            let units = project.output_units.clone().unwrap_or_else(|| prog.units.clone());
            let number = prog.program_number().unwrap_or(prog.o_number);
            let mut info = tr(
                "Program {number} · {units} · {lines} lines · {size} bytes",
                &[("number", &number), ("units", &units), ("lines", &prog.statements.len()), ("size", &size)],
            );
            if pv.data.is_none() {
                info += &format!(" · {}", gettext("incomplete (see banner)"));
            }
            imp.program_info.set_label(&info);
        }
        imp.state.borrow_mut().block_lines = block_lines;
    }

    fn open_find(&self) {
        let imp = self.imp();
        imp.stack.set_visible_child_name("program");
        imp.program_searchbar.set_search_mode(true);
        imp.program_search.grab_focus();
    }

    /// Highlight the next (step 1), previous (-1) or first (0) match from the last one; "N10" or "10" alone goes to
    /// that block's label line.
    fn program_find(&self, text: &str, step: i32) {
        let imp = self.imp();
        let buf = imp.program_view.buffer();
        let found = &self.parts().found;
        buf.remove_tag(found, &buf.start_iter(), &buf.end_iter());
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        let digits = text.strip_prefix(['N', 'n']).unwrap_or(text);
        if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) {
            let all = buf.text(&buf.start_iter(), &buf.end_iter(), false);
            let target = format!("N{digits}");
            if let Some(hit) = all.split('\n').position(|l| l.trim() == target)
                && let Some(a) = buf.iter_at_line(hit as i32)
            {
                let mut b = a;
                b.forward_to_line_end();
                self.show_found(&buf, &a, &b);
                return;
            }
        }
        let start = imp.state.borrow().find_at;
        let it = match (step, start) {
            (0, _) | (_, None) => buf.start_iter(),
            (_, Some(at)) => buf.iter_at_offset(at + if step > 0 { 1 } else { 0 }),
        };
        let flags = gtk::TextSearchFlags::CASE_INSENSITIVE;
        let res = if step >= 0 { it.forward_search(text, flags, None) } else { it.backward_search(text, flags, None) };
        let res = res.or_else(|| {
            // wrap around
            if step >= 0 {
                buf.start_iter().forward_search(text, flags, None)
            } else {
                buf.end_iter().backward_search(text, flags, None)
            }
        });
        if let Some((a, b)) = res {
            self.show_found(&buf, &a, &b);
        }
    }

    fn show_found(&self, buf: &gtk::TextBuffer, a: &gtk::TextIter, b: &gtk::TextIter) {
        buf.apply_tag(&self.parts().found, a, b);
        self.imp().state.borrow_mut().find_at = Some(a.offset());
        let mark = buf.mark("found").unwrap_or_else(|| buf.create_mark(Some("found"), a, true));
        buf.move_mark(&mark, a);
        self.imp().program_view.scroll_to_mark(&mark, 0.1, true, 0.0, 0.3);
    }

    fn highlight_program(&self, index: Option<usize>) {
        let imp = self.imp();
        let buf = imp.program_view.buffer();
        let hl = &self.parts().hl;
        buf.remove_tag(hl, &buf.start_iter(), &buf.end_iter());
        let Some(index) = index else { return };
        let spans: Vec<(usize, usize)> =
            imp.state.borrow().block_lines.iter().filter(|t| t.2 == Some(index)).map(|t| (t.0, t.1)).collect();
        for (a, b) in &spans {
            if let (Some(it_a), Some(mut it_b)) = (buf.iter_at_line(*a as i32 - 1), buf.iter_at_line(*b as i32 - 1)) {
                it_b.forward_to_line_end();
                buf.apply_tag(hl, &it_a, &it_b);
            }
        }
        if let Some(it) = spans.first().and_then(|(a, _)| buf.iter_at_line(*a as i32 - 1)) {
            let mark = buf.mark("op-start").unwrap_or_else(|| buf.create_mark(Some("op-start"), &it, true));
            buf.move_mark(&mark, &it);
            let view = imp.program_view.downgrade();
            glib::idle_add_local_once(move || {
                if let Some(view) = view.upgrade() {
                    view.scroll_to_mark(&mark, 0.1, true, 0.0, 0.0);
                }
            });
        }
    }

    /// Simulation position -> the matching line on the Program page (TOUCH.md 7.2/7.3).
    fn sim_line(&self, line: usize) {
        let view = &self.imp().program_view;
        let buf = view.buffer();
        let Some(it) = buf.iter_at_line(line as i32 - 1) else { return };
        let mut end = it;
        end.forward_to_line_end();
        buf.select_range(&it, &end);
        let mark = buf.mark("sim").unwrap_or_else(|| buf.create_mark(Some("sim"), &it, true));
        buf.move_mark(&mark, &it);
        view.scroll_to_mark(&mark, 0.2, false, 0.0, 0.0);
    }

    fn set_font(&self, pt: f64) {
        let pt = pt.clamp(7.0, 32.0);
        self.imp().font_pt.set(pt);
        self.parts().font_css.load_from_string(&format!("textview.program-text {{ font-size: {pt:.1}pt; }}"));
    }

    /// The font size is remembered (TOUCH.md 7.3), once a pinch, key or wheel turn has set it.
    fn save_font(&self) {
        let pt = self.imp().font_pt.get();
        if settings().program_font_pt != pt {
            update_settings(|s| s.program_font_pt = pt);
        }
    }

    fn on_program_page(&self) -> bool {
        self.imp().stack.visible_child_name().as_deref() == Some("program")
    }

    /// Ctrl+= / Ctrl+− / Ctrl+0: the program's font on the Program page, the drawing elsewhere (POINTER_KEYBOARD.md 8).
    fn zoom_key(&self, factor: Option<f64>) {
        let imp = self.imp();
        match (self.on_program_page(), factor) {
            (true, f) => {
                self.set_font(f.map_or(PROGRAM_FONT_PT, |f| imp.font_pt.get() * f));
                self.save_font();
            }
            (false, Some(f)) => imp.canvas.zoom(f),
            (false, None) => imp.canvas.fit(),
        }
    }

    /// The operation that wrote the program's line (1-based), if any.
    fn op_at_line(&self, line: usize) -> Option<usize> {
        self.imp().state.borrow().block_lines.iter().find(|(a, b, _)| *a <= line && line <= *b).and_then(|t| t.2)
    }

    /// Program page keys: Enter selects the operation that owns the caret's block; Ctrl+wheel sizes the font; Enter in
    /// the search selects the hit's operation (POINTER_KEYBOARD.md 8).
    fn setup_program_keys(&self) {
        let imp = self.imp();
        let keys = gtk::EventControllerKey::new();
        let win = self.downgrade();
        keys.connect_key_pressed(move |_, keyval, _, _| {
            let Some(win) = win.upgrade() else { return glib::Propagation::Proceed };
            if !matches!(keyval, gdk::Key::Return | gdk::Key::KP_Enter | gdk::Key::ISO_Enter) {
                return glib::Propagation::Proceed;
            }
            let buf = win.imp().program_view.buffer();
            let line = buf.iter_at_mark(&buf.get_insert()).line() as usize + 1;
            if let Some(op) = win.op_at_line(line) {
                win.select_op(Some(op));
            }
            glib::Propagation::Stop
        });
        imp.program_view.add_controller(keys);
        let wheel = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::VERTICAL);
        let win = self.downgrade();
        wheel.connect_scroll(move |c, _, dy| {
            let Some(win) = win.upgrade() else { return glib::Propagation::Proceed };
            if !c.current_event_state().contains(gdk::ModifierType::CONTROL_MASK) || dy == 0.0 {
                return glib::Propagation::Proceed;
            }
            win.set_font(win.imp().font_pt.get() * if dy < 0.0 { 1.1 } else { 1.0 / 1.1 });
            win.save_font();
            glib::Propagation::Stop
        });
        imp.program_view.add_controller(wheel);
        let pinch = imp.program_view.observe_controllers().into_iter().flatten().find_map(|c| c.downcast::<gtk::GestureZoom>().ok());
        if let Some(pinch) = pinch {
            let win = self.downgrade();
            pinch.connect_end(move |_, _| {
                if let Some(win) = win.upgrade() {
                    win.save_font();
                }
            });
        }
        let win = self.downgrade();
        imp.program_search.connect_activate(move |_| {
            let Some(win) = win.upgrade() else { return };
            let Some(at) = win.imp().state.borrow().find_at else { return };
            let line = win.imp().program_view.buffer().iter_at_offset(at).line() as usize + 1;
            if let Some(op) = win.op_at_line(line).filter(|op| win.selected_index() != Some(*op)) {
                win.select_op(Some(op));
                // the selection scrolls to the operation's start; the hit stays in view
                let view = win.imp().program_view.downgrade();
                glib::idle_add_local_once(move || {
                    if let Some(view) = view.upgrade()
                        && let Some(mark) = view.buffer().mark("found")
                    {
                        view.scroll_to_mark(&mark, 0.1, true, 0.0, 0.3);
                    }
                });
            }
        });
    }
}

fn set_tool(op: &mut Operation, tool: i32) {
    match op {
        Operation::ToolChange(o) => o.tool = tool,
        Operation::AreaClear(o) => o.tool = tool,
        Operation::FaceClear(o) => o.tool = tool,
        Operation::Groove(o) => o.tool = tool,
        Operation::FinishPass(o) => o.tool = tool,
        Operation::Drill(o) => o.tool = tool,
        Operation::Thread(o) => o.tool = tool,
        Operation::Dwell(_) | Operation::MoveTo(_) => {}
    }
}

fn set_feed(op: &mut Operation, feed: f64) {
    match op {
        Operation::ToolChange(o) => o.feed = feed,
        Operation::AreaClear(o) => o.feed = feed,
        Operation::FaceClear(o) => o.feed = feed,
        Operation::Groove(o) => o.feed = feed,
        Operation::FinishPass(o) => o.feed = feed,
        Operation::Drill(o) => o.feed = feed,
        Operation::MoveTo(o) => o.feed = feed,
        Operation::Thread(_) | Operation::Dwell(_) => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alphat_names() {
        assert_eq!(alphat_name("shaft-2 v1.final"), "SHAFT-2V");
        assert_eq!(alphat_name("ÄÖ"), "PART");
    }
}
