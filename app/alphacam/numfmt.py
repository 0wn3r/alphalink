"""Number formatting as ALPHA-T writes it (DESIGN.md C6).

Values always carry a decimal point and lose trailing zeros: 100 -> "100.", 0.30 -> "0.3", -0.8 -> "-0.8".
mm uses at most 3 decimals, inch at most 4."""

from decimal import ROUND_HALF_UP, Decimal

DECIMALS = {"mm": 3, "inch": 4}


def format_number(value: float, decimals: int) -> str:
    """Format a coordinate/feed the way ALPHA-T does. Rounding is half-up on the decimal value
    (assumed; not yet confirmed against a sample with a tie)."""
    d = Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if d == 0:
        return "0."
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0")
    else:
        text += "."
    return text

