import pytest

from shop.cart import Cart
from shop.catalog import demo_catalog
from shop.inventory import Inventory, OutOfStock
from shop.models import Customer
from shop.orders import OrderError, Status, place_order


def _setup(**qty):
    catalog = demo_catalog()
    inventory = Inventory({"PEN-01": 100, "NB-A5": 100, "MUG-W": 5, "BOOK-PY": 10})
    cart = Cart(catalog)
    for sku, n in qty.items():
        cart.add(sku.replace("_", "-"), n)
    return cart, inventory


def test_simple_order_total():
    cart, inv = _setup(MUG_W=1)
    order = place_order(cart, Customer("c1", "Ana"), inv)
    # 899 + 7% tax (63) + flat shipping 499
    assert order.total() == 899 + 63 + 499


def test_bulk_discount_at_threshold():
    # Exactly BULK_THRESHOLD (10) units must get the 10% bulk rate.
    cart, inv = _setup(NB_A5=10)
    order = place_order(cart, Customer("c2", "Ben"), inv)
    assert order.subtotal() == 4_250 - 425


def test_out_of_stock_reserves_nothing():
    cart, inv = _setup(PEN_01=2, MUG_W=6)
    with pytest.raises(OutOfStock):
        place_order(cart, Customer("c3", "Cy"), inv)
    assert inv.available("PEN-01") == 100


def test_refund_flow():
    cart, inv = _setup(BOOK_PY=2)
    order = place_order(cart, Customer("c4", "Di", region="EU"), inv)
    order.pay()
    order.refund(order.total())
    assert order.status is Status.REFUNDED
    with pytest.raises(OrderError):
        order.refund(1)
