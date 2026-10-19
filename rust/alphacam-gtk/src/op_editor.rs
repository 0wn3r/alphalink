//! Operation editor (op_editor.py, ui/op-editor.blp; TOUCH.md 7.1): rows generated from the operation's fields.
//!
//! Every change goes through the window's UndoStack as one undoable step, and the preview regenerates. The fields
//! are read and written through the core's .alcam serde (the operation as its JSON object), so each operation type
//! gets its rows in its field order without a list of fields per type here.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use adw::prelude::*;
use adw::subclass::prelude::*;
use alphacam::error::Error;
use alphacam::model::{Element, Operation, Project};
use alphacam::py::{fmt_g, round, round_int};
use alphacam::toolpath::contour::chain;
use gtk::{gdk, glib};
use serde_json::{Value, json};

use crate::i18n::{fill, gettext, n_, ngettext, tr};

/// (label, subtitle, lower, upper, step, digits) of a number field; a subtitle fits one line of the editor pane
type NumberSpec = (&'static str, Option<&'static str>, f64, f64, f64, u32);

const NUMBERS: &[(&str, NumberSpec)] = &[
    ("tool", ("Tool", Some(n_!("Tool memory slot")), 1.0, 99.0, 1.0, 0)),
    ("feed", ("Feed", Some("Per revolution"), 0.001, 5.0, 0.01, 3)),
    ("depth_of_cut", ("Depth of cut", Some("Per pass, radius"), 0.01, 50.0, 0.1, 3)),
    ("offset_x", ("Offset X", Some(n_!("Allowance, radius")), -10.0, 10.0, 0.05, 3)),
    ("offset_z", ("Offset Z", Some(n_!("Allowance")), -10.0, 10.0, 0.05, 3)),
    ("finish_offset_x", ("Finish offset X", None, -10.0, 10.0, 0.05, 3)),
    ("finish_offset_z", ("Finish offset Z", None, -10.0, 10.0, 0.05, 3)),
    ("peck_depth", ("Peck depth", Some(n_!("0 = one plunge")), 0.0, 50.0, 0.5, 3)),
    ("stepover", ("Stepover", None, 0.01, 50.0, 0.1, 3)),
    ("tip_width", ("Tip width", None, 0.01, 50.0, 0.1, 3)),
    ("z_shift", ("Z shift", None, -500.0, 500.0, 1.0, 3)),
    ("repeat", ("Repeats", None, 0.0, 50.0, 1.0, 0)),
    ("dwell_revs", ("Dwell", Some("Revolutions"), 0.0, 100.0, 1.0, 1)),
    ("depth", ("Hole bottom Z", Some("Absolute"), -1000.0, 0.0, 1.0, 3)),
    ("peck", ("Peck", Some("0 = no pecking"), 0.0, 100.0, 1.0, 3)),
    ("pitch", ("Pitch", None, 0.01, 20.0, 0.05, 3)),
    ("cut_depth", ("First cut depth", Some(n_!("Radius; then × √n")), 0.01, 5.0, 0.05, 3)),
    ("cuts", ("Number of cuts", Some(n_!("0 = from the depth")), 0.0, 500.0, 1.0, 0)),
    ("lead_in", ("Lead in", None, 0.0, 50.0, 0.5, 3)),
    ("retract", ("Retract distance", None, 0.01, 50.0, 0.1, 3)),
    ("lead_out", ("Lead out", None, 0.0, 50.0, 0.5, 3)),
    ("spring_passes", ("Spring passes", Some(n_!("At the final diameter")), 0.0, 9.0, 1.0, 0)),
    ("angle", ("Infeed angle", Some(n_!("0° = straight in")), 0.0, 60.0, 1.0, 1)),
    ("revs", ("Dwell", Some("Revolutions"), 0.1, 100.0, 1.0, 1)),
];

/// field, (label, subtitle)
const SWITCHES: &[(&str, (&str, Option<&str>))] = &[
    ("external", ("External", Some(n_!("Off = boring")))),
    ("towards", ("Towards the chuck", None)),
    ("pre_finish", ("Pre-finish pass", Some(n_!("After the roughing")))),
    ("full_retract", ("Full retract", None)),
    ("front", ("Front", Some(n_!("Off = facing")))),
    ("follow", ("Follow contour", Some(n_!("Cleans up each step")))),
    ("face", ("Face", Some(n_!("Pulls off to the axis")))),
    ("rapid", ("Rapid", Some("Off = feed move"))),
];

/// field, (label, choices); a choice's label is its translated value ("left", "right", "area", "finish")
const CHOICES: &[(&str, (&str, &[&str]))] = &[("datum", ("Datum", &["left", "right"])), ("mode", ("Mode", &["area", "finish"]))];

const POINTS: &[(&str, &str)] = &[("start", "Tool start"), ("change_point", "Tool change point"), ("point", "Move to")];

fn spec<T: Copy>(table: &[(&str, T)], name: &str) -> Option<T> {
    table.iter().find(|(n, _)| *n == name).map(|(_, s)| *s)
}

/// Reserve room for the widest value, so the number is never clipped when a long (e.g. Ukrainian) title takes the
/// row's width: "120.00(" instead of "120.000".
pub fn fit_spin(row: &adw::SpinRow, lo: f64, hi: f64, digits: u32) {
    let d = digits as usize;
    let chars = format!("{lo:.d$}").len().max(format!("{hi:.d$}").len()) as i32;
    let mut stack: Vec<gtk::Widget> = vec![row.clone().upcast()];
    while let Some(w) = stack.pop() {
        if let Some(spin) = w.downcast_ref::<gtk::SpinButton>() {
            spin.set_width_chars(chars);
            spin.set_max_width_chars(chars);
            break;
        }
        let mut c = w.first_child();
        while let Some(child) = c {
            c = child.next_sibling();
            stack.push(child);
        }
    }
}

/// a finger that travels further than this is scrolling
const TAP_SLOP_PX: f64 = 12.0;

#[derive(Clone, Copy, PartialEq)]
enum Touched {
    Up,
    Down,
    Number,
}

#[derive(Default)]
struct Touch {
    what: Option<Touched>,
    moved: bool,
    start: f64,
    adj: Option<gtk::Adjustment>,
    scroller: Option<gtk::ScrolledWindow>,
    /// where the finger went down, in the scroller's coordinates
    y0: f64,
    /// the press and the last moves (event time ms, 0, finger y) for the fling at the lift
    samples: std::collections::VecDeque<(u32, f64, f64)>,
}

thread_local! {
    /// the tick of the editor's fling after a swipe from a number
    static FLING: RefCell<Option<gtk::TickCallbackId>> = const { RefCell::new(None) };
}

fn stop_fling() {
    if let Some(id) = FLING.with_borrow_mut(Option::take) {
        id.remove();
    }
}

/// A swipe from a number goes on after the lift like one from a title, which GTK's ScrolledWindow flings: the
/// canvas's fling (its speed over the last moves, 0.92 per frame, none after a rest) on the editor's scrolling, so it
/// no longer stopped dead at the lift (Eduard's recorded swipes, 2026-10-19). `speed` is px/s, down the content.
fn fling(scroller: &gtk::ScrolledWindow, speed: f64) {
    use crate::canvas::input::{FLING_DECAY, FLING_MIN};
    stop_fling();
    if speed.abs() < FLING_MIN {
        return;
    }
    let adj = scroller.vadjustment();
    let mine = Cell::new(adj.value());
    let state = Cell::new((speed, None::<i64>));
    let id = scroller.add_tick_callback(move |s, clock| {
        let adj = s.vadjustment();
        let (mut v, last) = state.get();
        let now = clock.frame_time();
        // anything else that scrolls the editor (GTK's own drag from a title, the keyboard) ends the fling
        if (adj.value() - mine.get()).abs() > 0.5 {
            FLING.with_borrow_mut(|f| f.take());
            return glib::ControlFlow::Break;
        }
        if let Some(t) = last {
            let dt = (now - t) as f64 / 1e6;
            let top = adj.upper() - adj.page_size();
            let value = (adj.value() + v * dt).max(adj.lower()).min(top);
            adj.set_value(value);
            mine.set(adj.value());
            v *= FLING_DECAY.powf(dt / 0.016);
            if v.abs() < FLING_MIN || value <= adj.lower() || value >= top {
                FLING.with_borrow_mut(|f| f.take());
                return glib::ControlFlow::Break;
            }
        }
        state.set((v, Some(now)));
        glib::ControlFlow::Continue
    });
    FLING.with_borrow_mut(|f| *f = Some(id));
}

/// The finger's height in the scroller's own coordinates. The drag's offsets are in the row's, and the row moves as
/// the editor scrolls under the finger: scrolling by them fed back, so the editor followed the finger about half
/// as far and in jumps (Eduard's video, 2026-10-19).
fn finger_y(gesture: &gtk::GestureDrag, scroller: &gtk::ScrolledWindow) -> Option<f64> {
    let row = gesture.widget()?;
    let (x, y) = gesture.point(gesture.current_sequence().as_ref())?;
    row.compute_point(scroller, &gtk::graphene::Point::new(x as f32, y as f32)).map(|p| p.y() as f64)
}

/// A finger on the number opens the AlphaLink keypad (TOUCH.md 6) instead of the system keyboard, which covers half
/// the screen; a finger on − or + steps the value. Both only for a still tap, on release: a finger that moves scrolls
/// the editor instead (Eduard 2026-10-16: swiping through the parameters kept opening the keypad and changing
/// values). The touch is taken on press (no focus, so no system keyboard), so the scrolling is done here. The mouse
/// and keyboard work as before. `row` is an adw::SpinRow or a gtk::SpinButton (POINTER_KEYBOARD.md 11: every number
/// field a finger can reach); a second call on the same widget does nothing.
pub fn keypad_on_touch(row: &impl IsA<gtk::Widget>) {
    let row = row.upcast_ref::<gtk::Widget>();
    if row.has_css_class("keypad-on-touch") {
        return;
    }
    row.add_css_class("keypad-on-touch");
    let drag = gtk::GestureDrag::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
    let state = Rc::new(RefCell::new(Touch::default()));
    let st = state.clone();
    drag.connect_drag_begin(move |gesture, x, y| {
        // a finger or the pen, which cannot type either (POINTER_KEYBOARD.md 10); the mouse works as before
        let source = crate::input_mode::event_source(gesture);
        if !matches!(source, Some(gdk::InputSource::Touchscreen | gdk::InputSource::Pen)) {
            gesture.set_state(gtk::EventSequenceState::Denied);
            return;
        }
        let Some(row) = gesture.widget() else { return };
        let mut w = row.pick(x, y, gtk::PickFlags::DEFAULT);
        let mut what = None;
        while let Some(widget) = w.filter(|w| *w != row) {
            if let Some(b) = widget.downcast_ref::<gtk::Button>() {
                what = if b.has_css_class("up") {
                    Some(Touched::Up)
                } else if b.has_css_class("down") {
                    Some(Touched::Down)
                } else {
                    None
                };
                break;
            }
            if widget.is::<gtk::Text>() {
                what = Some(Touched::Number);
                break;
            }
            w = widget.parent();
        }
        if what.is_none() {
            return;
        }
        let scroller = row.ancestor(gtk::ScrolledWindow::static_type()).and_downcast::<gtk::ScrolledWindow>();
        let adj = scroller.as_ref().map(|s| s.vadjustment());
        let start = adj.as_ref().map_or(0.0, |a| a.value());
        let y0 = scroller.as_ref().and_then(|s| finger_y(gesture, s)).unwrap_or(0.0);
        stop_fling(); // a finger that lands on the editor holds it, as on a title
        let samples = [(gesture.current_event_time(), 0.0, y0)].into();
        *st.borrow_mut() = Touch { what, moved: false, start, adj, scroller, y0, samples };
        gesture.set_state(gtk::EventSequenceState::Claimed);
    });
    let st = state.clone();
    drag.connect_drag_update(move |gesture, dx, dy| {
        let mut s = st.borrow_mut();
        if s.what.is_none() {
            return;
        }
        // before any scrolling the row has not moved, so its offsets measure the travel
        if !s.moved && dx.abs() + dy.abs() > TAP_SLOP_PX {
            s.moved = true;
        }
        let (Some(adj), Some(scroller)) = (&s.adj, &s.scroller) else { return };
        let Some(y) = finger_y(gesture, scroller) else { return };
        if s.moved {
            let top = adj.upper() - adj.page_size();
            adj.set_value((s.start + s.y0 - y).max(adj.lower()).min(top));
        }
        s.samples.push_back((gesture.current_event_time(), 0.0, y));
        if s.samples.len() > crate::canvas::input::VELOCITY_MOVES + 1 {
            s.samples.remove(1); // the press stays first
        }
    });
    drag.connect_drag_end(move |gesture, _, _| {
        let (what, moved) = {
            let mut s = state.borrow_mut();
            let (what, moved) = (s.what.take(), s.moved);
            if let (true, true, Some(scroller)) = (what.is_some(), moved, s.scroller.take()) {
                let time = gesture.current_event_time();
                let last = s.samples.back().map(|m| m.0);
                let rested = last.is_some_and(|t| time > t && time - t > crate::canvas::input::REST_MS);
                let (_, vy) = crate::canvas::input::velocity(s.samples.make_contiguous());
                glib::g_debug!("alphalink-cam", "editor fling: {:.0} px/s{}", vy, if rested { ", rested: none" } else { "" });
                if !rested {
                    fling(&scroller, -vy);
                }
            }
            s.samples.clear();
            (what, moved)
        };
        let Some(what) = what.filter(|_| !moved) else { return };
        let Some(row) = gesture.widget() else { return };
        let Some(spin) = spin_button(&row) else { return };
        match what {
            Touched::Up | Touched::Down => {
                let step = spin.adjustment().step_increment();
                spin.set_value(spin.value() + if what == Touched::Up { step } else { -step });
            }
            Touched::Number => {
                let title = number_title(&row);
                let value = format!("{:.*}", spin.digits() as usize, spin.value());
                let weak = spin.downgrade();
                crate::keypad::keypad(&row, Some(&title), &[("v", value)], &[("v", &title)], move |text| {
                    if let (Some(spin), Ok(v)) = (weak.upgrade(), text.parse::<f64>()) {
                        spin.set_value(v);
                    }
                });
            }
        }
    });
    row.add_controller(drag);
}

/// [`keypad_on_touch`] for every spin row, and every spin button outside one, under `root`.
pub fn keypad_on_touch_all(root: &impl IsA<gtk::Widget>) {
    let mut stack: Vec<gtk::Widget> = vec![root.clone().upcast()];
    while let Some(w) = stack.pop() {
        if w.is::<adw::SpinRow>() || w.is::<gtk::SpinButton>() {
            keypad_on_touch(&w);
            continue;
        }
        let mut c = w.first_child();
        while let Some(child) = c {
            c = child.next_sibling();
            stack.push(child);
        }
    }
}

/// The spin button that holds a number field's value: the widget itself or the one inside a spin row.
fn spin_button(w: &gtk::Widget) -> Option<gtk::SpinButton> {
    let mut stack = vec![w.clone()];
    while let Some(w) = stack.pop() {
        if let Ok(spin) = w.clone().downcast::<gtk::SpinButton>() {
            return Some(spin);
        }
        let mut c = w.first_child();
        while let Some(child) = c {
            c = child.next_sibling();
            stack.push(child);
        }
    }
    None
}

/// The keypad's title: the row's title, else the dialog's.
fn number_title(w: &gtk::Widget) -> String {
    let row = w.downcast_ref::<adw::PreferencesRow>().cloned().or_else(|| w.ancestor(adw::PreferencesRow::static_type()).and_downcast());
    if let Some(row) = row.filter(|r| !r.title().is_empty()) {
        return row.title().into();
    }
    w.ancestor(adw::Dialog::static_type()).and_downcast::<adw::Dialog>().map(|d| d.title().into()).unwrap_or_default()
}

mod imp {
    use super::*;

    #[derive(Default, gtk::CompositeTemplate)]
    #[template(resource = "/io/github/_0wn3r/AlphaLinkCAM/ui/op-editor.ui")]
    pub struct OpEditor {
        #[template_child]
        pub title: TemplateChild<adw::WindowTitle>,
        #[template_child]
        pub scroll: TemplateChild<gtk::ScrolledWindow>,
        pub index: Cell<Option<usize>>,
        pub building: Cell<bool>,
        pub hooks: RefCell<Option<Rc<EditorHooks>>>,
        pub feed_row: RefCell<Option<adw::SpinRow>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for OpEditor {
        const NAME: &'static str = "AlphaLinkOpEditor";
        type Type = super::OpEditor;
        type ParentType = gtk::Box;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            super::bind_callbacks(klass);
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for OpEditor {}
    impl WidgetImpl for OpEditor {}
    impl BoxImpl for OpEditor {}
}

glib::wrapper! {
    /// Shows one operation; the window sets its hooks: `apply`, its undoable edit entry point, and the header's
    /// callbacks (ui/op-editor.blp).
    pub struct OpEditor(ObjectSubclass<imp::OpEditor>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

/// A change to the open part, made as one undoable step (the window's apply_edit).
pub type ApplyFn = Box<dyn FnOnce(&mut Project) -> alphacam::error::Result<()>>;

/// Set by the window.
pub struct EditorHooks {
    /// (undo label, change): an edit from the editor's own rows (must not rebuild the editor)
    #[allow(clippy::type_complexity)]
    pub apply: Box<dyn Fn(&str, ApplyFn)>,
    /// move the operation up (-1) or down (+1)
    pub on_move: Box<dyn Fn(i32)>,
    pub on_delete: Box<dyn Fn()>,
    pub on_close: Box<dyn Fn()>,
    /// start contour picking for operation n
    pub on_pick: Box<dyn Fn(usize)>,
    /// copy the operation below itself
    pub on_duplicate: Box<dyn Fn()>,
    /// the tool's name for its number
    pub tool_name: Box<dyn Fn(i32) -> String>,
}

/// The header's buttons. The template scope passes (emitter, signal values..., template object).
fn bind_callbacks(klass: &mut <imp::OpEditor as ObjectSubclass>::Class) {
    type Handler = (&'static str, fn(&EditorHooks));
    let handlers: [Handler; 3] = [
        ("_move_up", |h| (h.on_move)(-1)),
        ("_move_down", |h| (h.on_move)(1)),
        ("_close", |h| (h.on_close)()),
    ];
    let scope = klass.rust_template_scope();
    for (name, handler) in handlers {
        scope.add_callback(name, move |values| {
            if let Some(hooks) = values.last().and_then(|v| v.get::<OpEditor>().ok()).and_then(|ed| ed.hooks()) {
                handler(&hooks);
            }
            None
        });
    }
}

/// Set one field of the operation, through its .alcam JSON object (the field must take `value`).
fn set_field(op: &mut Operation, name: &str, value: Value) -> alphacam::error::Result<()> {
    let json_error = |e: serde_json::Error| Error::Value(e.to_string());
    let mut fields = serde_json::to_value(&*op).map_err(json_error)?;
    fields[name] = value;
    *op = serde_json::from_value(fields).map_err(json_error)?;
    Ok(())
}

/// The value the row was built with, until the user changes it.
type Orig = Rc<Cell<Option<f64>>>;

/// Only the display rounding moved the value (focus-out re-rounds to the digits shown): not an edit.
fn unchanged(row: &adw::SpinRow, orig: &Orig) -> bool {
    orig.get().is_some_and(|o| (row.value() - o).abs() <= 0.5 * 10f64.powi(-(row.digits() as i32)) + 1e-12)
}

fn spin(title: &str, subtitle: Option<&str>, lo: f64, hi: f64, step: f64, digits: u32, value: f64) -> (adw::SpinRow, Orig) {
    let row = adw::SpinRow::with_range(lo, hi, step);
    row.set_title(title);
    if let Some(s) = subtitle.filter(|s| !s.is_empty()) {
        row.set_subtitle(s);
    }
    row.set_digits(digits);
    row.set_value(value);
    keypad_on_touch(&row);
    fit_spin(&row, lo, hi, digits);
    (row, Rc::new(Cell::new(Some(value))))
}

impl OpEditor {
    pub fn set_hooks(&self, hooks: EditorHooks) {
        *self.imp().hooks.borrow_mut() = Some(Rc::new(hooks));
    }

    fn hooks(&self) -> Option<Rc<EditorHooks>> {
        self.imp().hooks.borrow().clone()
    }

    fn tool_name(&self, n: i32) -> String {
        self.hooks().map(|h| (h.tool_name)(n)).unwrap_or_default()
    }

    pub fn show_op(&self, project: &Project, index: usize, title: &str) {
        let imp = self.imp();
        imp.index.set(Some(index));
        let op = &project.operations[index];
        imp.title.set_title(title);
        imp.title.set_subtitle(&tr("Operation {n}", &[("n", &(index + 1))]));
        let page = adw::PreferencesPage::new();
        let group = adw::PreferencesGroup::new();
        page.add(&group);
        imp.building.set(true);
        imp.feed_row.replace(None);
        let inch = project.units == "inch";
        let fields = serde_json::to_value(op).expect("operations serialise");
        let rapid = fields.get("rapid").and_then(Value::as_bool).unwrap_or(false);
        for (name, value) in fields.as_object().into_iter().flatten() {
            let name = name.as_str();
            if let Some(label) = spec(POINTS, name) {
                for (axis, lab) in [(0, "X (Ø)"), (1, "Z")] {
                    let v = value.get(axis).and_then(Value::as_f64).unwrap_or_default();
                    let title = format!("{} {lab}", gettext(label));
                    let (row, orig) = spin(&title, None, -2000.0, 2000.0, 1.0, if inch { 4 } else { 3 }, v);
                    self.on_number(&row, orig, name, move |op, name, v| {
                        let mut p = serde_json::to_value(&*op).map_err(|e| Error::Value(e.to_string()))?[name].clone();
                        p[axis] = json!(round(v, 4));
                        set_field(op, name, p)
                    });
                    group.add(&row);
                }
            } else if let Some((lab, sub, lo, hi, step, mut digits)) = spec(NUMBERS, name) {
                let mut sub = sub.map(gettext);
                let mut v = value.as_f64();
                if name == "retract" && v.is_none() {
                    // ALPHA-T's default: area clear 1 mm, drilling 2 mm
                    use alphacam::toolpath::{area::RETRACT_MM, drill::PECK_RETRACT_MM};
                    let mm = if matches!(op, Operation::Drill(_)) { PECK_RETRACT_MM } else { RETRACT_MM };
                    v = Some(round(mm / if inch { 25.4 } else { 1.0 }, 4));
                }
                let v = v.unwrap_or_default();
                if name == "tool" {
                    sub = Some(self.tool_name(v as i32)).filter(|s| !s.is_empty()).or(sub);
                }
                if inch && digits == 3 {
                    digits = 4; // inch values have 4 decimals (0.0787)
                }
                let (row, orig) = spin(&gettext(lab), sub.as_deref(), lo, hi, step, digits, v);
                if name == "tool" {
                    let weak = self.downgrade();
                    row.connect_value_notify(move |r| {
                        if let Some(ed) = weak.upgrade() {
                            r.set_subtitle(&ed.tool_name(r.value() as i32));
                        }
                    });
                }
                let is_int = value.is_i64() || value.is_u64();
                self.on_number(&row, orig, name, move |op, name, v| {
                    set_field(op, name, if is_int { json!(round_int(v)) } else { json!(round(v, 4)) })
                });
                if name == "feed" {
                    row.set_sensitive(!rapid); // a rapid move has no feed
                    imp.feed_row.replace(Some(row.clone()));
                }
                group.add(&row);
            } else if let Some((lab, sub)) = spec(SWITCHES, name) {
                let row = adw::SwitchRow::builder().title(gettext(lab)).active(value.as_bool().unwrap_or(false)).build();
                if let Some(sub) = sub {
                    row.set_subtitle(&gettext(sub));
                }
                let (weak, name) = (self.downgrade(), name.to_string());
                row.connect_active_notify(move |row| {
                    let Some(ed) = weak.upgrade() else { return };
                    let v = row.is_active();
                    if name == "rapid"
                        && let Some(feed) = ed.imp().feed_row.borrow().as_ref()
                    {
                        feed.set_sensitive(!v);
                    }
                    let name = name.clone();
                    ed.edit(tr("Change {what}", &[("what", &row.title())]), move |op| set_field(op, &name, json!(v)));
                });
                group.add(&row);
            } else if let Some((lab, opts)) = spec(CHOICES, name) {
                let labels: Vec<String> = opts.iter().map(|o| gettext(o)).collect();
                let labels: Vec<&str> = labels.iter().map(String::as_str).collect();
                let row = adw::ComboRow::builder()
                    .title(gettext(lab))
                    .model(&gtk::StringList::new(&labels))
                    .selected(value.as_str().and_then(|v| opts.iter().position(|o| *o == v)).unwrap_or(0) as u32)
                    .build();
                let (weak, name) = (self.downgrade(), name.to_string());
                row.connect_selected_notify(move |row| {
                    let Some(ed) = weak.upgrade() else { return };
                    let Some(v) = opts.get(row.selected() as usize) else { return };
                    let name = name.clone();
                    ed.edit(tr("Change {what}", &[("what", &row.title())]), move |op| set_field(op, &name, json!(v)));
                });
                group.add(&row);
            } else if name == "outer_line" || name == "inner_line" {
                let ids: Vec<String> = project
                    .geometry
                    .iter()
                    .filter(|g| matches!(g, Element::Line(_)))
                    .map(|g| g.id().to_string())
                    .collect();
                // short: the coordinates tell the lines apart
                let mut labels: Vec<String> = ids.iter().map(|i| line_label(project, i)).collect();
                if labels.is_empty() {
                    labels.push(gettext("(no lines)"));
                }
                let labels: Vec<&str> = labels.iter().map(String::as_str).collect();
                let current = value.as_str().unwrap_or_default();
                let row = adw::ComboRow::builder()
                    .title(gettext(if name == "outer_line" { "Outer diameter line" } else { "Inner diameter line" }))
                    .model(&gtk::StringList::new(&labels))
                    .selected(ids.iter().position(|i| i == current).unwrap_or(0) as u32)
                    .build();
                let (weak, name) = (self.downgrade(), name.to_string());
                row.connect_selected_notify(move |row| {
                    let Some(ed) = weak.upgrade() else { return };
                    let Some(v) = ids.get(row.selected() as usize).cloned() else { return };
                    let name = name.clone();
                    ed.edit(tr("Change {what}", &[("what", &row.title())]), move |op| set_field(op, &name, json!(v)));
                });
                group.add(&row);
            } else if name == "contour" {
                let ids: Vec<String> =
                    value.as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect();
                let row = adw::EntryRow::builder()
                    .title(gettext("Contour, in cutting order"))
                    .text(ids.join(" "))
                    .show_apply_button(true)
                    .tooltip_text(gettext("Element IDs, for example g0 g1 g2"))
                    .build();
                let weak = self.downgrade();
                row.connect_apply(move |row| {
                    let Some(ed) = weak.upgrade() else { return };
                    let ids: Vec<String> = row.text().replace(',', " ").split_whitespace().map(str::to_string).collect();
                    ed.edit(gettext("Change contour"), move |op| set_field(op, "contour", json!(ids)));
                });
                group.add(&row);
                let pick = adw::ButtonRow::builder()
                    .title(gettext("Pick Contour on the Drawing"))
                    .start_icon_name("find-location-symbolic")
                    .build();
                let weak = self.downgrade();
                pick.connect_activated(move |_| {
                    let Some(ed) = weak.upgrade() else { return };
                    if let (Some(h), Some(i)) = (ed.hooks(), ed.imp().index.get()) {
                        (h.on_pick)(i);
                    }
                });
                group.add(&pick);
                group.add(&contour_rows(project, &ids));
            }
        }
        match op {
            Operation::Groove(g) if !g.front => group.set_description(Some(&gettext(
                "Face grooving: plunges along -Z, stepping outwards; the tool reference is the tip's corner nearest the \
                 axis.",
            ))),
            Operation::Groove(_) => group.set_description(Some(&gettext(
                "Grooving follows the drawn profile; the tool reference is the tip's -Z corner.",
            ))),
            _ => {}
        }
        imp.building.set(false);
        if self.hooks().is_some() {
            // copy it (a second finish pass, the same groove elsewhere) instead of retyping
            let more = adw::PreferencesGroup::new();
            let dup = adw::ButtonRow::builder()
                .title(gettext("_Duplicate Operation"))
                .use_underline(true)
                .start_icon_name("edit-copy-symbolic")
                .build();
            let weak = self.downgrade();
            dup.connect_activated(move |_| {
                if let Some(h) = weak.upgrade().and_then(|ed| ed.hooks()) {
                    (h.on_duplicate)();
                }
            });
            more.add(&dup);
            // the header kept only Move Up/Down and Close, so the operation's name fits there by touch too
            let delete = adw::ButtonRow::builder()
                .title(gettext("Delete Operation"))
                .start_icon_name("user-trash-symbolic")
                .build();
            // added, not set: the builder's css-classes would drop the row's own "button" style
            delete.add_css_class("destructive-action");
            let weak = self.downgrade();
            delete.connect_activated(move |_| {
                if let Some(h) = weak.upgrade().and_then(|ed| ed.hooks()) {
                    (h.on_delete)();
                }
            });
            more.add(&delete);
            page.add(&more);
        }
        imp.scroll.set_child(Some(&page));
    }

    // --- rows ---------------------------------------------------------------------------------------------------

    /// A number row's edits: `set(op, field, value)` when the user (not the display rounding) changes the value.
    fn on_number(
        &self,
        row: &adw::SpinRow,
        orig: Orig,
        name: &str,
        set: impl Fn(&mut Operation, &str, f64) -> alphacam::error::Result<()> + Clone + 'static,
    ) {
        let (weak, name) = (self.downgrade(), name.to_string());
        row.connect_value_notify(move |row| {
            let Some(ed) = weak.upgrade() else { return };
            if unchanged(row, &orig) {
                return;
            }
            orig.set(None);
            let (v, name, set) = (row.value(), name.clone(), set.clone());
            ed.edit(tr("Change {what}", &[("what", &row.title())]), move |op| set(op, &name, v));
        });
    }

    fn edit(&self, label: String, change: impl FnOnce(&mut Operation) -> alphacam::error::Result<()> + 'static) {
        let imp = self.imp();
        let (Some(i), Some(hooks)) = (imp.index.get(), self.hooks()) else { return };
        if imp.building.get() {
            return;
        }
        (hooks.apply)(
            &label,
            Box::new(move |p| p.operations.get_mut(i).map_or(Ok(()), change)),
        );
    }
}

fn fmt_pt(p: alphacam::model::Point) -> String {
    format!("X{}  Z{}", fmt_g(p.x), fmt_g(p.z))
}

/// The picked contour as a list: one row per element in cutting order (kind and number, from → to, radius), folded
/// under a summary row with the element count and where the contour starts and ends.
fn contour_rows(project: &Project, ids: &[String]) -> adw::ExpanderRow {
    let by_id = |i: &String| project.geometry.iter().find(|g| g.id() == i);
    let elems: Vec<&Element> = ids.iter().filter_map(by_id).collect();
    let ends: Vec<_> = match chain(&elems) {
        Ok(segs) => segs.iter().map(|s| (s.a, s.b)).collect(), // oriented in cutting order, as the post walks it
        Err(_) => elems.iter().map(|g| (g.a(), g.b())).collect(),
    };
    let n = ids.len();
    let exp = adw::ExpanderRow::builder()
        .title(fill(&ngettext("{n} element", "{n} elements", n as u32), &[("n", &n)]))
        .build();
    if let (Some(first), Some(last)) = (ends.first(), ends.last()) {
        exp.set_subtitle(&tr("From {a} to {b}", &[("a", &fmt_pt(first.0)), ("b", &fmt_pt(last.1))]));
    }
    for (n, (g, (a, b))) in elems.iter().zip(&ends).enumerate() {
        let kind = gettext(if matches!(g, Element::Arc(_)) { "Arc" } else { "Line" });
        let row = adw::ActionRow::builder()
            .title(format!("{}. {kind}", n + 1))
            .subtitle(format!("{}  →  {}", fmt_pt(*a), fmt_pt(*b)))
            .build();
        row.add_css_class("property");
        if let Element::Arc(c) = g {
            let r = ((c.a.x / 2.0 - c.centre.x / 2.0).powi(2) + (c.a.z - c.centre.z).powi(2)).sqrt();
            let label = gtk::Label::new(Some(&format!("R{}", fmt_g(r))));
            label.add_css_class("dim-label");
            row.add_suffix(&label);
        }
        let label = gtk::Label::new(Some(g.id()));
        label.add_css_class("dim-label");
        label.add_css_class("caption");
        row.add_suffix(&label);
        exp.add_row(&row);
    }
    for gid in ids.iter().filter(|i| by_id(i).is_none()) {
        exp.add_row(&adw::ActionRow::builder().title(tr("{id} (missing)", &[("id", gid)])).build());
    }
    exp
}

fn line_label(project: &Project, gid: &str) -> String {
    let Some(g) = project.geometry.iter().find(|g| g.id() == gid) else {
        return tr("{id} (missing)", &[("id", &gid)]);
    };
    let (a, b) = (g.a(), g.b());
    if a.x == b.x {
        // a diameter line: X and its Z range
        return format!("X{}  Z{}…{}  ({gid})", fmt_g(a.x), fmt_g(a.z.max(b.z)), fmt_g(a.z.min(b.z)));
    }
    format!("X{} Z{} → X{} Z{}  ({gid})", fmt_g(a.x), fmt_g(a.z), fmt_g(b.x), fmt_g(b.z))
}

#[cfg(test)]
mod tests {
    use super::*;
    use alphacam::model::{Drill, Point};

    #[test]
    fn fields_set_through_the_alcam_format() {
        let mut op = Operation::Drill(Drill { start: Point::new(0.0, 5.0), ..Default::default() });
        set_field(&mut op, "tool", json!(7)).unwrap();
        set_field(&mut op, "depth", json!(-30.5)).unwrap();
        set_field(&mut op, "start", json!([0.0, 2.0])).unwrap();
        match op {
            Operation::Drill(ref d) => assert_eq!((d.tool, d.depth, d.start), (7, -30.5, Point::new(0.0, 2.0))),
            _ => unreachable!(),
        }
        assert!(set_field(&mut op, "tool", json!("seven")).is_err());
    }
}
