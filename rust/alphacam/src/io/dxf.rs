//! Import a turned-part profile from DXF (ALPHA-T's ATDXF; the file is read with the `dxf` crate).
//!
//! DXF X is the Z axis (spindle axis), DXF Y the distance from the axis: a radius (usual for lathe drawings) or a
//! diameter, as chosen. Entities: LINE, ARC, CIRCLE (two half arcs, as the app draws circles), POLYLINE and LWPOLYLINE
//! (bulges); TEXT, INSERT (blocks: the title block too), SPLINE, ELLIPSE and the rest are skipped and counted, as ADXF
//! does. A drawing from the office lands in place: it can be turned (a part drawn upright), put with its front face on
//! the right, its axis on Y0 (as drawn, the middle of a full section, or the lowest point of a half drawing) and its
//! front face on Z0. By default only the half at or above the axis is kept (a full-section drawing mirrors the part
//! below it).
//!
//! The Python app reads with ezdxf; the `dxf` crate reads the same ASCII and binary files. Its object coordinate
//! systems are computed here the way ezdxf does (the arbitrary axis algorithm), so the points come out the same.

use std::f64::consts::PI;
use std::path::Path;

use dxf::Drawing;
use dxf::entities::{Entity, EntityType};

use crate::error::{Error, Result, value};
use crate::i18n::tr;
use crate::model::{Billet, Element, Point, Project};
use crate::py::{hypot, round};

/// How to read the drawing (the import dialog's choices).
#[derive(Debug, Clone, PartialEq)]
pub struct DxfOptions {
    /// "radius" or "diameter": what DXF Y is
    pub y_is: String,
    /// unit of the DXF and of the new part ("mm" or "inch")
    pub units: String,
    /// keep only the half at or above the axis
    pub upper_half: bool,
    /// 0, 90, 180 or 270 degrees anticlockwise, first
    pub rotate: i32,
    /// "right" (as the lathe is seen) or "left" (the drawing is mirrored so the front face comes to the right)
    pub front: String,
    /// "as drawn" (DXF Y0), "middle" (a full section: half way between the lowest and the highest point) or
    /// "lowest" (a half drawing standing on its axis)
    pub axis: String,
    /// put the front face (the largest Z) on Z0
    pub datum: bool,
}

impl Default for DxfOptions {
    fn default() -> Self {
        DxfOptions {
            y_is: "radius".into(),
            units: "mm".into(),
            upper_half: true,
            rotate: 0,
            front: "right".into(),
            axis: "as drawn".into(),
            datum: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct DxfResult {
    pub project: Project,
    /// entity type -> how many were skipped, in the order first met
    pub skipped: Vec<(String, usize)>,
}

type V3 = [f64; 3];
type Xy = (f64, f64);

/// ("line", a, b) | ("arc", a, b, c, ccw) in WCS (x, y).
#[derive(Debug, Clone, Copy)]
enum Prim {
    Line(Xy, Xy),
    Arc(Xy, Xy, Xy, bool),
}

fn cross(a: V3, b: V3) -> V3 {
    [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

fn normalize(a: V3) -> V3 {
    let factor = 1.0 / (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt();
    [a[0] * factor, a[1] * factor, a[2] * factor]
}

/// ezdxf's `isclose` (rel 1e-9, abs 1e-12).
fn isclose(a: f64, b: f64) -> bool {
    let diff = (b - a).abs();
    diff <= (1e-9 * b).abs() || diff <= (1e-9 * a).abs() || diff <= 1e-12
}

/// An object coordinate system for an extrusion vector (ezdxf.math.OCS: the arbitrary axis algorithm).
struct Ocs {
    /// None: the extrusion is +Z and points pass through
    axes: Option<[V3; 3]>,
}

impl Ocs {
    fn new(extrusion: &dxf::Vector) -> Self {
        let az = normalize([extrusion.x, extrusion.y, extrusion.z]);
        if isclose(az[0], 0.0) && isclose(az[1], 0.0) && isclose(az[2], 1.0) {
            return Ocs { axes: None };
        }
        let ax = if az[0].abs() < 1.0 / 64.0 && az[1].abs() < 1.0 / 64.0 {
            cross([0.0, 1.0, 0.0], az)
        } else {
            cross([0.0, 0.0, 1.0], az)
        };
        let ax = normalize(ax);
        let ay = normalize(cross(az, ax));
        Ocs { axes: Some([ax, ay, az]) }
    }

    fn to_wcs(&self, p: V3) -> V3 {
        let Some([ux, uy, uz]) = self.axes else { return p };
        let [x, y, z] = p;
        [x * ux[0] + y * uy[0] + z * uz[0], x * ux[1] + y * uy[1] + z * uz[1], x * ux[2] + y * uy[2] + z * uz[2]]
    }

    fn uz_z(&self) -> f64 {
        self.axes.map_or(1.0, |a| a[2][2])
    }
}

fn v3(p: &dxf::Point) -> V3 {
    [p.x, p.y, p.z]
}

/// The DXF type name, as ezdxf's `dxftype()` gives it.
fn dxftype(e: &EntityType) -> String {
    use EntityType as T;
    match e {
        T::Face3D(_) => "3DFACE",
        T::Solid3D(_) => "3DSOLID",
        T::ProxyEntity(_) => "ACAD_PROXY_ENTITY",
        T::Arc(_) => "ARC",
        T::ArcAlignedText(_) => "ARCALIGNEDTEXT",
        T::AttributeDefinition(_) => "ATTDEF",
        T::Attribute(_) => "ATTRIB",
        T::Body(_) => "BODY",
        T::Circle(_) => "CIRCLE",
        T::RotatedDimension(_)
        | T::RadialDimension(_)
        | T::DiameterDimension(_)
        | T::AngularThreePointDimension(_)
        | T::OrdinateDimension(_) => "DIMENSION",
        T::Ellipse(_) => "ELLIPSE",
        T::Helix(_) => "HELIX",
        T::Image(_) => "IMAGE",
        T::Insert(_) => "INSERT",
        T::Leader(_) => "LEADER",
        T::Light(_) => "LIGHT",
        T::Line(_) => "LINE",
        T::LwPolyline(_) => "LWPOLYLINE",
        T::MLine(_) => "MLINE",
        T::MText(_) => "MTEXT",
        T::OleFrame(_) => "OLEFRAME",
        T::Ole2Frame(_) => "OLE2FRAME",
        T::ModelPoint(_) => "POINT",
        T::Polyline(_) => "POLYLINE",
        T::Ray(_) => "RAY",
        T::Region(_) => "REGION",
        T::RText(_) => "RTEXT",
        T::Section(_) => "SECTION",
        T::Seqend(_) => "SEQEND",
        T::Shape(_) => "SHAPE",
        T::Solid(_) => "SOLID",
        T::Spline(_) => "SPLINE",
        T::Text(_) => "TEXT",
        T::Tolerance(_) => "TOLERANCE",
        T::Trace(_) => "TRACE",
        T::DgnUnderlay(_) => "DGNUNDERLAY",
        T::DwfUnderlay(_) => "DWFUNDERLAY",
        T::PdfUnderlay(_) => "PDFUNDERLAY",
        T::Vertex(_) => "VERTEX",
        T::Wipeout(_) => "WIPEOUT",
        T::XLine(_) => "XLINE",
    }
    .into()
}

fn count(skipped: &mut Vec<(String, usize)>, t: String) {
    match skipped.iter_mut().find(|(k, _)| *k == t) {
        Some((_, n)) => *n += 1,
        None => skipped.push((t, 1)),
    }
}

/// A point on a circle of the OCS at `angle` degrees, in WCS (ezdxf's `Circle.vertices`).
fn on_circle(ocs: &Ocs, centre: &dxf::Point, radius: f64, angle: f64) -> V3 {
    let a = angle * (PI / 180.0);
    ocs.to_wcs([a.cos() * radius + centre.x, a.sin() * radius + centre.y, 0.0 + centre.z])
}

/// POLYLINE flags: 3D polyline, polygon mesh, polyface mesh.
const POLYLINE_3D: i32 = 8;
const ANY3D: i32 = 8 | 16 | 64;

/// LINE, ARC, CIRCLE and 2D/3D polylines as prims in WCS (x, y). Entities drawn with a flipped extrusion
/// (0, 0, -1), as some CAD programs write mirrored geometry, run the other way in WCS.
fn wcs_prims<'a>(entities: impl Iterator<Item = &'a Entity>, prims: &mut Vec<Prim>, skipped: &mut Vec<(String, usize)>) {
    for e in entities {
        match &e.specific {
            EntityType::Line(l) => line(prims, v3(&l.p1), v3(&l.p2)),
            EntityType::Arc(arc) => {
                // DXF counter-clockwise in (x, y) = (Z, radius) is counter-clockwise with Z right and X up = G3
                let ocs = Ocs::new(&arc.normal);
                let r = arc.radius.abs();
                let (a, b) = (on_circle(&ocs, &arc.center, r, arc.start_angle), on_circle(&ocs, &arc.center, r, arc.end_angle));
                let c = ocs.to_wcs(v3(&arc.center));
                prims.push(Prim::Arc((a[0], a[1]), (b[0], b[1]), (c[0], c[1]), arc.normal.z > 0.0));
            }
            EntityType::Circle(circle) => {
                // two half arcs, split at the 3 and 9 o'clock points
                let c = Ocs::new(&circle.normal).to_wcs(v3(&circle.center));
                let r = circle.radius;
                if r > 0.0 {
                    prims.push(Prim::Arc((c[0] + r, c[1]), (c[0] - r, c[1]), (c[0], c[1]), true));
                    prims.push(Prim::Arc((c[0] - r, c[1]), (c[0] + r, c[1]), (c[0], c[1]), true));
                }
            }
            EntityType::LwPolyline(p) => {
                let vertices: Vec<(Xy, f64)> = p.vertices.iter().map(|v| ((v.x, v.y), v.bulge)).collect();
                chain(prims, &Ocs::new(&p.extrusion_direction), vertices, p.is_closed());
            }
            EntityType::Polyline(p) if p.flags & ANY3D == 0 => {
                let vertices: Vec<(Xy, f64)> = p.vertices().map(|v| ((v.location.x, v.location.y), v.bulge)).collect();
                chain(prims, &Ocs::new(&p.normal), vertices, p.is_closed());
            }
            EntityType::Polyline(p) if p.flags & POLYLINE_3D != 0 => {
                // WCS points, no bulges
                let pts: Vec<V3> = p.vertices().map(|v| v3(&v.location)).collect();
                let mut ends: Vec<V3> = pts.iter().skip(1).copied().collect();
                if p.is_closed() {
                    ends.extend(pts.first());
                }
                for (a, b) in pts.iter().zip(ends) {
                    line(prims, *a, b);
                }
            }
            other => count(skipped, dxftype(other)),
        }
    }
}

fn line(prims: &mut Vec<Prim>, a: V3, b: V3) {
    if (a[0], a[1]) != (b[0], b[1]) {
        prims.push(Prim::Line((a[0], a[1]), (b[0], b[1])));
    }
}

/// Polyline vertices ((x, y) in OCS, bulge to the next): lines and arcs in the drawn order and direction.
fn chain(prims: &mut Vec<Prim>, ocs: &Ocs, mut v: Vec<(Xy, f64)>, closed: bool) {
    if closed && !v.is_empty() {
        v.push(v[0]);
    }
    let ccw_is_ccw = ocs.uz_z() > 0.0;
    for w in v.windows(2) {
        let ((a, bg), (b, _)) = (w[0], w[1]);
        let (wa, wb) = (ocs.to_wcs([a.0, a.1, 0.0]), ocs.to_wcs([b.0, b.1, 0.0]));
        if bg == 0.0 {
            line(prims, wa, wb);
            continue;
        }
        let d = hypot(a.0 - b.0, a.1 - b.1);
        if d == 0.0 {
            continue;
        }
        let th = 4.0 * bg.atan();
        let r = d / (2.0 * (th.abs() / 2.0).sin());
        let (mx, my) = ((a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0);
        let h = (r * r - (d / 2.0).powi(2)).max(0.0).sqrt()
            * (if th.abs() < PI { 1.0 } else { -1.0 })
            * (if bg > 0.0 { 1.0 } else { -1.0 });
        let (ux, uy) = ((b.0 - a.0) / d, (b.1 - a.1) / d);
        let c = ocs.to_wcs([mx - uy * h, my + ux * h, 0.0]);
        prims.push(Prim::Arc((wa[0], wa[1]), (wb[0], wb[1]), (c[0], c[1]), (bg > 0.0) == ccw_is_ccw));
    }
}

/// The model space of a DXF drawing as a part (see [`DxfOptions`]).
pub fn read_doc(doc: &Drawing, opts: &DxfOptions) -> DxfResult {
    let k = if opts.y_is == "radius" { 2.0 } else { 1.0 };
    let mut prims = vec![]; // in DXF (x, y), before turning and moving
    let mut skipped = vec![];
    wcs_prims(model_space(doc), &mut prims, &mut skipped);
    // turn, mirror, then move the axis and the front face into place (DXF units)
    let mirrored = opts.front == "left";
    let turned = |p: Xy| {
        let (mut x, mut y) = p;
        for _ in 0..opts.rotate.div_euclid(90).rem_euclid(4) {
            (x, y) = (-y, x);
        }
        (if mirrored { -x } else { x }, y)
    };
    let pts: Vec<Xy> = prims
        .iter()
        .flat_map(|pr| match *pr {
            Prim::Line(a, b) => vec![a, b],
            Prim::Arc(a, b, c, _) => vec![a, b, c],
        })
        .map(turned)
        .collect();
    let min_y = pts.iter().map(|p| p.1).fold(f64::INFINITY, f64::min);
    let max_y = pts.iter().map(|p| p.1).fold(f64::NEG_INFINITY, f64::max);
    let dy = match opts.axis.as_str() {
        _ if pts.is_empty() => 0.0,
        "middle" => -(min_y + max_y) / 2.0,
        "lowest" => -min_y,
        _ => 0.0,
    };
    let dx = if !pts.is_empty() && opts.datum { -pts.iter().map(|p| p.0).fold(f64::NEG_INFINITY, f64::max) } else { 0.0 };
    // (X diameter, Z)
    let pt = |p: Xy| {
        let (x, y) = turned(p);
        Point::new(round(k * (y + dy), 6), round(x + dx, 6))
    };

    let mut elements: Vec<Element> = prims
        .iter()
        .map(|pr| match *pr {
            Prim::Line(a, b) => Element::line(pt(a), pt(b), ""),
            Prim::Arc(a, b, c, ccw) => Element::arc(pt(a), pt(b), pt(c), ccw != mirrored, ""),
        })
        .collect();
    if opts.upper_half {
        elements.retain(|g| g.a().x.min(g.b().x) >= -1e-9 && !g.centre().is_some_and(|c| c.x < -1e-9));
    }
    for (n, g) in elements.iter_mut().enumerate() {
        g.set_id(format!("g{n}"));
    }
    let mut xs: Vec<f64> = elements.iter().flat_map(|g| [g.a().x, g.b().x]).collect();
    let mut zs: Vec<f64> = elements.iter().flat_map(|g| [g.a().z, g.b().z]).collect();
    if xs.is_empty() {
        (xs, zs) = (vec![50.0], vec![-80.0]);
    }
    let step = if opts.units == "inch" { 0.1 } else { 2.0 };
    let max_x = xs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let min_z = zs.iter().copied().fold(f64::INFINITY, f64::min);
    let billet = Billet {
        diameter: (max_x / step).ceil() * step + step,
        length: (-min_z.min(0.0) / step).ceil() * step + step,
        ..Billet::default()
    };
    let tc = if opts.units == "inch" {
        Point::new(round(100.0 / 25.4, 4), round(50.0 / 25.4, 4))
    } else {
        Point::new(100.0, 50.0)
    };
    let project = Project { units: opts.units.clone(), billet, tool_change: tc, geometry: elements, ..Project::default() };
    DxfResult { project, skipped }
}

fn model_space(doc: &Drawing) -> impl Iterator<Item = &Entity> {
    doc.entities().filter(|e| !e.common.is_in_paper_space)
}

/// A DXF given as text (see [`DxfOptions`]).
pub fn read_dxf(text: &str, opts: &DxfOptions) -> Result<DxfResult> {
    read(Drawing::load(&mut text.as_bytes()), opts)
}

/// A DXF file, ASCII (any version) or binary (see [`DxfOptions`]).
pub fn load_dxf(path: impl AsRef<Path>, opts: &DxfOptions) -> Result<DxfResult> {
    read(Drawing::load_file(path), opts)
}

fn read(doc: dxf::DxfResult<Drawing>, opts: &DxfOptions) -> Result<DxfResult> {
    let doc = doc.map_err(|e| Error::Value(tr("not a readable DXF file ({error})", &[("error", &e)])))?;
    if model_space(&doc).next().is_none() {
        return value(tr("the file has no drawing in it", &[]));
    }
    Ok(read_doc(&doc, opts))
}
