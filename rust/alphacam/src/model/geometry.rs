//! 2D lathe geometry in the XZ half-plane.
//!
//! X is a **diameter**, Z is 0 at the finished face and negative into the part (ALPHA-T convention, DESIGN.md 4.1).

use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::error::{Result, value};
use crate::i18n::tr;

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Point {
    /// diameter
    pub x: f64,
    pub z: f64,
}

impl Point {
    pub const fn new(x: f64, z: f64) -> Self {
        Self { x, z }
    }

    /// X is a diameter, so the radial distance is half the X difference.
    pub fn distance(&self, other: &Point) -> f64 {
        f64::hypot((self.x - other.x) / 2.0, self.z - other.z)
    }
}

/// Stored as `[x, z]`; `{"x": .., "z": ..}` is read too.
impl Serialize for Point {
    fn serialize<S: Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        [self.x, self.z].serialize(s)
    }
}

impl<'de> Deserialize<'de> for Point {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Repr {
            Pair(f64, f64),
            Named { x: f64, z: f64 },
        }
        Ok(match Repr::deserialize(d)? {
            Repr::Pair(x, z) | Repr::Named { x, z } => Point { x, z },
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Line {
    pub a: Point,
    pub b: Point,
    #[serde(default)]
    pub id: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Arc {
    pub a: Point,
    pub b: Point,
    pub centre: Point,
    #[serde(default = "yes")]
    pub ccw: bool,
    #[serde(default)]
    pub id: String,
}

fn yes() -> bool {
    true
}

/// A drawn element: what contours are picked from.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Element {
    Line(Line),
    Arc(Arc),
}

impl Element {
    pub fn line(a: Point, b: Point, id: impl Into<String>) -> Self {
        Element::Line(Line { a, b, id: id.into() })
    }

    pub fn arc(a: Point, b: Point, centre: Point, ccw: bool, id: impl Into<String>) -> Self {
        Element::Arc(Arc { a, b, centre, ccw, id: id.into() })
    }

    pub fn a(&self) -> Point {
        match self {
            Element::Line(l) => l.a,
            Element::Arc(c) => c.a,
        }
    }

    pub fn b(&self) -> Point {
        match self {
            Element::Line(l) => l.b,
            Element::Arc(c) => c.b,
        }
    }

    pub fn id(&self) -> &str {
        match self {
            Element::Line(l) => &l.id,
            Element::Arc(c) => &c.id,
        }
    }

    pub fn set_id(&mut self, id: String) {
        match self {
            Element::Line(l) => l.id = id,
            Element::Arc(c) => c.id = id,
        }
    }

    /// The centre of an arc, None for a line.
    pub fn centre(&self) -> Option<Point> {
        match self {
            Element::Line(_) => None,
            Element::Arc(c) => Some(c.centre),
        }
    }

    pub fn ccw(&self) -> bool {
        matches!(self, Element::Arc(c) if c.ccw)
    }

    pub fn is_arc(&self) -> bool {
        matches!(self, Element::Arc(_))
    }

    pub fn length(&self) -> f64 {
        self.a().distance(&self.b())
    }

    pub fn radius(&self) -> Option<f64> {
        self.centre().map(|c| self.a().distance(&c))
    }
}

/// A line of text on the drawing (ALPHA-T hit code T); not part of any contour or program.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Text {
    pub p: Point,
    pub text: String,
    #[serde(default = "text_size")]
    pub size: f64,
}

fn text_size() -> f64 {
    5.0
}

/// The elements of a contour (picked element ids, in pick order).
pub fn resolve<'g>(geometry: &'g [Element], ids: &[String]) -> Result<Vec<&'g Element>> {
    let missing: Vec<&str> =
        ids.iter().filter(|id| !geometry.iter().any(|g| g.id() == id.as_str())).map(String::as_str).collect();
    if !missing.is_empty() {
        let join_missing = missing.join(", ");
        return value(tr("contour references unknown geometry: {join_missing}", &[("join_missing", &join_missing)]));
    }
    // the last element with an id wins, like a dict built from the list
    Ok(ids.iter().map(|id| geometry.iter().rev().find(|g| g.id() == id.as_str()).unwrap()).collect())
}
