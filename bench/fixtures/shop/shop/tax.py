"""Sales tax."""
from __future__ import annotations

from . import config
from .money import apply_rate


def rate_for(region: str) -> float:
    return config.TAX_RATES.get(region, config.TAX_RATES[config.DEFAULT_REGION])


def tax_cents(taxable_cents: int, region: str) -> int:
    return apply_rate(max(0, taxable_cents), rate_for(region))
