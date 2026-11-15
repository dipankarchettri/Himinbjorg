"""Discount rules. Every function returns a discount in cents (>= 0)."""
from __future__ import annotations

from . import config
from .money import apply_rate

COUPONS = {
    "WELCOME5": ("fixed", 500),
    "SPRING10": ("rate", 0.10),
}


def bulk_discount(quantity: int, line_cents: int) -> int:
    """Bulk rate on a single line once the quantity reaches the threshold."""
    if quantity > config.BULK_THRESHOLD:
        return apply_rate(line_cents, config.BULK_RATE)
    return 0


def loyalty_discount(past_orders: int, subtotal_cents: int) -> int:
    if past_orders >= config.LOYALTY_MIN_ORDERS:
        return apply_rate(subtotal_cents, config.LOYALTY_RATE)
    return 0


def coupon_discount(code: str | None, subtotal_cents: int) -> int:
    if not code:
        return 0
    kind, value = COUPONS.get(code.upper(), (None, 0))
    if kind == "fixed":
        return min(int(value), subtotal_cents)
    if kind == "rate":
        return apply_rate(subtotal_cents, float(value))
    return 0
