//! Construction commands (ALPHA-T manual pp. 20-34) and the feature helpers (workflow improvements, step 3).
//!
//! A small state machine: each step says what to tap in the banner; numbers come from a dialog; where there are
//! several solutions they are shown dashed and the tap nearest one picks it (ALPHA-T cycles them with Space).

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use adw::prelude::*;
use alphacam::drawing::{self, BlendRef};
use alphacam::features::{self, STANDARDS};
use alphacam::model::{Element, Operation, Point, Project};
use alphacam::py::{fmt_g, round};

use super::{DrawingTools, element, grid, weak};
use crate::canvas::widget::{PreviewShape, arc_polyline};
use crate::i18n::{fill, gettext, ngettext, tr};
use crate::ui::{builder, object};

/// What a construction tap means: an arc or a line under the finger, else the nearest point (snapped).
#[derive(Debug, Clone, PartialEq)]
pub(super) enum Ref {
    Point(Point),
    Line(String),
    Arc(String),
}

type Then = Rc<dyn Fn(PreviewShape)>;

/// The construction going on.
pub(super) struct Construct {
    kind: String,
    refs: Vec<Ref>,
    /// several solutions shown dashed: a tap picks the nearest
    choices: Option<(Vec<PreviewShape>, Then)>,
}

fn dist_to_preview(pv: &PreviewShape, p: Point) -> f64 {
    match *pv {
        PreviewShape::Line(a, b) => drawing::distance_to(&Element::line(a, b, ""), p),
        PreviewShape::Arc(a, c, b) => {
            let pts = arc_polyline(a, c, b, 32);
            pts.into_iter().map(|(x, z)| Point::new(x, z).distance(&p)).fold(f64::INFINITY, f64::min)
        }
    }
}

fn lines(found: Vec<(Point, Point)>) -> Vec<PreviewShape> {
    found.into_iter().map(|(a, b)| PreviewShape::Line(a, b)).collect()
}

impl DrawingTools {
    pub(super) fn start_construct(&self, kind: &str) {
        if !self.w().has_project() {
            return;
        }
        let selected = self.s.borrow().selected.clone();
        if kind == "thread" && self.diameter_line(selected.as_deref()).is_some() {
            self.thread_dialog(selected.as_deref().unwrap_or_default());
            return;
        }
        if let Some(which) = kind.strip_prefix("corners-") {
            self.corners_dialog(which);
            return;
        }
        if self.s.borrow().pick_op.is_some() {
            self.cancel_pick();
        }
        self.finish_chain();
        self.set_tool("select");
        if kind == "circle" {
            let c = self.s.borrow().cursor.unwrap_or_default();
            let this = self.rc();
            self.numbers(
                &gettext("Circle"),
                &[
                    (gettext("Centre X (Ø)"), c.x, -2000.0, 2000.0),
                    (gettext("Centre Z"), c.z, -2000.0, 2000.0),
                    (gettext("Radius"), self.mm(10.0), 0.001, 1000.0),
                ],
                weak!(this, move |v: Vec<f64>| this.construct_edit(&gettext("Circle"), |p| {
                    drawing::add_circle(p, Point::new(v[0], v[1]), v[2]).map(|_| ())
                })),
            );
            return;
        }
        if kind == "mirror" {
            self.mirror_image();
            return;
        }
        let first = match kind {
            "tangent" => "Tangent: tap a point, or an arc",
            "tangent-angle" => "Tangent at an angle: tap the arc",
            "parallel" => "Parallel line: tap the line",
            "split" => "Split arc: tap the arc where to split it",
            "blend" => "Blend arc: tap the 1st point, line or arc",
            "thread" => "Standard thread: tap the diameter line to thread",
            _ => return,
        };
        self.s.borrow_mut().construct = Some(Construct { kind: kind.into(), refs: vec![], choices: None });
        self.prompt(&gettext(first));
    }

    // --- feature helpers (workflow improvements, step 3) ------------------------------------------------

    /// Chamfer or round every outside corner of the profile with one size; single ones can be changed after.
    fn corners_dialog(&self, kind: &str) {
        let Some(project) = self.project() else { return };
        if features::outside_corners(&project).is_empty() {
            self.toast(&gettext("The profile has no outside corner between two lines"));
            return;
        }
        let chamfer = kind == "chamfer";
        let (title, fields) = if chamfer {
            (
                gettext("Chamfer Outside Corners"),
                vec![(gettext("Size"), self.mm(1.0), 0.001, 100.0), (gettext("Angle"), 45.0, 1.0, 89.0)],
            )
        } else {
            (gettext("Round Outside Corners"), vec![(gettext("Radius"), self.mm(1.0), 0.001, 100.0)])
        };
        let this = self.rc();
        let t = title.clone();
        self.numbers(&title, &fields, weak!(this, move |v: Vec<f64>| {
            let count = Cell::new(0);
            let angle = if chamfer { v[1] } else { 45.0 };
            if this.edit(&t, |p| {
                count.set(features::all_corners(p, if chamfer { "chamfer" } else { "fillet" }, v[0], angle));
                Ok(())
            }) {
                let n = count.get();
                this.toast(&if n > 0 {
                    fill(&ngettext("{n} corner done", "{n} corners done", n as u32), &[("n", &n)])
                } else {
                    gettext("The lines at the corners are too short for that size")
                });
            }
        }));
    }

    /// The element gid if it is a line along Z (a diameter the thread can go on).
    fn diameter_line(&self, gid: Option<&str>) -> Option<Element> {
        let project = self.project()?;
        let g = element(&project, gid?)?;
        let (a, b) = (g.a(), g.b());
        (!g.is_arc() && (a.x - b.x).abs() < 1e-9 && (a.z - b.z).abs() > 1e-9).then(|| g.clone())
    }

    /// A standard thread on the selected diameter line: ISO metric, UNC or UNF, size and pitch from the table, the
    /// minor-diameter line drawn under it, a relief groove at its back end, and the Threading operation.
    fn thread_dialog(&self, gid: &str) {
        let Some(project) = self.project() else { return };
        let Some(g) = self.diameter_line(Some(gid)) else { return };
        let units = project.units.clone();
        let ui = builder("drawing-dialogs");
        let std_row: adw::ComboRow = object(&ui, "thread_standard_row"); // in the order of STANDARDS
        std_row.set_selected(if units == "inch" { 1 } else { 0 });
        let size_row: adw::ComboRow = object(&ui, "thread_size_row");
        let pitch_row: adw::ComboRow = object(&ui, "thread_pitch_row");
        // the standard and its sizes
        let state: Rc<RefCell<(String, Vec<String>)>> = Rc::default();

        let size_changed = {
            let (state, size_row, pitch_row) = (state.clone(), size_row.clone(), pitch_row.clone());
            Rc::new(move || {
                let (std, size) = {
                    let s = state.borrow();
                    match s.1.get(size_row.selected() as usize) {
                        Some(size) => (s.0.clone(), size.clone()),
                        None => return,
                    }
                };
                let pitches = features::pitches(&std, &size).unwrap_or_default();
                let labels: Vec<String> = if std == "M" {
                    let d: f64 = size.parse().unwrap_or_default();
                    pitches
                        .iter()
                        .map(|&pt| {
                            if features::coarse(d, pt) { tr("{p} coarse", &[("p", &fmt_g(pt))]) } else { fmt_g(pt) }
                        })
                        .collect()
                } else {
                    pitches.iter().map(|&t| tr("{n} threads per inch", &[("n", &fmt_g(t))])).collect()
                };
                let labels: Vec<&str> = labels.iter().map(String::as_str).collect();
                pitch_row.set_model(Some(&gtk::StringList::new(&labels)));
                pitch_row.set_selected(0);
            })
        };
        let standard_changed = {
            let (state, size_row, size_changed) = (state.clone(), size_row.clone(), size_changed.clone());
            let units = units.clone();
            move |std_row: &adw::ComboRow| {
                let std = STANDARDS.get(std_row.selected() as usize).copied().unwrap_or("M");
                let sizes = features::sizes(std);
                let labels: Vec<String> =
                    sizes.iter().map(|d| if std == "M" { format!("M{d}") } else { format!("{d}\"") }).collect();
                let nearest = features::nearest(std, g.a().x, &units);
                let at = sizes.iter().position(|s| *s == nearest).unwrap_or(0);
                *state.borrow_mut() = (std.into(), sizes);
                let labels: Vec<&str> = labels.iter().map(String::as_str).collect();
                size_row.set_model(Some(&gtk::StringList::new(&labels)));
                size_row.set_selected(at as u32);
                size_changed();
            }
        };
        {
            let size_changed = size_changed.clone();
            size_row.connect_selected_notify(move |row| {
                if row.model().is_some() {
                    size_changed()
                }
            });
        }
        standard_changed(&std_row);
        std_row.connect_selected_notify(standard_changed);
        let relief: adw::SwitchRow = object(&ui, "thread_relief_row");
        let with_op: adw::SwitchRow = object(&ui, "thread_op_row");
        let dlg: adw::AlertDialog = object(&ui, "thread_alert");
        dlg.set_body(&self.element_label(gid));
        let this = self.rc();
        let gid = gid.to_string();
        dlg.connect_response(None, weak!(this, move |_, r| {
            if r != "add" {
                return;
            }
            let (std, size) = {
                let s = state.borrow();
                (s.0.clone(), s.1.get(size_row.selected() as usize).cloned().unwrap_or_default())
            };
            let t = match features::spec(&std, &size, pitch_row.selected() as usize, &units) {
                Ok(t) => t,
                Err(e) => {
                    this.toast(&e.to_string());
                    return;
                }
            };
            let (relief, with_op, w) = (relief.is_active(), with_op.is_active(), this.w());
            if this.edit(&tr("Thread {name}", &[("name", &t.name())]), |q| {
                features::add_thread(q, &gid, &t, None, relief)?;
                if with_op {
                    let at = q.operations.len();
                    w.insert_op(q, at, "thread", |op| {
                        if let Operation::Thread(th) = op {
                            th.pitch = t.pitch;
                        }
                    });
                }
                Ok(())
            }) {
                this.select_element_(None);
                this.toast(&tr(
                    "{name}: minor Ø{minor:g}, depth {depth:g}",
                    &[("name", &t.name()), ("minor:g", &fmt_g(t.minor())), ("depth:g", &fmt_g(t.depth()))],
                ));
            }
        }));
        dlg.present(Some(&self.w()));
    }

    /// ALPHA-T MIRROR IMAGE: mirror the whole drawing about the axis at once, show it, then ask whether it is correct;
    /// No takes it back.
    fn mirror_image(&self) {
        if !self.edit(&gettext("Mirror Image"), |p| drawing::mirror_axis(p).map(|_| ())) {
            return;
        }
        self.c.fit();
        // ALPHA-T asks "Is this correct?" over the drawing; a banner keeps the mirrored drawing in full view
        self.s.borrow_mut().construct = Some(Construct { kind: "mirror-confirm".into(), refs: vec![], choices: None });
        self.construct_banner.set_button_label(Some(&gettext("_No, Take It Back")));
        self.prompt(&gettext("Mirrored about the spindle axis. Is this correct? If yes, just carry on."));
    }

    pub(super) fn banner_button(&self) {
        let mirror = self.s.borrow().construct.as_ref().is_some_and(|c| c.kind == "mirror-confirm");
        if mirror {
            // No: take the mirror image back
            let w = self.w();
            w.undo();
            w.refresh(false);
            self.c.fit();
        }
        self.cancel_construct();
    }

    fn prompt(&self, text: &str) {
        self.construct_banner.set_title(text);
        self.construct_banner.set_revealed(true);
    }

    pub(super) fn cancel_construct(&self) {
        self.s.borrow_mut().construct = None;
        self.construct_banner.set_button_label(Some(&gettext("_Cancel")));
        self.construct_banner.set_revealed(false);
        self.c.overlay(|o| o.previews.clear());
    }

    /// The construction's change, as one undoable step; the construction ends either way.
    fn construct_edit(&self, label: &str, change: impl FnOnce(&mut Project) -> alphacam::error::Result<()>) {
        self.edit(label, change);
        self.cancel_construct();
    }

    /// A default size given in mm, in the part's units.
    fn mm(&self, v: f64) -> f64 {
        if self.project().is_some_and(|p| p.units == "inch") { round(v / 25.4, 3) } else { v }
    }

    fn ref_at(&self, p: Point, tol: f64, el: &str) -> Ref {
        let Some(project) = self.project() else { return Ref::Point(p) };
        match element(&project, el) {
            Some(g) if g.is_arc() => return Ref::Arc(g.id().into()),
            Some(g) if drawing::distance_to(g, p) <= tol => {
                let near = if p.distance(&g.a()) <= p.distance(&g.b()) { g.a() } else { g.b() };
                if near.distance(&p) > tol {
                    return Ref::Line(g.id().into());
                }
                return Ref::Point(near);
            }
            _ => {}
        }
        Ref::Point(drawing::snap(&project, p, tol, grid(&project)).0)
    }

    /// Several solutions: show them dashed and let a tap pick one; one: take it; none: say so.
    fn choose(&self, previews: Vec<PreviewShape>, then: Then) {
        if previews.is_empty() {
            self.toast(&gettext("No solution for these elements"));
            self.cancel_construct();
            return;
        }
        if previews.len() == 1 {
            then(previews[0].clone());
            return;
        }
        self.c.overlay(|o| o.previews = previews.clone());
        if let Some(c) = self.s.borrow_mut().construct.as_mut() {
            c.choices = Some((previews, then));
        }
        self.prompt(&gettext("Tap the one you want (dashed)"));
    }

    /// The tap of a line to add: one undoable step that ends the construction.
    fn add_line_then(&self, label: &'static str) -> Then {
        let this = self.rc();
        Rc::new(weak!(this, move |pv: PreviewShape| {
            if let PreviewShape::Line(a, b) = pv {
                this.construct_edit(&gettext(label), |p| drawing::add_line(p, a, b).map(|_| ()));
            }
        }))
    }

    /// Solutions found later (after a numbers dialog): an error is said and ends the construction.
    fn choose_found(&self, found: alphacam::error::Result<Vec<PreviewShape>>, then: Then) {
        match found {
            Ok(previews) => self.choose(previews, then),
            Err(e) => {
                self.toast(&e.to_string());
                self.cancel_construct();
            }
        }
    }

    pub(super) fn construct_tap(&self, p: Point, tol: f64, el: &str) {
        let (kind, choices) = {
            let s = self.s.borrow();
            let Some(c) = s.construct.as_ref() else { return };
            (c.kind.clone(), c.choices.clone())
        };
        if let Some((previews, then)) = choices {
            let best = previews.iter().min_by(|a, b| dist_to_preview(a, p).total_cmp(&dist_to_preview(b, p)));
            if let Some(best) = best {
                then(best.clone());
            }
            return;
        }
        if kind == "mirror-confirm" {
            // carrying on means yes
            self.cancel_construct();
            return;
        }
        // a wrong tap is said, and the construction waits for the right one
        if let Err(message) = self.construct_step(&kind, p, tol, el) {
            self.toast(&message);
        }
    }

    fn construct_step(&self, kind: &str, p: Point, tol: f64, el: &str) -> Result<(), String> {
        let mut r = self.ref_at(p, tol, el);
        let Some(project) = self.project() else { return Ok(()) };
        let this = self.rc();
        match kind {
            "thread" => {
                if self.diameter_line(Some(el)).is_none() {
                    return Err(gettext("Tap a line along Z (a diameter)"));
                }
                self.cancel_construct();
                self.thread_dialog(el);
            }
            "split" => {
                let Ref::Arc(id) = r else { return Err(gettext("Tap an arc")) };
                self.construct_edit(&gettext("Split arc"), |pr| drawing::split_arc(pr, &id, p).map(|_| ()));
            }
            "parallel" => {
                if matches!(r, Ref::Point(_)) && element(&project, el).is_some_and(|g| !g.is_arc()) {
                    r = Ref::Line(el.into());
                }
                let Ref::Line(id) = r else { return Err(gettext("Tap a line")) };
                let then = self.add_line_then("Parallel line");
                self.numbers(
                    &gettext("Parallel Line"),
                    &[(gettext("Distance"), self.mm(1.0), 0.001, 1000.0)],
                    weak!(this, move |v: Vec<f64>| {
                        let found = this.project().map(|q| drawing::parallels(&q, &id, v[0]).map(lines));
                        if let Some(found) = found {
                            this.choose_found(found, then.clone());
                        }
                    }),
                );
            }
            "tangent-angle" => {
                let Ref::Arc(id) = r else { return Err(gettext("Tap an arc")) };
                let then = self.add_line_then("Tangent");
                self.numbers(
                    &gettext("Tangent at an Angle"),
                    &[(gettext("Angle"), 0.0, -360.0, 360.0)],
                    weak!(this, move |v: Vec<f64>| {
                        let found = this.project().map(|q| drawing::tangents_at_angle(&q, &id, v[0]).map(lines));
                        if let Some(found) = found {
                            this.choose_found(found, then.clone());
                        }
                    }),
                );
            }
            "tangent" => {
                let refs = {
                    let mut s = self.s.borrow_mut();
                    let Some(c) = s.construct.as_mut() else { return Ok(()) };
                    if c.refs.is_empty() && matches!(r, Ref::Line(_)) {
                        return Err(gettext("Tap a point or an arc"));
                    }
                    c.refs.push(r);
                    c.refs.clone()
                };
                if refs.len() == 1 {
                    self.prompt(&gettext("Tangent: now tap the arc"));
                    return Ok(());
                }
                let Ref::Arc(second) = &refs[1] else {
                    if let Some(c) = self.s.borrow_mut().construct.as_mut() {
                        c.refs.pop();
                    }
                    return Err(gettext("Tap an arc"));
                };
                let found = match &refs[0] {
                    Ref::Arc(first) => drawing::tangents_arc_arc(&project, first, second),
                    Ref::Point(first) => drawing::tangents_point_arc(&project, *first, second),
                    Ref::Line(_) => return Err(gettext("Tap a point or an arc")),
                };
                self.choose(lines(found.map_err(|e| e.to_string())?), self.add_line_then("Tangent"));
            }
            "blend" => {
                let refs = {
                    let mut s = self.s.borrow_mut();
                    let Some(c) = s.construct.as_mut() else { return Ok(()) };
                    c.refs.push(r);
                    c.refs.clone()
                };
                if refs.len() == 1 {
                    self.prompt(&gettext("Blend arc: now the 2nd point, line or arc"));
                    return Ok(());
                }
                // an arc can be touched from outside (convex) or inside: both are tried
                let expand = |r: &Ref| match r {
                    Ref::Point(p) => vec![BlendRef::Point(*p)],
                    Ref::Line(id) => vec![BlendRef::Line(id.clone())],
                    Ref::Arc(id) => vec![BlendRef::Arc(id.clone(), true), BlendRef::Arc(id.clone(), false)],
                };
                let same = |b: &BlendRef, r: &Ref| match (b, r) {
                    (BlendRef::Point(a), Ref::Point(b)) => a == b,
                    (BlendRef::Line(a) | BlendRef::Arc(a, _), Ref::Line(b) | Ref::Arc(b)) => a == b,
                    _ => false,
                };
                let all: Vec<BlendRef> = refs.iter().flat_map(expand).collect();
                let r1: Vec<BlendRef> = all.iter().filter(|b| same(b, &refs[0])).cloned().collect();
                let r2: Vec<BlendRef> = all.iter().filter(|b| same(b, &refs[1])).cloned().collect();
                let then: Then = Rc::new(weak!(this, move |pv: PreviewShape| {
                    if let PreviewShape::Arc(s, c, e) = pv {
                        this.construct_edit(&gettext("Blend arc"), |pr| drawing::add_blend(pr, s, c, e).map(|_| ()));
                    }
                }));
                let this = self.rc();
                self.numbers(
                    &gettext("Blend Arc"),
                    &[(gettext("Radius"), self.mm(5.0), 0.001, 1000.0)],
                    weak!(this, move |v: Vec<f64>| {
                        let Some(q) = this.project() else { return };
                        let mut sols = vec![];
                        for a in &r1 {
                            for b in &r2 {
                                sols.extend(drawing::blends(&q, a, b, v[0]).unwrap_or_default());
                            }
                        }
                        let previews = sols.into_iter().map(|(s, c, e)| PreviewShape::Arc(s, c, e)).collect();
                        this.choose(previews, then.clone());
                    }),
                );
            }
            _ => {}
        }
        Ok(())
    }
}
