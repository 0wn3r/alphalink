//! Read ALPHA-T / ALPHACAM tool libraries (ATOOLIB.TL1, also the ATTOOLS working copy WORKLIB.TL1).
//!
//! 256-byte records; record 0 is a header (first int16 = number of slots), record n is tool n:
//!   name[20] (space padded) · float32 nose radius · int16 front clearance angle · int16 rear clearance angle ·
//!   description text (space/NUL padded). Empty names are free slots. (RE_NOTES Progress 2 and 12.)
//!
//! ATOOLIB.TL2 holds the tool pictures ALPHA-T draws, one 780-byte record per tool (record 0 = tool 1), in the
//! segment/point layout of its drawings (RE_NOTES Progress 41): int16 segment count, int16 point count, then
//! (start, end, centre) triples with points as -(index+1) and centre 0 for a line, >0 for an anticlockwise and <0 for a
//! clockwise arc about point centre-1; float32 A[64] at 0x104 and B[64] at 0x208. A runs along Z and B along the radius
//! (X/2), in mm, with the tool's tip at (0, 0). The .IM1/.IM2 pair is ATSETUP's alternative library of the same layout.

use std::collections::BTreeMap;
use std::f64::consts::PI;
use std::path::{Path, PathBuf};

use super::{f32_at, i16_at, py_strip, read_file};
use crate::error::{Result, value};
use crate::i18n::tr;
use crate::model::tools::{Polyline, tool_from_record};
use crate::model::{Tool, ToolLibrary};
use crate::py::{fmod_floor, hypot, round};

pub const RECORD: usize = 256;

/// Latin-1 text: every byte is the code point of the same number.
fn latin1(bytes: &[u8]) -> String {
    bytes.iter().map(|&b| b as char).collect()
}

pub fn read_tl1(data: &[u8]) -> Result<ToolLibrary> {
    if data.len() < 2 * RECORD || !data.len().is_multiple_of(RECORD) {
        return value(tr("not a TL1 tool library ({len_data} bytes)", &[("len_data", &data.len())]));
    }
    let mut tools: Vec<Tool> = vec![];
    for n in 1..data.len() / RECORD {
        let rec = &data[n * RECORD..(n + 1) * RECORD];
        let name = latin1(&rec[..20]);
        let name = name.trim_matches([' ', '\0']);
        if name.is_empty() {
            continue;
        }
        let radius = f32_at(rec, 20);
        let (front, rear) = (i16_at(rec, 24), i16_at(rec, 26));
        let desc = latin1(&rec[28..]).replace('\0', " ");
        tools.push(tool_from_record(n as i32, name, round(radius, 4), front as f64, rear as f64, py_strip(&desc)));
    }
    Ok(ToolLibrary { tools })
}

pub fn load_tl1(path: impl AsRef<Path>) -> Result<ToolLibrary> {
    read_tl1(&read_file(path.as_ref())?)
}

pub const TL2_RECORD: usize = 780;

/// Tool number -> the picture as polylines of (Z, radius) points in mm, tip at (0, 0); arcs sampled.
pub fn read_tl2(data: &[u8]) -> Result<BTreeMap<i32, Vec<Polyline>>> {
    if data.len() < TL2_RECORD || !data.len().is_multiple_of(TL2_RECORD) {
        return value(tr("not a TL2 tool picture file ({len_data} bytes)", &[("len_data", &data.len())]));
    }
    let mut out = BTreeMap::new();
    for i in 0..data.len() / TL2_RECORD {
        let rec = &data[i * TL2_RECORD..(i + 1) * TL2_RECORD];
        let (count, npts) = (i16_at(rec, 0) as i64, i16_at(rec, 2) as i64);
        if !(0 < count && count <= 40 && 0 < npts && npts <= 64) {
            continue;
        }
        let words: Vec<i64> = (0..3 * count as usize).map(|k| i16_at(rec, 4 + 2 * k) as i64).collect();
        let pts: Vec<(f64, f64)> =
            (0..npts as usize).map(|j| (f32_at(rec, 0x104 + 4 * j), f32_at(rec, 0x208 + 4 * j))).collect();
        let mut lines: Vec<Vec<(f64, f64)>> = vec![];
        for k in 0..count as usize {
            let (s, e, c) = (words[3 * k], words[3 * k + 1], words[3 * k + 2]);
            if s >= 0 || e >= 0 || -s > npts || -e > npts || c.abs() > npts {
                continue; // 0 marks a deleted segment
            }
            let (p, q) = (pts[(-s - 1) as usize], pts[(-e - 1) as usize]);
            if c == 0 {
                lines.push(vec![p, q]);
                continue;
            }
            let (cz, cr) = pts[(c.abs() - 1) as usize];
            let r = hypot(p.0 - cz, p.1 - cr);
            let (a0, a1) = ((p.1 - cr).atan2(p.0 - cz), (q.1 - cr).atan2(q.0 - cz));
            let sweep = if c > 0 { fmod_floor(a1 - a0, 2.0 * PI) } else { -fmod_floor(a0 - a1, 2.0 * PI) };
            let steps = 4.max((sweep.abs() / (PI / 18.0)) as i64);
            lines.push(
                (0..=steps)
                    .map(|t| {
                        let a = a0 + sweep * t as f64 / steps as f64;
                        (cz + r * a.cos(), cr + r * a.sin())
                    })
                    .collect(),
            );
        }
        if !lines.is_empty() {
            let pics = lines.iter().map(|l| l.iter().map(|&(z, x)| [round(z, 4), round(x, 4)]).collect()).collect();
            out.insert(i as i32 + 1, pics);
        }
    }
    Ok(out)
}

pub fn load_tl2(path: impl AsRef<Path>) -> Result<BTreeMap<i32, Vec<Polyline>>> {
    read_tl2(&read_file(path.as_ref())?)
}

/// ATOOLIB.TL1 -> ATOOLIB.TL2 next to it, whatever the case of the name.
pub fn sibling(path: impl AsRef<Path>, suffix: &str) -> Option<PathBuf> {
    let p = path.as_ref();
    let (want_stem, want) = (stem(p)?.to_lowercase(), suffix.to_lowercase());
    let parent = p.parent().unwrap_or(Path::new(""));
    let listed = if parent.as_os_str().is_empty() { Path::new(".") } else { parent };
    std::fs::read_dir(listed).ok()?.flatten().map(|e| parent.join(e.file_name())).find(|cand| {
        let name = cand.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        // glob("*") leaves out hidden files
        !name.starts_with('.')
            && stem(cand).is_some_and(|s| s.to_lowercase() == want_stem)
            && suffix_of(cand).to_lowercase() == want
    })
}

/// Python's `Path.stem`.
fn stem(p: &Path) -> Option<String> {
    p.file_stem().map(|s| s.to_string_lossy().into_owned())
}

/// Python's `Path.suffix`: ".TL2", or "" without one.
fn suffix_of(p: &Path) -> String {
    p.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default()
}
