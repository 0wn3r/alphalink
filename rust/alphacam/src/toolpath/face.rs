//! Face clear (#127=8): radial passes at constant Z, cutting towards the axis (FACE sample).
//!
//! Rules from FACE_metric (MET2 profile, depth 2, retract 0.2):
//!   * the profile's faces (constant-Z elements) split it into zones; each zone is cut from its top face down in
//!     steps of the depth of cut, the last pass clamped onto the next face (Z-2 ... -24, -25, -27 ... -49, -50);
//!   * pass: rapid to (start X, Z), feed to the profile, 45-degree pull-off (-2R in X, +R in Z, R = retract),
//!     rapid to (start X - 2r, pull-off Z + r) with the nose radius r (FACE_tool1: X118.4 for the 0.8R rougher,
//!     X119.2 for the 0.4R finisher); after the last pass, rapid to the start;
//!   * the cut ends on the element spanning Z, half-open towards the face below (lo <= Z < hi); on slopes and arcs
//!     (FACE_slope, FACE_arcs) at the tip path's diameter at Z. ALPHA-T's values there differ from the exact ones
//!     by up to 0.001 on diameter, either way (a numeric search, presumably), so those two samples are known diffs.
//!
//! Area clear with FACE instead of FRONT (AREA_front_face, SD1 E101) posts the same passes as #127=8, but with the
//! area clear retract (1 mm) and the pull-off going outwards: (+2R in X, +R in Z).

use super::contour::{Seg, mm, tip_path, translate, x_at};
use super::{MAX_PASSES, Move, Pass, Toolpath, too_many};
use crate::error::{Result, not_implemented, value};
use crate::i18n::tr;
use crate::model::{AreaClear, FaceClear, Point};
use crate::py::{fmt_g, round};

/// Face clear default (SD1 R0.2).
pub const RETRACT_MM: f64 = 0.2;

/// What face clear needs from its operation: a face clear, or an area clear with FACE.
#[derive(Debug, Clone, Copy)]
pub struct FaceParams {
    pub start: Point,
    pub depth_of_cut: f64,
    pub offset_x: f64,
    pub offset_z: f64,
    pub feed: f64,
    pub towards: bool,
    pub external: bool,
}

impl From<&FaceClear> for FaceParams {
    fn from(op: &FaceClear) -> Self {
        FaceParams { start: op.start, depth_of_cut: op.depth_of_cut, offset_x: op.offset_x, offset_z: op.offset_z,
                     feed: op.feed, towards: op.towards, external: true }
    }
}

impl From<&AreaClear> for FaceParams {
    fn from(op: &AreaClear) -> Self {
        FaceParams { start: op.start, depth_of_cut: op.depth_of_cut, offset_x: op.offset_x, offset_z: op.offset_z,
                     feed: op.feed, towards: op.towards, external: op.external }
    }
}

fn end_x(boundary: &[Seg], z: f64, external: bool) -> Result<f64> {
    let hits: Vec<f64> = boundary
        .iter()
        .filter(|s| s.a.z != s.b.z && s.a.z.min(s.b.z) <= z && z < s.a.z.max(s.b.z))
        .filter_map(|s| x_at(s, z))
        .collect();
    if hits.is_empty() {
        return value(tr("face clear: no profile at Z{z}", &[("z", &fmt_g(z))]));
    }
    Ok(if external { hits.iter().copied().fold(f64::NEG_INFINITY, f64::max) }
       else { hits.iter().copied().fold(f64::INFINITY, f64::min) })
}

/// Internal work (area clear FACE on a bore, AREA_face_internal) mirrors X: passes feed outwards from the start X
/// to the bore wall, the pull-off and the rapid move towards the axis. A zone also starts at the top of the profile
/// when that is not a face (a bore starting at Z0).
#[allow(clippy::too_many_arguments)]
pub fn face_clear(op: FaceParams, path: &[Seg], nose_radius: f64, units: &str, decimals: usize, retract_mm: f64,
                  outward: bool) -> Result<Toolpath> {
    if !op.towards {
        // FACE_away_tool5 / _tool3_nocheck: ALPHA-T writes a single pass at Z0, still feeding towards the centre
        return not_implemented(tr("face clear AWAY: ALPHA-T itself writes only one pass at Z0 for it, so the app \
                                   does not offer it; use TOWARDS", &[]));
    }
    let external = op.external;
    let side = if external { 1.0 } else { -1.0 };
    // offsets translate the profile like area clear (FACE_offset: X40 -> 41, faces Z0/-25/-50 -> 0.2/-24.8/-49.8)
    let boundary = translate(tip_path(path, nose_radius, external, true)?, op.offset_x, op.offset_z, external);
    let mut levels: Vec<f64> = boundary.iter().filter(|s| s.a.z == s.b.z && !s.is_arc()).map(|s| s.a.z).collect();
    levels.push(boundary.iter().map(|s| s.a.z.max(s.b.z)).fold(f64::NEG_INFINITY, f64::max));
    levels.sort_by(|a, b| b.total_cmp(a));
    levels.dedup();
    let r = round(mm(retract_mm, units), decimals);
    let pull = side * if outward { 2.0 * r } else { -2.0 * r };
    let (sx, d) = (op.start.x, op.depth_of_cut);
    let mut passes = vec![];
    for pair in levels.windows(2) {
        let (top, bottom) = (pair[0], pair[1]);
        if passes.len() as f64 + (top - bottom) / d > MAX_PASSES as f64 {
            return Err(too_many(&tr("face clear", &[])));
        }
        let n = round((top - bottom) / d, 9).ceil() as i64;
        for k in 1..=n {
            let z = round((top - k as f64 * d).max(bottom), 9);
            let x = end_x(&boundary, z, external)?;
            passes.push(Pass { moves: vec![
                Move::rapid(sx, z),
                Move::feed(x, z, op.feed),
                Move::feed(x + pull, z + r, op.feed),
                Move::rapid(sx - side * 2.0 * nose_radius, z + r + nose_radius),
            ] });
        }
    }
    Ok(Toolpath::new(passes, vec![Move::rapid(sx, op.start.z)]))
}
