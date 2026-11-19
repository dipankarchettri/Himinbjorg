"""Budget pre-flight and the degradation ladder.

Enforcement belongs to the gateway and the 402 is the truth. This module
is the well-behaved-client half: check headroom cheaply, downgrade rather
than die, and explain clearly when stopping.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import trace
from .router import Tier

QUOTA_PATH = "/api/governance/virtual-keys/quota"


@dataclass
class Quota:
    max_limit: float = 0.0
    current_usage: float = 0.0
    reset_duration: str = ""
    last_reset: str = ""
    available: bool = False
    # Budgets on single providers inside the key: provider -> (used, limit).
    # A provider over its budget is dropped from routing and the gateway's
    # fallback chain moves on, so no 402 reaches the agent; this is how the
    # agent can still say why the provider changed.
    providers: dict = field(default_factory=dict)

    @property
    def headroom(self) -> float:
        return max(0.0, self.max_limit - self.current_usage)

    def describe(self) -> str:
        if not self.available:
            return "budget: unknown (quota endpoint unavailable)"
        return (f"budget: ${self.current_usage:.4f} of ${self.max_limit:.2f} used, "
                f"${self.headroom:.4f} left, resets every {self.reset_duration}")


_cache: tuple[float, Quota] | None = None


def poll(settings, virtual_key: str, ttl_seconds: int = 20) -> Quota:
    """Cached. The pre-flight check must not become a per-call cost."""
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < ttl_seconds:
        return _cache[1]

    url = settings.bifrost_base_url.rstrip("/") + QUOTA_PATH
    req = urllib.request.Request(url, headers={"x-bf-vk": virtual_key})
    quota = Quota()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        budgets = data.get("budgets") or []
        providers = {
            str(pc.get("provider")): (float(b.get("current_usage") or 0),
                                      float(b.get("max_limit") or 0))
            for pc in data.get("provider_configs") or []
            for b in (pc.get("budgets") or [])[:1]}
        if budgets:
            b = budgets[0]
            quota = Quota(
                max_limit=float(b.get("max_limit") or 0),
                current_usage=float(b.get("current_usage") or 0),
                reset_duration=str(b.get("reset_duration") or ""),
                last_reset=str(b.get("last_reset") or ""),
                available=True,
                providers=providers,
            )
        else:
            # No budget attached is a valid configuration (the confidential
            # key has none, because local inference has no marginal cost).
            quota = Quota(available=True, max_limit=0.0, providers=providers)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError):
        quota = Quota(available=False)

    _cache = (now, quota)
    return quota


def provider_exhausted(quota: Quota, provider: str | None) -> tuple[float, float] | None:
    """(used, limit) when this provider's own budget is used up, else None."""
    used, limit = quota.providers.get(provider or "", (0.0, 0.0))
    return (used, limit) if limit > 0 and used >= limit else None


def should_preempt(quota: Quota, tier: Tier, estimate: float = 0.01) -> bool:
    """Downgrade before issuing a call that will be refused.

    An optimisation and a courtesy, never the enforcement.
    """
    if not quota.available or quota.max_limit <= 0:
        return False
    if tier is Tier.T0_LOCAL:
        return False      # local costs nothing; never pre-empt it
    return quota.headroom < estimate


def degrade(tier: Tier) -> Tier | None:
    """T2 -> T1 -> T0 -> stop. T0 is free, so a budget-exhausted session
    can continue at reduced capability indefinitely."""
    return {Tier.T2_CAPABLE: Tier.T1_CHEAP, Tier.T1_CHEAP: Tier.T0_LOCAL}.get(tier)


def report_exhausted(quota: Quota) -> str:
    trace.emit("budget_event", {"event": "exhausted", **quota.__dict__})
    lines = [
        "",
        "  Budget exhausted at the gateway (HTTP 402).",
        f"  {quota.describe()}",
        "  The request was refused before it reached any provider.",
    ]
    return "\n".join(lines)
