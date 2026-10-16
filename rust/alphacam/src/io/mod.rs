//! ALPHA-T's files and the drawings people bring: .SDG drawings (read and write), .SD1 listings (rebuilding the
//! operations), .PRG programs (parsing), the ATOOLIB tool library and DXF.

pub mod dxf;
pub mod prg_parse;
pub mod sd1;
pub mod sdg;
pub mod sdg_write;
pub mod toollib;

use crate::error::{Error, Result};

/// Python's `str.isspace()` for the characters these files hold (Rust's `is_whitespace` leaves out \x1c-\x1f).
pub(crate) fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// Python's `str.strip()`.
pub(crate) fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// Bytes decoded as Python's `decode("ascii", errors="replace")`: one U+FFFD per byte above 0x7F.
pub(crate) fn ascii_replace(data: &[u8]) -> String {
    data.iter().map(|&b| if b < 0x80 { b as char } else { '\u{FFFD}' }).collect()
}

/// Python's `float(text)` for the number words of ALPHA-T's files.
pub(crate) fn py_float(text: &str) -> Result<f64> {
    py_strip(text).parse().map_err(|_| Error::Value(format!("could not convert string to float: '{text}'")))
}

/// Python's `int(text)`.
pub(crate) fn py_int(text: &str) -> Result<i64> {
    py_strip(text).parse().map_err(|_| Error::Value(format!("invalid literal for int() with base 10: '{text}'")))
}

/// A little-endian float32 at `at`, widened like Python's `struct.unpack("<f")`.
pub(crate) fn f32_at(data: &[u8], at: usize) -> f64 {
    f32::from_le_bytes(data[at..at + 4].try_into().expect("4 bytes")) as f64
}

/// A little-endian int16 at `at`.
pub(crate) fn i16_at(data: &[u8], at: usize) -> i16 {
    i16::from_le_bytes(data[at..at + 2].try_into().expect("2 bytes"))
}

pub(crate) fn put_i16(buf: &mut [u8], at: usize, v: i16) {
    buf[at..at + 2].copy_from_slice(&v.to_le_bytes());
}

/// Python's `struct.pack_into("<f")`: the double rounded to the nearest float32.
pub(crate) fn put_f32(buf: &mut [u8], at: usize, v: f64) {
    buf[at..at + 4].copy_from_slice(&(v as f32).to_le_bytes());
}

pub(crate) fn read_file(path: &std::path::Path) -> Result<Vec<u8>> {
    std::fs::read(path).map_err(|e| Error::Value(e.to_string()))
}
