//! Grooving (#127=9, finish mode 17): radial plunges along the profile, external and internal, and face grooving
//! (face_groove, FRONT off). The rules below are the external area mode (GROOVE sample).
//!
//! Rules from GROOVE_metric (stepover 2, tip width 3, datum left):
//!   * the tool reference is the tip's left (-Z) corner, so a plunge at Z covers Z .. Z + tip width;
//!   * plunges start at (profile top Z - tip width) and step by the stepover; a plunge is inserted exactly on a
//!     left wall (where the profile gets shallower going -Z, e.g. Z-30) when a step would jump over it, and the
//!     last one is clamped to the profile end;
//!   * each plunge goes to the highest profile diameter under the tip (elements touching only at the tip's edges do
//!     not count; a slope counts with its part under the tip, GV), backs off 2 mm on diameter, and rapids out to
//!     the start X; after the last, rapid to the start;
//!   * with a peck depth the plunge is preceded by pecks every peck depth (see pecks); FULL RET YES retracts out
//!     to the start X after each peck.

use super::contour::{Seg, mm, points, x_at};
use super::{MAX_PASSES, Move, Pass, Toolpath, too_many};
use crate::error::{Result, not_implemented, value};
use crate::i18n::tr;
use crate::model::{Datum, Groove, GrooveMode, Point};
use crate::py::{fmt_g, round};

pub const RETRACT_DIA_MM: f64 = 2.0;

fn depth(boundary: &[Seg], z0: f64, z1: f64) -> Result<f64> {
    let mut xs = vec![];
    for s in boundary {
        let (lo, hi) = (s.a.z.min(s.b.z), s.a.z.max(s.b.z));
        if lo == hi {
            if z0 < lo && lo < z1 {
                xs.push(s.a.x.max(s.b.x));
            }
        } else if hi.min(z1) - lo.max(z0) > 1e-9 {
            let (w0, w1) = (lo.max(z0), hi.min(z1));
            if let Some(c) = s.centre {
                // its highest point under the tip: at the window's edges or its crown (GR: X30.172)
                let crown = (w0 < c.z && c.z < w1).then_some(c.z);
                xs.extend([Some(w0), Some(w1), crown].into_iter().flatten().filter_map(|z| x_at(s, z)));
            } else {
                // a slope: its highest point inside the tip's width (GV: X33.334 at Z-22 of a 40 -> 30 wall)
                xs.extend([w0, w1].map(|z| s.a.x + (s.b.x - s.a.x) * (z - s.a.z) / (s.b.z - s.a.z)));
            }
        }
    }
    match xs.into_iter().reduce(f64::max) {
        Some(x) => Ok(x),
        None => value(tr("grooving: no profile under Z{z0}..{z1}", &[("z0", &fmt_g(z0)), ("z1", &fmt_g(z1))])),
    }
}

/// Face Zs where the profile is shallower (larger X) just below than just above (`deeper` false), or deeper
/// (smaller X, `deeper` true: a plunge is inserted at wall - tip width when a step would jump over it,
/// GROOVE_finishoffset: -22.8 -> -23.2 -> -25.2).
fn walls(boundary: &[Seg], deeper: bool) -> Vec<f64> {
    let band = |z0: f64, z1: f64| {
        boundary
            .iter()
            .filter(|s| s.a.z != s.b.z && s.a.z.max(s.b.z).min(z1) - s.a.z.min(s.b.z).max(z0) > 1e-9)
            .map(|s| s.a.x.max(s.b.x))
            .reduce(f64::max)
    };
    let mut out = vec![];
    for s in boundary {
        if s.a.z == s.b.z && !s.is_arc()
            && let (Some(above), Some(below)) = (band(s.a.z, s.a.z + 1e-3), band(s.a.z - 1e-3, s.a.z))
                && (if deeper { below < above } else { below > above })
            {
                out.push(s.a.z);
            }
    }
    out
}

#[derive(Clone, Copy)]
enum Edge {
    X(f64),
    Z(f64),
}

/// FINISH OFFSET X/Z (GROOVE_finishoffset, U0.5 W0.2): leave material on every side of the drawn profile:
/// diameters move out by 2 fx, faces move by fz towards the open side (the front face and shoulders +Z, a groove's
/// +Z wall -Z), so the groove gets narrower and shallower. Corners are the crossings of the moved elements; the
/// two ends move only across their own element.
fn allowance(path: &[Seg], fx: f64, fz: f64) -> Result<Vec<Seg>> {
    if path.iter().any(|s| s.is_arc() || (s.a.x != s.b.x && s.a.z != s.b.z)) {
        return not_implemented(tr("grooving finish offsets: only faces and diameters (no sample with slopes/arcs)",
                                  &[]));
    }
    let lines: Vec<Edge> = path
        .iter()
        .map(|s| {
            if s.a.z == s.b.z {
                // face: going up (x grows) has the material on the -Z side, so it moves +Z
                Edge::Z(s.a.z + if s.b.x > s.a.x { fz } else { -fz })
            } else {
                Edge::X(s.a.x + 2.0 * fx)
            }
        })
        .collect();
    let corner = |l1: Edge, l2: Edge, fallback: Point| {
        let x = [l1, l2].iter().find_map(|e| if let Edge::X(v) = e { Some(*v) } else { None }).unwrap_or(fallback.x);
        let z = [l1, l2].iter().find_map(|e| if let Edge::Z(v) = e { Some(*v) } else { None }).unwrap_or(fallback.z);
        Point::new(round(x, 9), round(z, 9))
    };
    let n = path.len();
    let mut pts = vec![corner(lines[0], lines[0], path[0].a)];
    pts.extend((0..n - 1).map(|i| corner(lines[i], lines[i + 1], path[i].b)));
    pts.push(corner(lines[n - 1], lines[n - 1], path[n - 1].b));
    Ok(pts.windows(2).map(|w| Seg::line(w[0], w[1])).collect())
}

fn mirror(path: &[Seg]) -> Vec<Seg> {
    path.iter().map(|s| Seg::line(Point::new(-s.a.x, s.a.z), Point::new(-s.b.x, s.b.z))).collect()
}

pub fn groove(op: &Groove, path: &[Seg], units: &str, decimals: usize) -> Result<Toolpath> {
    if !op.front {
        return face_groove(op, path, units, decimals);
    }
    if op.mode == GrooveMode::Finish {
        if op.finish_offset_x != 0.0 || op.finish_offset_z != 0.0 || !op.external {
            return not_implemented(tr("grooving finish mode: only external, no finish offsets (no sample)", &[]));
        }
        return Ok(groove_finish(op, path));
    }
    if !op.external && op.peck_depth > 0.0 {
        return not_implemented(tr("internal grooving with pecking (no sample)", &[]));
    }
    // The drawn profile, not the tip path: a groove's -Z-facing wall would move by 2r with the (-r, -r) tip shift,
    // and the sample (made with the 0.4R tool 3) plunges against the drawn walls. Grooving tools have r = 0 anyway.
    let mut boundary = if op.finish_offset_x != 0.0 || op.finish_offset_z != 0.0 {
        allowance(path, op.finish_offset_x, op.finish_offset_z)?
    } else {
        path.to_vec()
    };
    // internal (GROOVE_internal, tool 14): the same rules with X mirrored: plunges go out from the start X to the
    // nearest wall of the bore under the tip and back off 2 towards the axis
    let side = if op.external { 1.0 } else { -1.0 };
    if side < 0.0 {
        boundary = mirror(&boundary);
    }
    // z is rounded below; unrounded ends could never be reached
    let zs: Vec<f64> = points(&boundary).iter().map(|p| round(p.z, 9)).collect();
    let top = zs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let end = zs.iter().copied().fold(f64::INFINITY, f64::min);
    let (w, step) = (op.tip_width, op.stepover);
    let mut jumps = walls(&boundary, false);
    jumps.extend(walls(&boundary, true).into_iter().map(|e| round(e - w, 9)));
    let back = round(mm(RETRACT_DIA_MM, units), decimals);
    let sx = op.start.x;
    // plunge positions for the tip's -Z corner, then the reference corner: DATUM RIGHT is the +Z corner
    // (GROOVE_datumright: every plunge 3 = tip width further +Z)
    let mut plunges = vec![];
    let mut z = round(top - w, 9);
    loop {
        plunges.push((z, side * depth(&boundary, z, z + w)?));
        if plunges.len() > MAX_PASSES {
            return Err(too_many(&tr("grooving", &[])));
        }
        if z <= end {
            break;
        }
        let nz = round((z - step).max(end), 9);
        z = jumps.iter().copied().filter(|e| nz < *e && *e < z).reduce(f64::max).unwrap_or(nz);
    }
    if op.datum == Datum::Right {
        for p in &mut plunges {
            p.0 = round(p.0 + w, 9);
        }
    }
    // Z-SHIFT / REPEATS: each repeat is another pass, the same plunges shifted by z_shift (GROOVE_zshift10_rep1)
    let mut passes = vec![];
    for k in 0..(op.repeat + 1).max(0) {
        let mut moves = vec![];
        for &(z, x) in &plunges {
            let z = round(z + k as f64 * op.z_shift, 9);
            moves.push(Move::rapid(sx, z));
            if op.peck_depth > 0.0 {
                moves.extend(pecks(op, sx, x, z, back));
            }
            let bottom_back = if op.peck_depth > 0.0 && op.full_retract {
                let last = peck_xs(op, sx, x).last().copied().unwrap_or(sx);
                moves.extend([Move::rapid(last + 2.0 * op.peck_depth, z), Move::feed(x, z, op.feed)]);
                x + 2.0 * op.peck_depth
            } else {
                moves.push(Move::feed(x, z, op.feed));
                x + side * back
            };
            if op.dwell_revs != 0.0 {
                // GROOVE_dwell2: G4 at the bottom of every plunge
                moves.push(Move::dwell(Some((x, z)), op.dwell_revs));
            }
            moves.extend([Move::feed(bottom_back, z, op.feed), Move::rapid(sx, z)]);
        }
        passes.push(Pass { moves });
    }
    Ok(Toolpath { passes, retract: vec![Move::rapid(sx, op.start.z)], plunges: plunges.len() })
}

/// The shallowest (largest) Z of the profile under the tip's radial band r0..r1 (radius); elements touching the
/// band only at its edges do not count.
fn face_depth(boundary: &[Seg], r0: f64, r1: f64) -> Result<f64> {
    let mut zs = vec![];
    for s in boundary {
        let (lo, hi) = ((s.a.x / 2.0).min(s.b.x / 2.0), (s.a.x / 2.0).max(s.b.x / 2.0));
        if lo == hi {
            if r0 < lo && lo < r1 {
                zs.push(s.a.z.max(s.b.z));
            }
        } else if hi.min(r1) - lo.max(r0) > 1e-9 {
            zs.push(s.a.z.max(s.b.z));
        }
    }
    match zs.into_iter().reduce(f64::max) {
        Some(z) => Ok(z),
        None => value(tr("face grooving: no profile under X{x0}..{x1}",
                         &[("x0", &fmt_g(2.0 * r0)), ("x1", &fmt_g(2.0 * r1))])),
    }
}

/// FACE grooving (ALPHA-T's FRONT -> FACE toggle, SD1 L210; GROOVE_face: a groove X30..36 drawn 5 deep into the
/// front face, tip width 2, stepover 1): plunges along -Z, stepping outwards in X.
///   * DATUM BTM (the default; stored as datum "left"): the reference is the tip's corner nearest the axis, so a
///     plunge at X covers X .. X + 2 * tip width on diameter;
///   * plunges start at the profile's smallest diameter and step out by the stepover (radius), the last one
///     clamped to the largest diameter - 2 * tip width;
///   * each plunge feeds from the start Z to the shallowest profile Z under the tip, backs off 1 mm in +Z (the
///     radial 2 mm on diameter), and rapids out to the start Z; after the last, rapid to the start X.
///
/// GROOVE_back (an older build's FRONT -> BACK, the whole turned profile picked) is the same cycle with the
/// whole profile as the groove. DATUM TOP shifts the reference to the outer corner (by symmetry, no sample).
pub fn face_groove(op: &Groove, path: &[Seg], units: &str, decimals: usize) -> Result<Toolpath> {
    if !op.external || op.peck_depth > 0.0 || op.z_shift != 0.0 || op.repeat != 0 || op.mode == GrooveMode::Finish
        || op.finish_offset_x != 0.0 || op.finish_offset_z != 0.0
    {
        return not_implemented(tr("face grooving: only external, area mode, without pecks, X shift or finish \
                                   offsets (no sample)", &[]));
    }
    let rs: Vec<f64> = points(path).iter().map(|p| p.x / 2.0).collect();
    let lo = rs.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = rs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let (w, step) = (op.tip_width, op.stepover);
    let back = round(mm(RETRACT_DIA_MM, units) / 2.0, decimals);
    let sz = op.start.z;
    let mut plunges = vec![];
    let mut r = lo;
    loop {
        r = round(r.min(hi - w), 9);
        plunges.push((r, face_depth(path, r, r + w)?));
        if plunges.len() > MAX_PASSES {
            return Err(too_many(&tr("face grooving", &[])));
        }
        if r >= hi - w - 1e-9 {
            break;
        }
        r = round(r + step, 9);
    }
    let mut moves = vec![];
    for &(r, z) in &plunges {
        let x = round(2.0 * if op.datum == Datum::Right { r + w } else { r }, 9);
        moves.push(Move::rapid(x, sz));
        moves.push(Move::feed(x, z, op.feed));
        if op.dwell_revs != 0.0 {
            moves.push(Move::dwell(Some((x, z)), op.dwell_revs));
        }
        moves.extend([Move::feed(x, round(z + back, 9), op.feed), Move::rapid(x, sz)]);
    }
    Ok(Toolpath { passes: vec![Pass { moves }], retract: vec![Move::rapid(op.start.x, sz)], plunges: plunges.len() })
}

/// FINISH mode (#127=17, GROOVE_finishmode): one pass along the drawn profile. The tip covers Z .. Z + tip width
/// (datum left), so the vertices of a wall where the profile steps down (the groove's +Z wall) move by -tip width;
/// datum right covers Z - tip width .. Z and moves the walls where it steps up by +tip width instead (assumed by
/// symmetry, no sample). Rapid to (start X, first Z), feed along the profile, feed out to the start X. A radius
/// moves with its wall, centre and all (GRF: the R1.5 at the foot of the +Z wall by -3; the far one stays).
pub fn groove_finish(op: &Groove, path: &[Seg]) -> Toolpath {
    let w = op.tip_width;
    let pts = points(path);
    let mut shift = vec![0.0; pts.len()];
    for (i, s) in path.iter().enumerate() {
        if s.a.x != s.b.x {
            // faces and slopes (GROOVE_finishmode_slope: both ends of the 45-degree side move)
            let down = s.b.x < s.a.x;
            if op.datum == Datum::Left && down {
                (shift[i], shift[i + 1]) = (-w, -w);
            } else if op.datum == Datum::Right && !down {
                (shift[i], shift[i + 1]) = (w, w);
            }
        }
    }
    let zs: Vec<f64> = pts.iter().zip(&shift).map(|(p, d)| round(p.z + d, 9)).collect();
    let sx = op.start.x;
    let mut moves = vec![Move::rapid(sx, zs[0]), Move::feed(pts[0].x, zs[0], op.feed)];
    for (i, seg) in path.iter().enumerate() {
        let (p, z) = (pts[i + 1], zs[i + 1]);
        moves.push(match seg.centre {
            Some(c) => {
                let cz = round(c.z + if shift[i] == shift[i + 1] { shift[i] } else { 0.0 }, 9);
                Move::arc(seg.ccw, p.x, z, op.feed, c.x, cz)
            }
            None => Move::feed(p.x, z, op.feed),
        });
    }
    moves.push(Move::feed(sx, zs[zs.len() - 1], op.feed));
    Toolpath::new(vec![Pass { moves }], vec![Move::rapid(sx, op.start.z)])
}

/// Peck diameters: one peck (radius) deeper each time, while not below the bottom (PECK_groove: 116 ... 40).
fn peck_xs(op: &Groove, sx: f64, depth: f64) -> Vec<f64> {
    let step = 2.0 * op.peck_depth;
    let mut xs = vec![];
    let mut k = 1.0;
    while round(sx - k * step, 9) >= depth - 1e-9 {
        xs.push(round(sx - k * step, 9));
        k += 1.0;
    }
    xs
}

/// Pecks before the final plunge. FULL RET NO: back off `back` (2 mm dia) after each peck. FULL RET YES
/// (PECK_fullret_groove): back off one peck, rapid out to the start X, rapid in to one peck above the last.
fn pecks(op: &Groove, sx: f64, depth: f64, z: f64, back: f64) -> Vec<Move> {
    let step = 2.0 * op.peck_depth;
    let mut moves = vec![];
    for (n, x) in peck_xs(op, sx, depth).into_iter().enumerate() {
        if !op.full_retract {
            moves.extend([Move::feed(x, z, op.feed), Move::feed(x + back, z, op.feed)]);
            continue;
        }
        if n > 0 {
            moves.push(Move::rapid(x + 2.0 * step, z));
        }
        moves.extend([Move::feed(x, z, op.feed), Move::feed(x + step, z, op.feed), Move::rapid(sx, z)]);
    }
    moves
}
