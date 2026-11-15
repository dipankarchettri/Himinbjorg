from shop.discounts import bulk_discount, coupon_discount, loyalty_discount


def test_no_bulk_discount_below_threshold():
    assert bulk_discount(3, 3_000) == 0


def test_bulk_discount_well_above_threshold():
    assert bulk_discount(25, 10_000) == 1_000


def test_loyalty():
    assert loyalty_discount(4, 10_000) == 0
    assert loyalty_discount(5, 10_000) == 500


def test_coupons():
    assert coupon_discount("welcome5", 10_000) == 500
    assert coupon_discount("WELCOME5", 300) == 300
    assert coupon_discount("SPRING10", 10_000) == 1_000
    assert coupon_discount("NOPE", 10_000) == 0
