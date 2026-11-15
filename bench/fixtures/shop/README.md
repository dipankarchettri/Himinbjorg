# shop

A small order-processing library: catalog, inventory, cart pricing with
discounts and tax, orders with refunds, and sales reports.

Run the tests with `python -m pytest -q`.

Money is always handled as integer cents. Convert at the edges only, with
`shop.money.to_cents` and `shop.money.format_money`.
