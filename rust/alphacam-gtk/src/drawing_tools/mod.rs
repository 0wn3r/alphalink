//! Drawing tools on the Part canvas (drawing_tools.py, ui/drawing-tools.blp; TOUCH.md 5): Select, Line, Arc, Fillet,
//! Chamfer, Corner, Edit Element, the More commands (circle, tangents, parallel, split arc, blend arc, Mirror Image),
//! ALPHA-T's one-letter keys, contour picking.
//!
//! Rough placement by tap or click is snapped (end points, axis, 1 mm grid) and direction-locked (Z, X, 45 degrees);
//! exact values come from the command entry under the canvas: `X,Z` absolute or `@dX,dZ` relative to the last point,
//! as in ALPHA-T. Every change is one undoable step through the window.
//!
//! Split in parts: this file has the tools, taps, keys and the line being drawn; [`construct`] the construction
//! commands and the feature helpers (thread, corners); [`dialogs`] the dialogs and popovers; [`handles`] dragging the
//! selected element's end points.

mod construct;
mod dialogs;
mod handles;

use std::cell::RefCell;
use std::rc::Rc;
use std::sync::LazyLock;

use adw::prelude::*;
use alphacam::drawing::{self, Kind, SNAP_LOCK_DEG};
use alphacam::model::{Element, Point, Project};
use alphacam::py::{fmt_g, round};
use gtk::{gdk, gio, glib};
use regex::Regex;

use crate::canvas::view::ViewTransform;
use crate::canvas::widget::MarkerKind;
use crate::canvas::LatheCanvas;
use crate::hit_keys::{Hit, cursor_key, hit_action, key_char, step_key};
use crate::i18n::{fill, gettext, n_, ngettext, tr};
use crate::ui::{builder, object};
use crate::window::MainWindow;

use construct::Construct;
use handles::Move;

/// A closure that holds the drawing tools weakly (the window owns them; GTK owns the closures).
macro_rules! weak {
    ($t:ident, move |$a:ident: $ty:ty| $body:expr) => {{
        let w = std::rc::Rc::downgrade(&$t);
        move |$a: $ty| {
            if let Some($t) = w.upgrade() {
                $body;
            }
        }
    }};
    ($t:ident, move || $body:expr) => {
        weak!($t, (), move | | {
            $body;
        })
    };
    ($t:ident, $default:expr, move |$($a:pat_param),*| $body:expr) => {{
        let w = std::rc::Rc::downgrade(&$t);
        move |$($a),*| match w.upgrade() {
            Some($t) => $body,
            None => $default,
        }
    }};
    ($t:ident, move |$($a:pat_param),*| $body:expr) => {
        weak!($t, (), move |$($a),*| {
            $body;
        })
    };
}
pub(crate) use weak;

const CONSTRUCT_MENU: &[(&str, &str, Option<&str>)] = &[
    ("Circle…", "circle", None),
    ("Tangent", "tangent", None),
    ("Tangent at an Angle…", "tangent-angle", None),
    ("Parallel Line…", "parallel", None),
    ("Split Arc", "split", None),
    ("Blend Arc…", "blend", Some("o")),
    ("Mirror Image", "mirror", None),
];
/// feature helpers (workflow improvements, step 3): a section of their own in the More menu
const FEATURE_MENU: &[(&str, &str)] = &[
    ("Standard Thread…", "thread"),
    ("Chamfer Outside Corners…", "corners-chamfer"),
    ("Round Outside Corners…", "corners-round"),
];
/// ALPHA-T's letter commands that open a dialog: a section of the More menu, so a finger or a pen reaches them
const LETTER_MENU: &[(&str, char)] = &[
    ("Draw Line by Length and Angle", 'd'),
    ("Move Cursor to X,Z", 'g'),
    ("Move Cursor by dX,dZ", 'h'),
    ("Move Cursor by Length and Angle", 'y'),
    ("Measure by X,Z", 'm'),
    ("Measure by Length and Angle", 'n'),
];
/// the text commands: a section of their own after the cursor and measure ones
const TEXT_MENU: &[(&str, char)] = &[("Add Text", 't'), ("Erase Text", 'u')];
/// (name, label, tooltip with the one-letter keys of hit_keys.rs)
const TOOLS: &[(&str, &str, &str)] = &[
    ("select", "Select", "Select an element or an operation. Press L or A to select the nearest line or arc"),
    (
        "line",
        "Line",
        "Draw lines. Press X to type X,Z, D to type length and angle, + to draw by taps, S to start at the cursor",
    ),
    ("arc", "Arc", "Draw Arc Through Three Points (W)"),
    ("fillet", "Fillet", "Round a corner. Press F to round the corner nearest the cursor"),
    ("chamfer", "Chamfer", "Chamfer Corner (B)"),
    ("corner", "Corner", "Trim or Extend Two Lines to Meet (K)"),
];
/// between the last point and its value chip
const CHIP_GAP_PX: f64 = 16.0;
/// keys that make a key press a shortcut, never a hit key
const COMMAND_KEYS: gdk::ModifierType =
    gdk::ModifierType::CONTROL_MASK.union(gdk::ModifierType::ALT_MASK).union(gdk::ModifierType::SUPER_MASK);

/// What Enter in the command entry does, after the ALPHA-T key that asked for the point (hit_keys.rs).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum EntryMode {
    Point,
    /// only a hint: the Arc tool takes points like Point
    Arc,
    LineLa,
    MoveAbs,
    MoveInc,
    MoveLa,
}

impl EntryMode {
    fn hint(self) -> &'static str {
        match self {
            EntryMode::Point => "X,Z  or  @dX,dZ  then Enter (line tool)",
            EntryMode::Arc => "Arc: X,Z  or  @dX,dZ  then Enter: start, a point on the arc, end",
            EntryMode::LineLa => "Draw line: length,angle  then Enter (0° = towards -Z, 90° = +X)",
            EntryMode::MoveAbs => "Move the cursor to X,Z  then Enter",
            EntryMode::MoveInc => "Move the cursor by dX,dZ  then Enter",
            EntryMode::MoveLa => "Move the cursor by length,angle  then Enter (0° = towards -Z, 90° = +X)",
        }
    }
}

/// What the command entry says while no command asks for anything: the active tool in a few words, short enough
/// for the touch layout's narrow entry. The whole guidance (`tool_guide`) goes to the read-out line.
fn tool_hint_text(tool: &str) -> String {
    match tool {
        "select" => gettext("Tap an element or an operation"),
        "fillet" | "chamfer" => gettext("Tap a corner"),
        "corner" => gettext("Tap two lines"),
        "arc" => gettext("Arc: X,Z or @dX,dZ, then Enter"),
        _ => gettext("X,Z or @dX,dZ, then Enter"),
    }
}

/// How to use the active tool, in full: shown in the read-out line when the tool is chosen.
fn tool_guide(tool: &str) -> String {
    gettext(match tool {
        "select" => "Tap an element or an operation; L or A finds the nearest line or arc",
        "fillet" | "chamfer" => "Tap a corner, or move the cursor near it and press Enter",
        "corner" => "Tap two lines, or move the cursor to each and press Enter",
        "arc" => EntryMode::Arc.hint(),
        _ => EntryMode::Point.hint(),
    })
}

static RE_COORD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*(@)?\s*(-?\d*\.?\d+)\s*[,; ]\s*(-?\d*\.?\d+)\s*$").expect("regex"));

/// A typed point: (relative, x, z), or None when the text is not one.
fn parse_coord(text: &str) -> Option<(bool, f64, f64)> {
    let m = RE_COORD.captures(text)?;
    Some((m.get(1).is_some(), m[2].parse().ok()?, m[3].parse().ok()?))
}

/// Python's `f"{x:.4g}"`.
fn fmt_g4(x: f64) -> String {
    if x == 0.0 || !x.is_finite() {
        return fmt_g(x);
    }
    let exp = x.abs().log10().floor() as i32;
    if !(-4..4).contains(&exp) {
        let s = format!("{x:.3e}");
        let (m, e) = s.split_once('e').unwrap_or((&s, "0"));
        let m = if m.contains('.') { m.trim_end_matches('0').trim_end_matches('.') } else { m };
        let e: i32 = e.parse().unwrap_or(0);
        return format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs());
    }
    let s = format!("{:.*}", (3 - exp).max(0) as usize, x);
    if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s }
}

fn readout_xz(p: Point) -> String {
    format!("X {:9.3}   Z {:9.3}", p.x, p.z)
}

/// The snapping grid: 1 mm, or 0.05 inch.
fn grid(project: &Project) -> f64 {
    if project.units == "mm" { 1.0 } else { 0.05 }
}

fn element<'p>(project: &'p Project, gid: &str) -> Option<&'p Element> {
    project.geometry.iter().find(|g| g.id() == gid)
}

fn marker_kind(kind: &str) -> MarkerKind {
    if kind == "end" { MarkerKind::End } else { MarkerKind::Other }
}

fn screen_rect(sx: f64, sy: f64) -> gdk::Rectangle {
    gdk::Rectangle::new(sx as i32, sy as i32, 1, 1)
}

/// The first widget of type T under root (depth first).
fn find_widget<T: IsA<gtk::Widget>>(root: &gtk::Widget) -> Option<T> {
    let mut stack = vec![root.clone()];
    while let Some(w) = stack.pop() {
        if let Ok(t) = w.clone().downcast::<T>() {
            return Some(t);
        }
        let mut c = w.first_child();
        while let Some(child) = c {
            c = child.next_sibling();
            stack.push(child);
        }
    }
    None
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Measure {
    Xz,
    La,
}

struct St {
    /// line tool: the chain being drawn
    points: Vec<Point>,
    selected: Option<String>,
    pick_op: Option<usize>,
    /// arc tool: start, through, end
    arc_pts: Vec<Point>,
    /// corner tool: first line
    corner_first: Option<String>,
    /// ALPHA-T's cursor: the pointer's last place, or where the keys put it
    cursor: Option<Point>,
    /// cursor key step (radius / Z)
    step: f64,
    /// where the pointer was when the keys moved the cursor
    key_pointer: Option<(f64, f64)>,
    entry_mode: EntryMode,
    measure: Option<(Measure, Option<Point>)>,
    prev_view: Option<ViewTransform>,
    construct: Option<Construct>,
    /// where the context menu was opened (Zoom Here)
    ctx_at: (f64, f64),
    /// an end point being dragged
    mov: Option<Move>,
    touch: bool,
    /// picking a contour by keys: the element Space adds or removes ([ ] l a move it)
    pick_at: Option<String>,
    /// the end point last tapped with Select: a typed point draws a line from it (Eduard, 2026-10-20)
    start_at: Option<Point>,
}

impl Default for St {
    fn default() -> Self {
        St {
            points: vec![],
            selected: None,
            pick_op: None,
            arc_pts: vec![],
            corner_first: None,
            cursor: None,
            step: 10.0,
            key_pointer: None,
            entry_mode: EntryMode::Point,
            measure: None,
            prev_view: None,
            construct: None,
            ctx_at: (0.0, 0.0),
            mov: None,
            touch: false,
            start_at: None,
            pick_at: None,
        }
    }
}

pub struct DrawingTools {
    /// itself, for the closures it hands out
    me: std::rc::Weak<DrawingTools>,
    win: glib::WeakRef<MainWindow>,
    c: LatheCanvas,
    /// the value chip on the last drawn segment (touch), over the drawing
    pub chip: gtk::Button,
    pub construct_banner: adw::Banner,
    pub pick_banner: adw::Banner,
    /// the tool bar under the drawing
    pub bar: adw::WrapBox,
    palette: adw::ToggleGroup,
    keypad_button: gtk::Button,
    entry: gtk::Entry,
    done: gtk::Button,
    cancel_pick_btn: gtk::Button,
    edit_el: gtk::Button,
    delete_el: gtk::Button,
    /// the context menu's actions (TOUCH.md 4: long press)
    ctx: gio::SimpleActionGroup,
    s: RefCell<St>,
}

impl DrawingTools {
    pub fn new(window: &MainWindow, canvas: &LatheCanvas) -> Rc<Self> {
        let ui = builder("drawing-tools");
        let more: gtk::MenuButton = object(&ui, "more");
        let this = Rc::new_cyclic(|me| DrawingTools {
            me: me.clone(),
            win: window.downgrade(),
            c: canvas.clone(),
            chip: object(&ui, "chip"),
            construct_banner: object(&ui, "construct_banner"),
            pick_banner: object(&ui, "pick_banner"),
            bar: object(&ui, "bar"),
            palette: object(&ui, "palette"),
            keypad_button: object(&ui, "keypad_button"),
            entry: object(&ui, "entry"),
            done: object(&ui, "done"),
            cancel_pick_btn: object(&ui, "cancel_pick_btn"),
            edit_el: object(&ui, "edit_el"),
            delete_el: object(&ui, "delete_el"),
            ctx: gio::SimpleActionGroup::new(),
            s: RefCell::new(St::default()),
        });
        for (name, label, tip) in TOOLS {
            this.palette.add(adw::Toggle::builder().name(*name).label(gettext(label)).tooltip(gettext(tip)).build());
        }
        this.palette.set_active_name(Some("select"));
        this.palette.connect_active_name_notify(weak!(this, move |_| this.palette_changed()));

        this.entry.set_placeholder_text(Some(&tool_hint_text("select")));
        this.entry.connect_activate(weak!(this, move |_| this.entry_activate()));
        let entry_keys = gtk::EventControllerKey::new();
        entry_keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        entry_keys.connect_key_pressed(weak!(this, glib::Propagation::Proceed,
            move |_, keyval, code, state| this.entry_key(keyval, code, state)));
        this.entry.add_controller(entry_keys);
        this.done.connect_clicked(weak!(this, move |_| this.finish_chain()));
        this.delete_el.connect_clicked(weak!(this, move |_| this.delete_selected_element()));
        this.edit_el.connect_clicked(weak!(this, move |_| this.edit_selected_element()));
        this.keypad_button.connect_clicked(weak!(this, move |_| this.open_keypad()));
        // a finger or the pen on the entry opens the keypad, as on every number field (POINTER_KEYBOARD.md 11)
        crate::op_editor::on_tap_by_hand(&this.entry, weak!(this, move || this.open_keypad()));

        // sections with headings, so the items read as groups (HIG: menus)
        let menu = gio::Menu::new();
        let construct = gio::Menu::new();
        for (label, kind, key) in CONSTRUCT_MENU {
            let item = gio::MenuItem::new(Some(&gettext(label)), None);
            if let Some(key) = key {
                // shown at the right of the item, as GTK shows shortcuts (the key itself is handled in hit_keys)
                item.set_attribute_value("accel", Some(&key.to_variant()));
            }
            item.set_action_and_target_value(Some("win.construct"), Some(&kind.to_variant()));
            construct.append_item(&item);
        }
        menu.append_section(Some(&gettext("Construct")), &construct);
        let features = gio::Menu::new();
        for (label, kind) in FEATURE_MENU {
            let item = gio::MenuItem::new(Some(&gettext(label)), None);
            item.set_action_and_target_value(Some("win.construct"), Some(&kind.to_variant()));
            features.append_item(&item);
        }
        menu.append_section(Some(&gettext("Features")), &features);
        // ALPHA-T's letter commands with dialogs, reachable without a keyboard (POINTER_KEYBOARD.md 4); the six cursor
        // and measure ones in a submenu, so the menu fits above the drawing tools on a Surface in landscape (a finger
        // does not scroll a popover menu, found by tests/e2e)
        for (title, items, submenu) in [(n_!("Cursor and Measure"), LETTER_MENU, true), (n_!("Text"), TEXT_MENU, false)] {
            let section = gio::Menu::new();
            for (label, key) in items {
                let item = gio::MenuItem::new(Some(&gettext(label)), None);
                item.set_attribute_value("accel", Some(&key.to_string().to_variant()));
                item.set_action_and_target_value(Some("win.hit-key"), Some(&key.to_string().to_variant()));
                section.append_item(&item);
            }
            if submenu {
                let wrap = gio::Menu::new();
                wrap.append_submenu(Some(&gettext(title)), &section);
                menu.append_section(None, &wrap);
            } else {
                menu.append_section(Some(&gettext(title)), &section);
            }
        }
        let hit_key = gio::SimpleAction::new("hit-key", Some(glib::VariantTy::STRING));
        hit_key.connect_activate(weak!(this, move |_, v| {
            if let Some(c) = v.and_then(|v| v.get::<String>()).and_then(|s| s.chars().next()) {
                this.hit_key(c);
            }
        }));
        window.add_action(&hit_key);
        // ALPHA-T's Q, reachable by touch too (Eduard 2026-10-16: no way to clear the drawing in touch mode)
        let erase = gio::Menu::new();
        erase.append(Some(&gettext("Erase Drawing…")), Some("win.erase-drawing"));
        menu.append_section(None, &erase);
        more.set_menu_model(Some(&menu));
        // the headed menu is about 800 px tall: on a short screen (a Surface in landscape) it scrolls inside the
        // window instead of opening past the screen edge, where X11 does not show it at all
        if let Some(popover) = more.popover() {
            popover.connect_show(|p| {
                if let (Some(sw), Some(root)) = (p.child().and_downcast::<gtk::ScrolledWindow>(), p.root()) {
                    sw.set_max_content_height((root.height() - 120).max(240));
                }
            });
        }
        let erase_action = gio::SimpleAction::new("erase-drawing", None);
        erase_action.connect_activate(weak!(this, move |_, _| {
            if this.w().has_project() {
                this.erase_drawing_dialog()
            }
        }));
        window.add_action(&erase_action);
        let construct = gio::SimpleAction::new("construct", Some(glib::VariantTy::STRING));
        construct.connect_activate(weak!(this, move |_, v| {
            if let Some(kind) = v.and_then(|v| v.get::<String>()) {
                this.start_construct(&kind)
            }
        }));
        window.add_action(&construct);
        this.construct_banner.connect_button_clicked(weak!(this, move |_| this.banner_button()));
        this.cancel_pick_btn.connect_clicked(weak!(this, move |_| this.cancel_pick()));
        this.pick_banner.connect_button_clicked(weak!(this, move |_| this.finish_pick()));

        canvas.connect_local("canvas-tapped", false, weak!(this, None, move |v| {
            let (x, z, tol) = (v[1].get().unwrap_or(0.0), v[2].get().unwrap_or(0.0), v[3].get().unwrap_or(0.0));
            let op: i32 = v[4].get().unwrap_or(-1);
            let el: String = v[5].get().unwrap_or_default();
            this.tapped(x, z, tol, op, &el);
            None
        }));
        canvas.connect_local("canvas-long-pressed", false, weak!(this, None, move |v| {
            let el: String = v[5].get().unwrap_or_default();
            this.long_pressed(v[3].get().unwrap_or(0.0), v[4].get().unwrap_or(0.0), &el);
            None
        }));
        canvas.connect_local("view-changed", false, weak!(this, None, move |_| {
            this.place_chip();
            None
        }));
        for (name, f) in [
            ("edit", Self::edit_selected_element as fn(&Self)),
            ("delete", Self::delete_selected_element),
            ("zoom", Self::zoom_here),
        ] {
            let a = gio::SimpleAction::new(name, None);
            a.connect_activate(weak!(this, move |_, _| f(&this)));
            this.ctx.add_action(&a);
        }
        // begin / drag / end / cancel: moving a selected element's end (TOUCH.md 5.2)
        canvas.set_handle_hooks(Some(Rc::new(handles::Hooks(Rc::downgrade(&this)))));
        this.chip.connect_clicked(weak!(this, move |_| this.correct_last_point()));
        let motion = gtk::EventControllerMotion::new();
        motion.connect_motion(weak!(this, move |_, x, y| this.motion(x, y)));
        canvas.add_controller(motion);
        let keys = gtk::EventControllerKey::new();
        let proceed = glib::Propagation::Proceed;
        keys.connect_key_pressed(weak!(this, proceed, move |_, keyval, _, state| this.key(keyval, state)));
        canvas.add_controller(keys);
        let esc = gtk::EventControllerKey::new();
        esc.set_propagation_phase(gtk::PropagationPhase::Capture);
        esc.connect_key_pressed(weak!(this, proceed, move |_, keyval, _, _| this.window_esc(keyval)));
        window.add_controller(esc);
        // ALPHA-T's one-letter commands (hit_keys.rs); bubble phase, so focused text fields get the keys first
        let hit = gtk::EventControllerKey::new();
        hit.connect_key_pressed(weak!(this, glib::Propagation::Proceed,
            move |_, keyval, code, state| this.hit_key_pressed(keyval, code, state)));
        window.add_controller(hit);
        this
    }

    fn rc(&self) -> Rc<Self> {
        self.me.upgrade().expect("the drawing tools are alive")
    }

    fn w(&self) -> MainWindow {
        self.win.upgrade().expect("the window outlives its drawing tools")
    }

    fn project(&self) -> Option<Rc<Project>> {
        self.w().project()
    }

    fn tool(&self) -> String {
        self.c.state().tool.clone()
    }

    fn readout(&self, text: &str) {
        self.w().readout().set_label(text);
    }

    fn toast(&self, text: &str) {
        self.w().toast(text);
    }

    /// One undoable change; the window refreshes when it went in.
    fn edit(&self, label: &str, change: impl FnOnce(&mut Project) -> alphacam::error::Result<()>) -> bool {
        let w = self.w();
        if w.apply_edit(label, change) {
            w.refresh(false);
            return true;
        }
        false
    }

    // --- the API the window uses ------------------------------------------------------------------------

    /// the operation whose contour is being picked
    pub fn pick_op(&self) -> Option<usize> {
        self.s.borrow().pick_op
    }

    /// the keyboard put the cursor (the read-out shows it until the pointer really moves)
    pub fn key_pointer(&self) -> bool {
        self.s.borrow().key_pointer.is_some()
    }

    /// What the end-to-end tests read through the test bus (test_bus.rs State).
    pub fn test_state(&self) -> serde_json::Value {
        let s = self.s.borrow();
        serde_json::json!({
            "selected_element": s.selected,
            "cursor": s.cursor.map(|c| [c.x, c.z]),
            "picking": s.pick_op,
            "constructing": s.construct.is_some(),
            "chain": s.points.len(),
            "picked": self.c.state().overlay.picked.clone(),
            "pick_at": s.pick_at,
        })
    }

    pub fn select_element(&self, id: Option<&str>) {
        self.select_element_(id.map(str::to_string));
    }

    // --- tools ------------------------------------------------------------------------------------------

    fn palette_changed(&self) {
        if let Some(name) = self.palette.active_name() {
            self.set_tool(&name);
        }
        if !gtk::prelude::GtkWindowExt::focus(&self.w()).is_some_and(|f| f.is::<gtk::Editable>()) {
            self.c.grab_focus(); // a tool button keeps no focus: the arrows move the cursor, not between buttons
        }
    }

    fn tool_hint(&self) -> String {
        tool_hint_text(&self.tool())
    }

    pub fn set_tool(&self, name: &str) {
        if self.palette.active_name().as_deref() != Some(name) {
            self.palette.set_active_name(Some(name));
            return;
        }
        self.finish_chain();
        self.c.state_mut().tool = name.into();
        self.done.set_visible(matches!(name, "line" | "arc"));
        if self.s.borrow().entry_mode == EntryMode::Point {
            self.entry.set_placeholder_text(Some(&self.tool_hint()));
            self.readout(&tool_guide(name));
        }
        self.select_element_(None);
        if name != "select" {
            self.w().select_op(None);
        }
        self.c.queue_draw();
    }

    fn tapped(&self, x: f64, z: f64, tol: f64, op_index: i32, element: &str) {
        let Some(project) = self.project() else { return };
        if self.s.borrow().pick_op.is_some() {
            self.toggle_pick(element);
            return;
        }
        let p = Point::new(x, z);
        {
            let mut s = self.s.borrow_mut();
            s.cursor = Some(p);
            s.start_at = None;
        }
        self.c.state_mut().overlay.cursor = None;
        self.readout(&readout_xz(p)); // a tap shows where it landed, as the pointer does
        if self.s.borrow().construct.is_some() {
            self.construct_tap(p, tol, element);
            return;
        }
        if self.s.borrow().measure.is_some() {
            self.measure_point(p);
            return;
        }
        let w = self.w();
        match self.tool().as_str() {
            "select" => {
                if op_index >= 0 {
                    self.select_element_(None);
                    w.select_op(Some(op_index as usize));
                    let at = {
                        let s = self.c.state();
                        s.last_tap.segment.and_then(|seg| s.segs.iter().position(|x| *x == seg))
                    };
                    if let Some(i) = at {
                        w.parts().sim.goto(Some(i + 1)); // the simulation jumps to the tapped move (TOUCH.md 7.2)
                    }
                } else if !element.is_empty() {
                    w.select_op(None);
                    let close = self.c.state().last_tap.candidates.clone();
                    if close.len() > 1 {
                        self.choose_element(&close); // equally close (a corner): ask which
                    } else {
                        self.select_element_(Some(element.into()));
                    }
                } else {
                    w.select_op(None);
                    self.select_element_(None);
                }
                let (pt, kind) = drawing::snap(&project, p, tol, grid(&project));
                if kind == "end" {
                    self.s.borrow_mut().start_at = Some(pt);
                    self.c.overlay(|o| o.marker = Some((pt, MarkerKind::End)));
                }
            }
            "line" => {
                let (pt, _) = drawing::snap(&project, p, tol, grid(&project));
                self.add_point(pt);
            }
            "arc" => {
                let (pt, _) = drawing::snap(&project, p, tol, grid(&project));
                self.arc_point(pt);
            }
            "corner" => self.corner_pick(element),
            tool @ ("fillet" | "chamfer") => match self.corner_near(p, tol) {
                Some(corner) => self.size_popover(tool, corner),
                None => self.toast(&gettext("Tap a corner between two lines")),
            },
            _ => {}
        }
    }

    fn motion(&self, sx: f64, sy: f64) {
        let Some(project) = self.project() else { return };
        let has_cursor = self.c.state().overlay.cursor.is_some();
        {
            let mut s = self.s.borrow_mut();
            if let Some((kx, ky)) = s.key_pointer
                && has_cursor
                && (sx - kx).abs() + (sy - ky).abs() < 6.0
            {
                return; // the keys put the cursor; a motion event without a real move (a redraw, a jitter) keeps it
            }
            s.key_pointer = None;
        }
        let (x, z, scale) = {
            let s = self.c.state();
            let (x, z) = s.view.to_world(sx, sy);
            (x, z, s.view.scale)
        };
        self.s.borrow_mut().cursor = Some(Point::new(x, z)); // the mouse is ALPHA-T's cursor too
        if has_cursor {
            self.c.overlay(|o| o.cursor = None);
        }
        let tool = self.tool();
        if tool != "line" && tool != "arc" {
            return;
        }
        let (mut pt, kind) = drawing::snap(&project, Point::new(x, z), 10.0 / scale, grid(&project));
        let last = self.s.borrow().points.last().copied();
        if tool == "line"
            && let Some(last) = last
            && kind != "end"
        {
            pt = drawing::lock_direction(last, pt, SNAP_LOCK_DEG);
        }
        self.c.overlay(|o| {
            o.marker = Some((pt, marker_kind(kind)));
            o.rubber = if tool == "line" { last.map(|l| (l, pt)) } else { None };
        });
    }

    /// Esc ends whatever is going on (a construction, picking, a typed command, a line) and gives the keyboard back
    /// to the drawing, so ALPHA-T's letter keys work again (after X the entry has the focus).
    fn window_esc(&self, keyval: gdk::Key) -> glib::Propagation {
        let w = self.w();
        if keyval != gdk::Key::Escape || w.visible_dialog().is_some() {
            return glib::Propagation::Proceed;
        }
        let stack = w.stack();
        if stack.visible_child_name().as_deref() == Some("program")
            && let Some(page) = stack.child_by_name("program")
            && let Some(bar) = find_widget::<gtk::SearchBar>(&page)
            && bar.is_search_mode()
        {
            bar.set_search_mode(false); // the Program page's search bar closes, as GNOME's do
            w.program_view().grab_focus();
            return glib::Propagation::Stop;
        }
        let cleared = w.dismiss_toasts();
        let mut handled = true;
        let (construct, pick, command, chain) = {
            let s = self.s.borrow();
            (
                s.construct.is_some(),
                s.pick_op.is_some(),
                s.measure.is_some() || s.entry_mode != EntryMode::Point,
                !s.points.is_empty() || !s.arc_pts.is_empty() || s.corner_first.is_some(),
            )
        };
        self.s.borrow_mut().start_at = None;
        if construct {
            self.cancel_construct();
        } else if pick {
            self.cancel_pick();
        } else if command {
            self.s.borrow_mut().measure = None;
            self.ask_done();
            self.entry.set_text("");
            self.readout(&gettext("Command aborted"));
        } else if chain {
            self.finish_chain();
            self.entry.set_text("");
        } else {
            handled = cleared;
        }
        if let Some(focus) = gtk::prelude::GtkWindowExt::focus(&w)
            && (focus == self.entry.clone().upcast::<gtk::Widget>() || focus.is_ancestor(&self.entry))
        {
            self.entry.set_text("");
            self.c.grab_focus();
            handled = true;
        }
        if handled { glib::Propagation::Stop } else { glib::Propagation::Proceed }
    }

    fn key(&self, keyval: gdk::Key, state: gdk::ModifierType) -> glib::Propagation {
        if keyval == gdk::Key::Menu || (keyval == gdk::Key::F10 && state.contains(gdk::ModifierType::SHIFT_MASK)) {
            // the context menu from the keyboard, at the ALPHA-T cursor (or the middle of the drawing)
            let cursor = self.s.borrow().cursor;
            let (sx, sy) = match cursor {
                Some(c) => self.c.state().view.to_screen(c.x, c.z),
                None => (self.c.width() as f64 / 2.0, self.c.height() as f64 / 2.0),
            };
            let (el, (x, z)) = {
                let s = self.c.state();
                let r = crate::canvas::hittest::Source::Touch.radius_px();
                let el = s.project.as_ref().and_then(|p| crate::canvas::hittest::hit_element(p, &s.view, sx, sy, r));
                (el.unwrap_or_default(), s.view.to_world(sx, sy))
            };
            self.c.emit_by_name::<()>("canvas-long-pressed", &[&x, &z, &sx, &sy, &el]);
            return glib::Propagation::Stop;
        }
        let back = keyval == gdk::Key::ISO_Left_Tab || state.contains(gdk::ModifierType::SHIFT_MASK);
        if matches!(keyval, gdk::Key::Tab | gdk::Key::KP_Tab | gdk::Key::ISO_Left_Tab)
            && self.cycle_choice(if back { -1 } else { 1 })
        {
            return glib::Propagation::Stop;
        }
        let (chain, pick) = {
            let s = self.s.borrow();
            (!s.points.is_empty(), s.pick_op.is_some())
        };
        if keyval == gdk::Key::Escape && (chain || pick) {
            if pick { self.cancel_pick() } else { self.finish_chain() }
            return glib::Propagation::Stop;
        }
        glib::Propagation::Proceed
    }

    fn hit_key_pressed(&self, keyval: gdk::Key, code: u32, state: gdk::ModifierType) -> glib::Propagation {
        let w = self.w();
        let focus = gtk::prelude::GtkWindowExt::focus(&w);
        let on_part = w.has_project()
            && w.visible_dialog().is_none()
            && w.stack().visible_child_name().as_deref() == Some("part");
        if on_part
            && (state & gtk::accelerator_get_default_mod_mask()) == gdk::ModifierType::CONTROL_MASK
            && focus.as_ref().is_none_or(|f| *f == self.c.clone().upcast::<gtk::Widget>())
            && self.pan_key(keyval)
        {
            return glib::Propagation::Stop;
        }
        if state.intersects(COMMAND_KEYS)
            || !w.has_project()
            || w.visible_dialog().is_some()
            || w.stack().visible_child_name().as_deref() != Some("part")
            || focus.as_ref().is_some_and(|f| f.is::<gtk::Editable>())
        {
            return glib::Propagation::Proceed;
        }
        let name = keyval.name().map(|n| n.to_string()).unwrap_or_default();
        let on_canvas = focus.as_ref().is_none_or(|f| *f == self.c.clone().upcast::<gtk::Widget>());
        // Delete erases the selected element wherever the Part page has the focus (POINTER_KEYBOARD.md 5)
        if matches!(keyval, gdk::Key::Delete | gdk::Key::KP_Delete | gdk::Key::BackSpace)
            && self.s.borrow().selected.is_some()
            && focus.as_ref().is_none_or(|f| w.stack().child_by_name("part").is_some_and(|page| f.is_ancestor(&page)) || on_canvas)
        {
            self.delete_selected_element();
            return glib::Propagation::Stop;
        }
        let done = if cursor_key(&name).is_some()
            || step_key(&name).is_some()
            || matches!(name.as_str(), "Home" | "End" | "Return" | "KP_Enter")
        {
            // the cursor keys and Enter keep their usual meaning on buttons and lists
            on_canvas && self.cursor_key(&name)
        } else {
            keyval.to_unicode().is_some_and(|c| self.hit_key(key_char(c, code)))
        };
        if done { glib::Propagation::Stop } else { glib::Propagation::Proceed }
    }

    /// A coordinate never has letters, so in the command entry a letter is ALPHA-T's one-letter command too (typing
    /// X,Z then pressing A works as in ALPHA-T, without clicking the drawing first).
    fn entry_key(&self, keyval: gdk::Key, code: u32, state: gdk::ModifierType) -> glib::Propagation {
        if state.intersects(COMMAND_KEYS) {
            return glib::Propagation::Proceed;
        }
        let Some(c) = keyval.to_unicode().map(|c| key_char(c, code)) else { return glib::Propagation::Proceed };
        if !c.is_ascii_alphabetic() || self.s.borrow().pick_op.is_some() {
            return glib::Propagation::Proceed;
        }
        self.hit_key(c);
        glib::Propagation::Stop
    }

    /// Run ALPHA-T's command for a typed character; true when it was one.
    pub fn hit_key(&self, c: char) -> bool {
        let (picking, selected, cur) = {
            let s = self.s.borrow();
            (s.pick_op.is_some(), s.selected.clone(), s.cursor)
        };
        if matches!(c, '[' | ']') {
            return self.walk_profile(if c == ']' { 1 } else { -1 }, picking);
        }
        if picking {
            match c {
                ' ' => return self.pick_key(),
                'l' | 'a' | 'L' | 'A' => {
                    let kind = if c.eq_ignore_ascii_case(&'l') { Kind::Line } else { Kind::Arc };
                    let found = self.project().and_then(|p| drawing::nearest_element(&p, cur.unwrap_or_default(), Some(kind)));
                    if let Some(gid) = found {
                        self.set_pick_at(Some(gid));
                    }
                    return true;
                }
                _ => {}
            }
        }
        let Some(action) = hit_action(c, picking, selected.is_some()) else { return false };
        let Some(p) = self.project() else { return false };
        let w = self.w();
        let here = cur.unwrap_or_default();
        match action {
            Hit::PickDone => self.finish_pick(),
            Hit::Accept => return self.accept_cursor(),
            Hit::Type => self.ask(EntryMode::Point, &c.to_string()),
            Hit::Line => self.ask(EntryMode::Point, ""),
            Hit::Freehand => self.line_tool(),
            Hit::LineLa => self.ask(EntryMode::LineLa, ""),
            Hit::MoveAbs => self.ask(EntryMode::MoveAbs, ""),
            Hit::MoveInc => self.ask(EntryMode::MoveInc, ""),
            Hit::MoveLa => self.ask(EntryMode::MoveLa, ""),
            Hit::Start => self.move_cursor(here, true),
            Hit::FindPoint | Hit::FindIntersection => {
                let found = if action == Hit::FindPoint {
                    drawing::nearest_point(&p, here)
                } else {
                    drawing::nearest_intersection(&p, here)
                };
                match found {
                    None => self.toast(&gettext(if action == Hit::FindPoint {
                        "No point to find"
                    } else {
                        "No intersection to find"
                    })),
                    Some(pt) => {
                        let start = self.tool() == "line" && self.s.borrow().points.is_empty();
                        self.move_cursor(pt, start)
                    }
                }
            }
            Hit::FindLine | Hit::FindArc => {
                let line = action == Hit::FindLine;
                match drawing::nearest_element(&p, here, Some(if line { Kind::Line } else { Kind::Arc })) {
                    None => self.toast(&gettext(if line { "No line to find" } else { "No arc to find" })),
                    Some(gid) => {
                        self.set_tool("select");
                        self.select_element_(Some(gid.clone()));
                        self.toast(&tr("{what} found. Press Delete or minus to erase it", &[("what", &gid)]));
                    }
                }
            }
            Hit::Erase => self.delete_selected_element(),
            Hit::Radius => match cur.and_then(|c| self.corner_near(c, f64::INFINITY)) {
                None => self.set_tool("fillet"),
                Some(corner) => self.size_popover("fillet", corner),
            },
            // ALPHA-T's O: 2 PKT RADIUS; O again takes the cursor as the next point, as on DOS
            Hit::Blend if self.constructing("blend") => return self.accept_cursor(),
            Hit::Blend => self.start_construct("blend"),
            Hit::Corner => self.set_tool("corner"),
            Hit::Chamfer => self.set_tool("chamfer"),
            Hit::Arc => self.set_tool("arc"),
            Hit::Edit => {
                if selected.is_some() {
                    self.edit_selected_element();
                } else {
                    self.toast(&gettext("Select a line or arc first, or press L or A to find the nearest"));
                }
            }
            Hit::MeasureXz => self.start_measure(Measure::Xz),
            Hit::MeasureLa => self.start_measure(Measure::La),
            Hit::Text => self.text_dialog(here),
            Hit::EraseText => match drawing::nearest_text(&p, here) {
                None => self.toast(&gettext("No text to erase")),
                Some(i) => {
                    self.edit(&gettext("Erase text"), |q| {
                        q.texts.remove(i);
                        Ok(())
                    });
                }
            },
            Hit::EraseDrawing => self.erase_drawing_dialog(),
            Hit::ZoomIn | Hit::Fit => {
                let view = self.c.state().view;
                self.s.borrow_mut().prev_view = Some(view);
                match (action, cur) {
                    (Hit::Fit, _) => self.c.fit(),
                    // ALPHA-T's Z zooms in at the cursor (the middle of the view when there is none)
                    (_, Some(c)) => {
                        let (sx, sy) = view.to_screen(c.x, c.z);
                        self.c.state_mut().view.zoom_at(sx, sy, 1.25);
                        self.c.user_view_change();
                    }
                    _ => self.c.zoom(1.25),
                }
            }
            Hit::RecallView => {
                let prev = self.s.borrow().prev_view;
                if let Some(prev) = prev {
                    let now = std::mem::replace(&mut self.c.state_mut().view, prev);
                    self.s.borrow_mut().prev_view = Some(now);
                    self.c.user_view_change();
                }
            }
            Hit::Cam => {
                w.add_op_button().popup();
            }
        }
        true
    }

    // --- ALPHA-T cursor ---------------------------------------------------------------------------------

    /// Cursor keys move the ALPHA-T cursor by the step (PgUp x10, PgDn :10), Home: middle of the view, End: the
    /// reference point X0 Z0, Enter accepts the cursor (see accept_cursor).
    fn cursor_key(&self, name: &str) -> bool {
        if matches!(name, "Return" | "KP_Enter") {
            return self.hit_key('\r');
        }
        if let Some(f) = step_key(name) {
            let step = {
                let mut s = self.s.borrow_mut();
                s.step = (s.step * f).clamp(0.001, 1000.0);
                s.step
            };
            self.readout(&tr("Cursor step {step}", &[("step", &fmt_g(step))]));
            return true;
        }
        let (cur, step) = {
            let s = self.s.borrow();
            (s.cursor.unwrap_or_default(), s.step)
        };
        let pt = match name {
            "Home" => {
                let (x, z) = self.c.state().view.to_world(self.c.width() as f64 / 2.0, self.c.height() as f64 / 2.0);
                Point::new(round(x, 3), round(z, 3))
            }
            "End" => Point::new(0.0, 0.0),
            _ => {
                let Some((dx, dz)) = cursor_key(name) else { return false };
                Point::new(round(cur.x + 2.0 * dx * step, 6), round(cur.z + dz * step, 6))
            }
        };
        self.move_cursor(pt, false);
        true
    }

    /// Put the ALPHA-T cursor on pt; `start`: a line starts there (ALPHA-T S, or a move with the Line tool).
    fn move_cursor(&self, pt: Point, start: bool) {
        let pointer = self.c.state().pointer;
        {
            let mut s = self.s.borrow_mut();
            s.cursor = Some(pt);
            s.key_pointer = Some(pointer); // the mouse takes the cursor back only when it really moves
        }
        self.c.overlay(|o| {
            o.cursor = Some(pt);
            o.marker = Some((pt, MarkerKind::End));
        });
        self.readout(&readout_xz(pt));
        self.follow(pt);
        if start {
            self.line_tool();
            self.finish_chain();
            self.s.borrow_mut().points.push(pt);
            self.done.set_sensitive(true);
            self.c.overlay(|o| o.marker = Some((pt, MarkerKind::End)));
        }
        self.c.grab_focus();
        self.c.queue_draw();
    }

    /// The view follows the ALPHA-T cursor: a key that moves it past the edge pans so it stays 10 % inside
    /// (POINTER_KEYBOARD.md 3; ALPHA-T users steer the cursor, not the view).
    fn follow(&self, pt: Point) {
        let (w, h) = (self.c.width() as f64, self.c.height() as f64);
        let (sx, sy) = self.c.state().view.to_screen(pt.x, pt.z);
        let inside = |v: f64, size: f64| {
            let m = 0.1 * size;
            if v < m { m - v } else if v > size - m { size - m - v } else { 0.0 }
        };
        let (dx, dy) = (inside(sx, w), inside(sy, h));
        if (dx != 0.0 || dy != 0.0) && w > 0.0 && h > 0.0 {
            self.c.state_mut().view.pan(dx, dy);
            self.c.user_view_change();
        }
    }

    /// Ctrl+arrows pan the view by an eighth of its size without moving the cursor (POINTER_KEYBOARD.md 3).
    fn pan_key(&self, keyval: gdk::Key) -> bool {
        let (w, h) = (self.c.width() as f64 / 8.0, self.c.height() as f64 / 8.0);
        let (dx, dy) = match keyval {
            gdk::Key::Left | gdk::Key::KP_Left => (w, 0.0),
            gdk::Key::Right | gdk::Key::KP_Right => (-w, 0.0),
            gdk::Key::Up | gdk::Key::KP_Up => (0.0, h),
            gdk::Key::Down | gdk::Key::KP_Down => (0.0, -h),
            _ => return false,
        };
        self.c.state_mut().view.pan(dx, dy);
        self.c.user_view_change();
        true
    }

    /// Enter on the drawing: the cursor is the next measuring point or the next point of the line or arc; with
    /// Fillet or Chamfer it picks the corner nearest the cursor, with Corner the nearest line.
    fn accept_cursor(&self) -> bool {
        let (cursor, measuring) = {
            let s = self.s.borrow();
            (s.cursor, s.measure.is_some())
        };
        let Some(cursor) = cursor else { return false };
        let tool = self.tool();
        if self.s.borrow().construct.is_some() {
            self.construct_key(cursor);
        } else if measuring {
            self.measure_point(cursor);
        } else if tool == "line" {
            self.add_point(cursor);
        } else if tool == "arc" {
            self.arc_point(cursor);
        } else if tool == "fillet" || tool == "chamfer" {
            match self.corner_near(cursor, f64::INFINITY) {
                None => self.toast(&gettext("No corner between two lines")),
                Some(corner) => self.size_popover(&tool, corner),
            }
        } else if tool == "corner" {
            match self.project().and_then(|p| drawing::nearest_element(&p, cursor, Some(Kind::Line))) {
                None => self.toast(&gettext("No line to find")),
                Some(gid) => self.corner_pick(&gid),
            }
        } else {
            return false;
        }
        true
    }

    fn line_tool(&self) {
        let tool = self.tool();
        if tool != "line" && tool != "arc" {
            self.set_tool("line");
        }
    }

    /// Focus the command entry for a typed point; `mode` says what Enter does with it.
    fn ask(&self, mode: EntryMode, text: &str) {
        if matches!(mode, EntryMode::Point | EntryMode::LineLa) {
            self.line_tool();
        }
        self.s.borrow_mut().entry_mode = mode;
        self.entry.set_placeholder_text(Some(&gettext(mode.hint())));
        self.entry.grab_focus();
        self.entry.set_text(text);
        self.entry.set_position(-1);
    }

    // --- measure (ALPHA-T M, N) -------------------------------------------------------------------------

    fn start_measure(&self, kind: Measure) {
        self.s.borrow_mut().measure = Some((kind, None));
        self.readout(&gettext("Measure: tap the 1st point, or move the cursor and press Enter"));
    }

    fn measure_point(&self, p: Point) {
        let pt = self.project().and_then(|q| drawing::nearest_point(&q, p)).unwrap_or(p); // ALPHA-T: the point nearest
        let Some((kind, first)) = self.s.borrow().measure else { return };
        self.c.overlay(|o| o.marker = Some((pt, MarkerKind::End)));
        let Some(first) = first else {
            self.s.borrow_mut().measure = Some((kind, Some(pt)));
            self.readout(&gettext("Measure: now the 2nd point"));
            return;
        };
        self.s.borrow_mut().measure = None;
        let text = match kind {
            Measure::Xz => {
                let (dx, dz) = drawing::measure_xz(first, pt);
                format!("X {}  Z {}", fmt_g(dx), fmt_g(dz))
            }
            Measure::La => {
                let (length, angle) = drawing::measure_la(first, pt);
                format!("L {}  A {}°", fmt_g4(length), fmt_g4(angle))
            }
        };
        self.readout(&text);
        self.toast(&text);
    }

    // --- line tool --------------------------------------------------------------------------------------

    fn add_point(&self, pt: Point) {
        let Some(project) = self.project() else { return };
        let mut pt = pt;
        let (prev, first, n) = {
            let s = self.s.borrow();
            (s.points.last().copied(), s.points.first().copied(), s.points.len())
        };
        if let Some(prev) = prev {
            if !drawing::endpoints(&project).contains(&pt) {
                pt = drawing::lock_direction(prev, pt, SNAP_LOCK_DEG);
            }
            if pt == prev {
                return;
            }
            if !self.edit(&gettext("Draw line"), |p| drawing::add_line(p, prev, pt).map(|_| ())) {
                return;
            }
            if n >= 2 && Some(pt) == first {
                // tapped the first point: the profile is closed
                self.finish_chain();
                self.toast(&gettext("Profile closed"));
                return;
            }
        }
        {
            let mut s = self.s.borrow_mut();
            s.points.push(pt);
            s.cursor = Some(pt);
        }
        self.done.set_sensitive(true);
        self.c.overlay(|o| o.marker = Some((pt, MarkerKind::End)));
        self.place_chip();
    }

    /// Arc tool: start, a point on the arc, end (TOUCH.md 5.2). Exact points can be typed as for lines.
    fn arc_point(&self, pt: Point) {
        let n = {
            let mut s = self.s.borrow_mut();
            s.arc_pts.push(pt);
            s.arc_pts.len()
        };
        self.done.set_sensitive(true); // Done / Esc drops a half-drawn arc
        self.c.overlay(|o| o.marker = Some((pt, MarkerKind::End)));
        if n < 3 {
            let hints = ["Arc: now a point on the arc", "Arc: now the end point"];
            self.readout(&gettext(hints[n - 1]));
            return;
        }
        let pts = std::mem::take(&mut self.s.borrow_mut().arc_pts);
        let (a, m, b) = (pts[0], pts[1], pts[2]);
        self.edit(&gettext("Draw arc"), |p| drawing::add_arc_3p(p, a, m, b).map(|_| ()));
    }

    /// Corner tool: tap two lines; both are trimmed or extended to meet.
    fn corner_pick(&self, element: &str) {
        if element.is_empty() {
            return;
        }
        let first = self.s.borrow().corner_first.clone();
        match first {
            Some(first) if first != element => {
                self.s.borrow_mut().corner_first = None;
                self.c.overlay(|o| o.highlight.clear());
                let second = element.to_string();
                self.edit(&gettext("Corner"), |p| drawing::corner(p, &first, &second).map(|_| ()));
            }
            _ => {
                self.s.borrow_mut().corner_first = Some(element.into());
                self.c.overlay(|o| o.highlight = [element.to_string()].into());
                self.readout(&gettext("Corner: now tap the second line"));
            }
        }
    }

    /// After undo / redo: a line being drawn continues from a point that still exists (Ctrl+Z of the last line goes
    /// back to its start), and a selected element that is gone is deselected.
    pub fn after_history(&self) {
        let Some(project) = self.project() else { return };
        let ends = drawing::endpoints(&project);
        let (last, selected) = {
            let mut s = self.s.borrow_mut();
            while s.points.len() > 1 && !ends.contains(s.points.last().expect("points")) {
                s.points.pop();
            }
            if s.points.last().is_some_and(|p| !ends.contains(p)) && !project.geometry.is_empty() {
                s.points.clear();
            }
            if let Some(&last) = s.points.last() {
                s.cursor = Some(last);
            }
            (s.points.last().copied(), s.selected.clone())
        };
        if let Some(last) = last {
            self.c.overlay(|o| o.marker = Some((last, MarkerKind::End)));
        }
        self.done.set_sensitive(last.is_some());
        if selected.is_some_and(|g| element(&project, &g).is_none()) {
            self.select_element_(None);
        }
        self.c.queue_draw();
    }

    /// end the line being drawn
    pub fn finish_chain(&self) {
        self.chip.set_visible(false);
        {
            let mut s = self.s.borrow_mut();
            s.arc_pts.clear();
            s.corner_first = None;
            s.points.clear();
        }
        self.done.set_sensitive(false);
        self.c.overlay(|o| {
            o.rubber = None;
            o.marker = None;
        });
    }

    fn ask_done(&self) {
        self.s.borrow_mut().entry_mode = EntryMode::Point;
        self.entry.set_placeholder_text(Some(&self.tool_hint()));
    }

    fn entry_activate(&self) {
        let mode = self.s.borrow().entry_mode;
        let parsed = parse_coord(&self.entry.text());
        let Some((rel, mut x, mut z)) = parsed.filter(|(rel, _, _)| !rel || mode == EntryMode::Point) else {
            self.toast(&gettext(if mode == EntryMode::Point {
                "Type X,Z (diameter, Z) or @dX,dZ"
            } else {
                mode.hint()
            }));
            return;
        };
        if mode != EntryMode::Point {
            self.ask_done();
            self.entry.set_text("");
            let (last, cursor) = {
                let s = self.s.borrow();
                (s.points.last().copied(), s.cursor)
            };
            if mode == EntryMode::LineLa {
                // ALPHA-T D: a line of that length and angle from the last point (or the cursor)
                let here = last.or(cursor).unwrap_or_default();
                self.line_tool();
                if self.s.borrow().points.is_empty() {
                    self.s.borrow_mut().points.push(here);
                }
                self.add_point_exact(drawing::polar(here, x, z));
                return;
            }
            let cur = cursor.unwrap_or_default();
            let target = match mode {
                EntryMode::MoveAbs => Point::new(x, z),
                EntryMode::MoveInc => Point::new(round(cur.x + x, 6), round(cur.z + z, 6)),
                _ => drawing::polar(cur, x, z),
            };
            self.move_cursor(target, self.tool() == "line");
            return;
        }
        let arc = self.tool() == "arc";
        let start = if arc || !self.s.borrow().points.is_empty() { None } else { self.s.borrow_mut().start_at.take() };
        if let Some(start) = start {
            self.line_tool();
            self.s.borrow_mut().points.push(start);
        }
        if rel {
            let last = {
                let s = self.s.borrow();
                if arc && !s.arc_pts.is_empty() { s.arc_pts.last().copied() } else { s.points.last().copied() }
            };
            let Some(prev) = last else {
                self.toast(&gettext("@ needs a previous point"));
                return;
            };
            (x, z) = (round(prev.x + x, 6), round(prev.z + z, 6)); // no float noise (-34.800000000000004)
        }
        if arc {
            self.arc_point(Point::new(x, z));
            self.entry.set_text("");
            return;
        }
        if self.tool() != "line" {
            self.set_tool("line");
        }
        self.add_point_exact(Point::new(x, z));
        self.entry.set_text("");
    }

    /// Next point of the line, typed: no snapping, no direction lock.
    fn add_point_exact(&self, pt: Point) {
        let prev = self.s.borrow().points.last().copied();
        if let Some(prev) = prev {
            if pt == prev {
                return;
            }
            if !self.edit(&gettext("Draw line"), |p| drawing::add_line(p, prev, pt).map(|_| ())) {
                return;
            }
        }
        {
            let mut s = self.s.borrow_mut();
            s.points.push(pt);
            s.cursor = Some(pt);
        }
        self.done.set_sensitive(true);
        self.c.overlay(|o| o.marker = Some((pt, MarkerKind::End)));
    }

    fn open_keypad(&self) {
        let title = gettext(if self.s.borrow().points.is_empty() { "Start Point" } else { "Next Point" });
        let this = self.rc();
        crate::keypad::keypad(&self.w(), Some(&title), &[], &[], weak!(this, move |text: String| {
            this.entry.set_text(&text);
            this.entry_activate();
        }));
    }

    /// Touch mode: the keypad replaces the system keyboard for points (TOUCH.md 6). The pen gets the keypad and the
    /// value chip too, but keeps the pointer's handles (POINTER_KEYBOARD.md 10).
    pub fn set_touch(&self, touch: bool, pen: bool) {
        self.keypad_button.set_visible(touch || pen);
        self.c.state_mut().handle_px = if touch { 12.0 } else { 6.0 };
        self.s.borrow_mut().touch = touch || pen;
        self.place_chip();
    }

    // --- value chip (TOUCH.md 5.1: place roughly, then correct exactly) ---------------------------------

    /// In touch mode, a chip beside the last point of the line being drawn shows it exactly (Z for a step along Z,
    /// Ø for a face, both for a taper); tapping it opens the keypad on that point. It sits left of the point (right
    /// when there is no room) and moves with it; off the screen it hides. (It used to sit mid-segment and stick to
    /// the canvas edge when that was off-screen, so it jumped about while the drawing panned.)
    fn place_chip(&self) {
        let (a, b) = {
            let s = self.s.borrow();
            let n = s.points.len();
            if !s.touch || n < 2 || self.c.state().tool != "line" {
                drop(s);
                self.chip.set_visible(false);
                return;
            }
            (s.points[n - 2], s.points[n - 1])
        };
        let text = if a.x == b.x {
            format!("Z {}", fmt_g(b.z))
        } else if a.z == b.z {
            format!("Ø {}", fmt_g(b.x))
        } else {
            format!("X{} Z{}", fmt_g(b.x), fmt_g(b.z))
        };
        self.chip.set_label(&text);
        let (sx, sy) = self.c.state().view.to_screen(b.x, b.z);
        let (cw, ch) = (self.c.width() as f64, self.c.height() as f64);
        if !((0.0..=cw).contains(&sx) && (0.0..=ch).contains(&sy)) {
            self.chip.set_visible(false);
            return;
        }
        // a hidden widget measures 0 x 0, and a measure includes the margins that place the chip: show it first and
        // take the margins off, or it lands on the point, or (after a zoom) far from it
        self.chip.set_visible(true);
        let w = (self.chip.measure(gtk::Orientation::Horizontal, -1).1 - self.chip.margin_start()) as f64;
        let h = (self.chip.measure(gtk::Orientation::Vertical, -1).1 - self.chip.margin_top()) as f64;
        let (x, y) = if sx - w - CHIP_GAP_PX >= 4.0 {
            (sx - w - CHIP_GAP_PX, sy - h / 2.0) // left of the point (its own label is drawn up and to the right)
        } else {
            (sx + CHIP_GAP_PX, sy + CHIP_GAP_PX) // no room: below and to the right, clear of that label
        };
        let x = x.min((cw - w - 4.0).max(4.0));
        let y = y.max(4.0).min((ch - h - 4.0).max(4.0));
        self.chip.set_margin_start(x as i32);
        self.chip.set_margin_top(y as i32);
    }

    fn correct_last_point(&self) {
        let (prev, last) = {
            let s = self.s.borrow();
            let n = s.points.len();
            if n < 2 {
                return;
            }
            (s.points[n - 2], s.points[n - 1])
        };
        let this = self.rc();
        let done = weak!(this, move |text: String| {
            let Some((rel, x, z)) = parse_coord(&text) else { return };
            let new = if rel { Point::new(prev.x + x, prev.z + z) } else { Point::new(x, z) };
            if new == prev || new == last {
                return;
            }
            let Some(project) = this.project() else { return };
            let gid = project
                .geometry
                .iter()
                .find(|g| matches!(g, Element::Line(l) if l.a == prev && l.b == last))
                .map(|g| g.id().to_string());
            if let Some(gid) = gid
                && this.edit(&gettext("Correct point"), |p| drawing::edit_element(p, &gid, prev, new, None, false))
            {
                {
                    let mut s = this.s.borrow_mut();
                    if let Some(p) = s.points.last_mut() {
                        *p = new;
                    }
                    s.cursor = Some(new);
                }
                this.c.overlay(|o| o.marker = Some((new, MarkerKind::End)));
                this.place_chip();
            }
        });
        crate::keypad::keypad(
            &self.w(),
            Some(&gettext("Correct Point")),
            &[("x", fmt_g(last.x)), ("z", fmt_g(last.z))],
            &[],
            done,
        );
    }

    fn element_label(&self, gid: &str) -> String {
        let Some(project) = self.project() else { return gid.into() };
        let Some(g) = element(&project, gid) else { return gid.into() };
        let (a, b) = (g.a(), g.b());
        if let Some(r) = g.radius() {
            return tr("Arc R{r}", &[("r", &fmt_g(r))]);
        }
        if a.x == b.x {
            return tr("Line Ø{x}", &[("x", &fmt_g(a.x))]);
        }
        if a.z == b.z {
            return tr("Line Z{z}", &[("z", &fmt_g(a.z))]);
        }
        tr(
            "Line X{x1} Z{z1} → X{x2} Z{z2}",
            &[("x1", &fmt_g(a.x)), ("z1", &fmt_g(a.z)), ("x2", &fmt_g(b.x)), ("z2", &fmt_g(b.z))],
        )
    }

    fn zoom_here(&self) {
        let (sx, sy) = self.s.borrow().ctx_at;
        self.c.state_mut().view.zoom_at(sx, sy, 2.0);
        self.c.user_view_change();
    }

    // --- select / delete --------------------------------------------------------------------------------

    fn select_element_(&self, gid: Option<String>) {
        self.s.borrow_mut().selected = gid.clone();
        let project = self.project();
        let g = gid.as_deref().zip(project.as_deref()).and_then(|(id, p)| element(p, id)).cloned();
        self.c.overlay(|o| {
            o.highlight = gid.iter().cloned().collect();
            o.handles = g.as_ref().map(|g| vec![g.a(), g.b()]).unwrap_or_default();
        });
        self.delete_el.set_visible(gid.is_some());
        self.edit_el.set_visible(gid.is_some());
        let readout = self.w().readout();
        if gid.is_none() && !readout.label().starts_with("X ") {
            readout.set_label(" "); // not the last selected element any more (a position stays)
        }
        if let (Some(gid), Some(g)) = (gid, g) {
            let kind = gettext(if g.is_arc() { "Arc" } else { "Line" });
            let (a, b) = (g.a(), g.b());
            readout.set_label(&format!(
                "{gid} {kind}  X{} Z{} → X{} Z{}",
                fmt_g(a.x),
                fmt_g(a.z),
                fmt_g(b.x),
                fmt_g(b.z)
            ));
            self.c.grab_focus();
        }
    }

    fn delete_selected_element(&self) {
        let Some(gid) = self.s.borrow().selected.clone() else { return };
        let w = self.w();
        if w.apply_edit(&tr("Delete {what}", &[("what", &gid)]), |p| drawing::delete_element(p, &gid)) {
            self.select_element_(None);
            w.refresh(false);
            w.add_toast(
                adw::Toast::builder()
                    .title(tr("Deleted {what}", &[("what", &gid)]))
                    .button_label(gettext("_Undo"))
                    .action_name("win.undo")
                    .timeout(6)
                    .build(),
            );
        }
    }

    // --- fillet / chamfer -------------------------------------------------------------------------------

    fn corner_near(&self, p: Point, tol: f64) -> Option<Point> {
        let project = self.project()?;
        let (mut best, mut best_d) = (None, tol.max(1e-9));
        for q in drawing::endpoints(&project) {
            let lines = project.geometry.iter().filter(|g| !g.is_arc() && (g.a() == q || g.b() == q)).count();
            if lines == 2 && p.distance(&q) <= best_d {
                (best, best_d) = (Some(q), p.distance(&q));
            }
        }
        best
    }

    fn apply_corner(&self, tool: &str, corner: Point, size: f64, angle: f64) {
        if tool == "fillet" {
            self.edit(&tr("Fillet R{size}", &[("size", &fmt_g(size))]), |p| {
                drawing::fillet(p, corner, size).map(|_| ())
            });
        } else {
            let label = tr("Chamfer {size}", &[("size", &format!("{}×{}°", fmt_g(size), fmt_g(angle)))]);
            self.edit(&label, |p| drawing::chamfer(p, corner, size, angle).map(|_| ()));
        }
    }

    // --- pick contour (TOUCH.md 7.1) --------------------------------------------------------------------

    /// pick the contour of operation n on the drawing
    pub fn start_pick(&self, op_index: usize) {
        self.finish_chain();
        self.s.borrow_mut().pick_op = Some(op_index);
        self.c.overlay(|o| o.picked.clear());
        let w = self.w();
        w.stack().set_visible_child_name("part");
        self.pick_banner.set_revealed(true);
        self.cancel_pick_btn.set_visible(true);
        w.layout_editor(); // a small window shows the drawing while picking
        self.c.grab_focus(); // Space, [ ] and Enter pick from the keyboard (POINTER_KEYBOARD.md 5)
    }

    /// `[` / `]`: the previous / next element along the profile, in the contour order the operations use
    /// (POINTER_KEYBOARD.md 5); while picking a contour it moves the element Space adds.
    fn walk_profile(&self, step: i32, picking: bool) -> bool {
        let Some(project) = self.project() else { return false };
        let (now, cur) = {
            let s = self.s.borrow();
            (if picking { s.pick_at.clone() } else { s.selected.clone() }, s.cursor)
        };
        // nothing current yet: the first press takes the element nearest the cursor (or the profile's first)
        let first = now.is_none();
        let now = now.or_else(|| cur.and_then(|c| drawing::nearest_element(&project, c, None)));
        let groups = drawing::components(&project);
        let group = match &now {
            Some(gid) => groups.into_iter().find(|g| g.contains(gid)),
            None => Some(drawing::outer_profile(&project)),
        };
        let Some(group) = group.filter(|g| !g.is_empty()) else { return false };
        let order = drawing::pick_order(&project, &group).unwrap_or(group);
        let next = match now.as_ref().and_then(|g| order.iter().position(|x| x == g)) {
            Some(i) if !first => order[(i as i32 + step).rem_euclid(order.len() as i32) as usize].clone(),
            Some(i) => order[i].clone(),
            None => order[0].clone(),
        };
        if picking {
            self.set_pick_at(Some(next));
        } else {
            self.set_tool("select");
            self.select_element_(Some(next));
        }
        true
    }

    /// The element Space adds or removes while picking, outlined and named in the read-out.
    fn set_pick_at(&self, gid: Option<String>) {
        self.s.borrow_mut().pick_at = gid.clone();
        self.c.overlay(|o| o.highlight = gid.iter().cloned().collect());
        if let Some(gid) = gid {
            self.readout(&format!("{gid} {}", self.element_label(&gid)));
        }
    }

    /// Space while picking: add or remove the element [ ] l a moved to, or else the one nearest the cursor.
    fn pick_key(&self) -> bool {
        let (at, cur) = {
            let s = self.s.borrow();
            (s.pick_at.clone(), s.cursor)
        };
        let gid = at.or_else(|| self.project().and_then(|p| drawing::nearest_element(&p, cur.unwrap_or_default(), None)));
        match gid {
            Some(gid) => {
                self.toggle_pick(&gid);
                self.set_pick_at(Some(gid));
                true
            }
            None => false,
        }
    }

    fn toggle_pick(&self, gid: &str) {
        if gid.is_empty() {
            return;
        }
        let n = {
            let mut s = self.c.state_mut();
            let picked = &mut s.overlay.picked;
            match picked.iter().position(|g| g == gid) {
                Some(i) => {
                    picked.remove(i);
                }
                None => picked.push(gid.into()),
            }
            picked.len()
        };
        let text = "{n} picked — tap again to remove, then Done";
        self.pick_banner.set_title(&fill(&ngettext(text, text, n as u32), &[("n", &n)]));
        self.c.queue_draw();
    }

    fn finish_pick(&self) {
        let i = self.s.borrow().pick_op;
        let picked = self.c.state().overlay.picked.clone();
        self.cancel_pick();
        let (Some(i), false) = (i, picked.is_empty()) else { return };
        let Some(project) = self.project() else { return };
        let order = match drawing::pick_order(&project, &picked) {
            Ok(order) => order,
            Err(e) => {
                self.toast(&e.to_string());
                return;
            }
        };
        if self.edit(&gettext("Pick contour"), move |p| {
            if let Some(contour) = p.operations.get_mut(i).and_then(drawing::contour_mut) {
                *contour = order;
            }
            Ok(())
        }) {
            self.w().select_op(Some(i));
        }
    }

    fn cancel_pick(&self) {
        self.s.borrow_mut().pick_op = None;
        self.set_pick_at(None);
        self.c.overlay(|o| o.picked.clear());
        self.pick_banner.set_revealed(false);
        self.cancel_pick_btn.set_visible(false);
        self.pick_banner.set_title(&gettext("Tap the contour elements in cutting order"));
        self.w().layout_editor();
    }
}
