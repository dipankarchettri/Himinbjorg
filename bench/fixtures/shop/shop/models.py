"""Plain data objects."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    unit_cents: int
    taxable: bool = True


@dataclass
class LineItem:
    product: Product
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass
class Customer:
    customer_id: str
    name: str
    region: str = "US"
    past_orders: int = 0
    tags: set[str] = field(default_factory=set)
