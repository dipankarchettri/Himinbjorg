"""In-memory product catalog."""
from __future__ import annotations

from .models import Product


class Catalog:
    def __init__(self, products: list[Product] | None = None) -> None:
        self._by_sku: dict[str, Product] = {}
        for p in products or []:
            self.add(p)

    def add(self, product: Product) -> None:
        if product.sku in self._by_sku:
            raise KeyError(f"duplicate sku {product.sku}")
        self._by_sku[product.sku] = product

    def get(self, sku: str) -> Product:
        try:
            return self._by_sku[sku]
        except KeyError:
            raise KeyError(f"unknown sku {sku}") from None

    def search(self, text: str) -> list[Product]:
        text = text.lower()
        return [p for p in self._by_sku.values() if text in p.name.lower()]

    def __len__(self) -> int:
        return len(self._by_sku)


def demo_catalog() -> Catalog:
    return Catalog([
        Product("PEN-01", "Ballpoint pen", 150),
        Product("NB-A5", "A5 notebook", 425),
        Product("MUG-W", "White mug", 899),
        Product("BOOK-PY", "Python handbook", 3_999, taxable=False),
    ])
