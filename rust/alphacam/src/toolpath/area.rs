//! Area clear (#127=6, internal 7), towards the chuck: axial passes at constant X.
//!
//! Rules from the samples (AREA1, ARC_area, SLOPE_area, OFFSET_area, INT_area, TOOLCHG):
//!   * the boundary is the imaginary tool-tip path (contour::tip_path), translated by the offsets;
//!   * external: passes step down by 2*depth from the boundary's Xmax to Xmin - 2r (Xmin from the drawing);
//!     internal: up from the boundary's Xmin to its Xmax. Each Z-parallel boundary diameter in between gets a pass
//!     and restarts the stepping (zones, seen in the inch samples);
//!   * each pass: rapid to (X, start Z), feed along -Z to the boundary, feed a 45-degree retract (RETRACT DIST,
//!     default 1 mm, in X radius, outwards from the cut: -X internal, and in +Z), rapid back to start Z; after the
//!     last, rapid to start; FOLLOW (AREA_follow_contour) first feeds along the profile up to where the previous
//!     pass stopped;
//!   * pre-finish (op.pre_finish) is posted as a separate finish block, see post::alphat_prg;
//!   * the cut ends on the first boundary element met that spans X, with spans half-open on the side the passes
//!     come from (external: lo <= X < hi; internal: lo < X <= hi), so a pass exactly on a shoulder goes on to the
//!     next face;
//!   * pockets (external; mirrored in X inside a bore, IDIVE1/2): where the profile dips below a shoulder and comes
//!     back up (an undercut), the main passes treat it as filled and do not restart their stepping at its floor;
//!     after them each pocket gets its own passes (DIVE1-3, RE_NOTES Progress 43). From the shoulder's tip-path
//!     corner the tool goes down a line no steeper than the tool's rear clearance angle (ALPHA-T's collision
//!     check; the profile itself when that is shallower), level by level (the stepping grid inside the pocket, then
//!     its floor), and along each level to the far wall;
//!   * FOLLOW (FOL3, FOLG): no zones, the follow stops where a pocket starts, and each pocket pass climbs the far
//!     wall back to where the previous pass on it stopped.

use std::collections::HashSet;

use super::contour::{MM_PER_INCH, Seg, limit_dives, mm, points, tip_path, translate, z_at};
use super::{MAX_PASSES, Move, Pass, Toolpath, too_many};
use crate::error::{Result, not_implemented, value};
use crate::i18n::tr;
use crate::model::{AreaClear, Point, Tool};
use crate::py::{fmt_g, hypot, round};

/// RETRACT DIST default.
pub const RETRACT_MM: f64 = 1.0;

/// An operation's RETRACT DIST in mm (it is entered in drawing units; None = ALPHA-T's default).
pub fn retract_mm(retract: Option<f64>, units: &str, default_mm: f64) -> f64 {
    match retract {
        None => default_mm,
        Some(r) if units == "inch" => r * MM_PER_INCH,
        Some(r) => r,
    }
}

fn pow10(decimals: usize) -> f64 {
    10f64.powf(-(decimals as f64))
}

/// (index of the boundary element the pass at X ends on, its Z).
fn end_hit(boundary: &[Seg], x: f64, external: bool) -> Result<(usize, f64)> {
    let pts = points(boundary);
    let xq = if external {
        x.max(pts.iter().map(|p| p.x).fold(f64::INFINITY, f64::min))
    } else {
        x.min(pts.iter().map(|p| p.x).fold(f64::NEG_INFINITY, f64::max))
    };
    let mut best: Option<(usize, f64)> = None;
    for (k, s) in boundary.iter().enumerate() {
        let (lo, hi) = (s.a.x.min(s.b.x), s.a.x.max(s.b.x));
        if lo == hi {
            continue;
        }
        if if external { lo <= xq && xq < hi } else { lo < xq && xq <= hi } {
            let z = z_at(s, xq);
            if best.is_none_or(|b| z > b.1) {
                best = Some((k, z));
            }
        }
    }
    match best {
        Some(b) => Ok(b),
        None => value(tr("area clear: no contour below X{x}", &[("x", &fmt_g(x))])),
    }
}

fn near(p: Point, q: Point, tol: f64) -> bool {
    (p.x - q.x).abs() < tol && (p.z - q.z).abs() < tol
}

/// Is p on the element (between its ends)? tol: p may be rounded (a pass end on a slope, FOL3).
fn on(seg: &Seg, p: Point, tol: f64) -> bool {
    if near(p, seg.a, 1e-6) || near(p, seg.b, 1e-6) {
        return true;
    }
    if seg.is_arc() {
        return false; // targets on arcs are their end points (pass ends land on the arc ends or a straight element)
    }
    let ((ar, az), (br, bz), (pr, pz)) = ((seg.a.x / 2.0, seg.a.z), (seg.b.x / 2.0, seg.b.z), (p.x / 2.0, p.z));
    let cross = (br - ar) * (pz - az) - (bz - az) * (pr - ar);
    let dot = (pr - ar) * (br - ar) + (pz - az) * (bz - az);
    let length = hypot(br - ar, bz - az);
    cross.abs() < 1e-6f64.max(tol * length) && -tol * length <= dot && dot <= length * length + tol * length
}

/// FOLLOW (AREA_follow_contour): from where the pass stops, feed along the profile towards the chuck to where
/// the previous pass stopped, cleaning the step it left (pass X56 ends at Z-25: up to X60 Z-25, along X60 to
/// Z-50). It does not go down into a pocket: it stops where one starts (stops: the pockets' first elements;
/// FOL3 X56: along X60 to the corner at Z-21.066).
fn follow(boundary: &[Seg], k: usize, end: Point, target: Point, feed: f64, decimals: usize,
          stops: &HashSet<usize>) -> Vec<Move> {
    let mut moves = vec![];
    let mut here = end;
    let tol = pow10(decimals);
    for (j, s) in boundary.iter().enumerate().skip(k) {
        if stops.contains(&j) && j != k {
            break;
        }
        let at_target = on(s, target, tol) && !near(target, here, 1e-6);
        let stop = if at_target { target } else { s.b };
        let q = Point::new(round(stop.x, decimals), round(stop.z, decimals));
        if let Some(c) = s.centre {
            moves.push(Move::arc(s.ccw, q.x, q.z, feed, c.x, c.z));
        } else if (q.x, q.z) != (round(here.x, decimals), round(here.z, decimals)) {
            moves.push(Move::feed(q.x, q.z, feed));
        }
        here = q;
        if at_target {
            break;
        }
    }
    moves
}

/// ALPHA-T's tip angle test (ALPHA-T.EXE overlay 10, the routine before message 242; RE_NOTES Progress 42): the
/// tool's clearance angles (degrees from +Z, negative for internal tools) against the cut. Tools without angles
/// (both 0: grooving, drills, ball-ends) pass, and ALPHA-T then skips its collision check. Face clear only looks at
/// the size of the angles; area clear (FRONT or FACE) wants the rear edge on the side the tool moves away from, and
/// the front edge steeper than the rear one.
pub fn tip_angle_ok(front: f64, rear: f64, external: bool, towards: bool, face: bool) -> bool {
    if front == 0.0 && rear == 0.0 {
        return true;
    }
    if face {
        if towards {
            return (-90.0..=90.0).contains(&rear) && (-90.0..=90.0).contains(&front);
        }
        return rear.abs() >= 90.0 && front.abs() >= 90.0;
    }
    let (rear_ok, front_ok) = if external {
        (if towards { 0.0 < rear && rear <= 90.0 } else { (90.0..180.0).contains(&rear) }, 0.0 < front && front < 180.0)
    } else {
        (if towards { -90.0 < rear && rear < 0.0 } else { -180.0 < rear && rear < -90.0 }, -180.0 < front && front < 0.0)
    };
    if !(rear_ok && front_ok) || front == rear {
        return false;
    }
    if towards { front.abs() >= rear.abs() } else { front.abs() <= rear.abs() }
}

/// The steepest plunge ALPHA-T's collision check allows this tool in this cut (degrees from the Z axis), or None
/// when it does not check (a tool without angles, or angles that fail the tip angle test: the operator then goes
/// on without the check). `external` and `towards` are the operation's (true for operations without them).
pub fn collision_angle(tool: Option<&Tool>, external: bool, towards: bool) -> Option<f64> {
    let tool = tool?;
    if tool.front_angle == 0.0 && tool.rear_angle == 0.0 {
        return None;
    }
    if !tip_angle_ok(tool.front_angle, tool.rear_angle, external, towards, false) {
        return None;
    }
    Some(tool.rear_angle.abs())
}

/// A pocket: (first, deepest, last) element indices.
type Pocket = (usize, usize, usize);

/// Pockets: from the element that leaves the highest level so far (external; the lowest for internal work, side
/// -1) away from the tool to the one that comes back to it (or the end of the contour).
fn pockets(boundary: &[Seg], side: f64) -> Vec<Pocket> {
    let (mut out, mut top, mut k) = (vec![], f64::NEG_INFINITY, 0);
    while k < boundary.len() {
        let s = &boundary[k];
        let (a, b) = (side * s.a.x, side * s.b.x);
        if b < a - 1e-9 && a >= top - 1e-9 {
            let (first, level, mut deepest) = (k, a, k);
            while k < boundary.len() && side * boundary[k].b.x < level - 1e-9 {
                if side * boundary[k].b.x <= side * boundary[deepest].b.x + 1e-9 {
                    deepest = k; // the last element at the floor
                }
                k += 1;
            }
            out.push((first, deepest, k.min(boundary.len() - 1)));
            top = level;
            k += 1;
            continue;
        }
        top = top.max(a).max(b);
        k += 1;
    }
    out
}

fn floor(boundary: &[Seg], pocket: Pocket, side: f64) -> f64 {
    let xs = boundary[pocket.0..=pocket.1].iter().flat_map(|s| [s.a.x, s.b.x]);
    if side > 0.0 { xs.fold(f64::INFINITY, f64::min) } else { xs.fold(f64::NEG_INFINITY, f64::max) }
}

/// A pocket's levels: the stepping grid's diameters inside it (non-FOLLOW: restarted at the pocket's shoulder,
/// DIVE1 36, 32; FOLLOW: the main passes' grid, FOLG 28 in a groove from 30), then its floor (DIVE1 30; RADG: the
/// point where the rear-angle line meets the far radius, an empty pass); with FOLLOW only when that is a
/// Z-parallel line (not where the line meets a far wall, FOLG). None inside: no passes (RG2).
fn pocket_levels(boundary: &[Seg], pocket: Pocket, side: f64, grid: &[f64], follow: bool) -> Vec<f64> {
    let (first, deepest, _) = pocket;
    let (top, floor) = (boundary[first].a.x, floor(boundary, pocket, side));
    let mut levels: Vec<f64> =
        grid.iter().copied().filter(|x| side * floor < side * x && side * x < side * top - 1e-9).collect();
    let end = (deepest + 2).min(boundary.len());
    let flat = boundary[first..end]
        .iter()
        .any(|s| !s.is_arc() && (s.a.x - s.b.x).abs() < 1e-9 && (s.a.x - floor).abs() < 1e-9);
    if let Some(&last) = levels.last()
        && (flat || !follow)
        && (last - floor).abs() > 1e-9
    {
        levels.push(floor);
    }
    levels
}

/// Where a pocket's passes start and how they climb back (see [`pocket_passes`]).
struct PocketRun {
    angle: Option<f64>,
    retract: f64,
    feed: f64,
    z0: f64,
    decimals: usize,
    side: f64,
    here_x: f64,
    follow_from: Option<Point>,
    offset_z: f64,
    entry_z: Option<f64>,
}

/// ALPHA-T's passes for one pocket (DIVE1-3, IDIVE1/2, FOL3, FOLG): see the module docs. side -1 = internal
/// (mirrored in X). here_x: where the tool is (X) before the first pass, z0 the Z of the first rapid.
/// follow_from: FOLLOW, where the last pass on the far wall stopped (each pass climbs the far wall back to it).
/// offset_z: OFFSET Z; a rear-angle line starts that much towards the chuck from the (offset) corner, not away
/// from it (OFS1: Z-38.214 for the app's former -37.814 with W0.1).
/// entry_z: the first pass also cuts the pocket's top level from the front (FOLLOW in a bore, IFOL: the main
/// passes stop one step short of it): G0 to that Z, G1 out to the top, G1 along it to the corner.
/// Returns (passes, the last line Z).
fn pocket_passes(boundary: &[Seg], pocket: Pocket, levels: &[f64], run: &PocketRun) -> (Vec<Pass>, Option<f64>) {
    let (first, deepest, last) = pocket;
    let &PocketRun { angle, retract, feed, z0, decimals, side, here_x, follow_from, offset_z, entry_z } = run;
    let top = boundary[first].a;
    // the way down: the first straight element (after a rounded shoulder's radius, RSH)
    let s0 = boundary[first..=deepest]
        .iter()
        .find(|s| !s.is_arc() && !near(s.a, s.b, 1e-9))
        .unwrap_or(&boundary[first]);
    let corner = s0.a;
    let drop = side * (s0.a.x - s0.b.x) / 2.0;
    let run_z = (s0.a.z - s0.b.z).abs();
    let slope = if s0.is_arc() { 90.0 } else { drop.atan2(run_z).to_degrees() };
    let line = match angle {
        None => slope,
        Some(a) => slope.min(a),
    };
    let tan = if line < 90.0 { line.to_radians().tan() } else { f64::INFINITY };
    let cz = corner.z - if angle.is_some_and(|a| line >= a - 1e-9) { 2.0 * offset_z } else { 0.0 };

    let on_line = |x: f64| cz + if tan != f64::INFINITY { side * (x - corner.x) / 2.0 / tan } else { 0.0 };
    // where the way down meets level x: the profile, nearest the shoulder
    let profile = |x: f64| {
        boundary[first..=deepest]
            .iter()
            .filter(|s| s.a.x.min(s.b.x) - 1e-9 <= x && x <= s.a.x.max(s.b.x) + 1e-9 && s.a.x != s.b.x)
            .map(|s| z_at(s, x))
            .reduce(f64::max)
            .unwrap_or_else(|| on_line(x))
    };
    let cut = |x: f64| round(if angle.is_some() { on_line(x).min(profile(x)) } else { profile(x) }, decimals);
    // the far wall at level x
    let far = |x: f64| {
        for s in &boundary[deepest + 1..] {
            let (lo, hi) = (s.a.x.min(s.b.x), s.a.x.max(s.b.x));
            if lo != hi && (if side > 0.0 { lo <= x && x < hi } else { lo < x && x <= hi }) {
                return round(z_at(s, x), decimals);
            }
        }
        round(boundary[last].b.z, decimals)
    };

    let (mut passes, mut prev, mut back, mut last_z) = (vec![], top.x, f64::NAN, None);
    let mut target = follow_from;
    for (n, &x) in levels.iter().enumerate() {
        let z = cut(x);
        if n > 0 && far(x) >= z - pow10(decimals) {
            // nothing left at this level (RADG's floor): an empty pass
            passes.push(Pass::default());
            continue;
        }
        let mut moves;
        if n == 0 {
            let up = prev + side * 2.0 * retract;
            moves = if (here_x - up).abs() < 1e-9 { vec![] } else { vec![Move::rapid(up, z0)] };
            if let Some(entry_z) = entry_z {
                // IFOL
                let ze = round(entry_z, decimals);
                moves = vec![Move::rapid(here_x, ze), Move::feed(prev, ze, feed), Move::feed(prev, ze, feed),
                             Move::feed(prev, round(top.z, decimals), feed)];
            } else if follow_from.is_some() {
                // FOL3: in level with the corner, then onto it
                let zc = round(top.z, decimals);
                moves.extend([Move::rapid(up, zc), Move::feed(prev, zc, feed), Move::feed(prev, zc, feed)]);
            } else {
                let zu = round(on_line(up), decimals);
                moves.extend([Move::rapid(up, zu), Move::feed(up, zu, feed)]);
            }
        } else {
            let zp = cut(prev);
            moves = vec![Move::rapid(back, zp), Move::feed(prev, zp, feed)];
        }
        let ze = far(x);
        moves.extend([Move::feed(x, z, feed), Move::feed(x, ze, feed)]);
        let (mut ex, mut ez) = (x, ze);
        if let Some(t) = target
            && ((t.x - x).abs() > 1e-9 || (t.z - ze).abs() > 1e-9)
        {
            moves.push(Move::feed(t.x, t.z, feed)); // up the far wall
            (ex, ez) = (t.x, t.z);
        }
        if follow_from.is_some() {
            target = Some(Point::new(x, ze));
        }
        back = ex + side * 2.0 * retract;
        moves.extend([Move::feed(back, round(ez + retract, decimals), feed), Move::rapid(back, z)]);
        passes.push(Pass { moves });
        (prev, last_z) = (x, Some(z));
    }
    (passes, last_z)
}

fn sorted_unique(mut xs: Vec<f64>, descending: bool) -> Vec<f64> {
    xs.sort_by(|a, b| if descending { b.total_cmp(a) } else { a.total_cmp(b) });
    xs.dedup();
    xs
}

/// angle: the steepest plunge into a pocket (collision_angle), None = follow the profile.
pub fn area_clear(op: &AreaClear, path: &[Seg], nose_radius: f64, units: &str, decimals: usize,
                  angle: Option<f64>) -> Result<Toolpath> {
    if !op.towards {
        // AWAY_area_tool5 and its rerun: ALPHA-T's passes end 1.6 mm past the dia-100 shoulder (its preview does
        // not)
        return not_implemented(tr("area clear AWAY: ALPHA-T itself cuts 1.6 mm past the shoulder for it, so the \
                                   app does not offer it; use TOWARDS", &[]));
    }
    if op.depth_of_cut <= 0.0 {
        return value(tr("depth of cut must be > 0", &[]));
    }
    let side = if op.external { 1.0 } else { -1.0 };
    let boundary = translate(tip_path(path, nose_radius, op.external, true)?, op.offset_x, op.offset_z, op.external);
    // ALPHA-T's collision check first: the passes see walls too steep for the tool at the rear angle (RG1, IDIVE2)
    let boundary = limit_dives(boundary, angle, side);
    let bx: Vec<f64> = points(&boundary).iter().map(|p| p.x).collect();
    let step = 2.0 * op.depth_of_cut;
    let pockets = pockets(&boundary, side);
    let inside: HashSet<usize> = pockets.iter().flat_map(|&(first, _, last)| first..=last).collect();
    let (mut start, limit) = if op.external {
        (bx.iter().copied().fold(f64::NEG_INFINITY, f64::max),
         points(path).iter().map(|p| p.x).fold(f64::INFINITY, f64::min) - 2.0 * nose_radius)
    } else {
        // up to the widest diameter outside the pockets (IDIVE1: 50, not the undercut's 60)
        (bx.iter().copied().fold(f64::INFINITY, f64::min),
         boundary
             .iter()
             .enumerate()
             .filter(|(k, _)| !inside.contains(k))
             .flat_map(|(_, s)| [s.a.x, s.b.x])
             .fold(f64::NEG_INFINITY, f64::max))
    };
    let first_x = start;
    // Zones: every diameter of a Z-parallel boundary line between start and limit is a pass of its own, and the
    // stepping restarts from it (inch samples: ... 2.426, 2.4, 2.2426 ...; in the metric samples the steps happened
    // to land on the shoulders, so this was invisible there).
    // (not with FOLLOW: each pass cleans the step above it anyway, FOLG has no pass at the X50 shoulder)
    let walls = if op.follow {
        vec![]
    } else {
        let (lo, hi) = (start.min(limit), start.max(limit));
        sorted_unique(
            boundary
                .iter()
                .enumerate()
                .filter(|(k, s)| !s.is_arc() && s.a.x == s.b.x && !inside.contains(k) && lo < s.a.x && s.a.x < hi)
                .map(|(_, s)| s.a.x)
                .collect(),
            op.external,
        )
    };
    let mut xs = vec![];
    for stop in walls.into_iter().chain([limit]) {
        let stop = round(stop, 9); // x is rounded below; an unrounded stop would never be reached
        let mut x = start;
        loop {
            x = round(if op.external { (x - step).max(stop) } else { (x + step).min(stop) }, 9);
            xs.push(x);
            if xs.len() > MAX_PASSES {
                return Err(too_many(&tr("area clear", &[])));
            }
            if x == stop {
                break;
            }
        }
        start = stop;
    }
    let retract = round(mm(retract_mm(op.retract, units, RETRACT_MM), units), decimals);
    let zs = op.start.z;
    let mut passes: Vec<Pass> = vec![];
    let mut prev: Option<Point> = None; // where the previous pass stopped (FOLLOW goes back up to it)
    let stops: HashSet<usize> = pockets.iter().map(|p| p.0).collect();
    let mut hits = vec![]; // (element, X, Z) where each main pass met the profile
    let mut entry = None;
    if op.follow && !op.external && !pockets.is_empty() && xs.last().is_some_and(|x| {
        (x - boundary[pockets[0].0].a.x).abs() < 1e-9
    }) {
        xs.pop(); // IFOL: the bore's last diameter is the pocket's top; its first pass cuts it
        entry = Some(boundary[0].a.z - nose_radius); // the tip path's first Z (the drawn end point is kept)
    }
    for &x in &xs {
        let (k, z) = end_hit(&boundary, x, op.external)?;
        let z = round(z, decimals);
        hits.push((k, x, z));
        let mut moves = vec![Move::rapid(x, zs), Move::feed(x, z, op.feed)];
        let (mut ex, mut ez) = (x, z);
        if op.follow {
            // the first pass goes back up to where the stepping started (the top of the profile)
            let target = *prev.get_or_insert_with(|| {
                let mut best: Option<Point> = None;
                for p in points(&boundary) {
                    if (p.x - first_x).abs() < 1e-9 && best.is_none_or(|b| p.z > b.z) {
                        best = Some(p);
                    }
                }
                best.expect("the stepping starts on the boundary")
            });
            let path = follow(&boundary, k, Point::new(x, z), target, op.feed, decimals, &stops);
            if let Some(last) = path.last() {
                (ex, ez) = last.xz();
            }
            moves.extend(path);
            prev = Some(Point::new(x, z));
        }
        let xr = ex + side * 2.0 * retract;
        moves.extend([Move::feed(xr, ez + retract, op.feed), Move::rapid(xr, zs)]);
        passes.push(Pass { moves });
    }
    let mut retract_moves = vec![Move::rapid(op.start.x, op.start.z)];
    let mut last_z = None;
    // the first way in starts at the start Z less r (DIVE1: Z4.6)
    let mut z0 = round(op.start.z - nose_radius, decimals);
    for &pocket in &pockets {
        // FOLLOW: the main passes' grid; otherwise restarted at the pocket's shoulder
        let mut x = if op.follow { first_x } else { boundary[pocket.0].a.x };
        let mut grid = vec![];
        let fl = floor(&boundary, pocket, side);
        while side * x > side * fl && grid.len() <= MAX_PASSES {
            x = round(x - side * step, 9);
            grid.push(x);
        }
        let levels = pocket_levels(&boundary, pocket, side, &grid, op.follow);
        if levels.is_empty() {
            continue;
        }
        let follow_from = if op.follow {
            hits.iter().filter(|(k, _, _)| pocket.1 < *k && *k <= pocket.2).map(|&(_, x, z)| Point::new(x, z)).next_back()
        } else {
            None
        };
        let here = passes.iter().rev().find_map(|q| q.moves.last()).map_or(op.start.x, |m| m.xz().0);
        let run = PocketRun { angle, retract, feed: op.feed, z0, decimals, side, here_x: here, follow_from,
                              offset_z: op.offset_z, entry_z: entry };
        let (more, z) = pocket_passes(&boundary, pocket, &levels, &run);
        entry = None;
        if let Some(m) = more.iter().rev().find_map(|q| q.moves.last()) {
            z0 = m.xz().1; // the next pocket's first rapid starts where this one left the tool
        }
        passes.extend(more);
        last_z = z.or(last_z);
    }
    if let Some(last_z) = last_z {
        // out of the last pocket: up past the part, then back to the start
        retract_moves.insert(0, Move::rapid(round(op.start.x - side * 2.0 * nose_radius, decimals), last_z));
    }
    Ok(Toolpath::new(passes, retract_moves))
}
