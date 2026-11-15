"""Stock levels and reservations."""
from __future__ import annotations


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self, levels: dict[str, int] | None = None) -> None:
        self._on_hand = dict(levels or {})
        self._reserved: dict[str, int] = {}

    def available(self, sku: str) -> int:
        return self._on_hand.get(sku, 0) - self._reserved.get(sku, 0)

    def reserve(self, sku: str, qty: int) -> None:
        if qty > self.available(sku):
            raise OutOfStock(f"{sku}: wanted {qty}, have {self.available(sku)}")
        self._reserved[sku] = self._reserved.get(sku, 0) + qty

    def release(self, sku: str, qty: int) -> None:
        self._reserved[sku] = max(0, self._reserved.get(sku, 0) - qty)

    def commit(self, sku: str, qty: int) -> None:
        self.release(sku, qty)
        self._on_hand[sku] = self._on_hand.get(sku, 0) - qty

    def restock(self, sku: str, qty: int) -> None:
        self._on_hand[sku] = self._on_hand.get(sku, 0) + qty
