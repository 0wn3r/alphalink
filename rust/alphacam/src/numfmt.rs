//! Number formatting as ALPHA-T writes it (DESIGN.md C6).
//!
//! Values always carry a decimal point and lose trailing zeros: 100 -> "100.", 0.30 -> "0.3", -0.8 -> "-0.8".
//! mm uses at most 3 decimals, inch at most 4.

use rust_decimal::{Decimal, RoundingStrategy};
use std::str::FromStr;

/// Format a coordinate/feed the way ALPHA-T does. Rounding is half-up on the shortest decimal form of the value
/// (Python's `Decimal(repr(x))` with ROUND_HALF_UP).
pub fn format_number(value: f64, decimals: u32) -> String {
    // Rust's Display gives the shortest round-trip digits, like Python's repr (without its exponent form)
    let d = Decimal::from_str(&value.to_string())
        .unwrap_or_default()
        .round_dp_with_strategy(decimals, RoundingStrategy::MidpointAwayFromZero);
    if d.is_zero() {
        return "0.".into();
    }
    let text = d.normalize().to_string();
    if text.contains('.') { text } else { text + "." }
}

#[cfg(test)]
mod tests {
    use super::format_number;

    #[test]
    fn like_alpha_t() {
        assert_eq!(format_number(100.0, 3), "100.");
        assert_eq!(format_number(0.30, 3), "0.3");
        assert_eq!(format_number(-0.8, 3), "-0.8");
        assert_eq!(format_number(-0.0001, 3), "0.");
        assert_eq!(format_number(0.0005, 3), "0.001");
        assert_eq!(format_number(1.00005, 4), "1.0001");
        assert_eq!(format_number(-49.6, 3), "-49.6");
        assert_eq!(format_number(1e-12, 3), "0.");
    }
}
