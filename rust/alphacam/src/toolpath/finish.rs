//! Finish pass (#127=14, internal 15, FACE 16 with the pull-off towards the axis): one pass along the imaginary
//! tool-tip path (contour::tip_path).

use super::contour::{Seg, leg45, limit_dives, mm, tip_path, translate};
use super::{Move, Pass, Toolpath};
use crate::error::Result;
use crate::model::FinishPass;

/// 45-degree approach to the first point, from +Z and outside in X (-X internal).
pub const LEAD_IN_MM: f64 = 2.0;
/// 45-degree pull-off from the last point, same directions (ALPHA-T; V1.81 uses 2).
pub const LEAD_OUT_MM: f64 = 0.2;

/// `lead_out` (project units) overrides the 0.2 mm pull-off: the area pre-finish (#127=10) pulls off by the area
/// retract distance as entered (1 mm; inch 0.0394, which gives 0.0279 at 45 degrees, not 0.0278).
/// `face_end`: the face clear pre-finish (#127=12, FACE_prefinish) ends on the shifted offset end, not the drawn end
/// point, and pulls off 0.2 mm inwards (-X) like face clear.
/// `angle`: ALPHA-T's collision check (contour::limit_dives; not FACE).
#[allow(clippy::too_many_arguments)]
pub fn finish_pass(op: &FinishPass, path: &[Seg], nose_radius: f64, units: &str, decimals: usize,
                   lead_out: Option<f64>, face_end: bool, angle: Option<f64>) -> Result<Toolpath> {
    let side = if op.external { 1.0 } else { -1.0 };
    let mut tip = translate(tip_path(path, nose_radius, op.external, !face_end)?, op.offset_x, op.offset_z,
                            op.external);
    if !op.face {
        tip = limit_dives(tip, angle, side);
    }
    let lead_in = leg45(mm(LEAD_IN_MM, units), decimals);
    let lead_out = leg45(lead_out.unwrap_or_else(|| mm(LEAD_OUT_MM, units)), decimals);
    let (first, last) = (tip[0].a, tip[tip.len() - 1].b);
    let mut moves = vec![Move::rapid(first.x + side * 2.0 * lead_in, first.z + lead_in),
                         Move::feed(first.x, first.z, op.feed)];
    for s in &tip {
        moves.push(match s.centre {
            Some(c) => Move::arc(s.ccw, s.b.x, s.b.z, op.feed, c.x, c.z),
            None => Move::feed(s.b.x, s.b.z, op.feed),
        });
    }
    let out_side = if face_end || op.face { -side } else { side }; // FINISH_face: X99.718, not 100.282
    moves.push(Move::feed(last.x + out_side * 2.0 * lead_out, last.z + lead_out, op.feed));
    Ok(Toolpath::new(vec![Pass { moves }], vec![Move::rapid(op.start.x, op.start.z)]))
}
