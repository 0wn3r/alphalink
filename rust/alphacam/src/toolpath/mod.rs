//! Toolpaths: passes of moves in project units, unscaled (the post applies the machine profile's scaling).

pub mod area;
pub mod contour;
pub mod drill;
pub mod face;
pub mod finish;
pub mod groove;
pub mod thread;

use crate::error::Error;
use crate::i18n::tr;

/// More passes or plunges than this is a typing error (depth of cut 0.001), not a part.
pub const MAX_PASSES: usize = 5000;

pub fn too_many(what: &str) -> Error {
    Error::Value(tr("{what}: more than {max} passes; check the depth of cut / stepover",
                    &[("what", &what), ("max", &MAX_PASSES)]))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MoveKind {
    /// G0
    Rapid,
    /// G1
    Feed,
    /// G32 (and its modal U/W retract)
    Thread,
    /// G4
    Dwell,
    /// G2
    ArcCw,
    /// G3
    ArcCcw,
}

impl MoveKind {
    pub fn as_str(self) -> &'static str {
        match self {
            MoveKind::Rapid => "rapid",
            MoveKind::Feed => "feed",
            MoveKind::Thread => "thread",
            MoveKind::Dwell => "dwell",
            MoveKind::ArcCw => "arc_cw",
            MoveKind::ArcCcw => "arc_ccw",
        }
    }

    pub fn arc(ccw: bool) -> Self {
        if ccw { MoveKind::ArcCcw } else { MoveKind::ArcCw }
    }
}

/// X is a diameter. Arcs carry their absolute centre (diameter, Z).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Move {
    pub kind: MoveKind,
    pub x: Option<f64>,
    pub z: Option<f64>,
    pub feed: Option<f64>,
    pub dwell_revs: Option<f64>,
    pub centre_x: Option<f64>,
    pub centre_z: Option<f64>,
}

impl Move {
    fn at(kind: MoveKind, x: f64, z: f64, feed: Option<f64>) -> Self {
        Move { kind, x: Some(x), z: Some(z), feed, dwell_revs: None, centre_x: None, centre_z: None }
    }

    pub fn rapid(x: f64, z: f64) -> Self {
        Self::at(MoveKind::Rapid, x, z, None)
    }

    pub fn feed(x: f64, z: f64, feed: f64) -> Self {
        Self::at(MoveKind::Feed, x, z, Some(feed))
    }

    pub fn arc(ccw: bool, x: f64, z: f64, feed: f64, centre_x: f64, centre_z: f64) -> Self {
        Move { centre_x: Some(centre_x), centre_z: Some(centre_z), ..Self::at(MoveKind::arc(ccw), x, z, Some(feed)) }
    }

    /// A dwell at a point (grooving, drilling) or where the tool is (x, z None).
    pub fn dwell(at: Option<(f64, f64)>, revs: f64) -> Self {
        Move {
            kind: MoveKind::Dwell,
            x: at.map(|p| p.0),
            z: at.map(|p| p.1),
            feed: None,
            dwell_revs: Some(revs),
            centre_x: None,
            centre_z: None,
        }
    }

    /// (x, z) of a move that has them (all but a bare dwell).
    pub fn xz(&self) -> (f64, f64) {
        (self.x.unwrap_or(f64::NAN), self.z.unwrap_or(f64::NAN))
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Pass {
    pub moves: Vec<Move>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Toolpath {
    pub passes: Vec<Pass>,
    /// After the last pass (back to the tool start).
    pub retract: Vec<Move>,
    /// Grooving: plunges per pass (SD1 G75 H).
    pub plunges: usize,
}

impl Toolpath {
    pub fn new(passes: Vec<Pass>, retract: Vec<Move>) -> Self {
        Toolpath { passes, retract, plunges: 0 }
    }

    pub fn moves(&self) -> Vec<Move> {
        self.passes.iter().flat_map(|p| p.moves.iter().copied()).chain(self.retract.iter().copied()).collect()
    }
}
