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

/// Python's `sum()` of floats (3.12 and later): Neumaier's compensated sum, the first value added to the int 0.
pub fn sum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut it = values.into_iter();
    let Some(first) = it.next() else { return 0.0 };
    let (mut total, mut c) = (0.0 + first, 0.0f64);
    for x in it {
        let t = total + x;
        if total.abs() >= x.abs() {
            c += (total - t) + x;
        } else {
            c += (x - t) + total;
        }
        total = t;
    }
    // CPython keeps the sign of a negative zero and does not turn an infinite sum into a NaN
    if c != 0.0 && c.is_finite() { total + c } else { total }
}

const HASH_BITS: u32 = 61;
const HASH_MODULUS: u64 = (1 << HASH_BITS) - 1;

/// CPython's `hash(x)` of a finite float (64-bit builds): its value modulo 2**61 - 1, as the unsigned word the set
/// table uses.
pub fn hash_float(v: f64) -> u64 {
    let (mut m, mut e) = libm::frexp(v);
    let negative = m < 0.0;
    m = m.abs();
    let mut x: u64 = 0;
    while m != 0.0 {
        x = ((x << 28) & HASH_MODULUS) | x >> (HASH_BITS - 28);
        m *= 268435456.0; // 2**28
        e -= 28;
        let y = m as u64; // the integer part
        m -= y as f64;
        x += y;
        if x >= HASH_MODULUS {
            x -= HASH_MODULUS;
        }
    }
    let bits = HASH_BITS as i32;
    let e = if e >= 0 { e % bits } else { bits - 1 - ((-1 - e) % bits) } as u32;
    x = ((x << e) & HASH_MODULUS) | x >> (HASH_BITS - e);
    if negative {
        x = x.wrapping_neg();
    }
    if x == u64::MAX { u64::MAX - 1 } else { x }
}

/// CPython's `hash((a, b))` of a pair of floats (the xxHash-based tuple hash).
pub fn hash_pair(a: f64, b: f64) -> u64 {
    const P1: u64 = 11400714785074694791;
    const P2: u64 = 14029467366897019727;
    const P5: u64 = 2870177450012600261;
    let mut acc = P5;
    for lane in [hash_float(a), hash_float(b)] {
        acc = acc.wrapping_add(lane.wrapping_mul(P2));
        acc = acc.rotate_left(31);
        acc = acc.wrapping_mul(P1);
    }
    acc = acc.wrapping_add(2 ^ (P5 ^ 3527539));
    if acc == u64::MAX { 1546275796 } else { acc }
}

/// The order Python iterates the set literal `{first, second}`: one value when they are equal, else by their slots
/// in the 8-entry table (the first one at `hash & 7`, the second one probing on from its own slot).
pub fn set_of_two<T: PartialEq>(first: T, first_hash: u64, second: T, second_hash: u64) -> Vec<T> {
    if first == second {
        return vec![first];
    }
    const MASK: u64 = 7;
    let taken = first_hash & MASK;
    let (mut slot, mut perturb) = (second_hash & MASK, second_hash);
    while slot == taken {
        perturb >>= 5;
        slot = (slot * 5 + 1 + perturb) & MASK;
    }
    if slot < taken { vec![second, first] } else { vec![first, second] }
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
    fn rounding_and_hypot_like_python() {
        assert_eq!(round(2.675, 2), 2.67); // the binary value is below the tie
        assert_eq!(round(0.125, 2), 0.12); // an exact tie: to even
        assert_eq!(round(-0.0005, 3), -0.001);
        assert_eq!(round_int(2.5), 2);
        assert_eq!(round_int(3.5), 4);
        assert_eq!(hypot(3.0, 4.0), 5.0);
        // a case where older glibc's hypot differed from Python's in the last bit (newer glibc rounds it right too)
        assert_eq!(hypot(-22.409529581998356, 22.335855757150796), 31.6398082878311);
        assert_eq!(fmod_floor(-1.0, 2.0 * std::f64::consts::PI), 2.0 * std::f64::consts::PI - 1.0);
        assert_eq!(fmt_g(100.0), "100");
        assert_eq!(fmt_g(-49.6), "-49.6");
        assert_eq!(fmt_g(0.15), "0.15");
        assert_eq!(fmt_g(-0.0), "-0");
    }

    #[test]
    fn sums_and_hashes_like_python() {
        assert_eq!(sum([0.1, 0.2, 0.3]), 0.6); // plain left-to-right addition gives 0.6000000000000001
        assert_eq!(sum([1e100, 1.0, -1e100]), 1.0);
        assert_eq!(sum([]), 0.0);
        // hash(1.5), hash(-2.25), hash(1e-300), hash((1.5, -2.25)) in CPython 3.13
        assert_eq!(hash_float(1.5) as i64, 1152921504606846977);
        assert_eq!(hash_float(-2.25) as i64, -576460752303423490);
        assert_eq!(hash_float(1e-300) as i64, 482449582752280463);
        assert_eq!(hash_float(-1.0) as i64, -2);
        assert_eq!(hash_pair(1.5, -2.25) as i64, -6151272265558060089);
    }
}
