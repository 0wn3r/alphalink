//! Feature helpers on the drawing (workflow improvements, step 3): ISO metric and unified inch (UNC, UNF) threads
//! with a DIN 76 relief groove, and chamfers or radii on every outside corner of the profile at once. Pure geometry:
//! the app applies them as one undoable edit.

use crate::drawing::{self, add_line, outer_profile};
use crate::error::{Error, Result, value};
use crate::i18n::tr;
use crate::model::{Element, Point, Project};
use crate::py::{fmt_g, round};

/// ISO 261 metric threads (mm): nominal diameter -> coarse pitch, then the usual fine pitches (sorted by diameter)
pub const METRIC: &[(i32, &[f64])] = &[
    (6, &[1.0, 0.75]), (8, &[1.25, 1.0]), (10, &[1.5, 1.25, 1.0]), (12, &[1.75, 1.5, 1.25]), (14, &[2.0, 1.5]),
    (16, &[2.0, 1.5]), (18, &[2.5, 2.0, 1.5]), (20, &[2.5, 2.0, 1.5]), (22, &[2.5, 2.0, 1.5]), (24, &[3.0, 2.0, 1.5]),
    (27, &[3.0, 2.0, 1.5]), (30, &[3.5, 2.0, 1.5]), (33, &[3.5, 2.0]), (36, &[4.0, 3.0, 2.0]), (39, &[4.0, 3.0]),
    // fine pitches only (no coarse M40 or M50 in ISO 261)
    (40, &[3.0, 2.0, 1.5]),
    (42, &[4.5, 3.0]), (45, &[4.5, 3.0]), (48, &[5.0, 3.0]),
    (50, &[3.0, 2.0, 1.5]),
    (52, &[5.0, 4.0, 3.0]), (56, &[5.5, 4.0]), (60, &[5.5, 4.0]), (64, &[6.0, 4.0]),
];
pub const FINE_ONLY: &[i32] = &[40, 50];

/// Unified inch threads (ASME B1.1): size, major diameter (inch), UNC and UNF threads per inch
pub const UNIFIED: &[(&str, f64, f64, Option<f64>)] = &[
    ("1/4", 0.25, 20.0, Some(28.0)), ("5/16", 0.3125, 18.0, Some(24.0)), ("3/8", 0.375, 16.0, Some(24.0)),
    ("7/16", 0.4375, 14.0, Some(20.0)), ("1/2", 0.5, 13.0, Some(20.0)), ("9/16", 0.5625, 12.0, Some(18.0)),
    ("5/8", 0.625, 11.0, Some(18.0)), ("3/4", 0.75, 10.0, Some(16.0)), ("7/8", 0.875, 9.0, Some(14.0)),
    ("1", 1.0, 8.0, Some(12.0)), ("1-1/8", 1.125, 7.0, Some(12.0)), ("1-1/4", 1.25, 7.0, Some(12.0)),
    ("1-3/8", 1.375, 6.0, Some(12.0)), ("1-1/2", 1.5, 6.0, Some(12.0)), ("1-3/4", 1.75, 5.0, None),
    ("2", 2.0, 4.5, None),
];
pub const STANDARDS: &[&str] = &["M", "UNC", "UNF"];

/// External thread: minor diameter d - k P and depth on the radius h P. ISO 965-1 d3 (17/24 H); UNR (ASME B1.1)
/// rounded root d3 = d - 1.19078 P.
fn root(standard: &str) -> (f64, f64) {
    if standard == "M" { (1.22687, 0.61343) } else { (1.19078, 0.59539) }
}

fn metric(d: i64) -> Option<&'static [f64]> {
    METRIC.iter().find(|(n, _)| i64::from(*n) == d).map(|(_, p)| *p)
}

fn unified(size: &str) -> Result<&'static (&'static str, f64, f64, Option<f64>)> {
    UNIFIED.iter().find(|r| r.0 == size).ok_or_else(|| Error::Value(size.into()))
}

pub fn coarse(d: f64, pitch: f64) -> bool {
    let d = d.trunc() as i64;
    !FINE_ONLY.iter().any(|f| i64::from(*f) == d) && metric(d).is_some_and(|p| p[0] == pitch)
}

/// The sizes of a standard: mm diameters for M, size names ("1/2") for UNC/UNF.
pub fn sizes(standard: &str) -> Vec<String> {
    match standard {
        "M" => METRIC.iter().map(|(d, _)| d.to_string()).collect(),
        "UNC" => UNIFIED.iter().map(|r| r.0.to_string()).collect(),
        _ => UNIFIED.iter().filter(|r| r.3.is_some()).map(|r| r.0.to_string()).collect(),
    }
}

/// Pitches in the standard's own terms: mm for M (coarse first), threads per inch for UNC/UNF (one).
pub fn pitches(standard: &str, size: &str) -> Result<Vec<f64>> {
    if standard == "M" {
        let d = size.parse::<i64>().map_err(|_| Error::Value(size.into()))?;
        return metric(d).map(<[f64]>::to_vec).ok_or_else(|| Error::Value(size.into()));
    }
    let row = unified(size)?;
    if standard == "UNC" {
        return Ok(vec![row.2]);
    }
    row.3.map(|tpi| vec![tpi]).ok_or_else(|| Error::Value(format!("no UNF thread for {size}")))
}

fn major_mm(standard: &str, size: &str) -> Result<f64> {
    if standard == "M" {
        return size.parse().map_err(|_| Error::Value(size.into()));
    }
    Ok(unified(size)?.1 * 25.4)
}

/// The size of the standard nearest a drawn diameter (in the part's units).
pub fn nearest(standard: &str, diameter: f64, units: &str) -> String {
    let mm = if units == "inch" { diameter * 25.4 } else { diameter };
    let mut best: Option<(String, f64)> = None;
    for s in sizes(standard) {
        let k = (major_mm(standard, &s).unwrap_or(f64::NAN) - mm).abs();
        if best.as_ref().is_none_or(|(_, bk)| k < *bk) {
            best = Some((s, k));
        }
    }
    best.map(|(s, _)| s).unwrap_or_default()
}

/// The thread in the part's units: a metric thread on an inch part, or an inch one on a mm part, converted.
pub fn spec(standard: &str, size: &str, index: usize, units: &str) -> Result<ThreadSpec> {
    let pitch = *pitches(standard, size)?.get(index).ok_or_else(|| Error::Value("list index out of range".into()))?;
    let d_mm = major_mm(standard, size)?;
    let p_mm = if standard == "M" { pitch } else { 25.4 / pitch };
    let k = if units == "inch" { 1.0 / 25.4 } else { 1.0 };
    Ok(ThreadSpec {
        d: round(d_mm * k, 5),
        pitch: p_mm * k,
        standard: standard.into(),
        units: units.into(),
        size: size.into(),
        tpi: if standard == "M" { None } else { Some(pitch) },
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct ThreadSpec {
    /// nominal (major) diameter, in the part's units
    pub d: f64,
    /// in the part's units
    pub pitch: f64,
    pub standard: String,
    pub units: String,
    /// the standard's size name, "1/2" for UNC/UNF
    pub size: String,
    pub tpi: Option<f64>,
}

impl ThreadSpec {
    /// A metric thread in mm.
    pub fn new(d: f64, pitch: f64) -> Self {
        ThreadSpec { d, pitch, standard: "M".into(), units: "mm".into(), size: String::new(), tpi: None }
    }

    fn r(&self, v: f64, mm: usize, inch: usize) -> f64 {
        round(v, if self.units == "inch" { inch } else { mm })
    }

    /// External thread's minor diameter: ISO 965-1 d3 = d - 1.22687 P, UNR d3 = d - 1.19078 P.
    pub fn minor(&self) -> f64 {
        self.r(self.d - root(&self.standard).0 * self.pitch, 3, 4)
    }

    /// Thread depth on the radius: 0.61343 P (ISO h3), 0.59539 P (UNR).
    pub fn depth(&self) -> f64 {
        self.r(root(&self.standard).1 * self.pitch, 3, 4)
    }

    /// DIN 76-1 external undercut diameter, about d - 1.5 P (to 0.1 mm or 0.001 inch); the same proportions for
    /// inch threads.
    pub fn relief_diameter(&self) -> f64 {
        self.r(self.d - 1.5 * self.pitch, 1, 3)
    }

    /// DIN 76-1 undercut length, between g1 (about 2 P) and g2 (3.5 P): 3 P.
    pub fn relief_width(&self) -> f64 {
        self.r(3.0 * self.pitch, 1, 3)
    }

    pub fn name(&self) -> String {
        if self.standard != "M" {
            let tpi = self.tpi.map_or("None".into(), fmt_g);
            return format!("{}-{tpi} {}", self.size, self.standard);
        }
        let k = if self.units == "inch" { 25.4 } else { 1.0 };
        let (d, p) = (round(self.d * k, 3), round(self.pitch * k, 3));
        if coarse(d, p) { format!("M{}", fmt_g(d)) } else { format!("M{} × {}", fmt_g(d), fmt_g(p)) }
    }
}

pub fn nearest_size(diameter: f64) -> i32 {
    nearest("M", diameter, "mm").parse().unwrap_or_default()
}

/// Thread the profile line gid (a diameter along Z): it is set to the nominal diameter if it is not, the thread's
/// minor-diameter line is drawn under it (for the Threading operation), and with `relief` a DIN 76 undercut is cut
/// into the line at its chuck end. Returns the minor line's id.
pub fn add_thread(project: &mut Project, gid: &str, spec: &ThreadSpec, length: Option<f64>, relief: bool)
                  -> Result<String> {
    let index = |project: &Project| project.geometry.iter().position(|e| e.id() == gid);
    let g = match index(project).map(|i| &project.geometry[i]) {
        Some(Element::Line(l)) if (l.a.x - l.b.x).abs() <= 1e-9 && (l.a.z - l.b.z).abs() >= 1e-9 => l.clone(),
        _ => return value(tr("choose a line along Z (a diameter) for the thread", &[])),
    };
    let (front, back) = if g.a.z > g.b.z { (g.a, g.b) } else { (g.b, g.a) };
    let span = front.z - back.z;
    let width = if relief { spec.relief_width() } else { 0.0 };
    if width >= span {
        return value(tr("the line is too short for {name} and its relief groove", &[("name", &spec.name())]));
    }
    let d = spec.d;
    // the line at the nominal diameter, shortened by the groove; the ends of the neighbours follow
    move_point(project, front, Point::new(d, front.z));
    move_point(project, back, Point::new(d, back.z));
    if relief {
        let start = Point::new(d, back.z + width);
        if let Some(Element::Line(g)) = index(project).map(|i| &mut project.geometry[i]) {
            if g.a.z > g.b.z { g.b = start } else { g.a = start }
        }
        let dg = spec.relief_diameter();
        add_line(project, start, Point::new(dg, start.z))?;
        add_line(project, Point::new(dg, start.z), Point::new(dg, back.z))?;
        add_line(project, Point::new(dg, back.z), Point::new(d, back.z))?;
    }
    let thread_end = if relief { back.z + width / 2.0 } else { back.z };
    let end = match length {
        Some(length) if length != 0.0 => front.z - length,
        _ => thread_end,
    };
    let end = if back.z > end { back.z } else { end };
    add_line(project, Point::new(spec.minor(), front.z), Point::new(spec.minor(), round(end, 3)))
}

fn move_point(project: &mut Project, old: Point, new: Point) {
    if old == new {
        return;
    }
    for e in &mut project.geometry {
        let (a, b) = match e {
            Element::Line(l) => (&mut l.a, &mut l.b),
            Element::Arc(c) => (&mut c.a, &mut c.b),
        };
        if *a == old {
            *a = new;
        }
        if *b == old {
            *b = new;
        }
    }
}

/// The convex corners between two lines of the outer profile (where a chamfer or radius takes material off the
/// outside of the part), front to back; corners on the axis are left out.
pub fn outside_corners(project: &Project) -> Vec<Point> {
    let chain: Vec<&Element> =
        outer_profile(project).iter().filter_map(|i| project.geometry.iter().rev().find(|g| g.id() == i)).collect();
    if chain.is_empty() {
        return vec![];
    }
    // walk the chain as one path from its front end
    let mut path = vec![];
    let mut pos = if chain[0].a().z >= chain[0].b().z { chain[0].a() } else { chain[0].b() };
    for e in &chain {
        let (a, b) = if e.a() == pos { (e.a(), e.b()) } else { (e.b(), e.a()) };
        path.push((*e, a, b));
        pos = b;
    }
    let mut corners = vec![];
    for w in path.windows(2) {
        let ((e1, a1, b1), (e2, a2, b2)) = (w[0], w[1]);
        if e1.is_arc() || e2.is_arc() || b1.x.abs() < 1e-9 {
            continue;
        }
        let d1 = (b1.z - a1.z, (b1.x - a1.x) / 2.0);
        let d2 = (b2.z - a2.z, (b2.x - a2.x) / 2.0);
        if d1.0 * d2.1 - d1.1 * d2.0 > 1e-9 {
            // turning away from the material below the profile
            corners.push(b1);
        }
    }
    corners
}

/// Chamfer ("chamfer") or round ("fillet") every outside corner; returns how many were done (a corner whose lines
/// are too short for the size is left as it is).
pub fn all_corners(project: &mut Project, kind: &str, size: f64, angle: f64) -> usize {
    let mut done = 0;
    for c in outside_corners(project) {
        let r = if kind == "chamfer" { drawing::chamfer(project, c, size, angle) } else { drawing::fillet(project, c, size) };
        if r.is_ok() {
            done += 1;
        }
    }
    done
}
