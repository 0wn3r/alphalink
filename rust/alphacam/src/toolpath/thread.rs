//! Threading (#127=20 external and internal), G32 passes (THREAD sample: X40 -> X20 to Z-25, pitch 1.5, first
//! cut 1; THREAD_internal: X20 -> X40, U-21.; THREAD_p2_len30: pitch 2 over 30, W-4.).
//!
//! Cut n (n = 1..N) goes to radial depth first * sqrt(n) (constant chip area), N = (total depth / first)^2 rounded
//! up, the last cut clamped to the inner diameter. Each cut ends one pitch before the thread end and pulls out over
//! the last pitch (U = outer - inner + 1 on diameter: U21 for 40 -> 20, U13 for 40 -> 28 in THREAD_depth6, U-21
//! internal). An infeed angle moves each cut's start Z (ThreadPlan::cut_start_z, THREAD_angle30); spring passes
//! repeat the last cut in a #127=21 block (THREAD_spring2: two).

use crate::error::{Result, not_implemented, value};
use crate::i18n::tr;
use crate::model::{Element, Thread};
use crate::py::round;

/// #121 counts the cuts; more than this is a typing error (first cut 0.01 over 10 mm = 1M cuts).
pub const MAX_CUTS: i64 = 999;

#[derive(Debug, Clone, PartialEq)]
pub struct ThreadPlan {
    /// Cut diameters.
    pub xs: Vec<f64>,
    /// Thread end incl. lead-out (G32 stops one pitch before).
    pub z_end: f64,
    /// Incremental X of the pull-out.
    pub pullout_u: f64,
    /// Final diameter of the last cut (#125): inner line external, outer line internal.
    pub inner: f64,
    /// Where every cut starts: top of the thread + lead-in.
    pub z_start: f64,
    /// Radial depth of the first cut (SD1 G76 D).
    pub first_cut: f64,
    /// ANGLE (flank infeed, degrees).
    pub angle: f64,
    /// Radial thread depth.
    pub depth: f64,
    pub decimals: usize,
}

impl ThreadPlan {
    /// THREAD_angle30: with an infeed angle cut n starts depth_n * tan(angle / 2) further towards the chuck
    /// (cut 1, depth 1 at 30 degrees: Z5 -> 4.732), so the tool follows one flank. depth_n is the exact
    /// first * sqrt(n), not the rounded diameter (cut 51: 7.14143, Z3.086, not 3.087); the real Z is rounded.
    pub fn cut_start_z(&self, n: usize) -> f64 {
        if self.angle == 0.0 {
            return self.z_start;
        }
        let depth = (self.first_cut * (n as f64).sqrt()).min(self.depth);
        round(self.z_start - depth * (self.angle / 2.0).to_radians().tan(), self.decimals)
    }
}

/// The radial depth of each cut is rounded to the output decimals before it is doubled (cut 6:
/// sqrt(6) -> 2.449, X35.102, not 35.101).
pub fn plan_thread(op: &Thread, outer: &Element, inner: &Element, decimals: usize) -> Result<ThreadPlan> {
    if !op.towards {
        // THREAD_away: ALPHA-T only moves the start to Z-5 (inside the thread) and flips the pull-out W; the cuts
        // still run towards the chuck
        return not_implemented(tr("threading AWAY: ALPHA-T itself starts every cut inside the thread for it, so \
                                   the app does not offer it; use TOWARDS", &[]));
    }
    let (oa, ob, ia, ib) = (outer.a(), outer.b(), inner.a(), inner.b());
    if outer.is_arc() || inner.is_arc() || oa.x != ob.x || ia.x != ib.x {
        return value(tr("thread lines must be parallel to Z", &[]));
    }
    let (xo, xi) = (oa.x, ia.x);
    let depth = (xo - xi) / 2.0;
    // NO. OF CUTS mode: ALPHA-T stores the first cut rounded to 4 decimals (SD1 D4.0825)
    let first = if op.cuts > 0 { round(depth / (op.cuts as f64).sqrt(), 4) } else { op.cut_depth };
    if depth <= 0.0 || first <= 0.0 {
        return value(tr("thread: outer diameter must be above the inner one, first cut > 0", &[]));
    }
    // cuts at first x sqrt(k), their number the rounded (depth / first)^2, the last one always at the full depth
    // (UNC1/UNC2, 1/2-13 UNC depth 1.1633: D1 -> one cut, D0.6 -> 0.6, 0.849, 1.039, 1.1633)
    let n = ((round((depth / first).powi(2), 9) + 0.5).floor() as i64).max(1);
    if n > MAX_CUTS {
        return value(tr("threading: {n} cuts; make the first cut deeper or set fewer cuts (at most {max})",
                        &[("n", &n), ("max", &MAX_CUTS)]));
    }
    let mut steps: Vec<f64> = (1..n).map(|k| 2.0 * round(first * (k as f64).sqrt(), decimals)).collect();
    steps.push(2.0 * depth);
    // THREAD_leadin2_leadout2: cuts start at the thread top + lead-in (default 5) and end at its end - lead-out
    let z_end = round(oa.z.min(ob.z) - op.lead_out, 9);
    let z_start = round(oa.z.max(ob.z) + op.lead_in, 9);
    let plan = |xs, pullout_u, inner| ThreadPlan { xs, z_end, pullout_u, inner, z_start, first_cut: first,
                                                   angle: op.angle, depth, decimals };
    Ok(if op.external {
        // from the outer diameter in towards the inner one
        plan(steps.iter().map(|d| round((xo - d).max(xi), 9)).collect(), xo - xi + 1.0, xi)
    } else {
        // internal (THREAD_internal): from the inner (bore) diameter out to the outer one, pull-out inwards
        plan(steps.iter().map(|d| round((xi + d).min(xo), 9)).collect(), -(xo - xi + 1.0), xo)
    })
}
