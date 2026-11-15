"""Shopping cart."""
from __future__ import annotations

from .catalog import Catalog
from .models import LineItem
from .pricing import line_total


class Cart:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.items: dict[str, LineItem] = {}

    def add(self, sku: str, quantity: int = 1) -> None:
        product = self.catalog.get(sku)
        if sku in self.items:
            self.items[sku].quantity += quantity
        else:
            self.items[sku] = LineItem(product, quantity)

    def remove(self, sku: str, quantity: int | None = None) -> None:
        if sku not in self.items:
            return
        if quantity is None or quantity >= self.items[sku].quantity:
            del self.items[sku]
        else:
            self.items[sku].quantity -= quantity

    def lines(self) -> list[LineItem]:
        return list(self.items.values())

    def subtotal(self) -> int:
        return sum(line_total(i) for i in self.items.values())

    def is_empty(self) -> bool:
        return not self.items
