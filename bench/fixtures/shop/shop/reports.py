"""Sales reporting."""
from __future__ import annotations

from collections import Counter, defaultdict

from .money import format_money
from .orders import Order, Status
from .pricing import line_total

COUNTED = (Status.PAID, Status.SHIPPED, Status.DELIVERED)


def revenue(orders: list[Order]) -> int:
    return sum(o.total() - o.refunded() for o in orders if o.status in COUNTED)


def units_by_sku(orders: list[Order]) -> Counter:
    c: Counter = Counter()
    for o in orders:
        if o.status in COUNTED:
            for line in o.lines:
                c[line.product.sku] += line.quantity
    return c


def sales_by_sku(orders: list[Order]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for o in orders:
        if o.status in COUNTED:
            for line in o.lines:
                out[line.product.sku] += line_total(line)
    return dict(out)


def top_products(orders: list[Order], n: int = 3) -> list[tuple[str, int]]:
    return sorted(sales_by_sku(orders).items(), key=lambda kv: kv[1], reverse=True)[:n]


def render(orders: list[Order]) -> str:
    lines = [f"Revenue: {format_money(revenue(orders))}"]
    for sku, cents in top_products(orders):
        lines.append(f"  {sku:<10} {format_money(cents)}")
    return "\n".join(lines)
