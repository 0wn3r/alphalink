//! Nearest toolpath segment within a screen-pixel radius (TOUCH.md 3: mouse 6, pen 10, touch 24 px).

use alphacam::model::{Element, Project};
use alphacam::moves::Segment;
use alphacam::toolpath::MoveKind;

use super::view::ViewTransform;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Mouse,
    Pen,
    Touch,
}

impl Source {
    pub fn radius_px(self) -> f64 {
        match self {
            Source::Mouse => 6.0,
            Source::Pen => 10.0,
            Source::Touch => 24.0,
        }
    }
}

fn dist_point_segment((px, py): (f64, f64), (ax, ay): (f64, f64), (bx, by): (f64, f64)) -> f64 {
    let (dx, dy) = (bx - ax, by - ay);
    let l2 = dx * dx + dy * dy;
    let t = if l2 == 0.0 { 0.0 } else { (((px - ax) * dx + (py - ay) * dy) / l2).clamp(0.0, 1.0) };
    (px - (ax + t * dx)).hypot(py - (ay + t * dy))
}

/// Distance from a screen point to a polyline given in (X diameter, Z); a single point counts as one.
fn distance(view: &ViewTransform, pts: &[(f64, f64)], sx: f64, sy: f64) -> f64 {
    let screen: Vec<(f64, f64)> = pts.iter().map(|&(x, z)| view.to_screen(x, z)).collect();
    if screen.len() == 1 {
        return dist_point_segment((sx, sy), screen[0], screen[0]);
    }
    screen.windows(2).map(|w| dist_point_segment((sx, sy), w[0], w[1])).fold(f64::INFINITY, f64::min)
}

pub fn hit<'a>(segs: &'a [Segment], view: &ViewTransform, sx: f64, sy: f64, radius_px: f64) -> Option<&'a Segment> {
    let (mut best, mut best_d) = (None, radius_px);
    for s in segs {
        let pts = s.polyline(16);
        let pts = if pts.len() < 2 { vec![pts[0], pts[0]] } else { pts };
        let d = distance(view, &pts, sx, sy);
        if d <= best_d {
            (best, best_d) = (Some(s), d);
        }
    }
    best
}

/// Points along a drawing element (lines: ends; arcs: sampled), as (X diameter, Z).
pub fn element_polyline(g: &Element) -> Vec<(f64, f64)> {
    let (a, b) = (g.a(), g.b());
    match g.centre() {
        Some(c) => Segment::new(if g.ccw() { MoveKind::ArcCcw } else { MoveKind::ArcCw }, (a.x, a.z), (b.x, b.z),
                                Some((c.x, c.z)))
            .polyline(16),
        None => vec![(a.x, a.z), (b.x, b.z)],
    }
}

/// Id of the drawing element nearest the screen point within radius_px, or None.
pub fn hit_element(project: &Project, view: &ViewTransform, sx: f64, sy: f64, radius_px: f64) -> Option<String> {
    let (mut best, mut best_d) = (None, radius_px);
    for g in &project.geometry {
        let d = distance(view, &element_polyline(g), sx, sy);
        if d <= best_d {
            (best, best_d) = (Some(g.id().to_string()), d);
        }
    }
    best
}

/// Ids of the elements within radius_px whose distance is within near_px of the nearest one (a corner gives two:
/// the Select tool then asks which one, TOUCH.md 5.2), nearest first.
pub fn hit_elements(project: &Project, view: &ViewTransform, sx: f64, sy: f64, radius_px: f64, near_px: f64)
                    -> Vec<String> {
    let mut found: Vec<(f64, String)> = project
        .geometry
        .iter()
        .map(|g| (distance(view, &element_polyline(g), sx, sy), g.id().to_string()))
        .filter(|(d, _)| *d <= radius_px)
        .collect();
    found.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    let nearest = found.first().map(|f| f.0).unwrap_or_default();
    found.into_iter().filter(|(d, _)| d - nearest <= near_px).map(|(_, id)| id).collect()
}
