from shop.cart import Cart
from shop.catalog import demo_catalog


def test_add_and_subtotal():
    cart = Cart(demo_catalog())
    cart.add("PEN-01", 2)
    cart.add("MUG-W")
    assert cart.subtotal() == 2 * 150 + 899


def test_remove_partial():
    cart = Cart(demo_catalog())
    cart.add("NB-A5", 3)
    cart.remove("NB-A5", 1)
    assert cart.items["NB-A5"].quantity == 2
