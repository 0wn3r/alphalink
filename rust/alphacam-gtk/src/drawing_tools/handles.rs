//! Moving an end point by dragging its handle (TOUCH.md 5.1 loupe and fine drag, 5.2 move point): the canvas' handle
//! hooks.

use std::rc::Weak;

use alphacam::drawing;
use alphacam::model::Point;
use alphacam::py::round;

use super::{DrawingTools, grid, readout_xz};
use crate::canvas::hittest::Source;
use crate::canvas::widget::{HandleHooks, MarkerKind};
use crate::i18n::gettext;

/// A handle being dragged.
pub(super) struct Move {
    /// where the point was
    orig: Point,
    /// screen position of the moved point (a slow finger moves it 1:4)
    cur: (f64, f64),
    /// the finger's last position
    last: (f64, f64),
    touch: bool,
    /// where it goes
    new: Point,
}

/// What the canvas calls: holds the drawing tools weakly (the canvas outlives nothing it should keep alive).
pub(super) struct Hooks(pub Weak<DrawingTools>);

impl HandleHooks for Hooks {
    fn begin(&self, x: f64, y: f64, source: Source) -> bool {
        self.0.upgrade().is_some_and(|t| t.handle_begin(x, y, source))
    }

    fn drag(&self, x: f64, y: f64) {
        if let Some(t) = self.0.upgrade() {
            t.handle_drag(x, y);
        }
    }

    fn end(&self) {
        if let Some(t) = self.0.upgrade() {
            t.handle_end();
        }
    }

    fn cancel(&self) {
        if let Some(t) = self.0.upgrade() {
            t.handle_cancel();
        }
    }
}

impl DrawingTools {
    /// A drag starts: true when it starts on a handle of the selected element (then it moves that point).
    fn handle_begin(&self, x: f64, y: f64, source: Source) -> bool {
        let best = {
            let s = self.c.state();
            if s.tool != "select" || self.s.borrow().selected.is_none() || s.overlay.handles.is_empty() {
                return false;
            }
            let r = source.radius_px();
            let mut best: Option<(f64, Point, f64, f64)> = None;
            for h in &s.overlay.handles {
                let (sx, sy) = s.view.to_screen(h.x, h.z);
                let d = (sx - x).hypot(sy - y);
                if d <= r && best.is_none_or(|b| d < b.0) {
                    best = Some((d, *h, sx, sy));
                }
            }
            best
        };
        let Some((_, h, sx, sy)) = best else { return false };
        let touch = source == Source::Touch;
        self.s.borrow_mut().mov = Some(Move { orig: h, cur: (sx, sy), last: (x, y), touch, new: h });
        true
    }

    fn handle_drag(&self, x: f64, y: f64) {
        let Some(p) = self.project() else { return };
        let (orig, cur, touch, fine) = {
            let mut s = self.s.borrow_mut();
            let Some(m) = s.mov.as_mut() else { return };
            let (dx, dy) = (x - m.last.0, y - m.last.1);
            m.last = (x, y);
            let fine = dx.hypot(dy) < 2.0; // slow finger: 1:4, for sub-grid adjustments
            let gain = if fine { 0.25 } else { 1.0 };
            m.cur.0 += dx * gain;
            m.cur.1 += dy * gain;
            (m.orig, m.cur, m.touch, fine)
        };
        let (view_pt, tol) = {
            let s = self.c.state();
            let (xd, z) = s.view.to_world(cur.0, cur.1);
            (Point::new(xd, z), 10.0 / s.view.scale)
        };
        let g = if fine { 0.0 } else { grid(&p) };
        let (mut pt, mut kind) = drawing::snap(&p, view_pt, tol, g);
        if kind == "end" && pt == orig {
            // its own old place does not hold it
            (pt, kind) = drawing::snap(&p, view_pt, 0.0, g);
        }
        if kind == "free" {
            pt = Point::new(round(pt.x, 3), round(pt.z, 3));
        }
        if let Some(m) = self.s.borrow_mut().mov.as_mut() {
            m.new = pt;
        }
        let ghost: Vec<(Point, Point)> = p
            .geometry
            .iter()
            .filter(|g| g.a() == orig || g.b() == orig)
            .map(|g| (if g.a() == orig { g.b() } else { g.a() }, pt))
            .collect();
        self.c.overlay(|o| {
            o.ghost = ghost;
            o.marker = Some((pt, MarkerKind::End));
            o.loupe = if touch { Some((x, y)) } else { None };
        });
        self.readout(&readout_xz(pt));
    }

    fn handle_end(&self) {
        let m = self.s.borrow_mut().mov.take();
        self.c.overlay(|o| {
            o.ghost.clear();
            o.loupe = None;
        });
        let Some(m) = m.filter(|m| m.new != m.orig) else { return };
        let Some(gid) = self.s.borrow().selected.clone() else { return };
        let Some(g) = self.project().and_then(|p| super::element(&p, &gid).cloned()) else { return };
        let (a, b) = if g.a() == m.orig { (m.new, g.b()) } else { (g.a(), m.new) };
        let radius = g.radius();
        if self.edit(&gettext("Move point"), |p| drawing::edit_element(p, &gid, a, b, radius, true)) {
            self.select_element_(Some(gid));
        }
        self.c.overlay(|o| o.marker = None);
    }

    fn handle_cancel(&self) {
        self.s.borrow_mut().mov = None;
        self.c.overlay(|o| {
            o.ghost.clear();
            o.loupe = None;
            o.marker = None;
        });
    }
}
