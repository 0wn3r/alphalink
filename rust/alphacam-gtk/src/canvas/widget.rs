//! LatheCanvas: draws the part, billet, chuck and the posted toolpaths; touch and pointer input via
//! [`super::input`].

use std::cell::{Cell, Ref, RefCell, RefMut};
use std::collections::{HashMap, HashSet};
use std::f64::consts::PI;
use std::rc::Rc;
use std::sync::OnceLock;

use adw::prelude::*;
use alphacam::checks;
use alphacam::model::{Element, Point, Project, ToolLibrary};
use alphacam::moves::{Segment, segments};
use alphacam::preview::Preview;
use alphacam::py::fmt_g;
use alphacam::tool_shapes::{Polyline, outline};
use alphacam::toolpath::MoveKind;
use gtk::glib::subclass::Signal;
use gtk::subclass::prelude::*;
use gtk::{cairo, glib};

use super::hittest::{Source, element_polyline, hit, hit_element, hit_elements};
use super::input::{self, Router};
use super::view::ViewTransform;

/// (light, dark) RGB
fn colour(name: &str) -> ((f64, f64, f64), (f64, f64, f64)) {
    match name {
        "bg" => ((0.98, 0.98, 0.97), (0.13, 0.13, 0.14)),
        "axis" => ((0.55, 0.55, 0.55), (0.5, 0.5, 0.5)),
        // grid labels: contrast >= 4.5:1 on "bg" (WCAG AA)
        "label" => ((0.36, 0.36, 0.36), (0.66, 0.66, 0.66)),
        "billet" => ((0.6, 0.6, 0.62), (0.45, 0.45, 0.48)),
        "chuck" => ((0.8, 0.8, 0.82), (0.28, 0.28, 0.31)),
        "part" => ((0.1, 0.1, 0.12), (0.92, 0.92, 0.92)),
        "feed" => ((0.11, 0.42, 0.85), (0.38, 0.62, 1.0)),
        "rapid" => ((0.9, 0.38, 0.0), (1.0, 0.6, 0.25)),
        "thread" => ((0.55, 0.2, 0.75), (0.75, 0.5, 0.95)),
        _ => ((1.0, 0.0, 1.0), (1.0, 0.0, 1.0)),
    }
}

/// A construction choice the drawing tools offer: tap picks one.
#[derive(Debug, Clone, PartialEq)]
pub enum PreviewShape {
    Line(Point, Point),
    /// a, centre, b
    Arc(Point, Point, Point),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarkerKind {
    End,
    Other,
}

/// Set by the drawing tools: dragging the selected element's end points (TOUCH.md 5.2).
pub trait HandleHooks {
    /// A press at (x, y): true when a handle is under it (the drag then moves it instead of panning).
    fn begin(&self, x: f64, y: f64, source: Source) -> bool;
    fn drag(&self, x: f64, y: f64);
    fn end(&self);
    fn cancel(&self);
}

/// What the canvas shows besides the part (the window's drawing tools set it).
#[derive(Default)]
pub struct Overlay {
    /// highlighted / picked element ids
    pub highlight: HashSet<String>,
    pub picked: Vec<String>,
    /// rubber-band line
    pub rubber: Option<(Point, Point)>,
    /// snap marker
    pub marker: Option<(Point, MarkerKind)>,
    /// construction choices
    pub previews: Vec<PreviewShape>,
    /// end points of the selected element, draggable (TOUCH.md 5.2)
    pub handles: Vec<Point>,
    /// (X, Z) where the safety check found a problem: red rings
    pub alerts: Vec<(f64, f64)>,
    /// (a, b) lines showing where a dragged point takes its elements
    pub ghost: Vec<(Point, Point)>,
    /// (finger x, y) while a finger drags a point: magnifier above it (5.1)
    pub loupe: Option<(f64, f64)>,
    /// ALPHA-T's cursor while the keys move it
    pub cursor: Option<Point>,
}

/// The last tap, for the window's handlers.
#[derive(Debug, Clone, Default)]
pub struct Tap {
    pub screen: (f64, f64),
    pub segment: Option<Segment>,
    /// a finger is wide: elements equally close to it are offered in a list (a mouse is precise enough)
    pub candidates: Vec<String>,
}

pub struct State {
    pub project: Option<Rc<Project>>,
    pub preview: Option<Rc<Preview>>,
    pub segs: Vec<Segment>,
    pub selected: Option<usize>,
    pub view: ViewTransform,
    pub overlay: Overlay,
    /// drawn radius; 12 in touch mode
    pub handle_px: f64,
    /// select | the drawing tools' names
    pub tool: String,
    pub show_grid: bool,
    /// simulation: number of moves done (None = show everything)
    pub sim_index: Option<usize>,
    /// the user's tool library, for the tool pictures in the simulation
    pub library: Option<Rc<ToolLibrary>>,
    pub pointer: (f64, f64),
    pub last_tap: Tap,
    shapes: HashMap<i64, Vec<Polyline>>,
    fit_pending: bool,
    before_fit: Option<ViewTransform>,
    /// keep the part fitted while the user has not panned or zoomed (e.g. when the editor pane opens)
    auto_fit: bool,
    print: bool,
    in_loupe: bool,
}

impl Default for State {
    fn default() -> Self {
        State {
            project: None,
            preview: None,
            segs: vec![],
            selected: None,
            view: ViewTransform::default(),
            overlay: Overlay::default(),
            handle_px: 6.0,
            tool: "select".into(),
            show_grid: true,
            sim_index: None,
            library: None,
            pointer: (0.0, 0.0),
            last_tap: Tap::default(),
            shapes: HashMap::new(),
            fit_pending: true,
            before_fit: None,
            auto_fit: true,
            print: false,
            in_loupe: false,
        }
    }
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct LatheCanvas {
        pub state: RefCell<State>,
        pub hooks: RefCell<Option<Rc<dyn HandleHooks>>>,
        pub router: RefCell<Option<Rc<Router>>>,
        pub dark_handler: Cell<Option<glib::SignalHandlerId>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for LatheCanvas {
        const NAME: &'static str = "AlphaLinkLatheCanvas";
        type Type = super::LatheCanvas;
        type ParentType = gtk::DrawingArea;

        fn class_init(klass: &mut Self::Class) {
            klass.set_css_name("drawingarea");
            // "application": an interactive surface with its own keys; a named role, so screen readers find it
            klass.set_accessible_role(gtk::AccessibleRole::Application);
        }
    }

    impl ObjectImpl for LatheCanvas {
        fn signals() -> &'static [Signal] {
            static SIGNALS: OnceLock<Vec<Signal>> = OnceLock::new();
            SIGNALS.get_or_init(|| {
                vec![
                    Signal::builder("cursor-moved").param_types([f64::static_type(), f64::static_type()]).build(),
                    // every tap: world X (dia), Z, the input's radius in mm, toolpath op (-1 none), element ('' none)
                    Signal::builder("canvas-tapped")
                        .param_types([f64::static_type(), f64::static_type(), f64::static_type(),
                                      i32::static_type(), String::static_type()])
                        .build(),
                    // a long press (touch): world X (dia), Z, screen x, y, element under the finger ('' none)
                    Signal::builder("canvas-long-pressed")
                        .param_types([f64::static_type(), f64::static_type(), f64::static_type(),
                                      f64::static_type(), String::static_type()])
                        .build(),
                    // the view moved (pan, zoom, fit, resize): overlays that follow the drawing reposition
                    Signal::builder("view-changed").build(),
                ]
            })
        }

        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.set_hexpand(true);
            obj.set_vexpand(true);
            obj.set_focusable(true);
            obj.add_css_class("lathe-canvas");
            obj.set_draw_func(glib::clone!(#[weak] obj, move |_, cr, w, h| obj.draw(cr, w as f64, h as f64)));
            obj.connect_resize(|obj, _, _| {
                let (pending, auto) = {
                    let s = obj.state();
                    (s.fit_pending, s.auto_fit)
                };
                if pending || auto {
                    obj.fit();
                }
                obj.emit_by_name::<()>("view-changed", &[]);
            });
            self.router.replace(Some(input::install(&obj)));
            let sm = adw::StyleManager::default();
            let id = sm.connect_dark_notify(glib::clone!(#[weak] obj, move |_| obj.queue_draw()));
            self.dark_handler.set(Some(id));
        }

        fn dispose(&self) {
            // the style manager outlives the window
            if let Some(id) = self.dark_handler.take() {
                adw::StyleManager::default().disconnect(id);
            }
        }
    }

    impl WidgetImpl for LatheCanvas {}
    impl DrawingAreaImpl for LatheCanvas {}
}

glib::wrapper! {
    pub struct LatheCanvas(ObjectSubclass<imp::LatheCanvas>)
        @extends gtk::DrawingArea, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl Default for LatheCanvas {
    fn default() -> Self {
        glib::Object::new()
    }
}

fn nice_step(min_units: f64) -> f64 {
    // the smallest 1 / 2 / 5 x 10^n at least min_units
    if min_units <= 0.0 {
        return 1.0;
    }
    let e = min_units.log10().floor();
    for m in [1.0, 2.0, 5.0, 10.0] {
        if m * 10f64.powf(e) >= min_units {
            return m * 10f64.powf(e);
        }
    }
    10f64.powf(e + 1.0)
}

/// (X dia, Z) points along the shorter arc about c from a to b (construction previews).
pub fn arc_polyline(a: Point, c: Point, b: Point, n: usize) -> Vec<(f64, f64)> {
    let (cr, cz) = (c.x / 2.0, c.z);
    let (ta, tb) = ((a.x / 2.0 - cr).atan2(a.z - cz), (b.x / 2.0 - cr).atan2(b.z - cz));
    let rad = (a.x / 2.0 - cr).hypot(a.z - cz);
    let d = (tb - ta + PI).rem_euclid(2.0 * PI) - PI;
    (0..=n)
        .map(|k| {
            let t = ta + d * k as f64 / n as f64;
            (2.0 * (cr + rad * t.sin()), cz + rad * t.cos())
        })
        .collect()
}

impl LatheCanvas {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn state(&self) -> Ref<'_, State> {
        self.imp().state.borrow()
    }

    pub fn state_mut(&self) -> RefMut<'_, State> {
        self.imp().state.borrow_mut()
    }

    pub fn set_handle_hooks(&self, hooks: Option<Rc<dyn HandleHooks>>) {
        self.imp().hooks.replace(hooks);
    }

    pub fn handle_hooks(&self) -> Option<Rc<dyn HandleHooks>> {
        self.imp().hooks.borrow().clone()
    }

    // --- data -------------------------------------------------------------------------------------------

    pub fn set_project(&self, project: Rc<Project>, preview: Rc<Preview>) {
        {
            let mut s = self.state_mut();
            s.segs = segments(&preview);
            (s.project, s.preview, s.selected) = (Some(project), Some(preview), None);
            s.shapes.clear();
            s.fit_pending = true;
        }
        self.fit();
    }

    /// New preview after an edit: keep the view (no refit) and the selection.
    pub fn update(&self, project: Rc<Project>, preview: Rc<Preview>) {
        {
            let mut s = self.state_mut();
            s.segs = segments(&preview);
            (s.project, s.preview) = (Some(project), Some(preview));
            s.shapes.clear();
        }
        self.queue_draw();
    }

    pub fn select(&self, op_index: Option<usize>) {
        self.state_mut().selected = op_index;
        self.queue_draw();
    }

    /// Change what the overlay shows and redraw.
    pub fn overlay(&self, change: impl FnOnce(&mut Overlay)) {
        change(&mut self.state_mut().overlay);
        self.queue_draw();
    }

    // --- view -------------------------------------------------------------------------------------------

    fn bounds(s: &State) -> (f64, f64, f64, f64) {
        let (mut zs, mut rs) = (vec![0.0], vec![0.0]);
        if let Some(p) = &s.project {
            let k = if p.units == "inch" { 1.0 / 25.4 } else { 1.0 }; // the jaws and margins are mm sizes
            zs.push(-p.billet.length - 21.0 * k);
            rs.push(p.billet.diameter / 2.0 + 16.0 * k);
            if p.billet.tailstock {
                zs.push((6.0 * 1.732 + 30.0) * k);
            }
            for g in &p.geometry {
                for pt in [g.a(), g.b()] {
                    zs.push(pt.z);
                    rs.push(pt.x / 2.0);
                }
            }
        }
        for seg in &s.segs {
            for (x, z) in [seg.a, seg.b] {
                zs.push(z);
                rs.push(x / 2.0);
            }
        }
        let max = |v: &[f64]| v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let min = |v: &[f64]| v.iter().copied().fold(f64::INFINITY, f64::min);
        let r1 = max(&rs);
        (min(&zs), max(&zs), min(&rs).min(-0.12 * r1), r1)
    }

    pub fn user_view_change(&self) {
        self.state_mut().auto_fit = false;
        self.queue_draw();
        self.emit_by_name::<()>("view-changed", &[]);
    }

    pub(super) fn long_press(&self, x: f64, y: f64) {
        let (el, (xd, z)) = {
            let s = self.state();
            let el = s.project.as_ref().and_then(|p| hit_element(p, &s.view, x, y, Source::Touch.radius_px()));
            (el, s.view.to_world(x, y))
        };
        self.emit_by_name::<()>("canvas-long-pressed", &[&xd, &z, &x, &y, &el.unwrap_or_default()]);
    }

    pub fn fit(&self) {
        let (w, h) = (self.width() as f64, self.height() as f64);
        {
            let mut s = self.state_mut();
            s.auto_fit = true;
            if w < 10.0 || h < 10.0 {
                s.fit_pending = true;
                return;
            }
            let b = Self::bounds(&s);
            s.view.fit(b, w, h, 24.0);
            s.fit_pending = false;
        }
        glib::idle_add_local_once(glib::clone!(#[weak(rename_to = obj)] self,
            move || obj.emit_by_name::<()>("view-changed", &[])));
        self.queue_draw();
    }

    /// Double tap: fit; double tap again returns to the previous view (TOUCH.md 4).
    pub fn toggle_fit(&self) {
        {
            let mut s = self.state_mut();
            if let Some(before) = s.before_fit.take() {
                s.view = before;
                drop(s);
                self.user_view_change();
                return;
            }
            s.before_fit = Some(s.view);
        }
        self.fit();
    }

    pub fn zoom(&self, factor: f64) {
        let (w, h) = (self.width() as f64, self.height() as f64);
        {
            let mut s = self.state_mut();
            s.view.zoom_at(w / 2.0, h / 2.0, factor);
        }
        self.user_view_change();
    }

    pub(super) fn pointer_moved(&self, x: f64, y: f64) {
        let (xd, z) = {
            let mut s = self.state_mut();
            s.pointer = (x, y);
            s.view.to_world(x, y)
        };
        self.emit_by_name::<()>("cursor-moved", &[&xd, &z]);
    }

    pub(super) fn tap(&self, x: f64, y: f64, source: Source) {
        let r = source.radius_px();
        let (xd, z, mm, op, el) = {
            let mut s = self.state_mut();
            let seg = if s.tool == "select" { hit(&s.segs, &s.view, x, y, r).copied() } else { None };
            let el = s.project.as_ref().and_then(|p| hit_element(p, &s.view, x, y, r));
            let candidates = match &s.project {
                Some(p) if source == Source::Touch => hit_elements(p, &s.view, x, y, r, 3.0),
                _ => vec![],
            };
            let (xd, z) = s.view.to_world(x, y);
            s.last_tap = Tap { screen: (x, y), segment: seg, candidates };
            let op = seg.and_then(|s| s.op_index).map_or(-1, |i| i as i32);
            (xd, z, r / s.view.scale, op, el.unwrap_or_default())
        };
        self.emit_by_name::<()>("canvas-tapped", &[&xd, &z, &mm, &op, &el]);
    }

    // --- drawing ----------------------------------------------------------------------------------------

    /// The part, billet, chuck and every toolpath, fitted to width x height in light colours, for the printed
    /// setup sheet (the screen's view, selection and simulation position are left alone).
    pub fn render_to(&self, cr: &cairo::Context, width: f64, height: f64) {
        let mut s = self.state_mut();
        let saved = (s.view, s.show_grid, s.print);
        let b = Self::bounds(&s);
        s.view.fit(b, width, height, 12.0);
        (s.show_grid, s.print) = (false, true);
        Self::draw_scene(&mut s, cr, width, height);
        (s.view, s.show_grid, s.print) = saved;
    }

    fn draw(&self, cr: &cairo::Context, width: f64, height: f64) {
        let mut s = self.state_mut();
        Self::draw_scene(&mut s, cr, width, height);
        if s.overlay.loupe.is_some() && s.overlay.marker.is_some() && !s.in_loupe {
            Self::draw_loupe(&mut s, cr, width, height);
        }
    }

    fn rgb(s: &State, cr: &cairo::Context, name: &str, alpha: f64) {
        let (light, dark) = colour(name);
        let dark_now = adw::StyleManager::default().is_dark() && !s.print;
        let (r, g, b) = if dark_now { dark } else { light };
        cr.set_source_rgba(r, g, b, alpha);
    }

    /// A x4 magnifier 80 px above the finger, centred on the dragged point: the finger never hides it.
    fn draw_loupe(s: &mut State, cr: &cairo::Context, width: f64, height: f64) {
        let ((fx, fy), (pt, _)) = (s.overlay.loupe.unwrap(), s.overlay.marker.unwrap());
        let r = 56.0;
        let (lx, ly) = (fx.max(r + 4.0).min(width - r - 4.0), (fy - 80.0 - r).max(r + 4.0));
        let saved = s.view;
        let mut z = saved;
        z.scale = saved.scale * 4.0;
        (z.ox, z.oy) = (lx - pt.z * z.scale, ly + pt.x / 2.0 * z.scale);
        let _ = cr.save();
        cr.arc(lx, ly, r, 0.0, 2.0 * PI);
        cr.clip();
        (s.view, s.in_loupe) = (z, true);
        Self::draw_scene(s, cr, width, height);
        (s.view, s.in_loupe) = (saved, false);
        let _ = cr.restore();
        Self::rgb(s, cr, "rapid", 1.0);
        cr.set_line_width(2.0);
        cr.arc(lx, ly, r, 0.0, 2.0 * PI);
        let _ = cr.stroke();
    }

    fn rect_rz(v: &ViewTransform, cr: &cairo::Context, r0: f64, r1: f64, z0: f64, z1: f64) {
        // fill the rectangle between radii r0..r1 and Z z0..z1 (model units)
        let ((ax, ay), (bx, by)) = (v.to_screen(2.0 * r0, z0), v.to_screen(2.0 * r1, z1));
        cr.rectangle(ax.min(bx), ay.min(by), (bx - ax).abs(), (by - ay).abs());
        let _ = cr.fill();
    }

    /// G3/ccw is counter-clockwise with Z right and X up: decreasing cairo angle on screen (y down).
    fn arc(v: &ViewTransform, cr: &cairo::Context, a: (f64, f64), b: (f64, f64), c: (f64, f64), ccw: bool) {
        let (cx, cy) = v.to_screen(c.0, c.1);
        let (ax, ay) = v.to_screen(a.0, a.1);
        let (bx, by) = v.to_screen(b.0, b.1);
        cr.new_sub_path();
        let (r, a0, a1) = ((ax - cx).hypot(ay - cy), (ay - cy).atan2(ax - cx), (by - cy).atan2(bx - cx));
        if ccw { cr.arc_negative(cx, cy, r, a0, a1) } else { cr.arc(cx, cy, r, a0, a1) }
    }

    fn polyline(v: &ViewTransform, cr: &cairo::Context, pts: &[(f64, f64)]) {
        for (i, &(x, z)) in pts.iter().enumerate() {
            let (sx, sy) = v.to_screen(x, z);
            if i == 0 { cr.move_to(sx, sy) } else { cr.line_to(sx, sy) }
        }
    }

    fn draw_scene(s: &mut State, cr: &cairo::Context, width: f64, height: f64) {
        Self::rgb(s, cr, "bg", 1.0);
        let _ = cr.paint();
        let Some(p) = s.project.clone() else {
            return;
        };
        let v = s.view;
        if s.show_grid {
            Self::grid(s, cr, width, height);
        }
        Self::rgb(s, cr, "axis", 1.0);
        cr.set_line_width(1.0);
        cr.set_dash(&[12.0, 4.0, 2.0, 4.0], 0.0);
        let y0 = v.to_screen(0.0, 0.0).1;
        cr.move_to(0.0, y0);
        cr.line_to(width, y0);
        let _ = cr.stroke();
        cr.set_dash(&[], 0.0);
        let b = &p.billet;
        let (zc, rb) = (-b.length, b.diameter / 2.0);
        let k = if p.units == "inch" { 1.0 / 25.4 } else { 1.0 }; // jaw and tailstock sizes are mm
        Self::rgb(s, cr, "chuck", 1.0);
        if b.jaws == "internal" {
            // expanding jaws gripping the bore: the first step inside the part, the next two behind it, each
            // reaching further in towards the axis (mirror of the external jaws)
            let rg = if b.bore > 0.0 { b.bore / 2.0 } else { rb * 0.4 };
            for i in 0..3 {
                let fi = i as f64;
                let z0 = if i == 0 { zc + 7.0 * k } else { zc - 7.0 * k * (fi - 1.0) };
                Self::rect_rz(&v, cr, (rg - 5.0 * k * (fi + 1.0)).max(0.0), rg + 5.0 * k * fi, z0 - 7.0 * k, z0);
            }
        } else {
            // stepped chuck jaws behind the billet (20 mm, like the ALPHA-T drawing layer)
            for (i, step) in [5.0 * k, 10.0 * k, 15.0 * k].into_iter().enumerate() {
                let fi = i as f64;
                let (x0, ytop) = v.to_screen(2.0 * (rb + step), zc - 7.0 * k * fi);
                let back = if i > 0 { 5.0 * k } else { 0.0 };
                let (x1, ybot) = v.to_screen(2.0 * (rb - 5.0 * k + step - back), zc - 7.0 * k * (fi + 1.0));
                cr.rectangle(x1, ytop, x0 - x1, ybot - ytop);
                let _ = cr.fill();
            }
        }
        if b.tailstock {
            // live centre at the front face: a 60-degree point on the axis, then the quill
            Self::rgb(s, cr, "chuck", 1.0);
            Self::polyline(&v, cr, &[(0.0, 0.0), (2.0 * 6.0 * k, 6.0 * 1.732 * k), (-2.0 * 6.0 * k, 6.0 * 1.732 * k)]);
            cr.close_path();
            let _ = cr.fill();
            Self::rect_rz(&v, cr, -12.0 * k, 12.0 * k, 6.0 * 1.732 * k, (6.0 * 1.732 + 30.0) * k);
        }
        // billet
        Self::rgb(s, cr, "billet", 1.0);
        cr.set_dash(&[6.0, 4.0], 0.0);
        let (xa, ya) = v.to_screen(b.diameter, 0.0);
        let (xb, yb) = v.to_screen(-b.diameter, -b.length);
        cr.rectangle(xb, ya, xa - xb, yb - ya);
        let _ = cr.stroke();
        if b.bore > 0.0 {
            // tube billet: its bore
            for sgn in [1.0, -1.0] {
                Self::polyline(&v, cr, &[(sgn * b.bore, 0.0), (sgn * b.bore, -b.length)]);
            }
            let _ = cr.stroke();
        }
        cr.set_dash(&[], 0.0);
        // part profile: upper half bold, mirror faint
        for (mirror, alpha, lw) in [(-1.0, 0.25, 1.0), (1.0, 1.0, 2.2)] {
            Self::rgb(s, cr, "part", alpha);
            cr.set_line_width(lw);
            for g in &p.geometry {
                let (a, bb) = (g.a(), g.b());
                match g {
                    Element::Arc(arc) => Self::arc(&v, cr, (a.x * mirror, a.z), (bb.x * mirror, bb.z),
                                                   (arc.centre.x * mirror, arc.centre.z), arc.ccw == (mirror > 0.0)),
                    Element::Line(_) => Self::polyline(&v, cr, &[(a.x * mirror, a.z), (bb.x * mirror, bb.z)]),
                }
                let _ = cr.stroke();
            }
        }
        // drawing text (ALPHA-T T): starts at its point, character height in drawing units
        Self::rgb(s, cr, "part", 1.0);
        for t in &p.texts {
            cr.set_font_size((t.size * v.scale).max(1.0));
            let (sx, sy) = v.to_screen(t.p.x, t.p.z);
            cr.move_to(sx, sy);
            let _ = cr.show_text(&t.text);
        }
        if s.tool == "select" || s.overlay.picked.is_empty() || s.print {
            Self::paths(s, cr);
        }
        if !s.print {
            Self::overlay_draw(s, cr);
            Self::axes(s, cr, height);
        }
    }

    /// Axis directions in the bottom-left corner (+Z right, +X up, like the lathe from the front) and a small cross
    /// on the part origin X0 Z0.
    fn axes(s: &State, cr: &cairo::Context, height: f64) {
        let (ox, oy, length) = (28.0, height - 28.0, 46.0);
        Self::rgb(s, cr, "part", 0.8);
        cr.set_line_width(2.0);
        cr.set_font_size(13.0);
        for (dx, dy, label) in [(length, 0.0, "+Z"), (0.0, -length, "+X")] {
            let (ex, ey) = (ox + dx, oy + dy);
            cr.move_to(ox, oy);
            cr.line_to(ex, ey);
            let _ = cr.stroke();
            let (ux, uy) = if dx != 0.0 { (1.0, 0.0) } else { (0.0, -1.0) }; // arrowhead
            cr.move_to(ex + 4.0 * ux, ey + 4.0 * uy);
            cr.line_to(ex - 7.0 * ux - 5.0 * uy, ey - 7.0 * uy + 5.0 * ux);
            cr.line_to(ex - 7.0 * ux + 5.0 * uy, ey - 7.0 * uy - 5.0 * ux);
            cr.close_path();
            let _ = cr.fill();
            if let Ok(ext) = cr.text_extents(label) {
                if dx != 0.0 {
                    cr.move_to(ex + 8.0, ey - ext.height() / 2.0 - ext.y_bearing());
                } else {
                    cr.move_to(ex - ext.width() / 2.0 - ext.x_bearing(), ey - 9.0);
                }
            }
            let _ = cr.show_text(label);
        }
        cr.arc(ox, oy, 3.0, 0.0, 2.0 * PI);
        let _ = cr.fill();
        // part origin
        let (sx, sy) = s.view.to_screen(0.0, 0.0);
        Self::rgb(s, cr, "axis", 1.0);
        cr.set_line_width(1.5);
        cr.move_to(sx - 7.0, sy);
        cr.line_to(sx + 7.0, sy);
        cr.move_to(sx, sy - 7.0);
        cr.line_to(sx, sy + 7.0);
        let _ = cr.stroke();
    }

    /// A faint coordinate grid under everything: lines every 1/2/5 x 10^n (at least ~48 px apart), every 5th a
    /// little stronger and labelled at the left (X, as a diameter) and bottom (Z) edges.
    fn grid(s: &State, cr: &cairo::Context, width: f64, height: f64) {
        let v = s.view;
        let min_px = 48.0;
        let zstep = nice_step(min_px / v.scale);
        let xstep = nice_step(2.0 * min_px / v.scale); // X is a diameter: 1 mm of X is half a mm on screen
        let (z0, z1) = (v.to_world(0.0, 0.0).1, v.to_world(width, 0.0).1);
        let (xa, xb) = (v.to_world(0.0, height).0, v.to_world(0.0, 0.0).0);
        cr.set_line_width(1.0);
        cr.set_font_size(10.0);
        let mut labels = vec![];
        let mut k = (z0 / zstep).ceil() as i64;
        while k as f64 * zstep <= z1 {
            let z = k as f64 * zstep;
            let sx = v.to_screen(0.0, z).0.round() + 0.5;
            let major = k % 5 == 0;
            Self::rgb(s, cr, "axis", if major { 0.16 } else { 0.07 });
            cr.move_to(sx, 0.0);
            cr.line_to(sx, height);
            let _ = cr.stroke();
            if major {
                labels.push((format!("Z{}", fmt_g(z)), sx + 3.0, height - 4.0));
            }
            k += 1;
        }
        let mut k = (xa / xstep).ceil() as i64;
        while k as f64 * xstep <= xb {
            let x = k as f64 * xstep;
            let sy = v.to_screen(x, 0.0).1.round() + 0.5;
            let major = k % 5 == 0;
            Self::rgb(s, cr, "axis", if major { 0.16 } else { 0.07 });
            cr.move_to(0.0, sy);
            cr.line_to(width, sy);
            let _ = cr.stroke();
            if major && x != 0.0 {
                labels.push((format!("X{}", fmt_g(x)), 4.0, sy - 3.0));
            }
            k += 1;
        }
        Self::rgb(s, cr, "label", 1.0);
        for (text, lx, ly) in labels {
            cr.move_to(lx, ly);
            let _ = cr.show_text(&text);
        }
    }

    fn paths(s: &mut State, cr: &cairo::Context) {
        let sel = if s.print { None } else { s.selected };
        let sim = if s.print { None } else { s.sim_index };
        let v = s.view;
        for (i, seg) in s.segs.iter().enumerate() {
            let focus = sel.is_none() || seg.op_index == sel;
            let mut alpha = if focus { 1.0 } else { 0.15 };
            if sim.is_some_and(|n| i >= n) {
                alpha = 0.1; // not cut yet
            }
            let lw = if sel.is_some() && focus { 2.0 } else { 1.1 };
            if seg.kind == MoveKind::Dwell {
                Self::rgb(s, cr, "feed", alpha);
                let (sx, sy) = v.to_screen(seg.b.0, seg.b.1);
                cr.arc(sx, sy, 3.5, 0.0, 2.0 * PI);
                let _ = cr.fill();
                continue;
            }
            let name = match seg.kind {
                MoveKind::Rapid => "rapid",
                MoveKind::Thread => "thread",
                _ => "feed",
            };
            Self::rgb(s, cr, name, alpha);
            cr.set_line_width(lw);
            cr.set_dash(if seg.kind == MoveKind::Rapid { &[5.0, 4.0] } else { &[] }, 0.0);
            match seg.centre {
                Some(c) => Self::arc(&v, cr, seg.a, seg.b, c, seg.kind == MoveKind::ArcCcw),
                None => Self::polyline(&v, cr, &[seg.a, seg.b]),
            }
            let _ = cr.stroke();
        }
        cr.set_dash(&[], 0.0);
        if let Some(sim) = sim
            && !s.segs.is_empty()
        {
            let seg = if sim > 0 { s.segs[sim.min(s.segs.len()) - 1] } else { s.segs[0] };
            let (x, z) = if sim > 0 { seg.b } else { seg.a };
            Self::tool_shape(s, cr, seg.block, x, z);
            let (sx, sy) = v.to_screen(x, z);
            Self::rgb(s, cr, "rapid", 1.0);
            cr.arc(sx, sy, 7.0, 0.0, 2.0 * PI);
            let _ = cr.fill();
            Self::rgb(s, cr, "bg", 1.0);
            cr.arc(sx, sy, 3.0, 0.0, 2.0 * PI);
            let _ = cr.fill();
        }
    }

    /// The tool picture of the PRG block with this label: (Z, radius) polylines in mm, cached per preview.
    fn shape(s: &mut State, label: i64) -> Vec<Polyline> {
        if let Some(lines) = s.shapes.get(&label) {
            return lines.clone();
        }
        let number = s
            .preview
            .as_ref()
            .and_then(|p| p.program.as_ref())
            .and_then(|prog| prog.blocks.iter().find(|b| b.label == label).and_then(|b| b.tool()));
        let lines = match (&s.project, number) {
            (Some(p), Some(n)) => outline(checks::tool(p, n as i32, s.library.as_deref())),
            _ => vec![],
        };
        s.shapes.insert(label, lines.clone());
        lines
    }

    /// The tool drawn with its tip at the simulation position (X is a diameter: the picture's radius doubles).
    fn tool_shape(s: &mut State, cr: &cairo::Context, label: i64, x: f64, z: f64) {
        let lines = Self::shape(s, label);
        let k = if s.project.as_ref().is_some_and(|p| p.units == "inch") { 1.0 / 25.4 } else { 1.0 };
        for line in &lines {
            let pts: Vec<(f64, f64)> = line.iter().map(|&(dz, r)| (x + 2.0 * r * k, z + dz * k)).collect();
            Self::polyline(&s.view, cr, &pts);
            if pts.first() == pts.last() {
                cr.close_path();
                Self::rgb(s, cr, "part", 0.12);
                let _ = cr.fill_preserve();
            }
            Self::rgb(s, cr, "part", 0.7);
            cr.set_line_width(1.2);
            let _ = cr.stroke();
        }
    }

    fn overlay_draw(s: &State, cr: &cairo::Context) {
        let (v, o) = (&s.view, &s.overlay);
        let Some(p) = &s.project else {
            return;
        };
        let by_id: HashMap<&str, &Element> = p.geometry.iter().map(|g| (g.id(), g)).collect();
        let marked = o.highlight.iter().map(|g| (g, "rapid", 4.0)).chain(o.picked.iter().map(|g| (g, "feed", 5.0)));
        for (gid, name, width) in marked {
            let Some(g) = by_id.get(gid.as_str()) else {
                continue;
            };
            Self::rgb(s, cr, name, 0.85);
            cr.set_line_width(width);
            Self::polyline(v, cr, &element_polyline(g));
            let _ = cr.stroke();
        }
        for (n, gid) in o.picked.iter().enumerate() {
            // order badges on picked elements
            let Some(g) = by_id.get(gid.as_str()) else {
                continue;
            };
            let pts = element_polyline(g);
            let (mx, my) = if pts.len() > 2 {
                v.to_screen(pts[pts.len() / 2].0, pts[pts.len() / 2].1)
            } else {
                v.to_screen((pts[0].0 + pts[1].0) / 2.0, (pts[0].1 + pts[1].1) / 2.0)
            };
            Self::rgb(s, cr, "feed", 1.0);
            cr.arc(mx, my, 10.0, 0.0, 2.0 * PI);
            let _ = cr.fill();
            Self::rgb(s, cr, "bg", 1.0);
            cr.set_font_size(11.0);
            let text = (n + 1).to_string();
            if let Ok(ext) = cr.text_extents(&text) {
                cr.move_to(mx - ext.width() / 2.0 - ext.x_bearing(), my - ext.height() / 2.0 - ext.y_bearing());
            }
            let _ = cr.show_text(&text);
        }
        for pv in &o.previews {
            Self::rgb(s, cr, "feed", 0.9);
            cr.set_line_width(2.0);
            cr.set_dash(&[6.0, 4.0], 0.0);
            match *pv {
                PreviewShape::Line(a, b) => Self::polyline(v, cr, &[(a.x, a.z), (b.x, b.z)]),
                PreviewShape::Arc(a, c, b) => Self::polyline(v, cr, &arc_polyline(a, c, b, 32)),
            }
            let _ = cr.stroke();
            cr.set_dash(&[], 0.0);
        }
        for (a, b) in &o.ghost {
            // a dragged point: where its elements go
            Self::rgb(s, cr, "rapid", 0.9);
            cr.set_line_width(2.0);
            cr.set_dash(&[6.0, 4.0], 0.0);
            Self::polyline(v, cr, &[(a.x, a.z), (b.x, b.z)]);
            let _ = cr.stroke();
            cr.set_dash(&[], 0.0);
        }
        for &(ax, az) in &o.alerts {
            // the safety check's places
            let (sx, sy) = v.to_screen(ax, az);
            cr.set_source_rgba(0.88, 0.11, 0.14, 0.9);
            cr.set_line_width(2.5);
            cr.arc(sx, sy, 10.0, 0.0, 2.0 * PI);
            let _ = cr.stroke();
            cr.move_to(sx - 1.5, sy - 5.0);
            cr.line_to(sx - 1.5, sy + 1.0);
            let _ = cr.stroke();
            cr.arc(sx - 1.5, sy + 4.5, 1.3, 0.0, 2.0 * PI);
            let _ = cr.fill();
        }
        for h in &o.handles {
            // the selected element's ends: drag them to move them
            let (sx, sy) = v.to_screen(h.x, h.z);
            Self::rgb(s, cr, "bg", 1.0);
            cr.arc(sx, sy, s.handle_px, 0.0, 2.0 * PI);
            let _ = cr.fill_preserve();
            Self::rgb(s, cr, "rapid", 1.0);
            cr.set_line_width(2.0);
            let _ = cr.stroke();
        }
        if let Some((a, b)) = o.rubber {
            Self::rgb(s, cr, "part", 0.7);
            cr.set_line_width(1.5);
            cr.set_dash(&[4.0, 3.0], 0.0);
            Self::polyline(v, cr, &[(a.x, a.z), (b.x, b.z)]);
            let _ = cr.stroke();
            cr.set_dash(&[], 0.0);
        }
        if let Some(c) = o.cursor {
            // ALPHA-T's cross-hair cursor
            let (sx, sy) = v.to_screen(c.x, c.z);
            Self::rgb(s, cr, "part", 0.8);
            cr.set_line_width(1.0);
            cr.move_to(sx - 18.0, sy);
            cr.line_to(sx + 18.0, sy);
            cr.move_to(sx, sy - 18.0);
            cr.line_to(sx, sy + 18.0);
            let _ = cr.stroke();
        }
        if let Some((pt, kind)) = o.marker {
            let (sx, sy) = v.to_screen(pt.x, pt.z);
            Self::rgb(s, cr, "rapid", 1.0);
            cr.set_line_width(2.0);
            if kind == MarkerKind::End {
                cr.rectangle(sx - 6.0, sy - 6.0, 12.0, 12.0);
            } else {
                cr.move_to(sx - 8.0, sy);
                cr.line_to(sx + 8.0, sy);
                cr.move_to(sx, sy - 8.0);
                cr.line_to(sx, sy + 8.0);
            }
            let _ = cr.stroke();
            cr.set_font_size(12.0);
            cr.move_to(sx + 10.0, sy - 10.0);
            let _ = cr.show_text(&format!("X{} Z{}", fmt_g(pt.x), fmt_g(pt.z)));
        }
    }
}
