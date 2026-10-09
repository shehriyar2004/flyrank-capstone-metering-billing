"""Money math: exact integer arithmetic, no floats.

Prices are pinned rationals (numerator, denominator) in USD cents (see config).
Totals are accumulated as exact Fractions and rounded ONCE, half-up, to cents.
"""
from fractions import Fraction


def cost_cents(
    api_calls: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    pricing: dict[str, tuple[int, int]],
) -> int:
    """Total metered cost in integer USD cents.

    Reasoning tokens are billed as output tokens (they are not a free or
    separate category); cached input tokens use their own cheaper price.
    """
    total = Fraction(0, 1)

    def add(units: int, key: str) -> None:
        nonlocal total
        numer, denom = pricing[key]
        total += Fraction(units * numer, denom)

    add(api_calls, "api_call")
    add(input_tokens, "input_token")
    add(cached_input_tokens, "cached_input_token")
    # Reasoning tokens count as output tokens:
    add(output_tokens + reasoning_tokens, "output_token")

    # Round half up to whole cents.
    n, d = total.numerator, total.denominator
    return (2 * n + d) // (2 * d)


def format_cents(cents: int) -> str:
    """Render integer cents as a display string, e.g. 2000 -> '$20.00'."""
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}${c // 100}.{c % 100:02d}"
