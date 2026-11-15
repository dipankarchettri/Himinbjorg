"""Per-line pricing."""
from __future__ import annotations

from .discounts import bulk_discount
from .models import LineItem


def line_total(item: LineItem) -> int:
    """Price of one line in cents, after the bulk discount."""
    gross = item.product.unit_cents * item.quantity
    return gross - bulk_discount(item.quantity, gross)


def taxable_total(items: list[LineItem]) -> int:
    return sum(line_total(i) for i in items if i.product.taxable)
