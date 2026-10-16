//! Second setup (workflow improvements, step 6): the part turned around in the chuck to machine its other end.
//!
//! The drawing is mirrored about the middle of the part's length, so the back face becomes the new front face at Z0
//! and the old front face lands at Z-length. X (diameters) stays. Arcs change direction with the mirror. The new
//! setup has no operations (its own roughing and finishing are added to it) and the next program number, so both
//! programs can sit in the control side by side. The app keeps it in the same .alcam as setup 2
//! (Project::setups).

use crate::error::{Result, value};
use crate::i18n::tr;
use crate::model::{Element, Point, Project};
use crate::py::round;

/// The drawn part's length: from Z0 to its deepest point (the profile's back face).
pub fn part_length(project: &Project) -> f64 {
    let zs = project.geometry.iter().flat_map(|g| [g.a().z, g.b().z]);
    // Python's min(): the first of equal values
    match zs.reduce(|m, z| if z < m { z } else { m }) {
        Some(deepest) => round(-(if 0.0 < deepest { 0.0 } else { deepest }), 6),
        None => 0.0,
    }
}

/// A new project for the second setup: geometry and texts mirrored so that Z0 is the old back face (z' = -length
/// - z), the billet as long as the part, no operations, the next program number.
pub fn turn_around(project: &Project, length: Option<f64>, program_number: Option<i64>) -> Result<Project> {
    let length = length.unwrap_or_else(|| part_length(project));
    if length <= 0.0 {
        return value(tr("the part has no length to turn around", &[]));
    }
    let mut new = project.clone();
    let flip = |p: Point| Point::new(p.x, round(-length - p.z, 6));
    for g in &mut new.geometry {
        match g {
            Element::Line(l) => (l.a, l.b) = (flip(l.a), flip(l.b)),
            Element::Arc(c) => {
                (c.a, c.b, c.centre) = (flip(c.a), flip(c.b), flip(c.centre));
                c.ccw = !c.ccw; // a mirror reverses the direction of travel
            }
        }
    }
    for t in &mut new.texts {
        t.p = flip(t.p);
    }
    new.operations.clear();
    new.setups.clear();
    new.billet.length = round(length, 6);
    new.program_number = program_number.unwrap_or(project.program_number + 1);
    Ok(new)
}
