//! Drawing operations on a project's geometry (the canvas tools call these through the undo stack).
//!
//! Coordinates are X diameter and Z. Geometry maths is done in (radius, Z). TOUCH.md 5.1: rough placement is snapped
//! (end points, the axis, a grid) and direction-locked (Z, X, 45 degrees); exact values come from typed entry.

use std::collections::HashMap;
use std::f64::consts::PI;

use indexmap::IndexMap;

use crate::error::{Error, Result, value};
use crate::i18n::tr;
use crate::model::{Arc, Element, Line, Operation, Point, Project, Text};
use crate::py::{self, fmod_floor, fmt_g, hypot, round, round_int};
use crate::toolpath::contour::chain;

pub const SNAP_LOCK_DEG: f64 = 8.0;

/// A point in (radius, Z).
type Rz = (f64, f64);

pub fn next_id(project: &Project) -> String {
    let mut n = project.geometry.len();
    while project.geometry.iter().any(|g| g.id() == format!("g{n}")) {
        n += 1;
    }
    format!("g{n}")
}

pub fn endpoints(project: &Project) -> Vec<Point> {
    let mut pts = vec![];
    for g in &project.geometry {
        for p in [g.a(), g.b()] {
            if !pts.contains(&p) {
                pts.push(p);
            }
        }
    }
    pts
}

/// Snap a rough point (tolerance in mm, from the input's screen radius): end points, then the axis, then grid.
/// The kind is "end", "axis", "grid" or "free".
pub fn snap(project: &Project, p: Point, tol: f64, grid: f64) -> (Point, &'static str) {
    let (mut best, mut best_d) = (None, tol);
    for q in endpoints(project) {
        let d = p.distance(&q);
        if d <= best_d {
            (best, best_d) = (Some(q), d);
        }
    }
    if let Some(q) = best {
        return (q, "end");
    }
    let (mut p, mut kind) = if p.x.abs() / 2.0 <= tol { (Point::new(0.0, p.z), "axis") } else { (p, "free") };
    if grid > 0.0 {
        let gx = if kind == "axis" { 0.0 } else { round_int(p.x / 2.0 / grid) as f64 * grid * 2.0 };
        p = Point::new(round(gx, 6), round(round_int(p.z / grid) as f64 * grid, 6));
        if kind != "axis" {
            kind = "grid";
        }
    }
    (p, kind)
}

/// Lock the segment prev -> p to Z, X or 45 degrees when it is within `deg` of them (radius/Z space).
pub fn lock_direction(prev: Point, p: Point, deg: f64) -> Point {
    let (dr, dz) = ((p.x - prev.x) / 2.0, p.z - prev.z);
    if hypot(dr, dz) == 0.0 {
        return p;
    }
    let ang = dr.atan2(dz).to_degrees();
    for target in [0.0, 45.0, 90.0, 135.0, 180.0, -45.0, -90.0, -135.0, -180.0f64] {
        if (ang - target).abs() <= deg {
            let a = target.to_radians();
            if [0.0, 180.0, -180.0].contains(&target) {
                // along Z: keep the Z length, X fixed
                return Point::new(prev.x, p.z);
            }
            if [90.0, -90.0].contains(&target) {
                return Point::new(p.x, prev.z);
            }
            let along = dr * a.sin() + dz * a.cos();
            return Point::new(round(prev.x + 2.0 * along * a.sin(), 6), round(prev.z + along * a.cos(), 6));
        }
    }
    p
}

pub fn add_line(project: &mut Project, a: Point, b: Point) -> Result<String> {
    if a == b {
        return value(tr("a line needs two different points", &[]));
    }
    let gid = next_id(project);
    project.geometry.push(Element::line(a, b, gid.clone()));
    Ok(gid)
}

fn no_element(gid: &str) -> Error {
    Error::Value(tr("no element {gid}", &[("gid", &gid)]))
}

/// The picked contour of an operation that has one.
pub fn contour_mut(op: &mut Operation) -> Option<&mut Vec<String>> {
    match op {
        Operation::AreaClear(o) => Some(&mut o.contour),
        Operation::FaceClear(o) => Some(&mut o.contour),
        Operation::Groove(o) => Some(&mut o.contour),
        Operation::FinishPass(o) => Some(&mut o.contour),
        _ => None,
    }
}

pub fn delete_element(project: &mut Project, gid: &str) -> Result<()> {
    let before = project.geometry.len();
    project.geometry.retain(|g| g.id() != gid);
    if project.geometry.len() == before {
        return Err(no_element(gid));
    }
    for op in &mut project.operations {
        // drop it from contours that used it
        if let Some(contour) = contour_mut(op) {
            contour.retain(|c| c != gid);
        }
    }
    Ok(())
}

fn rz(p: Point) -> Rz {
    (p.x / 2.0, p.z)
}

fn pt(rz: Rz) -> Point {
    Point::new(round(2.0 * rz.0, 6), round(rz.1, 6))
}

fn set_a(e: &mut Element, p: Point) {
    match e {
        Element::Line(l) => l.a = p,
        Element::Arc(c) => c.a = p,
    }
}

fn set_b(e: &mut Element, p: Point) {
    match e {
        Element::Line(l) => l.b = p,
        Element::Arc(c) => c.b = p,
    }
}

/// Python's `max(lo, min(hi, x))`.
fn clamp(x: f64, lo: f64, hi: f64) -> f64 {
    let y = if x < hi { x } else { hi };
    if y > lo { y } else { lo }
}

/// Python's `max(a, b)`: `a` unless `b` is larger.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Centre for the arc a -> b with this radius, on the same side of the chord as the arc's centre was (so the
/// direction and the short/long sweep stay as drawn).
fn refit_arc(arc: &Arc, a: Point, b: Point, radius: f64) -> Result<Point> {
    let ((ar, az), (br, bz)) = (rz(a), rz(b));
    let (dr, dz) = (br - ar, bz - az);
    let d = hypot(dr, dz);
    if d < 1e-9 || d > 2.0 * radius + 1e-9 {
        return value(tr("an arc of radius {r} cannot join X{ax} Z{az} and X{bx} Z{bz}",
                        &[("r", &fmt_g(radius)), ("ax", &fmt_g(a.x)), ("az", &fmt_g(a.z)), ("bx", &fmt_g(b.x)),
                          ("bz", &fmt_g(b.z))]));
    }
    let h = py_max(radius * radius - d * d / 4.0, 0.0).sqrt();
    let (mr, mz) = ((ar + br) / 2.0, (az + bz) / 2.0);
    let (nr, nz) = (-dz / d, dr / d); // unit normal to the chord
    let ((oar, oaz), (obr, obz), (ocr, ocz)) = (rz(arc.a), rz(arc.b), rz(arc.centre));
    let side = (obr - oar) * (ocz - oaz) - (obz - oaz) * (ocr - oar);
    let (c1, c2) = ((mr + nr * h, mz + nz * h), (mr - nr * h, mz - nz * h));
    let s1 = dr * (c1.1 - az) - dz * (c1.0 - ar);
    Ok(pt(if (s1 >= 0.0) == (side >= 0.0) { c1 } else { c2 }))
}

/// Give element gid new end points (and, for an arc, a new radius). With follow, the ends of other elements that
/// met the moved ends move with them, so the profile stays joined; arcs among them keep their radius.
pub fn edit_element(project: &mut Project, gid: &str, a: Point, b: Point, radius: Option<f64>, follow: bool)
                    -> Result<()> {
    let gi = project.geometry.iter().position(|e| e.id() == gid).ok_or_else(|| no_element(gid))?;
    let g = &project.geometry[gi];
    let moves: Vec<(Point, Point)> = if g.a() != g.b() { vec![(g.a(), a), (g.b(), b)] } else { vec![(g.a(), a)] };
    let moved = |p: Point| moves.iter().find(|(old, _)| *old == p).map(|(_, new)| *new);
    // element id -> its new ends (a dict: a later element with the same id wins)
    let mut new_ends: IndexMap<String, (Point, Point)> = IndexMap::new();
    for (i, e) in project.geometry.iter().enumerate() {
        if i == gi {
            new_ends.insert(e.id().into(), (a, b));
        } else if follow && (moved(e.a()).is_some() || moved(e.b()).is_some()) {
            new_ends.insert(e.id().into(), (moved(e.a()).unwrap_or(e.a()), moved(e.b()).unwrap_or(e.b())));
        }
    }
    for (i, e) in project.geometry.iter_mut().enumerate() {
        let Some(&(na, nb)) = new_ends.get(e.id()) else { continue };
        match e {
            Element::Line(l) => {
                if na.distance(&nb) < 1e-9 {
                    return value(tr("{gid} would have no length", &[("gid", &l.id)]));
                }
            }
            Element::Arc(c) => {
                let r = match radius {
                    Some(r) if i == gi && r != 0.0 => r,
                    _ => c.a.distance(&c.centre),
                };
                c.centre = refit_arc(c, na, nb, r)?;
            }
        }
        set_a(e, na);
        set_b(e, nb);
    }
    Ok(())
}

/// The two lines that meet at p (indices into the geometry, in drawing order).
fn corner_lines(project: &Project, p: Point) -> Result<(usize, usize)> {
    let lines: Vec<usize> = project
        .geometry
        .iter()
        .enumerate()
        .filter(|(_, g)| matches!(g, Element::Line(l) if l.a == p || l.b == p))
        .map(|(i, _)| i)
        .collect();
    if lines.len() != 2 {
        return value(tr("X{x} Z{z} is not a corner between two lines", &[("x", &fmt_g(p.x)), ("z", &fmt_g(p.z))]));
    }
    Ok((lines[0], lines[1]))
}

fn away(line: &Element, p: Point) -> Point {
    if line.a() == p { line.b() } else { line.a() }
}

fn set_end(line: &mut Element, p: Point, new: Point) {
    if line.a() == p { set_a(line, new) } else { set_b(line, new) }
}

/// A fillet or chamfer as long as a whole line uses it up: remove the zero-length line that is left (and its id
/// from the contours), so the next corner edit does not divide by its length.
fn drop_used_up(project: &mut Project, lines: [Element; 2]) -> Result<()> {
    for g in lines {
        if g.a().distance(&g.b()) < 1e-9 && project.geometry.contains(&g) {
            delete_element(project, g.id())?;
        }
    }
    Ok(())
}

/// True if l1 ends at p and l2 starts there (drawing order), else decided by element order.
fn is_before(project: &Project, i1: usize, i2: usize, p: Point) -> bool {
    let (l1, l2) = (&project.geometry[i1], &project.geometry[i2]);
    if l1.b() == p && l2.a() == p {
        return true;
    }
    if l2.b() == p && l1.a() == p {
        return false;
    }
    i1 < i2
}

fn unit(q: Point, p: Point) -> Rz {
    norm((q.x / 2.0 - p.x / 2.0, q.z - p.z))
}

fn norm(v: Rz) -> Rz {
    let n = hypot(v.0, v.1);
    (v.0 / n, v.1 / n)
}

fn len(line: &Element) -> f64 {
    line.a().distance(&line.b())
}

/// Round the corner at p between two lines with an arc tangent to both (ALPHA-T 'radius/chamfer F').
pub fn fillet(project: &mut Project, p: Point, radius: f64) -> Result<String> {
    if radius <= 0.0 {
        return value(tr("radius must be > 0", &[]));
    }
    let (i1, i2) = corner_lines(project, p)?;
    let (pr, pz) = (p.x / 2.0, p.z);
    let u1 = unit(away(&project.geometry[i1], p), p);
    let u2 = unit(away(&project.geometry[i2], p), p);
    let cos_t = clamp(u1.0 * u2.0 + u1.1 * u2.1, -1.0, 1.0);
    let theta = cos_t.acos();
    if theta < 1e-6 || (theta - PI).abs() < 1e-6 {
        return value(tr("the lines are parallel at this point", &[]));
    }
    let d = radius / (theta / 2.0).tan();
    let shorter = len(&project.geometry[i1]).min(len(&project.geometry[i2]));
    if d > shorter + 1e-9 {
        return value(tr("radius {radius} is too large for these lines", &[("radius", &fmt_g(radius))]));
    }
    let t1 = (pr + u1.0 * d, pz + u1.1 * d);
    let t2 = (pr + u2.0 * d, pz + u2.1 * d);
    let bis = norm((u1.0 + u2.0, u1.1 + u2.1));
    let h = radius / (theta / 2.0).sin();
    let c = (pr + bis.0 * h, pz + bis.1 * h);
    // orientation: walking from t1 to t2 around c; G3 (ccw=True) is counter-clockwise with Z right and X up, i.e.
    // in the (z, r) plane
    let cross = (t1.1 - c.1) * (t2.0 - c.0) - (t1.0 - c.0) * (t2.1 - c.1); // (z, r) cross product
    let mut ccw = cross > 0.0;
    let (tp1, tp2, centre) = (pt(t1), pt(t2), pt(c));
    let first_is_l1 = is_before(project, i1, i2, p);
    let (start, end) = if first_is_l1 { (tp1, tp2) } else { (tp2, tp1) };
    if !first_is_l1 {
        ccw = !ccw;
    }
    set_end(&mut project.geometry[i1], p, tp1);
    set_end(&mut project.geometry[i2], p, tp2);
    let gid = next_id(project);
    project.geometry.push(Element::arc(start, end, centre, ccw, gid.clone()));
    let lines = [project.geometry[i1].clone(), project.geometry[i2].clone()];
    drop_used_up(project, lines)?;
    Ok(gid)
}

/// Cut the corner at p with a straight chamfer: `size` along the more axial (Z-parallel) of the two lines and
/// `angle` degrees between the chamfer and that line (lathe drawing style "2 x 30 deg", angle from the axis). The
/// leg on the other line follows from the corner angle (law of sines; 45 deg on a square corner = size on both).
pub fn chamfer(project: &mut Project, p: Point, size: f64, angle: f64) -> Result<String> {
    if size <= 0.0 {
        return value(tr("chamfer size must be > 0", &[]));
    }
    if !(0.0 < angle && angle < 90.0) {
        return value(tr("chamfer angle must be between 0 and 90 degrees", &[]));
    }
    let (i1, i2) = corner_lines(project, p)?;
    let (pr, pz) = (p.x / 2.0, p.z);
    let (u1, u2) = (unit(away(&project.geometry[i1], p), p), unit(away(&project.geometry[i2], p), p));
    // reference line: the one closer to Z-parallel (|dz| component larger); the other gets the computed leg
    let ref_first = u1.1.abs() >= u2.1.abs();
    let (ua, ub) = if ref_first { (u1, u2) } else { (u2, u1) };
    let corner = clamp(ua.0 * ub.0 + ua.1 * ub.1, -1.0, 1.0).acos();
    let theta = angle.to_radians();
    if theta + corner >= PI - 1e-9 {
        return value(tr("a {angle} degree chamfer does not fit this corner", &[("angle", &fmt_g(angle))]));
    }
    let other = size * theta.sin() / (theta + corner).sin();
    let (s1, s2) = if ref_first { (size, other) } else { (other, size) };
    for (i, s) in [(i1, s1), (i2, s2)] {
        let (a, b) = (project.geometry[i].a(), project.geometry[i].b());
        if s > hypot((a.x - b.x) / 2.0, a.z - b.z) + 1e-9 {
            return value(tr("chamfer {size} x {angle} deg is too large for these lines",
                            &[("size", &fmt_g(size)), ("angle", &fmt_g(angle))]));
        }
    }
    let tp1 = pt((pr + u1.0 * s1, pz + u1.1 * s1));
    let tp2 = pt((pr + u2.0 * s2, pz + u2.1 * s2));
    let first_is_l1 = is_before(project, i1, i2, p);
    set_end(&mut project.geometry[i1], p, tp1);
    set_end(&mut project.geometry[i2], p, tp2);
    let (a, b) = if first_is_l1 { (tp1, tp2) } else { (tp2, tp1) };
    let gid = next_id(project);
    project.geometry.push(Element::line(a, b, gid.clone()));
    let lines = [project.geometry[i1].clone(), project.geometry[i2].clone()];
    drop_used_up(project, lines)?;
    Ok(gid)
}

/// The element with this id as a dict of the geometry finds it: the last one.
fn by_id<'a>(project: &'a Project, gid: &str) -> Option<&'a Element> {
    project.geometry.iter().rev().find(|g| g.id() == gid)
}

/// The element with this id as `next(e for e in geometry if e.id == gid)` finds it: the first one.
fn first_with_id<'a>(project: &'a Project, gid: &str) -> Option<&'a Element> {
    project.geometry.iter().find(|g| g.id() == gid)
}

/// Contour picking (TOUCH.md 7.1): keep the tapped elements, ordered as a connected chain from the first.
pub fn pick_order(project: &Project, picked: &[String]) -> Result<Vec<String>> {
    let elems: Vec<&Element> = picked.iter().filter_map(|i| by_id(project, i)).collect();
    if elems.is_empty() {
        return Ok(vec![]);
    }
    let mut remaining: Vec<&Element> = elems[1..].to_vec();
    let mut order = vec![elems[0]];
    let mut path = chain(&[elems[0]])?;
    while !remaining.is_empty() {
        let end = path[path.len() - 1].b;
        let start = path[0].a;
        let k = match remaining.iter().position(|e| e.a() == end || e.b() == end) {
            Some(k) => {
                order.push(remaining[k]);
                k
            }
            None => {
                let Some(k) = remaining.iter().position(|e| e.a() == start || e.b() == start) else {
                    return value(tr("{id} does not connect to the picked contour", &[("id", &remaining[0].id())]));
                };
                order.insert(0, remaining[k]);
                k
            }
        };
        remaining.remove(k);
        path = chain(&order)?;
    }
    Ok(order.iter().map(|e| e.id().to_string()).collect())
}

/// Arc from a through m to b (radius / Z space). The direction follows the three points: counter-clockwise with Z
/// right and X up is G3 (ccw=True).
pub fn add_arc_3p(project: &mut Project, a: Point, m: Point, b: Point) -> Result<String> {
    let ((az, ar), (mz, mr), (bz, br)) = ((a.z, a.x / 2.0), (m.z, m.x / 2.0), (b.z, b.x / 2.0));
    let d = 2.0 * (az * (mr - br) + mz * (br - ar) + bz * (ar - mr));
    if d.abs() < 1e-9 {
        return value(tr("the three points are on a line", &[]));
    }
    let (s_a, s_m, s_b) = (az * az + ar * ar, mz * mz + mr * mr, bz * bz + br * br);
    let cz = (s_a * (mr - br) + s_m * (br - ar) + s_b * (ar - mr)) / d;
    let cr = (s_a * (bz - mz) + s_m * (az - bz) + s_b * (mz - az)) / d;
    let ccw = d > 0.0; // orientation of a -> m -> b in the (z, r) plane
    let gid = next_id(project);
    project.geometry.push(Element::arc(a, b, pt((cr, cz)), ccw, gid.clone()));
    Ok(gid)
}

/// Trim or extend two lines so that they meet: the end of each line nearer the intersection moves onto it.
pub fn corner(project: &mut Project, id1: &str, id2: &str) -> Result<Point> {
    let index = |gid: &str| project.geometry.iter().rposition(|g| g.id() == gid);
    let (i1, i2) = match (index(id1), index(id2)) {
        (Some(i1), Some(i2)) if i1 != i2
            && matches!(project.geometry[i1], Element::Line(_))
            && matches!(project.geometry[i2], Element::Line(_)) => (i1, i2),
        _ => return value(tr("pick two different lines", &[])),
    };
    let (l1, l2) = (&project.geometry[i1], &project.geometry[i2]);
    let ((a1r, a1z), (b1r, b1z)) = (rz(l1.a()), rz(l1.b()));
    let ((a2r, a2z), (b2r, b2z)) = (rz(l2.a()), rz(l2.b()));
    let (d1r, d1z, d2r, d2z) = (b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z);
    let den = d1r * d2z - d1z * d2r;
    if den.abs() < 1e-12 {
        return value(tr("the lines are parallel", &[]));
    }
    let t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den;
    let p = pt((a1r + t * d1r, a1z + t * d1z));
    for i in [i1, i2] {
        let line = &mut project.geometry[i];
        if line.a().distance(&p) < line.b().distance(&p) {
            set_a(line, p);
        } else {
            set_b(line, p);
        }
        if line.a() == line.b() {
            return value(tr("a line would shrink to a point", &[]));
        }
    }
    Ok(p)
}

// --- ALPHA-T cursor commands (hit codes D, Y, M, N, P, I, L, A, T, U, Q) -------------------------------------

/// The point `length` away from origin at `angle` degrees, as ALPHA-T's D (draw line D,A) and Y (L,A move):
/// 0 degrees is 9 o'clock (towards -Z), 90 is +X; the length is true (X is a diameter, so X moves 2 x radial).
pub fn polar(origin: Point, length: f64, angle: f64) -> Point {
    let a = angle.to_radians();
    Point::new(round(origin.x + 2.0 * length * a.sin(), 6), round(origin.z - length * a.cos(), 6))
}

/// ALPHA-T M: X (diameter) and Z distance from a to b.
pub fn measure_xz(a: Point, b: Point) -> (f64, f64) {
    (round(b.x - a.x, 6), round(b.z - a.z, 6))
}

/// ALPHA-T N: length and angle from a to b, in the convention of polar().
pub fn measure_la(a: Point, b: Point) -> (f64, f64) {
    let (dr, dz) = ((b.x - a.x) / 2.0, b.z - a.z);
    (round(hypot(dr, dz), 6), round(fmod_floor(dr.atan2(-dz).to_degrees(), 360.0), 6))
}

/// Points ALPHA-T's Find Point (P) locks onto: line and arc ends and arc centres.
pub fn key_points(project: &Project) -> Vec<Point> {
    let mut pts = endpoints(project);
    for g in &project.geometry {
        if let Element::Arc(c) = g
            && !pts.contains(&c.centre) {
                pts.push(c.centre);
            }
    }
    pts
}

/// Python's `min(items, key=key)`: the first of the smallest.
fn first_min<T>(items: impl IntoIterator<Item = T>, key: impl Fn(&T) -> f64) -> Option<T> {
    let mut best: Option<(T, f64)> = None;
    for item in items {
        let k = key(&item);
        if best.as_ref().is_none_or(|(_, bk)| k < *bk) {
            best = Some((item, k));
        }
    }
    best.map(|(item, _)| item)
}

fn nearest(points: Vec<Point>, p: Point) -> Option<Point> {
    first_min(points, |q| p.distance(q))
}

pub fn nearest_point(project: &Project, p: Point) -> Option<Point> {
    nearest(key_points(project), p)
}

/// Is q (on the arc's circle) within the arc's sweep?
fn on_arc(arc: &Arc, q: Point) -> bool {
    let (cr, cz) = rz(arc.centre);
    let ang = |pt: Point| {
        let (r, z) = rz(pt);
        (r - cr).atan2(z - cz)
    };
    let (mut ta, mut tb, mut tq) = (ang(arc.a), ang(arc.b), ang(q));
    if !arc.ccw {
        (ta, tb, tq) = (-ta, -tb, -tq);
    }
    let mut sweep = fmod_floor(tb - ta, 2.0 * PI);
    if sweep == 0.0 {
        sweep = 2.0 * PI;
    }
    fmod_floor(tq - ta, 2.0 * PI) <= sweep + 1e-9
}

/// Shortest distance from p to a line or arc element (radius / Z space).
pub fn distance_to(g: &Element, p: Point) -> f64 {
    let (pr, pz) = rz(p);
    match g {
        Element::Arc(arc) => {
            let (cr, cz) = rz(arc.centre);
            let radius = arc.a.distance(&arc.centre);
            let d = hypot(pr - cr, pz - cz);
            if d > 1e-12 {
                let q = pt((cr + (pr - cr) / d * radius, cz + (pz - cz) / d * radius));
                if on_arc(arc, q) {
                    return (d - radius).abs();
                }
            }
            let (da, db) = (p.distance(&arc.a), p.distance(&arc.b));
            if db < da { db } else { da }
        }
        Element::Line(line) => {
            let ((ar, az), (br, bz)) = (rz(line.a), rz(line.b));
            let (dr, dz) = (br - ar, bz - az);
            let l2 = dr * dr + dz * dz;
            let t = if l2 == 0.0 { 0.0 } else { clamp(((pr - ar) * dr + (pz - az) * dz) / l2, 0.0, 1.0) };
            hypot(pr - (ar + t * dr), pz - (az + t * dz))
        }
    }
}

/// Which elements nearest_element looks at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Line,
    Arc,
}

/// Id of the line (kind=Line) or arc (kind=Arc) nearest p: ALPHA-T's Find Nearest Line / Arc (L, A).
pub fn nearest_element(project: &Project, p: Point, kind: Option<Kind>) -> Option<String> {
    let cands = project.geometry.iter().filter(|g| match kind {
        None => true,
        Some(Kind::Line) => !g.is_arc(),
        Some(Kind::Arc) => g.is_arc(),
    });
    first_min(cands, |g| distance_to(g, p)).map(|g| g.id().to_string())
}

/// (centre r, centre z, radius) of an arc.
fn circle(arc: &Arc) -> (f64, f64, f64) {
    let (cr, cz) = rz(arc.centre);
    (cr, cz, arc.a.distance(&arc.centre))
}

fn line_line(l1: &Line, l2: &Line) -> Vec<Rz> {
    let ((a1r, a1z), (b1r, b1z)) = (rz(l1.a), rz(l1.b));
    let ((a2r, a2z), (b2r, b2z)) = (rz(l2.a), rz(l2.b));
    let (d1r, d1z, d2r, d2z) = (b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z);
    let den = d1r * d2z - d1z * d2r;
    if den.abs() < 1e-12 {
        return vec![];
    }
    let t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den;
    let u = ((a2r - a1r) * d1z - (a2z - a1z) * d1r) / den;
    let eps = 1e-9;
    if -eps <= t && t <= 1.0 + eps && -eps <= u && u <= 1.0 + eps { vec![(a1r + t * d1r, a1z + t * d1z)] } else { vec![] }
}

/// Where the segment a -> b meets the circle, in the order Python iterates `{t1, t2}`.
fn line_circle(a: Point, b: Point, cr: f64, cz: f64, rad: f64) -> Vec<Rz> {
    let ((ar, az), (br, bz)) = (rz(a), rz(b));
    let (dr, dz) = (br - ar, bz - az);
    let (fr, fz) = (ar - cr, az - cz);
    let qa = dr * dr + dz * dz;
    let qb = 2.0 * (fr * dr + fz * dz);
    let qc = fr * fr + fz * fz - rad * rad;
    let disc = qb * qb - 4.0 * qa * qc;
    if qa == 0.0 || disc < 0.0 {
        return vec![];
    }
    let (t1, t2) = ((-qb - disc.sqrt()) / (2.0 * qa), (-qb + disc.sqrt()) / (2.0 * qa));
    py::set_of_two(t1, py::hash_float(t1), t2, py::hash_float(t2))
        .into_iter()
        .filter(|t| -1e-9 <= *t && *t <= 1.0 + 1e-9)
        .map(|t| (ar + t * dr, az + t * dz))
        .collect()
}

/// Where two circles (r, z, radius) meet, in the order Python iterates the set of the two points.
fn circle_circle(c1: (f64, f64, f64), c2: (f64, f64, f64)) -> Vec<Rz> {
    let ((r1, z1, a), (r2, z2, b)) = (c1, c2);
    let d = hypot(r2 - r1, z2 - z1);
    if d < 1e-12 || d > a + b + 1e-9 || d < (a - b).abs() - 1e-9 {
        return vec![];
    }
    let m = (a * a - b * b + d * d) / (2.0 * d);
    let h = py_max(a * a - m * m, 0.0).sqrt();
    let (pr, pz) = (r1 + m * (r2 - r1) / d, z1 + m * (z2 - z1) / d);
    let p1 = (pr + h * (z2 - z1) / d, pz - h * (r2 - r1) / d);
    let p2 = (pr - h * (z2 - z1) / d, pz + h * (r2 - r1) / d);
    py::set_of_two(p1, py::hash_pair(p1.0, p1.1), p2, py::hash_pair(p2.0, p2.1))
}

/// Every point where two drawing elements cross or meet (ALPHA-T's Find Nearest Intersection, I).
pub fn intersections(project: &Project) -> Vec<Point> {
    let geo = &project.geometry;
    let mut out: Vec<Point> = vec![];
    for (i, g) in geo.iter().enumerate() {
        for h in &geo[i + 1..] {
            let cand = match (g, h) {
                (Element::Line(g), Element::Line(h)) => line_line(g, h),
                (Element::Line(line), Element::Arc(arc)) | (Element::Arc(arc), Element::Line(line)) => {
                    let (cr, cz, rad) = circle(arc);
                    line_circle(line.a, line.b, cr, cz, rad).into_iter().filter(|q| on_arc(arc, pt(*q))).collect()
                }
                (Element::Arc(g), Element::Arc(h)) => circle_circle(circle(g), circle(h))
                    .into_iter()
                    .filter(|q| on_arc(g, pt(*q)) && on_arc(h, pt(*q)))
                    .collect(),
            };
            for q in cand {
                let p = pt(q);
                if !out.contains(&p) {
                    out.push(p);
                }
            }
        }
    }
    out
}

pub fn nearest_intersection(project: &Project, p: Point) -> Option<Point> {
    nearest(intersections(project), p)
}

/// ALPHA-T Q: erase all the drawing (lines, arcs and text); contours that used it become empty.
pub fn erase_drawing(project: &mut Project) {
    project.geometry.clear();
    project.texts.clear();
    for op in &mut project.operations {
        if let Some(contour) = contour_mut(op) {
            contour.clear();
        }
    }
}

pub fn add_text(project: &mut Project, p: Point, text: &str, size: f64) -> Result<()> {
    if text.trim().is_empty() {
        return value(tr("no text", &[]));
    }
    project.texts.push(Text { p, text: text.into(), size });
    Ok(())
}

/// Index of the text whose start point is nearest p (ALPHA-T U erases it).
pub fn nearest_text(project: &Project, p: Point) -> Option<usize> {
    first_min(0..project.texts.len(), |&i| p.distance(&project.texts[i].p))
}

// --- construction commands from the ALPHA-T manual (German manual pp. 20-34) ----------------------------------
// All work in (radius, Z) and return Points with X as a diameter. Where ALPHA-T offers several solutions and cycles
// them with the space bar, these return all of them and the app lets the user tap the one wanted.

/// ALPHA-T KREIS: a full circle, as two half arcs split at 3 and 9 o'clock (ALPHA-T draws circles from the
/// 3-o'clock point, so that point is already a split point).
pub fn add_circle(project: &mut Project, centre: Point, radius: f64) -> Result<Vec<String>> {
    if radius <= 0.0 {
        return value(tr("radius must be > 0", &[]));
    }
    let (cr, cz) = rz(centre);
    let (p3, p9) = (pt((cr, cz + radius)), pt((cr, cz - radius)));
    let first = add_arc_3p(project, p3, pt((cr + radius, cz)), p9)?;
    let second = add_arc_3p(project, p9, pt((cr - radius, cz)), p3)?;
    Ok(vec![first, second])
}

fn circle_of(project: &Project, gid: &str) -> Result<(f64, f64, f64)> {
    match first_with_id(project, gid) {
        Some(Element::Arc(arc)) => Ok(circle(arc)),
        _ => value(tr("pick an arc", &[])),
    }
}

fn line_of<'a>(project: &'a Project, gid: &str) -> Result<&'a Line> {
    match first_with_id(project, gid) {
        Some(Element::Line(line)) => Ok(line),
        _ => value(tr("pick a line", &[])),
    }
}

/// ALPHA-T TANGENTE 1: the (up to 2) lines from p touching the arc's circle.
pub fn tangents_point_arc(project: &Project, p: Point, arc_id: &str) -> Result<Vec<(Point, Point)>> {
    let (cr, cz, rad) = circle_of(project, arc_id)?;
    let (pr, pz) = rz(p);
    let d = hypot(pr - cr, pz - cz);
    if d <= rad + 1e-9 {
        return value(tr("the point is inside the arc", &[]));
    }
    let base = (pr - cr).atan2(pz - cz);
    let off = (rad / d).acos();
    Ok([1.0, -1.0]
        .iter()
        .map(|s| (p, pt((cr + rad * (base + s * off).sin(), cz + rad * (base + s * off).cos()))))
        .collect())
}

/// ALPHA-T TANGENTE 2: the (up to 4) lines touching both arcs' circles: 2 outer, 2 crossing.
pub fn tangents_arc_arc(project: &Project, id1: &str, id2: &str) -> Result<Vec<(Point, Point)>> {
    let ((r1, z1, a), (r2, z2, b)) = (circle_of(project, id1)?, circle_of(project, id2)?);
    let (dz, dr) = (z2 - z1, r2 - r1);
    let d = hypot(dz, dr);
    if d < 1e-9 {
        return value(tr("the arcs have the same centre", &[]));
    }
    let mut out = vec![];
    for sign in [1.0, -1.0] {
        // 1: outer tangents (same side), -1: inner (crossing)
        let c = (a - sign * b) / d;
        if c.abs() > 1.0 {
            continue;
        }
        let h = py_max(1.0 - c * c, 0.0).sqrt();
        for k in [1.0, -1.0] {
            // unit normal n with n . (centre2 - centre1)/d = c
            let nz = (dz * c - k * h * dr) / d;
            let nr = (dr * c + k * h * dz) / d;
            let t1 = pt((r1 + a * nr, z1 + a * nz));
            let t2 = pt((r2 + sign * b * nr, z2 + sign * b * nz));
            if t1 != t2 {
                out.push((t1, t2));
            }
        }
    }
    Ok(out)
}

/// ALPHA-T TANGENTE ZUM KREISBOGEN: a line at `angle` (as polar: 0 = towards -Z, 90 = +X) touching the arc's
/// circle, on either side; 1.5 x the diameter long, centred on the touching point (ALPHA-T draws it 1.5 x long).
pub fn tangents_at_angle(project: &Project, arc_id: &str, angle: f64) -> Result<Vec<(Point, Point)>> {
    let (cr, cz, rad) = circle_of(project, arc_id)?;
    let a = angle.to_radians();
    let (ur, uz) = (a.sin(), -a.cos()); // direction in (r, z)
    let half = 1.5 * rad;
    Ok([1.0, -1.0]
        .iter()
        .map(|s| {
            let (tr_, tz_) = (cr + s * rad * uz, cz - s * rad * ur); // normal to the direction
            (pt((tr_ - half * ur, tz_ - half * uz)), pt((tr_ + half * ur, tz_ + half * uz)))
        })
        .collect())
}

/// ALPHA-T PARALLEL-LINIE: the two lines `distance` away from the line, 1.5 x its length, centred on it.
pub fn parallels(project: &Project, line_id: &str, distance: f64) -> Result<Vec<(Point, Point)>> {
    if distance <= 0.0 {
        return value(tr("distance must be > 0", &[]));
    }
    let g = line_of(project, line_id)?;
    let ((ar, az), (br, bz)) = (rz(g.a), rz(g.b));
    let length = hypot(br - ar, bz - az);
    let (ur, uz) = ((br - ar) / length, (bz - az) / length);
    let (mr, mz) = ((ar + br) / 2.0, (az + bz) / 2.0);
    let half = 0.75 * length;
    Ok([1.0, -1.0]
        .iter()
        .map(|s| {
            let (nr, nz) = (-uz * s * distance, ur * s * distance);
            (pt((mr + nr - half * ur, mz + nz - half * uz)), pt((mr + nr + half * ur, mz + nz + half * uz)))
        })
        .collect())
}

/// ALPHA-T TEILE KREISBOGEN: split the arc at the point of it nearest p; returns the two new ids.
pub fn split_arc(project: &mut Project, arc_id: &str, p: Point) -> Result<Vec<String>> {
    let Some(Element::Arc(g)) = first_with_id(project, arc_id).cloned() else {
        return value(tr("pick an arc", &[]));
    };
    let (cr, cz, rad) = circle(&g);
    let (pr, pz) = rz(p);
    let ang = (pr - cr).atan2(pz - cz);
    let q = pt((cr + rad * ang.sin(), cz + rad * ang.cos()));
    if !on_arc(&g, q) || q == g.a || q == g.b {
        return value(tr("the point is not inside the arc", &[]));
    }
    let i = project.geometry.iter().position(|e| *e == Element::Arc(g.clone())).expect("the arc is in the drawing");
    let second_id = next_id(project);
    let first = Element::arc(g.a, q, g.centre, g.ccw, g.id.clone());
    let second = Element::arc(q, g.b, g.centre, g.ccw, second_id.clone());
    project.geometry.splice(i..=i, [first, second]);
    Ok(vec![g.id, second_id])
}

/// ALPHA-T MIRROR IMAGE (CAD page 2): add the mirror image of the whole drawing about the spindle axis (X0), so
/// the lower half appears too (a point X100 Z-50 gets a copy at X-100 Z-50). It asks for nothing. Elements lying
/// on the axis are their own image and are not copied. Returns the new ids.
pub fn mirror_axis(project: &mut Project) -> Result<Vec<String>> {
    let m = |p: Point| Point::new(if p.x != 0.0 { -p.x } else { 0.0 }, p.z);
    let mut new = vec![];
    for g in project.geometry.clone() {
        if !g.is_arc() && g.a().x == 0.0 && g.b().x == 0.0 {
            continue;
        }
        let gid = next_id(project);
        project.geometry.push(match &g {
            Element::Arc(c) => Element::arc(m(c.a), m(c.b), m(c.centre), !c.ccw, gid.clone()),
            Element::Line(l) => Element::line(m(l.a), m(l.b), gid.clone()),
        });
        new.push(gid);
    }
    if new.is_empty() {
        return value(tr("nothing to mirror", &[]));
    }
    Ok(new)
}

/// What a blend arc touches: a point, a line (by id), or an arc (by id) from outside (convex) or inside.
#[derive(Debug, Clone, PartialEq)]
pub enum BlendRef {
    Point(Point),
    Line(String),
    Arc(String, bool),
}

/// Where the centre of a blend arc can be for one reference.
enum Locus {
    /// a circle (r, z, radius)
    Circle(f64, f64, f64),
    /// a line through (r, z) along (dr, dz)
    Line(f64, f64, f64, f64),
}

/// Where the centre of a blend arc of `radius` can be for one reference: a point P -> circle radius R about P; a
/// line -> two parallel lines; an arc -> concentric circle r + R (convex) or |r - R|.
fn ref_loci(project: &Project, r: &BlendRef, radius: f64) -> Result<Vec<Locus>> {
    Ok(match r {
        BlendRef::Point(p) => {
            let (pr, pz) = rz(*p);
            vec![Locus::Circle(pr, pz, radius)]
        }
        BlendRef::Line(id) => {
            let g = line_of(project, id)?;
            let ((ar, az), (br, bz)) = (rz(g.a), rz(g.b));
            let length = hypot(br - ar, bz - az);
            let (nr, nz) = (-(bz - az) / length, (br - ar) / length);
            [1.0, -1.0]
                .iter()
                .map(|s| Locus::Line(ar + s * radius * nr, az + s * radius * nz, br - ar, bz - az))
                .collect()
        }
        BlendRef::Arc(id, convex) => {
            let (cr, cz, rad) = circle_of(project, id)?;
            vec![Locus::Circle(cr, cz, if *convex { rad + radius } else { (rad - radius).abs() })]
        }
    })
}

/// The point where a blend arc centred (cr, cz) touches the reference.
fn contact(project: &Project, r: &BlendRef, cr: f64, cz: f64) -> Result<Point> {
    match r {
        BlendRef::Point(p) => Ok(*p),
        BlendRef::Line(id) => {
            let g = line_of(project, id)?;
            let ((ar, az), (br, bz)) = (rz(g.a), rz(g.b));
            let (dr, dz) = (br - ar, bz - az);
            let t = ((cr - ar) * dr + (cz - az) * dz) / (dr * dr + dz * dz);
            Ok(pt((ar + t * dr, az + t * dz)))
        }
        BlendRef::Arc(id, _) => {
            let (r0, z0, rad) = circle_of(project, id)?;
            let d = hypot(cr - r0, cz - z0);
            if d < 1e-9 {
                // a blend centred on the arc's own centre (radius = the arc's): it touches nowhere in particular
                return value(tr("no blend arc touches this arc there", &[]));
            }
            Ok(pt((r0 + rad * (cr - r0) / d, z0 + rad * (cz - z0) / d)))
        }
    }
}

/// ALPHA-T VERSCHMELZUNG: arcs of `radius` touching two references (a point, a line, or an arc given as convex or
/// concave). Returns (start, centre, end) for every solution; the user picks one by its centre.
pub fn blends(project: &Project, ref1: &BlendRef, ref2: &BlendRef, radius: f64) -> Result<Vec<(Point, Point, Point)>> {
    if radius <= 0.0 {
        return value(tr("radius must be > 0", &[]));
    }
    let mut centres = vec![];
    for l1 in ref_loci(project, ref1, radius)? {
        for l2 in ref_loci(project, ref2, radius)? {
            let pts = match (&l1, &l2) {
                (&Locus::Circle(r1, z1, a), &Locus::Circle(r2, z2, b)) => circle_circle((r1, z1, a), (r2, z2, b)),
                (&Locus::Line(a1r, a1z, d1r, d1z), &Locus::Line(a2r, a2z, d2r, d2z)) => {
                    let den = d1r * d2z - d1z * d2r;
                    if den.abs() < 1e-12 {
                        continue;
                    }
                    let t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den;
                    vec![(a1r + t * d1r, a1z + t * d1z)]
                }
                (&Locus::Line(ar, az, dr, dz), &Locus::Circle(cr, cz, rad))
                | (&Locus::Circle(cr, cz, rad), &Locus::Line(ar, az, dr, dz)) => {
                    // the segment between the rounded points, as Python's stand-in Line
                    line_circle(pt((ar, az)), pt((ar + dr, az + dz)), cr, cz, rad)
                }
            };
            centres.extend(pts);
        }
    }
    let mut out = vec![];
    for (cr, cz) in centres {
        let (Ok(a), Ok(b)) = (contact(project, ref1, cr, cz), contact(project, ref2, cr, cz)) else { continue };
        if a != b {
            out.push((a, pt((cr, cz)), b));
        }
    }
    Ok(out)
}

/// The shorter arc about `centre` from start to end.
pub fn add_blend(project: &mut Project, start: Point, centre: Point, end: Point) -> Result<String> {
    let (cr, cz) = rz(centre);
    let ((ar, az), (br, bz)) = (rz(start), rz(end));
    let rad = hypot(ar - cr, az - cz);
    let (ta, tb) = ((ar - cr).atan2(az - cz), (br - cr).atan2(bz - cz));
    let d = fmod_floor(tb - ta + PI, 2.0 * PI) - PI; // signed shortest sweep
    let tm = ta + d / 2.0;
    add_arc_3p(project, start, pt((cr + rad * tm.sin(), cz + rad * tm.cos())), end)
}

// --- default contours for new operations (workflow quick win: "smarter default contour") ---------------------------

/// Element ids grouped by connection (elements that share an end point), each group in drawing order.
pub fn components(project: &Project) -> Vec<Vec<String>> {
    let elems = &project.geometry;
    let mut parent: HashMap<&str, &str> = elems.iter().map(|g| (g.id(), g.id())).collect();
    fn find<'a>(parent: &mut HashMap<&'a str, &'a str>, mut i: &'a str) -> &'a str {
        while parent[i] != i {
            let grand = parent[parent[i]];
            parent.insert(i, grand);
            i = grand;
        }
        i
    }
    let mut at: Vec<(Point, &str)> = vec![];
    for g in elems {
        for p in [g.a(), g.b()] {
            match at.iter().find(|(q, _)| *q == p) {
                Some(&(_, other)) => {
                    let (root, other_root) = (find(&mut parent, g.id()), find(&mut parent, other));
                    parent.insert(root, other_root);
                }
                None => at.push((p, g.id())),
            }
        }
    }
    let mut groups: IndexMap<&str, Vec<String>> = IndexMap::new();
    for g in elems {
        let root = find(&mut parent, g.id());
        groups.entry(root).or_default().push(g.id().into());
    }
    groups.into_values().collect()
}

/// The part's profile: the connected chain that starts on the axis at the front (X0 at the largest Z), or else the
/// longest chain; thread minor-diameter lines and other loose construction lines are left out.
pub fn outer_profile(project: &Project) -> Vec<String> {
    let groups = components(project);
    let element = |i: &String| by_id(project, i).expect("a component's element");
    let front_axis = |group: &[String]| {
        let zs = group.iter().flat_map(|i| [element(i).a(), element(i).b()]).filter(|p| p.x.abs() < 1e-9).map(|p| p.z);
        zs.reduce(|m, z| if z > m { z } else { m }).unwrap_or(f64::NEG_INFINITY)
    };
    let length = |group: &[String]| py::sum(group.iter().map(|i| element(i).a().distance(&element(i).b())));
    let key = |group: &[String]| {
        let fa = front_axis(group);
        (fa > f64::NEG_INFINITY, fa, length(group))
    };
    // Python's max(): the first of the largest (tuples compare item by item)
    let Some(first) = groups.first() else { return vec![] };
    let (mut best, mut best_key) = (first, key(first));
    for g in &groups[1..] {
        let k = key(g);
        if k.partial_cmp(&best_key) == Some(std::cmp::Ordering::Greater) {
            (best, best_key) = (g, k);
        }
    }
    pick_order(project, best).unwrap_or_else(|_| best.clone())
}

/// A groove in the profile: two faces going in and out with a bottom between them, plus the line on each side
/// (what the Grooving operation picks in the Training manual); [] when there is none.
pub fn groove_elements(project: &Project, profile: &[String]) -> Vec<String> {
    let elems: Vec<&Element> = profile.iter().filter_map(|i| by_id(project, i)).collect();
    let is_face = |e: &Element| (e.a().z - e.b().z).abs() < 1e-9 && (e.a().x - e.b().x).abs() > 1e-9;
    for k in 1..elems.len().saturating_sub(3) {
        let (down, bottom, up) = (elems[k], elems[k + 1], elems[k + 2]);
        if [down, bottom, up].iter().any(|e| e.is_arc()) {
            continue;
        }
        if is_face(down) && is_face(up) && (bottom.a().x - bottom.b().x).abs() < 1e-9 {
            let lowest = [down.a().x, down.b().x, up.a().x, up.b().x].into_iter().reduce(|m, x| if x < m { x } else { m });
            if bottom.a().x < lowest.unwrap_or_default() + 1e-9 {
                return elems[k - 1..k + 4].iter().map(|e| e.id().to_string()).collect();
            }
        }
    }
    vec![]
}

/// (outer, inner) for a thread: a loose line along Z under a profile line along Z that it overlaps in Z (the drawn
/// minor diameter under the major one), the closest pair.
pub fn thread_lines(project: &Project, profile: &[String]) -> Option<(String, String)> {
    let along_z: Vec<&Line> = project
        .geometry
        .iter()
        .filter_map(|g| match g {
            Element::Line(l) if (l.a.x - l.b.x).abs() < 1e-9 && (l.a.z - l.b.z).abs() > 1e-9 => Some(l),
            _ => None,
        })
        .collect();
    let sorted = |l: &Line| if l.b.z < l.a.z { (l.b.z, l.a.z) } else { (l.a.z, l.b.z) };
    let mut best: Option<(f64, &str, &str)> = None;
    for inner in along_z.iter().filter(|g| !profile.contains(&g.id)) {
        let (lo_i, hi_i) = sorted(inner);
        for outer in along_z.iter().filter(|g| profile.contains(&g.id) && g.a.x > inner.a.x) {
            let (lo_o, hi_o) = sorted(outer);
            let overlap = (if hi_o < hi_i { hi_o } else { hi_i }) - py_max(lo_i, lo_o);
            if overlap > 1e-9 {
                let d = outer.a.x - inner.a.x;
                if best.is_none_or(|b| d < b.0) {
                    best = Some((d, &outer.id, &inner.id));
                }
            }
        }
    }
    best.map(|(_, outer, inner)| (outer.to_string(), inner.to_string()))
}
