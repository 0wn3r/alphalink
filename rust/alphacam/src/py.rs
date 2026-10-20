//! The few Python float rules the posts and messages still rely on: rounding to n decimals with ties to even, the
//! modulus that takes the divisor's sign, `%g` and `repr` for messages. Everything else is Rust's own (`hypot`, sums;
//! the ALPHA-T samples in tests/post.rs come out byte for byte with them).

/// Python's `round(x, n)`: the exact binary value rounded to `n` decimals, ties to even. Rust's `{:.n}` formatting
/// is exact with the same tie rule, so the round trip through text gives the same float (checked on 200k values).
pub fn round(x: f64, n: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.n$}").parse().expect("a formatted float parses")
}

/// Python's `round(x)` to an integer: ties to even.
pub fn round_int(x: f64) -> i64 {
    x.round_ties_even() as i64
}

/// Python's `x % m` for a positive modulus (the result takes the modulus' sign).
pub fn fmod_floor(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r != 0.0 && (r < 0.0) != (m < 0.0) { r + m } else { r }
}

/// Python's `f"{x:g}"` for messages (6 significant digits, no trailing zeros).
pub fn fmt_g(x: f64) -> String {
    if x == 0.0 {
        return if x.is_sign_negative() { "-0" } else { "0" }.into();
    }
    let exp = x.abs().log10().floor() as i32;
    if !(-4..6).contains(&exp) {
        let s = format!("{x:.5e}");
        let (m, e) = s.split_once('e').unwrap();
        let m = m.trim_end_matches('0').trim_end_matches('.');
        let e: i32 = e.parse().unwrap();
        return format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs());
    }
    let s = format!("{:.*}", (5 - exp).max(0) as usize, x);
    if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s }
}

/// Python's `repr()` of a str (ASCII text: a .PRG line, a file name).
pub fn repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7F => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounding_like_python() {
        assert_eq!(round(2.675, 2), 2.67); // the binary value is below the tie
        assert_eq!(round(0.125, 2), 0.12); // an exact tie: to even
        assert_eq!(round(-0.0005, 3), -0.001);
        assert_eq!(round_int(2.5), 2);
        assert_eq!(round_int(3.5), 4);
        assert_eq!(fmod_floor(-1.0, 2.0 * std::f64::consts::PI), 2.0 * std::f64::consts::PI - 1.0);
        assert_eq!(fmt_g(100.0), "100");
        assert_eq!(fmt_g(-49.6), "-49.6");
        assert_eq!(fmt_g(0.15), "0.15");
        assert_eq!(fmt_g(-0.0), "-0");
    }
}
