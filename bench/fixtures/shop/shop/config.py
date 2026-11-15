"""Static configuration. Values are cents or fractions, never floats of money."""

DEFAULT_REGION = "US"

TAX_RATES = {
    "US": 0.07,
    "EU": 0.20,
    "UK": 0.20,
    "IN": 0.18,
}

# Bulk pricing: orders of at least this many units of one product get the
# bulk rate on that line.
BULK_THRESHOLD = 10
BULK_RATE = 0.10

LOYALTY_RATE = 0.05
LOYALTY_MIN_ORDERS = 5

FREE_SHIPPING_CENTS = 5_000
FLAT_SHIPPING_CENTS = 499
