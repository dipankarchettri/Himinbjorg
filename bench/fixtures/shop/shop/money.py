"""Integer-cent money helpers."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: str | int | float) -> int:
    """Convert a decimal amount such as '12.34' into integer cents."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_rate(cents: int, rate: float) -> int:
    """Return `rate` of `cents`, rounded half up to a whole cent."""
    return int((Decimal(cents) * Decimal(str(rate))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(cents: int, symbol: str = "$") -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{symbol}{cents // 100:,}.{cents % 100:02d}"
