//! Drilling (#127=18) on the axis, pecks written out as G1 moves (DRILL_DWELL sample).
//!
//! From the start Z, each peck goes `peck` deeper than the last one (not than the retract), then backs off by
//! RETRACT DIST (default 2; DRILL_retract1: 1); the final peck stops at the bottom, dwells, backs off the same, and
//! the tool rapids back to the start.
//!
//! FULL RET (DRILL_fullret, RETRACT DIST left at 2): after each peck it backs off half the retract distance (1),
//! rapids out to the start Z, then rapids back in to a quarter of it (0.5) above the last depth before the next
//! peck. Only the default distance is sampled with FULL RET, so the half / quarter split is inferred from that one
//! sample.

use super::contour::mm;
use super::{Move, Pass, Toolpath};
use crate::error::{Result, value};
use crate::i18n::tr;
use crate::model::Drill;
use crate::py::round;

/// RETRACT DIST default.
pub const PECK_RETRACT_MM: f64 = 2.0;

pub fn drill(op: &Drill, units: &str, decimals: usize) -> Result<Toolpath> {
    // z is rounded below; so is the bottom it must reach
    let (x, mut z, bottom) = (op.start.x, op.start.z, round(op.depth, 9));
    if bottom >= z {
        return value(tr("drill depth must be below the start Z", &[]));
    }
    let retract = op.retract.unwrap_or_else(|| mm(PECK_RETRACT_MM, units));
    if retract <= 0.0 {
        return value(tr("retract distance must be > 0", &[]));
    }
    let back = round(if op.full_retract { retract / 2.0 } else { retract }, decimals);
    let clear = round(retract / 4.0, decimals);
    let mut moves = vec![];
    loop {
        z = if op.peck > 0.0 { round((z - op.peck).max(bottom), 9) } else { bottom };
        moves.push(Move::feed(x, z, op.feed));
        if z == bottom {
            break;
        }
        moves.push(Move::feed(x, z + back, op.feed));
        if op.full_retract {
            moves.push(Move::rapid(x, op.start.z));
            moves.push(Move::rapid(x, round(z + clear, 9)));
        }
    }
    if op.dwell_revs != 0.0 {
        moves.push(Move::dwell(Some((x, z)), op.dwell_revs));
    }
    moves.push(Move::feed(x, bottom + back, op.feed));
    Ok(Toolpath::new(vec![Pass { moves }], vec![Move::rapid(x, op.start.z)]))
}
