//! Import ALPHA-T / ALPHACAM .SDG drawings (layout from tools/sdg_dump.py, docs/RE_NOTES.md).
//!
//! Segment table at 0x0000 (word0 = 0x3000|count, word1 = points, then int16 (start, end, centre) triples; the
//! 4-byte marker at 0x1FC and every 0x100 after it is skipped), float32 A[768] at 0x0C00 and B[768] at 0x1800.
//! Points are relative to the G92 reference in the settings block at 0x5700:
//!     Z = A - Zref        X (diameter) = 2 * (B - Xref/2)      all in mm, also in inch drawings.
//! Settings floats: [3] Zref, [4] Xref/2, [5] units (0 mm, 1 inch), [6] billet length, [7] billet diameter,
//! [8] first tool. Inch drawings are converted to inch (4 decimals).

use std::path::Path;

use super::{f32_at, i16_at, read_file};
use crate::error::{Error, Result, value};
use crate::i18n::tr;
use crate::model::{Billet, Element, Point, Project, Reference};
use crate::py::round;

pub const MM_PER_INCH: f64 = 25.4;

/// Byte offsets of the int16 words of a segment table, skipping the 4-byte markers.
pub(crate) fn table_offsets(base: usize) -> Vec<usize> {
    let (mut offs, mut pos) = (vec![], 0);
    while pos < 0xC00 {
        if pos == 0x1FC || (pos > 0x1FC && (pos - 0x1FC) % 0x100 == 0) {
            pos += 4;
            continue;
        }
        offs.push(base + pos);
        pos += 2;
    }
    offs
}

fn table(data: &[u8], base: usize) -> Vec<i16> {
    table_offsets(base).into_iter().map(|o| i16_at(data, o)).collect()
}

/// A point index as Python reads `pts[i]`: negative counts from the end.
fn at(pts: &[Point], i: i64) -> Result<Point> {
    let k = if i < 0 { pts.len() as i64 + i } else { i };
    usize::try_from(k).ok().and_then(|k| pts.get(k).copied()).ok_or_else(|| Error::Value("list index out of range".into()))
}

pub fn read_sdg(data: &[u8]) -> Result<Project> {
    if data.len() < 0x5740 {
        return value(tr("SDG too short ({len_data} bytes)", &[("len_data", &data.len())]));
    }
    let st: Vec<f64> = (0..16).map(|k| f32_at(data, 0x5700 + 4 * k)).collect();
    let (zref, xr2) = (st[3], st[4]);
    let units = if st[5] != 0.0 { "inch" } else { "mm" };
    let scale = if units == "inch" { MM_PER_INCH } else { 1.0 };

    let words = table(data, 0);
    let (count, npts) = ((words[0] & 0xFF) as usize, words[1]);
    if npts > 768 {
        return value("tuple index out of range".into());
    }
    let pts: Vec<Point> = (0..npts.max(0) as usize)
        .map(|i| {
            let (a, b) = (f32_at(data, 0x0C00 + 4 * i), f32_at(data, 0x1800 + 4 * i));
            Point::new(round(2.0 * (b - xr2) / scale, 4), round((a - zref) / scale, 4))
        })
        .collect();

    let mut geometry = vec![];
    for k in 0..count {
        let (s, e, c) = (words[2 + 3 * k] as i64, words[3 + 3 * k] as i64, words[4 + 3 * k] as i64);
        let (start, end) = (at(&pts, -s - 1)?, at(&pts, -e - 1)?);
        if c == 0 {
            geometry.push(Element::line(start, end, format!("g{k}")));
        } else {
            geometry.push(Element::arc(start, end, at(&pts, c.abs() - 1)?, c > 0, format!("g{k}")));
        }
    }

    Ok(Project {
        units: units.into(),
        // stored in mm
        billet: Billet { diameter: round(st[7] / scale, 6), length: round(st[6] / scale, 6), ..Billet::default() },
        reference: Reference { g92_x: round(2.0 * xr2, 4), g92_z: round(zref, 4) },
        first_tool: st[8] as i32,
        // ALPHA-T's tool change point X100 Z50 (mm) is not in the SDG; it is the same in every sample.
        tool_change: Point::new(round(100.0 / scale, 4), round(50.0 / scale, 4)),
        geometry,
        ..Project::default()
    })
}

pub fn load_sdg(path: impl AsRef<Path>) -> Result<Project> {
    read_sdg(&read_file(path.as_ref())?)
}
