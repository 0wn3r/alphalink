//! Contour geometry for the posts: chaining picked elements, the imaginary tool-tip path, offsets.
//!
//! ALPHA-T writes the **imaginary tool tip** path, not the drawing (cloud samples ARC_*, SLOPE_area, INT_*): the
//! nose-centre path (what the SD1 lists, the contour offset by the nose radius r towards the tool) shifted by
//! (-r, -r) in (radius, Z) for an external tool and (+r, -r) for an internal one. Lines along the axes therefore
//! stay on the drawing, while arcs become radius R + r (convex) or R - r (concave) and sloped lines move. The first
//! and last points are the drawing's own end points (the lead-in/lead-out start there).
//!
//! Internally everything is in (radius, Z); the API uses X as a diameter like the rest of the model.

use std::f64::consts::PI;

use crate::error::{Result, not_implemented, value};
use crate::i18n::tr;
use crate::model::{Element, Point};
use crate::py::{fmt_g, fmod_floor, hypot, round};

pub const MM_PER_INCH: f64 = 25.4;
pub const EPS: f64 = 1e-7;

pub fn cos45() -> f64 {
    45f64.to_radians().cos()
}

/// An oriented path element. X is a diameter. For arcs `ccw` follows the SDG/G-code sense (true = G3).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Seg {
    pub a: Point,
    pub b: Point,
    pub centre: Option<Point>,
    pub ccw: bool,
}

impl Seg {
    pub fn line(a: Point, b: Point) -> Self {
        Seg { a, b, centre: None, ccw: false }
    }

    pub fn new(a: Point, b: Point, centre: Option<Point>, ccw: bool) -> Self {
        Seg { a, b, centre, ccw }
    }

    pub fn is_arc(&self) -> bool {
        self.centre.is_some()
    }

    /// The centre of an arc (panics on a line: callers check is_arc first).
    pub fn c(&self) -> Point {
        self.centre.expect("an arc")
    }
}

/// Orient picked elements into a connected path from the first element's free end.
pub fn chain(elements: &[&Element]) -> Result<Vec<Seg>> {
    if elements.is_empty() {
        return value(tr("empty contour", &[]));
    }
    let seg = |e: &Element, reverse: bool| {
        if reverse { Seg::new(e.b(), e.a(), e.centre(), !e.ccw()) } else { Seg::new(e.a(), e.b(), e.centre(), e.ccw()) }
    };
    let first = elements[0];
    let reverse = elements.len() > 1 && (same(first.a(), elements[1].a()) || same(first.a(), elements[1].b()));
    let mut path = vec![seg(first, reverse)];
    for e in &elements[1..] {
        let end = path.last().unwrap().b;
        if same(e.a(), end) {
            path.push(seg(e, false));
        } else if same(e.b(), end) {
            path.push(seg(e, true));
        } else {
            return value(tr(
                "contour is not connected at X{x} Z{z} ({id})",
                &[("x", &fmt_g(end.x)), ("z", &fmt_g(end.z)), ("id", &e.id())],
            ));
        }
    }
    Ok(path)
}

/// The path running from the front (the end with the larger Z) towards the chuck. Area and face clear are
/// computed for that direction: ALPHA-T's own programs for a contour picked from the chuck end are not usable
/// (AREA_towards_from_chuck_end only skims the front face; FACE_towards_from_chuck_end makes one cut), so the
/// app cuts such a contour as if it had been picked from the front.
pub fn front_first(path: Vec<Seg>) -> Vec<Seg> {
    if let (Some(first), Some(last)) = (path.first(), path.last()) {
        if last.b.z > first.a.z + EPS {
            return path.iter().rev().map(|s| Seg::new(s.b, s.a, s.centre, if s.is_arc() { !s.ccw } else { false })).collect();
        }
    }
    path
}

pub fn points(path: &[Seg]) -> Vec<Point> {
    std::iter::once(path[0].a).chain(path.iter().map(|s| s.b)).collect()
}

pub fn same(p: Point, q: Point) -> bool {
    (p.x - q.x).abs() < 1e-6 && (p.z - q.z).abs() < 1e-6
}

// --- (radius, z) vector helpers --------------------------------------------------------------------------

pub(crate) fn rz(p: Point) -> (f64, f64) {
    (p.x / 2.0, p.z)
}

pub(crate) fn pt(r: f64, z: f64) -> Point {
    Point::new(round(2.0 * r, 9), round(z, 9))
}

/// Unit normal towards the tool at the start or end of a segment. side = +1 external, -1 internal.
fn tool_normal(seg: &Seg, at_start: bool, side: f64) -> (f64, f64) {
    let ((ar, az), (br, bz)) = (rz(seg.a), rz(seg.b));
    let (tr_, tz) = if let Some(c) = seg.centre {
        let (cr, cz) = rz(c);
        let (pr, pz) = if at_start { (ar, az) } else { (br, bz) };
        let (vr, vz) = (pr - cr, pz - cz);
        // G3 (ccw=true) is clockwise in a right-handed (r, z) frame
        if seg.ccw { (vz, -vr) } else { (-vz, vr) }
    } else {
        (br - ar, bz - az)
    };
    let n = hypot(tr_, tz);
    (side * -tz / n, side * tr_ / n)
}

fn offset(seg: &Seg, d: f64, side: f64) -> Seg {
    let ((ar, az), (br, bz)) = (rz(seg.a), rz(seg.b));
    let Some(c) = seg.centre else {
        let (nr, nz) = tool_normal(seg, true, side);
        return Seg::line(pt(ar + d * nr, az + d * nz), pt(br + d * nr, bz + d * nz));
    };
    let (cr, cz) = rz(c);
    let radius = hypot(ar - cr, az - cz);
    let (nr, nz) = tool_normal(seg, true, side);
    let grow = if nr * (ar - cr) + nz * (az - cz) > 0.0 { 1.0 } else { -1.0 };
    let k = (radius + grow * d) / radius;
    Seg::new(pt(cr + k * (ar - cr), cz + k * (az - cz)), pt(cr + k * (br - cr), cz + k * (bz - cz)), seg.centre, seg.ccw)
}

/// Corner point between two consecutive offset segments: the end/start if they already meet (tangent
/// continuity), else the intersection of the extended elements closest to `near`.
fn join(s1: &Seg, s2: &Seg, near: Point) -> Result<Point> {
    if same(s1.b, s2.a) {
        return Ok(s1.b);
    }
    if !s1.is_arc() && !s2.is_arc() {
        let ((a1r, a1z), (b1r, b1z)) = (rz(s1.a), rz(s1.b));
        let ((a2r, a2z), (b2r, b2z)) = (rz(s2.a), rz(s2.b));
        let (d1r, d1z, d2r, d2z) = (b1r - a1r, b1z - a1z, b2r - a2r, b2z - a2z);
        let den = d1r * d2z - d1z * d2r;
        if den.abs() < EPS {
            return Ok(s2.a);
        }
        let t = ((a2r - a1r) * d2z - (a2z - a1z) * d2r) / den;
        return Ok(pt(a1r + t * d1r, a1z + t * d1z));
    }
    let (line, arc) = if !s1.is_arc() { (s1, s2) } else { (s2, s1) };
    if line.is_arc() {
        return not_implemented(tr("arc-to-arc corners are not supported yet", &[]));
    }
    let ((ar, az), (br, bz), (cr, cz)) = (rz(line.a), rz(line.b), rz(arc.c()));
    let (pr, pz) = rz(arc.a);
    let radius = hypot(pr - cr, pz - cz);
    let (dr, dz) = (br - ar, bz - az);
    let (fr, fz) = (ar - cr, az - cz);
    let (qa, qb, qc) = (dr * dr + dz * dz, 2.0 * (fr * dr + fz * dz), fr * fr + fz * fz - radius * radius);
    let disc = (qb * qb - 4.0 * qa * qc).max(0.0);
    let cands = [1.0, -1.0].map(|sgn| (-qb + sgn * disc.sqrt()) / (2.0 * qa));
    let (nr, nz) = rz(near);
    let dist = |t: f64| hypot(ar + t * dr - nr, az + t * dz - nz);
    let best = if dist(cands[1]) < dist(cands[0]) { cands[1] } else { cands[0] }; // min() keeps the first on ties
    Ok(pt(ar + best * dr, az + best * dz))
}

/// (path without zero-length elements, offset elements, corner points incl. both offset ends).
fn offset_path(path: &[Seg], d: f64, side: f64) -> Result<(Vec<Seg>, Vec<Seg>, Vec<Point>)> {
    // a zero-length element (e.g. a diameter fully eaten by the fillets on both ends) has no direction
    let mut kept: Vec<Seg> = path.iter().filter(|s| !same(s.a, s.b)).copied().collect();
    if kept.is_empty() {
        kept = path.to_vec();
    }
    let off: Vec<Seg> = kept.iter().map(|s| offset(s, d, side)).collect();
    let mut corners = vec![off[0].a];
    for i in 1..off.len() {
        corners.push(join(&off[i - 1], &off[i], kept[i].a)?);
    }
    corners.push(off.last().unwrap().b);
    Ok((kept, off, corners))
}

/// The nose-centre path as ALPHA-T lists it in the .SD1: the contour offset by r towards the tool, the first point
/// offset along its element's normal, the last one too and then (extend_end; not for face clear) carried r further
/// along the last element (MET2: (100,-50) -> X100.8 Z-49.6).
pub fn centre_path(path: &[Seg], nose_radius: f64, external: bool, extend_end: bool) -> Result<Vec<Seg>> {
    let side = if external { 1.0 } else { -1.0 };
    let (path, off, mut corners) = offset_path(path, nose_radius, side)?;
    if extend_end && nose_radius != 0.0 {
        let last = off.last().unwrap();
        let (tr_, tz) = if last.is_arc() {
            let (nr, nz) = tool_normal(path.last().unwrap(), false, side);
            // tangent: the normal turned back to the path direction
            if side > 0.0 { (nz, -nr) } else { (-nz, nr) }
        } else {
            let ((ar, az), (br, bz)) = (rz(last.a), rz(last.b));
            let n = hypot(br - ar, bz - az);
            ((br - ar) / n, (bz - az) / n)
        };
        let (er, ez) = rz(*corners.last().unwrap());
        *corners.last_mut().unwrap() = pt(er + nose_radius * tr_, ez + nose_radius * tz);
    }
    Ok(off.iter().enumerate().map(|(i, s)| Seg::new(corners[i], corners[i + 1], s.centre, s.ccw)).collect())
}

/// The imaginary tool-tip path that ALPHA-T writes for a drawn contour (see the module docs).
/// end_on_drawing=false ends on the shifted offset end instead of the drawn end point (face clear pre-finish,
/// FACE_prefinish: last point X99.2 for the drawn X100 with r 0.4).
pub fn tip_path(path: &[Seg], nose_radius: f64, external: bool, end_on_drawing: bool) -> Result<Vec<Seg>> {
    if nose_radius == 0.0 {
        return Ok(path.to_vec());
    }
    let side = if external { 1.0 } else { -1.0 };
    let (path, off, mut corners) = offset_path(path, nose_radius, side)?;
    corners[0] = path[0].a;
    if end_on_drawing {
        *corners.last_mut().unwrap() = path.last().unwrap().b;
    }
    let (shift_r, shift_z) = (-side * nose_radius, -nose_radius);
    let shifted = |p: Point| {
        let (r, z) = rz(p);
        pt(r + shift_r, z + shift_z)
    };
    let n = off.len();
    Ok(off
        .iter()
        .enumerate()
        .map(|(i, s)| {
            let a = if i == 0 { corners[i] } else { shifted(corners[i]) };
            let b = if i == n - 1 && end_on_drawing { corners[i + 1] } else { shifted(corners[i + 1]) };
            Seg::new(a, b, s.centre.map(shifted), s.ccw)
        })
        .collect())
}

/// OFFSET X (radius) / Z: move the path by (2*offset_x, offset_z), X outwards for external work and inwards (-X)
/// for internal. The first point only moves across its element, not along it: an external face starting on the
/// axis keeps X0 (OFFSET_finish), an internal bore starting at the front keeps Z0 (INT_finish_offset).
pub fn translate(path: Vec<Seg>, offset_x: f64, offset_z: f64, external: bool) -> Vec<Seg> {
    if offset_x == 0.0 && offset_z == 0.0 {
        return path;
    }
    let dx = if external { 1.0 } else { -1.0 } * 2.0 * offset_x;
    let mv = |p: Point, keep_x: bool, keep_z: bool| {
        Point::new(if keep_x { p.x } else { round(p.x + dx, 9) }, if keep_z { p.z } else { round(p.z + offset_z, 9) })
    };
    let mut out: Vec<Seg> =
        path.iter().map(|s| Seg::new(mv(s.a, false, false), mv(s.b, false, false), s.centre.map(|c| mv(c, false, false)), s.ccw)).collect();
    let first = path[0];
    if !first.is_arc() {
        out[0].a = mv(first.a, first.a.z == first.b.z, first.a.x == first.b.x);
    }
    out
}

/// Z of the segment at diameter x (x within the segment's X range).
pub fn z_at(seg: &Seg, x: f64) -> f64 {
    let Some(c) = seg.centre else {
        if seg.a.x == seg.b.x {
            return seg.a.z.max(seg.b.z);
        }
        let t = (x - seg.a.x) / (seg.b.x - seg.a.x);
        return seg.a.z + t * (seg.b.z - seg.a.z);
    };
    let ((ar, az), (_, bz), (cr, cz)) = (rz(seg.a), rz(seg.b), rz(c));
    let radius = hypot(ar - cr, az - cz);
    let dz = (radius * radius - (x / 2.0 - cr).powi(2)).max(0.0).sqrt();
    // the arc's Z range picks the branch (fine for arcs up to a quarter circle, which is what corner radii are)
    let (lo, hi) = (az.min(bz), az.max(bz));
    let cands: Vec<f64> = [cz + dz, cz - dz].into_iter().filter(|z| lo - 1e-6 <= *z && *z <= hi + 1e-6).collect();
    if let Some(m) = cands.iter().copied().reduce(f64::max) {
        return m;
    }
    if (cz + dz - az).abs() < (cz - dz - az).abs() { cz + dz } else { cz - dz }
}

/// Diameter of the segment at Z, or None when the segment does not reach Z (faces never do).
pub fn x_at(seg: &Seg, z: f64) -> Option<f64> {
    let (lo, hi) = (seg.a.z.min(seg.b.z), seg.a.z.max(seg.b.z));
    if !(lo - 1e-9 <= z && z <= hi + 1e-9) || lo == hi {
        return None;
    }
    let Some(c) = seg.centre else {
        return Some(seg.a.x + (z - seg.a.z) / (seg.b.z - seg.a.z) * (seg.b.x - seg.a.x));
    };
    let ((ar, az), (cr, cz)) = (rz(seg.a), rz(c));
    let radius = hypot(ar - cr, az - cz);
    let dr = (radius * radius - (z - cz).powi(2)).max(0.0).sqrt();
    // the branch on the arc's own side of the centre (corner radii are at most a quarter circle)
    let (lo_r, hi_r) = (ar.min(seg.b.x / 2.0), ar.max(seg.b.x / 2.0));
    let r = [cr + dr, cr - dr].into_iter().find(|r| lo_r - 1e-6 <= *r && *r <= hi_r + 1e-6).unwrap_or(cr + dr);
    Some(2.0 * r)
}

/// A length given in mm (tool data, ALPHA-T constants) in project units.
pub fn mm(value_mm: f64, units: &str) -> f64 {
    if units == "inch" { value_mm / MM_PER_INCH } else { value_mm }
}

/// Radial (= Z) component of a 45-degree move of `length`, rounded to the output decimals first. ALPHA-T rounds
/// this component before adding it (MET2: lead-out 0.2 -> 0.141, so X100.282 Z-49.859, not 100.283).
pub fn leg45(length: f64, decimals: usize) -> f64 {
    round(length * cos45(), decimals)
}

/// #125/#126 (SD1 G115): drawn contour minimum X - 4r (diameter) and minimum Z - 2r. Same for internal (INT_*)
/// and with offsets (OFFSET_*).
pub fn limits(pts: &[Point], nose_radius: f64) -> (f64, f64) {
    let min_x = pts.iter().map(|p| p.x).fold(f64::INFINITY, f64::min);
    let min_z = pts.iter().map(|p| p.z).fold(f64::INFINITY, f64::min);
    (min_x - 4.0 * nose_radius, min_z - 2.0 * nose_radius)
}

/// The arc's sweep, measured like moves.Segment: angles of (r, z) about the centre, ccw (G3) positive.
fn sweep(a0: f64, a1: f64, ccw: bool) -> f64 {
    let mut sweep = a1 - a0;
    if ccw && sweep < 0.0 {
        sweep += 2.0 * PI;
    }
    if !ccw && sweep > 0.0 {
        sweep -= 2.0 * PI;
    }
    sweep
}

/// Where the line a -> e first meets the element s (None if they do not meet), in (X diameter, Z).
fn cross(a: Point, e: Point, s: &Seg) -> Option<Point> {
    let ((ar, az), (er, ez)) = (rz(a), rz(e));
    let (d1r, d1z) = (er - ar, ez - az);
    if let Some(c) = s.centre {
        let ((pr, pz), (qr, qz), (cr, cz)) = (rz(s.a), rz(s.b), rz(c));
        let rad = hypot(pr - cr, pz - cz);
        let (fr, fz) = (ar - cr, az - cz);
        let qa = d1r * d1r + d1z * d1z;
        let qb = 2.0 * (fr * d1r + fz * d1z);
        let qc = fr * fr + fz * fz - rad * rad;
        let disc = qb * qb - 4.0 * qa * qc;
        if qa < 1e-18 || disc < 0.0 {
            return None;
        }
        let a0 = (pr - cr).atan2(pz - cz);
        let a1 = (qr - cr).atan2(qz - cz);
        let sw = sweep(a0, a1, s.ccw);
        let mut ts = [(-qb - disc.sqrt()) / (2.0 * qa), (-qb + disc.sqrt()) / (2.0 * qa)];
        ts.sort_by(f64::total_cmp);
        for t in ts {
            if !(1e-9 < t && t <= 1.0 + 1e-9) {
                continue;
            }
            let (hr, hz) = (ar + t * d1r, az + t * d1z);
            let d = (hr - cr).atan2(hz - cz) - a0;
            let d = if sw > 0.0 { fmod_floor(d, 2.0 * PI) } else { -fmod_floor(-d, 2.0 * PI) };
            if d.abs() <= sw.abs() + 1e-9 {
                return Some(pt(hr, hz));
            }
        }
        return None;
    }
    let ((pr, pz), (qr, qz)) = (rz(s.a), rz(s.b));
    let (d2r, d2z) = (qr - pr, qz - pz);
    let den = d1r * d2z - d1z * d2r;
    if den.abs() < 1e-12 {
        return None;
    }
    let t = ((pr - ar) * d2z - (pz - az) * d2r) / den;
    let u = ((pr - ar) * d1z - (pz - az) * d1r) / den;
    if !(1e-9 < t && t <= 1.0 + 1e-9 && (-1e-9..=1.0 + 1e-9).contains(&u)) {
        return None;
    }
    Some(pt(ar + t * d1r, az + t * d1z))
}

/// ALPHA-T's collision check on a profile: a line dropping away from the tool (external: X falling as Z goes
/// towards the chuck) steeper than the tool's rear clearance angle is replaced by a line at that angle from the same
/// start, down to where it meets the profile again: the floor (FDIVE2: tip path X40 Z-20.671 -> X30 Z-28.992
/// instead of Z-22.671; the SD1 lists the nose-centre path the same way), the far wall of a narrow groove (FRG1:
/// X27.356 Z-23, the floor dropped) or a radius at the far corner, which then starts at that point (RADG: SD1
/// X31.4054 Z-28.2176, G2 on to X36 Z-29.6). side -1: internal work, X rising away from the boring bar (FIDIVE2: X50
/// Z-10.671 -> X60 Z-18.992).
pub fn limit_dives(path: Vec<Seg>, angle: Option<f64>, side: f64) -> Vec<Seg> {
    let Some(angle) = angle.filter(|a| *a > 0.0) else {
        return path;
    };
    let tan = angle.to_radians().tan();
    let mut out = path;
    let mut k = 0;
    while k < out.len() {
        let mut s = out[k];
        let drop;
        if s.is_arc() {
            let Some(p) = arc_turn(&s, angle, side) else {
                k += 1;
                continue;
            };
            // a rounded shoulder going over into a dive (RSH): the radius as far as it is no steeper than the
            // tool, then a straight line on from there at the rear angle
            if same(p, s.a) {
                out[k] = Seg::line(p, p); // a zero-length stand-in, replaced by the line below
            } else {
                out[k] = Seg::new(s.a, p, s.centre, s.ccw);
                out.insert(k + 1, Seg::line(p, p));
                k += 1;
            }
            s = out[k];
            drop = f64::INFINITY;
        } else {
            drop = side * (s.a.x - s.b.x) / 2.0;
            if drop <= 1e-9 || drop <= tan * (s.a.z - s.b.z).abs() + 1e-9 {
                k += 1;
                continue;
            }
        }
        // far enough down the rear-angle line to meet anything in the pocket
        let depth = out[k..]
            .iter()
            .flat_map(|t| [t.a, t.b])
            .map(|q| side * (s.a.x - q.x) / 2.0)
            .fold(f64::NEG_INFINITY, f64::max);
        let far = Point::new(s.a.x - side * 2.0 * depth, s.a.z - depth / tan);
        let mut hit_any = false;
        for j in k + 1..out.len() {
            let n = out[j];
            if let Some(hit) = cross(s.a, far, &n) {
                out.splice(k..=j, [Seg::line(s.a, hit), Seg::new(hit, n.b, n.centre, n.ccw)]);
                hit_any = true;
                break;
            }
        }
        if !hit_any {
            if drop == f64::INFINITY {
                // nothing below the radius: leave the stand-in out
                out.remove(k);
                continue;
            }
            let end = Point::new(s.b.x, s.a.z - drop / tan);
            out[k] = Seg::line(s.a, end);
            if k + 1 < out.len() {
                let n = out[k + 1];
                out[k + 1] = Seg::new(end, n.b, n.centre, n.ccw);
            }
        }
        k += 1;
    }
    out
}

/// Where a radius dropping away from the tool gets steeper than `angle` (degrees from Z): the point whose tangent
/// is at that angle, its start if it is steeper from the outset, None if it never is.
fn arc_turn(s: &Seg, angle: f64, side: f64) -> Option<Point> {
    let ((ar, az), (br, bz), (cr, cz)) = (rz(s.a), rz(s.b), rz(s.c()));
    let rad = hypot(ar - cr, az - cz);
    let a0 = (ar - cr).atan2(az - cz);
    let a1 = (br - cr).atan2(bz - cz);
    let sw = sweep(a0, a1, s.ccw);
    let sg = if sw > 0.0 { 1.0 } else { -1.0 };
    // travel direction (dz, dr) at angle th: dropping away from the tool steeper than `angle`?
    let steep = |th: f64| {
        let (dz, dr) = (-sg * th.sin(), sg * th.cos());
        side * dr < 0.0 && dr.abs() > angle.to_radians().tan() * dz.abs() + 1e-9
    };
    if !steep(a1) {
        return None;
    }
    if steep(a0) {
        return Some(s.a);
    }
    let (mut lo, mut hi) = (0.0, sw.abs());
    for _ in 0..60 {
        // the tangent turns monotonically along a radius: bisect
        let mid = (lo + hi) / 2.0;
        if steep(a0 + sg * mid) {
            hi = mid;
        } else {
            lo = mid;
        }
    }
    let th = a0 + sg * hi;
    Some(pt(cr + rad * th.sin(), cz + rad * th.cos()))
}

