//! Safety check before export (workflow improvements): the preview already has every move; this flags, per
//! operation, what could hurt the part or the machine. Warnings only: the program is still written, so a deliberate
//! program is never blocked.
//!
//! * gouge: a feed move inside the finished part (the operation's own contour, outside for external operations,
//!   inside for internal ones), or a rapid through it;
//! * chuck jaws: any move behind the billet length (the jaws hold the billet there);
//! * tailstock: any move into the live centre at the front face, when the part shows one;
//! * tip angle: ALPHA-T's own test before an area clear or face clear (RE_NOTES Progress 42): the tool's front and
//!   rear clearance angles must suit the cut's side and direction, or it says "Invalid tip angle" and drops its
//!   collision check;
//! * uncut: a wall steeper than the tool's rear clearance in a finish pass (away from the tool: down outside, up in a
//!   bore). ALPHA-T's collision check (contour::limit_dives) goes down it at the rear angle instead, so material
//!   stays on the wall;
//! * tool angle: a feed move diving steeper than the tool's clearance allows, when ALPHA-T's collision check is off
//!   (a tool without angles, or angles that fail the tip angle test): the surface it leaves behind would run into
//!   the tool's trailing edge.
//!
//! Moves are the PRG's: X is a diameter, the tool's imaginary tip; distances below are on the radius.

use std::collections::{HashMap, HashSet};

use crate::checks::tool;
use crate::i18n::tr;
use crate::model::{Element, Operation, Project, Tool, ToolLibrary};
use crate::moves::{Segment, segments};
use crate::preview::Preview;
// ALPHA-T's rules live with the area clear they guard
pub use crate::toolpath::area::{collision_angle, tip_angle_ok};
use crate::py::{fmod_floor, fmt_g, hypot, round};
use crate::toolpath::MoveKind;
use crate::toolpath::contour::chain;

/// mm: a move this close to the surface is on it
pub const TOL: f64 = 0.02;
/// degrees
pub const DIVE_SLACK: f64 = 0.5;

#[derive(Debug, Clone, PartialEq)]
pub struct Finding {
    pub op_index: Option<usize>,
    /// gouge | rapid | chuck | tailstock | tip | uncut | angle
    pub kind: &'static str,
    pub message: String,
    /// where (X diameter, Z)
    pub x: f64,
    pub z: f64,
}

/// A piece of a contour: (z0, r0, z1, r1), radius = X / 2.
type Piece = (f64, f64, f64, f64);

/// The contour as (z0, r0, z1, r1) pieces (arcs as short chords), radius = X / 2.
fn profile(project: &Project, contour: &[String]) -> Vec<Piece> {
    let mut pieces = vec![];
    for gid in contour {
        // the last element with the id, like a dict built from the list
        let Some(g) = project.geometry.iter().rev().find(|g| g.id() == gid.as_str()) else { continue };
        let (a, b) = ((g.a().x, g.a().z), (g.b().x, g.b().z));
        let pts = match g {
            Element::Arc(arc) => Segment::new(MoveKind::arc(arc.ccw), a, b, Some((arc.centre.x, arc.centre.z)))
                .polyline(24),
            Element::Line(_) => vec![a, b],
        };
        pieces.extend(pts.windows(2).map(|w| (w[0].1, w[0].0 / 2.0, w[1].1, w[1].0 / 2.0)));
    }
    pieces
}

/// The part's radius at z: the largest (external part) or smallest (bore) radius of the pieces spanning z.
fn surface(pieces: &[Piece], z: f64, outer: bool) -> Option<f64> {
    let rs = pieces.iter().filter_map(|&(z0, r0, z1, r1)| {
        let (lo, hi) = (py_min(z0, z1), py_max(z0, z1));
        if hi - lo < 1e-9 || !(lo <= z && z <= hi) {
            return None;
        }
        let t = (z - z0) / (z1 - z0);
        Some(r0 + t * (r1 - r0))
    });
    rs.reduce(|best, r| if outer { py_max(best, r) } else { py_min(best, r) })
}

/// Python's `min(a, b)`: `a` unless `b` is smaller.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Python's `max(a, b)`: `a` unless `b` is larger.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Inside the finished part, on both sides of z: a point on a step's face (the surface jumps there) is on the
/// part's surface, not in it.
fn inside(pieces: &[Piece], x: f64, z: f64, outer: bool, tol: f64, depth: f64) -> bool {
    if pieces.is_empty() {
        return false;
    }
    let zs = pieces.iter().map(|p| p.0).chain(pieces.iter().map(|p| p.2));
    let (lo, hi) = zs.fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), z| (py_min(lo, z), py_max(hi, z)));
    if !(lo + 2.0 * tol < z && z < hi - 2.0 * tol) {
        return false;
    }
    let r = x / 2.0;
    for zz in [z - tol, z + tol] {
        match surface(pieces, zz, outer) {
            Some(s) if (if outer { r < s - depth } else { r > s + depth }) => {}
            _ => return false,
        }
    }
    true
}

/// From (X diameter, Z) to the nearest point of the contour, on the radius.
fn distance(pieces: &[Piece], x: f64, z: f64) -> f64 {
    let (r, mut best) = (x / 2.0, f64::INFINITY);
    for &(z0, r0, z1, r1) in pieces {
        let (dz, dr) = (z1 - z0, r1 - r0);
        let l2 = dz * dz + dr * dr;
        let t = if l2 == 0.0 { 0.0 } else { py_max(0.0, py_min(1.0, ((z - z0) * dz + (r - r0) * dr) / l2)) };
        best = py_min(best, hypot(z - (z0 + t * dz), r - (r0 + t * dr)));
    }
    best
}

fn samples(seg: &Segment, step: f64) -> Vec<(f64, f64)> {
    let pts = seg.polyline(16);
    let mut out = vec![];
    for w in pts.windows(2) {
        let ((xa, za), (xb, zb)) = (w[0], w[1]);
        let n = ((hypot((xb - xa) / 2.0, zb - za) / step) as i64).max(1);
        for k in 1..=n {
            let t = k as f64 / n as f64;
            out.push((xa + t * (xb - xa), za + t * (zb - za)));
        }
    }
    out
}

/// The tool's body as an angle range from +Z towards +X (degrees, 0..360), from its clearance angles.
fn wedge(tool: Option<&Tool>) -> Option<(f64, f64)> {
    let tool = tool?;
    if matches!(tool.kind.as_str(), "groove" | "drill" | "thread") || tool.front_angle == tool.rear_angle {
        return None;
    }
    let (a, b) = (fmod_floor(tool.front_angle, 360.0), fmod_floor(tool.rear_angle, 360.0));
    Some((py_min(a, b), py_max(a, b)))
}

/// The tool number of an operation that has one (0 counts as none, as in Python's `if op.tool`).
fn op_tool<'a>(project: &'a Project, op: &Operation, library: Option<&'a ToolLibrary>) -> Option<&'a Tool> {
    op.tool().filter(|n| *n != 0).and_then(|n| tool(project, n, library))
}

pub fn check(project: &Project, preview: &Preview, library: Option<&ToolLibrary>) -> Vec<Finding> {
    if preview.program.is_none() {
        return vec![];
    }
    let mut found: Vec<Finding> = vec![];
    let mut seen: HashSet<(Option<usize>, &'static str)> = HashSet::new();
    let mut add = |op_index: Option<usize>, kind: &'static str, message: String, x: f64, z: f64| {
        // the first place per operation and kind is enough to look at
        if seen.insert((op_index, kind)) {
            found.push(Finding { op_index, kind, message, x, z });
        }
    };

    let ops = &project.operations;
    let k = if project.units == "inch" { 1.0 / 25.4 } else { 1.0 };
    let billet = &project.billet;
    let tol = TOL * k;
    let mut profiles: HashMap<usize, (Vec<Piece>, bool)> = HashMap::new();
    // how far the imaginary tip may lie inside the drawn contour: on tapers and arcs the tip of a radius-compensated
    // path is up to the nose radius inside, while the nose itself only touches
    let mut depth: HashMap<usize, f64> = HashMap::new();
    for (i, op) in ops.iter().enumerate() {
        depth.insert(i, tol + op_tool(project, op, library).map_or(0.0, |t| t.nose_radius * k));
        // the face clear's contour is the face it stops at, not a part outline: no gouge test for it
        match op {
            Operation::AreaClear(o) if !o.contour.is_empty() => {
                profiles.insert(i, (profile(project, &o.contour), o.external));
            }
            Operation::FinishPass(o) if !o.contour.is_empty() => {
                profiles.insert(i, (profile(project, &o.contour), o.external));
            }
            _ => {}
        }
    }
    for (i, op) in ops.iter().enumerate() {
        let (number, start, external, towards, face) = match op {
            Operation::AreaClear(o) => (o.tool, o.start, o.external, o.towards, false),
            Operation::FaceClear(o) => (o.tool, o.start, true, o.towards, true),
            _ => continue,
        };
        let Some(t) = tool(project, number, library) else { continue };
        if matches!(t.kind.as_str(), "groove" | "drill" | "thread") {
            continue;
        }
        if !tip_angle_ok(t.front_angle, t.rear_angle, external, towards, face) {
            let side = tr(if face { "face clear" } else if external { "external" } else { "internal" }, &[]);
            let way = tr(if towards { "towards the chuck" } else { "away from the chuck" }, &[]);
            add(Some(i), "tip",
                tr("T{n}'s angles (front {f:g}°, rear {r:g}°) don't suit this cut ({side}, {way}): ALPHA-T refuses \
                    it with Invalid tip angle",
                   &[("n", &number), ("f:g", &fmt_g(t.front_angle)), ("r:g", &fmt_g(t.rear_angle)), ("side", &side),
                     ("way", &way)]),
                start.x, start.z);
        }
    }
    for (i, op) in ops.iter().enumerate() {
        // walls ALPHA-T's collision check will not follow; the finish pass makes the final shape (the area clear
        // leaves the wall to it anyway)
        let Operation::FinishPass(fp) = op else { continue };
        if fp.face {
            continue;
        }
        let Some(angle) = collision_angle(tool(project, fp.tool, library), fp.external, true) else { continue };
        let tan = angle.to_radians().tan();
        let Ok(drawn) = project.contour(&fp.contour).and_then(|c| chain(&c)) else { continue };
        for el in drawn {
            let drop = (if fp.external { 1.0 } else { -1.0 }) * (el.a.x - el.b.x) / 2.0;
            if !el.is_arc() && drop > 1e-6 && drop > tan * (el.a.z - el.b.z).abs() + 1e-6 {
                add(Some(i), "uncut",
                    tr("T{n} cannot follow the wall down to X{x:g} Z{z:g}: it is steeper than the tool's {a:g}° rear \
                        clearance, so material stays there (as in ALPHA-T)",
                       &[("n", &fp.tool), ("x:g", &fmt_g(round(el.b.x, 3))), ("z:g", &fmt_g(round(el.b.z, 3))),
                         ("a:g", &fmt_g(angle))]),
                    el.b.x, el.b.z);
                break;
            }
        }
    }
    let mut wedges: HashMap<i32, Option<(f64, f64)>> = HashMap::new();
    for seg in segments(preview) {
        let i = seg.op_index;
        if seg.kind == MoveKind::Dwell {
            continue;
        }
        let part = i.and_then(|i| profiles.get(&i));
        for (x, z) in samples(&seg, 0.5) {
            if z < -billet.length - tol {
                add(i, "chuck",
                    tr("moves into the chuck jaws (Z{z:g}, behind the billet length {l:g})",
                       &[("z:g", &fmt_g(round(z, 3))), ("l:g", &fmt_g(billet.length))]),
                    x, z);
                break;
            }
            if billet.tailstock && z > -tol {
                // the live centre: its point sits in a centre hole (about 3 mm at the face), then the 60° cone and
                // the quill; facing down to X0 is not possible with it
                let cone = if z < 6.0 * 1.732 * k {
                    py_max(3.0 * k, 6.0 * k * py_min(z / (6.0 * 1.732 * k), 1.0))
                } else {
                    12.0 * k
                };
                if x.abs() / 2.0 < cone - tol && z < (6.0 * 1.732 + 30.0) * k {
                    add(i, "tailstock",
                        tr("moves into the tailstock centre (X{x:g} Z{z:g})",
                           &[("x:g", &fmt_g(round(x, 3))), ("z:g", &fmt_g(round(z, 3)))]),
                        x, z);
                    break;
                }
            }
            if let (Some((pieces, outer)), Some(i)) = (part, i)
                && inside(pieces, x, z, *outer, tol, depth.get(&i).copied().unwrap_or(tol)) {
                    let values: [(&str, &dyn std::fmt::Display); 2] =
                        [("x:g", &fmt_g(round(x, 3))), ("z:g", &fmt_g(round(z, 3)))];
                    if seg.kind == MoveKind::Rapid {
                        add(Some(i), "rapid", tr("a rapid move passes through the part at X{x:g} Z{z:g}", &values),
                            x, z);
                    } else {
                        add(Some(i), "gouge", tr("cuts into the part at X{x:g} Z{z:g}", &values), x, z);
                    }
                    break;
                }
        }
        // tool angle: the surface a feed move leaves behind must stay out of the tool's body. Only moves along the
        // part's contour leave a surface: lead-ins and pull-offs run in air
        let (Some(i), Some((pieces, _))) = (i, part) else { continue };
        if !matches!(seg.kind, MoveKind::Feed | MoveKind::ArcCw | MoveKind::ArcCcw) {
            continue;
        }
        let op = &ops[i];
        let Some(number) = op.tool() else { continue };
        let Some(&(lo, hi)) =
            wedges.entry(number).or_insert_with(|| wedge(tool(project, number, library))).as_ref()
        else {
            continue;
        };
        let t = tool(project, number, library);
        let (offset_x, offset_z) = match op {
            Operation::AreaClear(o) => (o.offset_x, o.offset_z),
            Operation::FinishPass(o) => (o.offset_x, o.offset_z),
            _ => (0.0, 0.0),
        };
        let near = py_max(py_max(offset_x, offset_z), 0.0) + t.map_or(0.0, |t| t.nose_radius * k) + 0.05 * k;
        let pts = seg.polyline(16);
        for w in pts.windows(2) {
            let ((xa, za), (xb, zb)) = (w[0], w[1]);
            let (dr, dz) = ((xb - xa) / 2.0, zb - za);
            if hypot(dr, dz) < 0.05 {
                continue;
            }
            if [(xa, za), (xb, zb), ((xa + xb) / 2.0, (za + zb) / 2.0)]
                .iter()
                .any(|&(xx, zz)| distance(pieces, xx, zz) > near)
            {
                continue; // not along the contour
            }
            let behind = fmod_floor((-dr).atan2(-dz).to_degrees(), 360.0);
            if lo + DIVE_SLACK < behind && behind < hi - DIVE_SLACK {
                let dive = dr.abs().atan2(dz.abs()).to_degrees();
                add(Some(i), "angle",
                    tr("dives at {a:.0f}°, steeper than T{n} clears", &[("a:.0f", &format!("{dive:.0}")), ("n", &number)]),
                    xb, zb);
                break;
            }
        }
    }
    // the worst first: the banner shows it
    let order = |kind: &str| ["chuck", "tailstock", "gouge", "rapid", "tip", "uncut", "angle"]
        .iter()
        .position(|k| *k == kind)
        .unwrap_or(9);
    found.sort_by_key(|f| order(f.kind));
    found
}
