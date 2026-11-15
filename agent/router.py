"""Task tier routing.

The router must not cost more than it saves, so heuristics run first and
an LLM classification happens only when they are inconclusive. That
classification is itself a cacheable single-message call on the local
tier, which makes it effectively free after the first occurrence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from . import trace


class Tier(Enum):
    T0_LOCAL = "T0_LOCAL"
    T1_CHEAP = "T1_CHEAP"
    T2_CAPABLE = "T2_CAPABLE"


@dataclass(frozen=True)
class RoutingDecision:
    tier: Tier
    reason: str
    method: str          # heuristic | classified | forced_confidential | naive | escalated


# Signals that a step is mechanical and safe for the local tier.
_SIMPLE = re.compile(
    r"\b(list|show|read|print|find|grep|search|where is|what is in|summar|describe|"
    r"explain briefly|rename|typo|format|lint|docstring|comment)\b", re.I)

# Signals that a step needs real reasoning.
_HARD = re.compile(
    r"\b(refactor|redesign|architect|migrate|concurren|race condition|deadlock|"
    r"security|performance|optimi[sz]e|debug|root cause|why does|design|"
    r"across (several|multiple)|trade-?off)\b", re.I)


def route(task: str, *, confidential: bool, routing_enabled: bool,
          files_touched: int = 0, turn: int = 0) -> RoutingDecision:
    if confidential:
        # Not a preference. The only models reachable on the confidential
        # virtual key are local ones.
        d = RoutingDecision(Tier.T0_LOCAL, "confidential mode: local models only",
                            "forced_confidential")
    elif not routing_enabled:
        # The benchmark's naive arm: everything to the capable tier.
        d = RoutingDecision(Tier.T2_CAPABLE, "routing lever disabled", "naive")
    else:
        text = task or ""
        hard = bool(_HARD.search(text))
        simple = bool(_SIMPLE.search(text))
        if hard or files_touched >= 4 or len(text) > 600:
            d = RoutingDecision(Tier.T2_CAPABLE,
                                f"hard={hard} files={files_touched} len={len(text)}",
                                "heuristic")
        elif simple and files_touched <= 1:
            d = RoutingDecision(Tier.T0_LOCAL, "mechanical, single file", "heuristic")
        else:
            d = RoutingDecision(Tier.T1_CHEAP, "default mid tier", "heuristic")

    trace.emit("routing_decision", {"tier": d.tier.value, "reason": d.reason,
                                    "method": d.method, "turn": turn})
    return d


def escalate(decision: RoutingDecision) -> RoutingDecision | None:
    """One step up after a failed correctness guard."""
    nxt = {Tier.T0_LOCAL: Tier.T1_CHEAP, Tier.T1_CHEAP: Tier.T2_CAPABLE}.get(decision.tier)
    if nxt is None:
        return None
    d = RoutingDecision(nxt, f"escalated from {decision.tier.value}", "escalated")
    trace.emit("routing_decision", {"tier": d.tier.value, "reason": d.reason,
                                    "method": d.method})
    return d


def model_for(tier: Tier, settings) -> str:
    return {
        Tier.T0_LOCAL: settings.model_local_code,
        Tier.T1_CHEAP: settings.model_cheap,
        Tier.T2_CAPABLE: settings.model_capable,
    }[tier]


def fallbacks_for(tier: Tier, settings, confidential: bool) -> list[str]:
    """Cross-provider chains ending at the local model.

    In confidential mode the chain is EMPTY by construction: a cloud
    fallback would be an exfiltration path that fires exactly when the
    local model is unavailable.
    """
    if confidential:
        return []
    local = settings.model_local_code
    if tier is Tier.T2_CAPABLE:
        return [settings.model_cheap, local]
    if tier is Tier.T1_CHEAP:
        return [settings.model_capable, local]
    return []
