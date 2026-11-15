"""Command-line demo: python -m shop.cli PEN-01=12 MUG-W=1 --region EU"""
from __future__ import annotations

import argparse

from .cart import Cart
from .catalog import demo_catalog
from .inventory import Inventory
from .models import Customer
from .money import format_money
from .orders import place_order


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("items", nargs="+", help="SKU=QTY")
    ap.add_argument("--region", default="US")
    ap.add_argument("--coupon")
    args = ap.parse_args(argv)

    catalog = demo_catalog()
    inventory = Inventory({p.sku: 100 for p in catalog.search("")})
    cart = Cart(catalog)
    for spec in args.items:
        sku, _, qty = spec.partition("=")
        cart.add(sku, int(qty or 1))
    order = place_order(cart, Customer("cli", "CLI user", args.region), inventory, args.coupon)
    for line in order.summary_lines():
        print(line)
    print("Total:", format_money(order.total()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
