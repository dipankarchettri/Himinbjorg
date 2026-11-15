from shop.money import apply_rate, format_money, to_cents


def test_to_cents():
    assert to_cents("12.34") == 1234
    assert to_cents(0.1) == 10


def test_apply_rate_rounds_half_up():
    assert apply_rate(125, 0.10) == 13


def test_format_money():
    assert format_money(123456) == "$1,234.56"
    assert format_money(-5) == "-$0.05"
