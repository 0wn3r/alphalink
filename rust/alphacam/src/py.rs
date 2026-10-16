//! Python's float semantics where they differ from Rust's, so the posts stay byte-identical to the Python app
//! (and through it to ALPHA-T). Everything else (`sqrt`, `atan2`, `sin`, `cos`, `tan`, `to_radians`) is the same
//! libm call in both languages.

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

/// Python's `math.hypot(a, b)` (CPython's `vector_norm`). It differs from libm's `hypot` in the last bit for about
/// 0.6% of inputs, enough to move a rounded coordinate now and then.
pub fn hypot(a: f64, b: f64) -> f64 {
    let (a, b) = (a.abs(), b.abs());
    let max = a.max(b);
    if max.is_infinite() {
        return max;
    }
    if a.is_nan() || b.is_nan() {
        return f64::NAN;
    }
    if max == 0.0 {
        return 0.0;
    }
    if max < f64::MIN_POSITIVE {
        // subnormal: scale up like CPython does
        return f64::MIN_POSITIVE * hypot(a / f64::MIN_POSITIVE, b / f64::MIN_POSITIVE);
    }
    let max_e = ((max.to_bits() >> 52) & 0x7ff) as i32 - 1022; // frexp's exponent
    let scale = 2f64.powi(-max_e);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    for x in [a, b] {
        let x = x * scale;
        let (hi, lo) = dl_mul(x, x);
        let (shi, slo) = dl_fast_sum(csum, hi);
        csum = shi;
        frac1 += lo;
        frac2 += slo;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = dl_mul(-h, h);
    let (shi, slo) = dl_fast_sum(csum, hi);
    csum = shi;
    frac1 += lo;
    frac2 += slo;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

fn dl_mul(x: f64, y: f64) -> (f64, f64) {
    let hi = x * y;
    (hi, x.mul_add(y, -hi))
}

fn dl_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let hi = a + b;
    (hi, (a - hi) + b)
}

/// Python's `x % m` for a positive modulus (the result takes the modulus' sign).
pub fn fmod_floor(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r != 0.0 && (r < 0.0) != (m < 0.0) { r + m } else { r }
}

/// Python's `f"{x:g}"` for messages (6 significant digits, no trailing zeros).
pub fn fmt_g(x: f64) -> String {
    if x == 0.0 {
        return "0".into();
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounding_and_hypot_like_python() {
        assert_eq!(round(2.675, 2), 2.67); // the binary value is below the tie
        assert_eq!(round(0.125, 2), 0.12); // an exact tie: to even
        assert_eq!(round(-0.0005, 3), -0.001);
        assert_eq!(round_int(2.5), 2);
        assert_eq!(round_int(3.5), 4);
        assert_eq!(hypot(3.0, 4.0), 5.0);
        // a case where libm's hypot differs from Python's in the last bit
        assert_eq!(hypot(-22.409529581998356, 22.335855757150796), 31.6398082878311);
        assert_ne!((-22.409529581998356f64).hypot(22.335855757150796), 31.6398082878311);
        assert_eq!(fmod_floor(-1.0, 2.0 * std::f64::consts::PI), 2.0 * std::f64::consts::PI - 1.0);
        assert_eq!(fmt_g(100.0), "100");
        assert_eq!(fmt_g(-49.6), "-49.6");
        assert_eq!(fmt_g(0.15), "0.15");
    }
}
