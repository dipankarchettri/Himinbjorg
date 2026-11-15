"""Orders: placement, totals, status transitions, refunds.

The total of an order is computed in this order, all in integer cents:

    subtotal   = sum of line totals (bulk discount already applied per line)
    discounts  = loyalty discount + coupon discount, on the subtotal
    taxable    = taxable lines' share of (subtotal - discounts)
    tax        = tax on taxable, at the customer's region rate
    shipping   = flat rate unless subtotal - discounts reaches the threshold
    total      = subtotal - discounts + tax + shipping
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from . import config
from .cart import Cart
from .discounts import coupon_discount, loyalty_discount
from .inventory import Inventory, OutOfStock
from .models import Customer, LineItem
from .pricing import line_total, taxable_total
from .tax import tax_cents

_ids = itertools.count(1001)


class OrderError(Exception):
    pass


class Status(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


ALLOWED = {
    Status.PENDING: {Status.PAID, Status.CANCELLED},
    Status.PAID: {Status.SHIPPED, Status.CANCELLED, Status.REFUNDED},
    Status.SHIPPED: {Status.DELIVERED, Status.REFUNDED},
    Status.DELIVERED: {Status.REFUNDED},
    Status.CANCELLED: set(),
    Status.REFUNDED: set(),
}


@dataclass
class Totals:
    subtotal: int
    discounts: int
    tax: int
    shipping: int

    @property
    def total(self) -> int:
        return self.subtotal - self.discounts + self.tax + self.shipping

    def as_dict(self) -> dict[str, int]:
        return {
            "subtotal": self.subtotal,
            "discounts": self.discounts,
            "tax": self.tax,
            "shipping": self.shipping,
            "total": self.total,
        }


@dataclass
class Refund:
    amount: int
    reason: str
    at: datetime


@dataclass
class Order:
    order_id: int
    customer: Customer
    lines: list[LineItem]
    coupon: str | None = None
    status: Status = Status.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    history: list[tuple[Status, datetime]] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)

    # ------------------------------------------------------------ totals

    def subtotal(self) -> int:
        return sum(line_total(i) for i in self.lines)

    def discounts(self) -> int:
        sub = self.subtotal()
        loyalty = loyalty_discount(self.customer.past_orders, sub)
        coupon = coupon_discount(self.coupon, sub - loyalty)
        return min(sub, loyalty + coupon)

    def taxable_after_discounts(self) -> int:
        sub = self.subtotal()
        if sub == 0:
            return 0
        share = taxable_total(self.lines) / sub
        return round((sub - self.discounts()) * share)

    def shipping(self) -> int:
        net = self.subtotal() - self.discounts()
        if net >= config.FREE_SHIPPING_CENTS:
            return 0
        return config.FLAT_SHIPPING_CENTS

    def totals(self) -> Totals:
        return Totals(
            subtotal=self.subtotal(),
            discounts=self.discounts(),
            tax=tax_cents(self.taxable_after_discounts(), self.customer.region),
            shipping=self.shipping(),
        )

    def total(self) -> int:
        return self.totals().total

    # ----------------------------------------------------------- status

    def transition(self, new: Status) -> None:
        if new not in ALLOWED[self.status]:
            raise OrderError(f"cannot go from {self.status.value} to {new.value}")
        self.history.append((self.status, datetime.now(timezone.utc)))
        self.status = new

    def pay(self) -> None:
        self.transition(Status.PAID)

    def ship(self) -> None:
        self.transition(Status.SHIPPED)

    def deliver(self) -> None:
        self.transition(Status.DELIVERED)

    def cancel(self, inventory: Inventory | None = None) -> None:
        self.transition(Status.CANCELLED)
        if inventory is not None:
            for line in self.lines:
                inventory.release(line.product.sku, line.quantity)

    # ---------------------------------------------------------- refunds

    def refunded(self) -> int:
        return sum(r.amount for r in self.refunds)

    def refundable(self) -> int:
        return max(0, self.total() - self.refunded())

    def refund(self, amount: int, reason: str = "") -> Refund:
        if self.status not in (Status.PAID, Status.SHIPPED, Status.DELIVERED, Status.REFUNDED):
            raise OrderError(f"cannot refund an order that is {self.status.value}")
        if amount <= 0:
            raise OrderError("refund amount must be positive")
        if amount > self.refundable():
            raise OrderError(f"refund {amount} exceeds refundable {self.refundable()}")
        r = Refund(amount, reason, datetime.now(timezone.utc))
        self.refunds.append(r)
        if self.refundable() == 0 and self.status is not Status.REFUNDED:
            self.transition(Status.REFUNDED)
        return r

    def refund_line(self, sku: str, reason: str = "") -> Refund:
        for line in self.lines:
            if line.product.sku == sku:
                share = line_total(line)
                if self.subtotal():
                    share = round(share * self.total() / self.subtotal())
                return self.refund(min(share, self.refundable()), reason or f"line {sku}")
        raise OrderError(f"order has no line for {sku}")

    # ---------------------------------------------------------- display

    def summary_lines(self) -> list[str]:
        out = [f"Order #{self.order_id} for {self.customer.name} [{self.status.value}]"]
        for line in self.lines:
            out.append(f"  {line.quantity:>3} x {line.product.name:<24} {line_total(line):>8}")
        for k, v in self.totals().as_dict().items():
            out.append(f"  {k:<10} {v:>8}")
        return out


# ------------------------------------------------------------- placement

def place_order(cart: Cart, customer: Customer, inventory: Inventory,
                coupon: str | None = None) -> Order:
    """Reserve stock for every line and create a pending order.

    Reservation is all-or-nothing: if any line is short, nothing stays
    reserved.
    """
    if cart.is_empty():
        raise OrderError("cart is empty")
    reserved: list[tuple[str, int]] = []
    try:
        for line in cart.lines():
            inventory.reserve(line.product.sku, line.quantity)
            reserved.append((line.product.sku, line.quantity))
    except OutOfStock:
        for sku, qty in reserved:
            inventory.release(sku, qty)
        raise
    lines = [LineItem(l.product, l.quantity) for l in cart.lines()]
    return Order(next(_ids), customer, lines, coupon=coupon)


def fulfil(order: Order, inventory: Inventory) -> None:
    """Pay-to-shipped path used by the warehouse job."""
    if order.status is Status.PENDING:
        order.pay()
    for line in order.lines:
        inventory.commit(line.product.sku, line.quantity)
    order.ship()


def reorder(previous: Order, cart: Cart) -> Cart:
    """Fill `cart` with the lines of a previous order."""
    for line in previous.lines:
        cart.add(line.product.sku, line.quantity)
    return cart


def open_orders(orders: list[Order]) -> list[Order]:
    return [o for o in orders if o.status in (Status.PENDING, Status.PAID, Status.SHIPPED)]


def orders_for(customer_id: str, orders: list[Order]) -> list[Order]:
    return [o for o in orders if o.customer.customer_id == customer_id]


def cancel_stale(orders: list[Order], inventory: Inventory, older_than_hours: int = 48,
                 now: datetime | None = None) -> list[Order]:
    """Cancel pending orders older than the cutoff and release their stock."""
    now = now or datetime.now(timezone.utc)
    cancelled = []
    for o in orders:
        age = (now - o.created_at).total_seconds() / 3600
        if o.status is Status.PENDING and age > older_than_hours:
            o.cancel(inventory)
            cancelled.append(o)
    return cancelled


def validate(order: Order) -> list[str]:
    """Return human-readable problems with an order; empty when valid."""
    problems = []
    if not order.lines:
        problems.append("no lines")
    for line in order.lines:
        if line.quantity <= 0:
            problems.append(f"{line.product.sku}: non-positive quantity")
        if line.product.unit_cents < 0:
            problems.append(f"{line.product.sku}: negative price")
    if order.coupon and order.coupon.upper() not in ("WELCOME5", "SPRING10"):
        problems.append(f"unknown coupon {order.coupon}")
    if order.customer.region not in config.TAX_RATES:
        problems.append(f"unknown region {order.customer.region}")
    return problems


def merge(a: Order, b: Order) -> Order:
    """Combine two pending orders for the same customer into a new one."""
    if a.customer.customer_id != b.customer.customer_id:
        raise OrderError("orders belong to different customers")
    if a.status is not Status.PENDING or b.status is not Status.PENDING:
        raise OrderError("only pending orders can be merged")
    qty: dict[str, int] = {}
    products = {}
    for line in a.lines + b.lines:
        qty[line.product.sku] = qty.get(line.product.sku, 0) + line.quantity
        products[line.product.sku] = line.product
    lines = [LineItem(products[s], q) for s, q in qty.items()]
    merged = Order(next(_ids), a.customer, lines, coupon=a.coupon or b.coupon)
    a.transition(Status.CANCELLED)
    b.transition(Status.CANCELLED)
    return merged


def split(order: Order, skus: set[str]) -> tuple[Order, Order]:
    """Split a pending order into (lines in skus, remaining lines)."""
    if order.status is not Status.PENDING:
        raise OrderError("only pending orders can be split")
    first = [l for l in order.lines if l.product.sku in skus]
    rest = [l for l in order.lines if l.product.sku not in skus]
    if not first or not rest:
        raise OrderError("split would leave an empty order")
    order.transition(Status.CANCELLED)
    return (Order(next(_ids), order.customer, first, coupon=order.coupon),
            Order(next(_ids), order.customer, rest))
