//! Write ALPHA-T .SDG drawings (and the matching empty .SDT text table), so ALPHA-T can open parts made here.
//!
//! The layout is the one io/sdg.rs reads (docs/RE_NOTES.md Progress 8 and 11), all values in mm:
//!   0x0000  segment table: word 0x3000 | segments, word points, then (start, end, centre) int16 per segment; a line
//!           stores -(i + 1) for its start and end points and 0 as the centre, an arc the centre as +(i + 1) for
//!           CCW and -(i + 1) for CW. The 4 bytes at 0x1FC and every 0x100 after it are skipped.
//!   0x0C00  float32 A[768] = Z + Zref           0x1800  float32 B[768] = X / 2 + Xref / 2
//!   0x2400  word 0x0400 (constant)
//!   0x2700  the same three blocks for the billet and chuck layer (segment centre word 0x7000): billet outline on
//!           both sides from Z0 to -(length + 20), and the jaws in the last 20 mm (6, 7, 7 long, dia +10/+20/+30)
//!   0x4B00  a third layer's table, empty (word 0x3000)
//!   The rest of the 0x2700 and 0x4B00 tables after the last entry holds leftover screen memory in ALPHA-T's files
//!   (menu texts with their colour bytes, the same in every sample); it is not part of the drawing and is written as
//!   zeros here.
//!   0x5700  settings float32: [0]/[11] screen scale, [1]/[2] view origin, [3] Zref = billet length + 20, [4] Xref/2,
//!           [5] units (0 mm, 1 inch), [6] billet length, [7] billet diameter, [8] first tool, [9]/[10] tool start
//!           (dia + 20, 10), [13]/[14]/[15] view limits.
//! Every sample's geometry, billet layer and settings come back byte for byte (tests/test_sdg_write.py); the view
//! values are ALPHA-T's for a fresh drawing (a sample that was zoomed or panned keeps its own).

use std::path::{Path, PathBuf};

use super::sdg::table_offsets;
use super::{put_f32, put_i16};
use crate::error::{Error, Result, value};
use crate::i18n::tr;
use crate::model::{Element, Point, Project};
use crate::py::round;

/// 28416
pub const SIZE: usize = 0x6F00;
/// 2304
pub const SDT_SIZE: usize = 0x900;
/// G92 X163.3803 / 2, fixed
pub const XREF2: f64 = 81.69014739990234;
pub const MM_PER_INCH: f64 = 25.4;
pub const MAX_POINTS: usize = 768;
/// the view fits Z into this many pixels ...
pub const SCREEN_W: f64 = 534.0;
/// ... and the larger of the Z and X extents into this many
pub const FIT: f64 = 425.0;

type Seg = (i32, i32, i32);

fn layer(buf: &mut [u8], table: usize, a_at: usize, b_at: usize, points: &[(f64, f64)], segs: &[Seg], zref: f64)
         -> Result<()> {
    if points.len() > MAX_POINTS {
        return value(tr("the drawing has too many points for ALPHA-T ({n} > {max})",
                        &[("n", &points.len()), ("max", &MAX_POINTS)]));
    }
    if segs.len() > 0xFF {
        // the count shares its word with the 0x3000 flag; ALPHA-T reads the low byte
        return value(tr("the drawing has too many elements for ALPHA-T ({n} > 255)", &[("n", &segs.len())]));
    }
    let mut words = vec![0x3000 | segs.len() as i32, points.len() as i32];
    words.extend(segs.iter().flat_map(|&(s, e, c)| [s, e, c]));
    let offs = table_offsets(table);
    if words.len() > offs.len() {
        return value(tr("the drawing has too many elements for ALPHA-T", &[]));
    }
    for (&off, &w) in offs.iter().zip(&words) {
        put_i16(buf, off, w as i16);
    }
    for (i, &(x_mm, z_mm)) in points.iter().enumerate() {
        put_f32(buf, a_at + 4 * i, z_mm + zref);
        put_f32(buf, b_at + 4 * i, x_mm / 2.0 + XREF2);
    }
    Ok(())
}

/// Billet outline and stepped chuck jaws, in ALPHA-T's point and segment order (AREA1_metric).
fn billet_layer(dia: f64, length: f64) -> (Vec<(f64, f64)>, Vec<Seg>) {
    let zc = -(length + 20.0);
    let mut pts = vec![(-dia, zc), (-dia, 0.0), (dia, zc), (dia, 0.0)];
    for side in [1.0, -1.0] {
        pts.extend([(side * (dia + 30.0), zc), (side * (dia + 30.0), zc + 6.0), (side * (dia + 20.0), zc + 6.0),
                    (side * (dia + 20.0), zc + 13.0), (side * (dia + 10.0), zc + 13.0),
                    (side * (dia + 10.0), zc + 20.0), (side * dia, zc + 20.0)]);
    }
    let idx = |k: i32| -(k + 1);
    let mut segs = vec![(idx(1), idx(0)), (idx(0), idx(2)), (idx(2), idx(3)), (idx(3), idx(1)), (idx(2), idx(4))];
    segs.extend((4..10).map(|k| (idx(k), idx(k + 1))));
    segs.push((idx(0), idx(11)));
    segs.extend((11..17).map(|k| (idx(k), idx(k + 1))));
    (pts, segs.into_iter().map(|(s, e)| (s, e, 0x7000)).collect())
}

pub fn write_sdg(project: &Project) -> Result<Vec<u8>> {
    let scale = if project.units == "inch" { MM_PER_INCH } else { 1.0 };
    let (dia, length) = (project.billet.diameter * scale, project.billet.length * scale);
    let zref = length + 20.0;
    let mut buf = vec![0u8; SIZE];

    // part geometry: points numbered in order of first use, shared between the elements that meet there (so the
    // contour stays connected); a drawing drawn as a chain gets ALPHA-T's own numbering
    let mut pts: Vec<(f64, f64)> = vec![];
    let mut index = |p: Point| -> i32 {
        let q = (round(p.x * scale, 6), round(p.z * scale, 6));
        match pts.iter().position(|&r| r == q) {
            Some(i) => i as i32,
            None => {
                pts.push(q);
                pts.len() as i32 - 1
            }
        }
    };

    let mut segs = vec![];
    for g in &project.geometry {
        // ALPHA-T numbers a new element's end point before its start point
        let (e, s) = (index(g.b()), index(g.a()));
        match g {
            Element::Arc(a) => {
                let c = index(a.centre) + 1;
                segs.push((-(s + 1), -(e + 1), if a.ccw { c } else { -c }));
            }
            Element::Line(_) => segs.push((-(s + 1), -(e + 1), 0)),
        }
    }
    layer(&mut buf, 0x0000, 0x0C00, 0x1800, &pts, &segs, zref)?;
    put_i16(&mut buf, 0x2400, 0x0400);
    let (bpts, bsegs) = billet_layer(dia, length);
    layer(&mut buf, 0x2700, 0x3300, 0x3F00, &bpts, &bsegs, zref)?;
    put_i16(&mut buf, 0x4B00, 0x3000); // a third layer, always empty

    let (lo, hi) = (XREF2 - (dia + 30.0) / 2.0, XREF2 + (dia + 30.0) / 2.0);
    let zspan = zref + 10.0;
    let fit = FIT / (hi - lo).max(zspan);
    let settings = [fit, -(SCREEN_W / fit - zspan) / 2.0, lo - 5.0 / fit, zref, XREF2,
                    if project.units == "inch" { 1.0 } else { 0.0 }, length, dia, project.first_tool as f64,
                    dia + 20.0, 10.0, fit, 0.0, lo, zspan, hi];
    for (k, v) in settings.into_iter().enumerate() {
        put_f32(&mut buf, 0x5700 + 4 * k, v);
    }
    Ok(buf)
}

/// The text table ALPHA-T writes for a drawing without text (texts stay in the .alcam part): word 0 = the part's
/// element count, word 1 = the billet layer's (18), and constant words at 0x80 / 0xCC (every sample).
pub fn write_sdt(project: &Project) -> Vec<u8> {
    let mut buf = vec![0u8; SDT_SIZE];
    put_i16(&mut buf, 0, project.geometry.len() as i16);
    put_i16(&mut buf, 2, 18);
    for (k, w) in [112, 40, 76].into_iter().enumerate() {
        put_i16(&mut buf, 0x80 + 2 * k, w);
    }
    put_i16(&mut buf, 0xCC, 12);
    buf
}

/// Write NAME.SDG and an empty NAME.SDT next to it; returns the files written.
pub fn save_sdg(project: &Project, path: impl AsRef<Path>) -> Result<Vec<PathBuf>> {
    let path = path.as_ref();
    let is_sdg = path.extension().is_some_and(|e| e.eq_ignore_ascii_case("sdg"));
    let sdg = if is_sdg { path.to_path_buf() } else { path.with_extension("SDG") };
    let sdt = sdg.with_extension(if sdg.extension().is_some_and(|e| e == "SDG") { "SDT" } else { "sdt" });
    let io = |e: std::io::Error| Error::Value(e.to_string());
    std::fs::write(&sdg, write_sdg(project)?).map_err(io)?;
    std::fs::write(&sdt, write_sdt(project)).map_err(io)?;
    Ok(vec![sdg, sdt])
}
