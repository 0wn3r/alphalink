//! The .alcam text exactly as the Python app writes it (`json.dumps(indent=1)`, ASCII only, Python's float repr,
//! then [x, z] pairs and id lists put back on one line), so files saved by either app compare equal.

use regex::Regex;
use serde::Serialize;
use serde_json::Value;
use serde_json::ser::{Formatter, PrettyFormatter, Serializer};
use std::io::{self, Write};
use std::sync::OnceLock;

struct PyFormatter<'a>(PrettyFormatter<'a>);

/// Python's `repr(float)`: the shortest digits, fixed notation for exponents -4..16, else `1e-05` style.
pub fn py_float_repr(x: f64) -> String {
    if x.is_nan() {
        return "NaN".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    let sci = format!("{x:e}"); // shortest round-trip digits, e.g. "1.5e-5"
    let (mant, exp) = sci.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    if (-4..16).contains(&exp) {
        let fixed = x.to_string();
        if fixed.contains('.') { fixed } else { fixed + ".0" }
    } else {
        format!("{mant}e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs())
    }
}

macro_rules! delegate {
    ($($name:ident($($arg:ident: $ty:ty),*);)*) => {
        $(fn $name<W: ?Sized + Write>(&mut self, w: &mut W $(, $arg: $ty)*) -> io::Result<()> {
            self.0.$name(w $(, $arg)*)
        })*
    };
}

impl Formatter for PyFormatter<'_> {
    delegate! {
        begin_array();
        end_array();
        begin_array_value(first: bool);
        end_array_value();
        begin_object();
        end_object();
        begin_object_key(first: bool);
        end_object_key();
        begin_object_value();
        end_object_value();
    }

    fn write_f64<W: ?Sized + Write>(&mut self, w: &mut W, value: f64) -> io::Result<()> {
        w.write_all(py_float_repr(value).as_bytes())
    }

    /// ensure_ascii: everything outside ASCII as \uXXXX (UTF-16 surrogate pairs above the BMP)
    fn write_string_fragment<W: ?Sized + Write>(&mut self, w: &mut W, fragment: &str) -> io::Result<()> {
        for c in fragment.chars() {
            if c.is_ascii() {
                w.write_all(&[c as u8])?;
            } else {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    write!(w, "\\u{unit:04x}")?;
                }
            }
        }
        Ok(())
    }
}

pub fn dumps(value: &Value) -> String {
    let mut out = Vec::new();
    let mut ser = Serializer::with_formatter(&mut out, PyFormatter(PrettyFormatter::with_indent(b" ")));
    value.serialize(&mut ser).expect("JSON values serialise");
    let text = String::from_utf8(out).expect("ASCII output");
    static PAIRS: OnceLock<(Regex, Regex)> = OnceLock::new();
    let (pairs, commas) = PAIRS.get_or_init(|| {
        // only arrays of numbers or plain strings (a text like "Bore [ H7 ]" must stay as it is)
        let item = r#"(?:-?[\d.eE+-]+|"[^"\[\]\\]*")"#;
        (Regex::new(&format!(r"\[\s+({item}(?:,\s+{item})*)\s+\]")).unwrap(), Regex::new(r",\s+").unwrap())
    });
    let text = pairs.replace_all(&text, |c: &regex::Captures| format!("[{}]", commas.replace_all(&c[1], ", ")));
    text.into_owned() + "\n"
}

#[cfg(test)]
mod tests {
    use super::py_float_repr;

    #[test]
    fn float_repr_like_python() {
        for (x, s) in [(100.0, "100.0"), (0.15, "0.15"), (-49.6, "-49.6"), (1e-5, "1e-05"), (0.0001, "0.0001"),
                       (1e16, "1e+16"), (123456789012345.0, "123456789012345.0"), (-0.0, "-0.0"), (2.5e-7, "2.5e-07")] {
            assert_eq!(py_float_repr(x), s);
        }
    }
}
