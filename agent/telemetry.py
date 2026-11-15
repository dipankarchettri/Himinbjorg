"""Extract routing, cache and usage facts from a Bifrost response.

Reads in three tiers and records which one produced the data:

  1. response body  extra_fields.routing_info / cache_debug
  2. response headers  x-bifrost-*
  3. GET /api/logs/{x-bifrost-request-id}

Verified against Bifrost on 2026-11-14: the /openai drop-in route returns
BOTH extra_fields in the body and x-bifrost-* headers, contrary to the
documentation, which says drop-in routes have no extra_fields. Tier 3 is
kept because it is the only independent record, and the evidence harness
uses it unconditionally: a number the agent prints about itself is not
evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Confirmed present in Access-Control-Expose-Headers on a live gateway.
H_REQUEST_ID = "x-bifrost-request-id"
H_PROVIDER = "x-bifrost-provider"
H_RESOLVED_MODEL = "x-bifrost-resolved-model"
H_ORIGINAL_MODEL = "x-bifrost-original-model"
H_IS_FALLBACK = "x-bifrost-routing-info-is-fallback"
H_PRIMARY_PROVIDER = "x-bifrost-routing-info-primary-provider"
H_PRIMARY_MODEL = "x-bifrost-routing-info-primary-model"
H_FALLBACK_INDEX = "x-bifrost-fallback-index"
H_CACHE_HIT = "x-bifrost-cache-hit"
H_CACHE_HIT_TYPE = "x-bifrost-cache-hit-type"
H_CACHE_SIMILARITY = "x-bifrost-cache-similarity"
H_CACHE_THRESHOLD = "x-bifrost-cache-threshold"
H_ERROR_TYPE = "x-bifrost-error-type"
H_LATENCY = "x-bifrost-latency-ms"


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


@dataclass
class RoutingInfo:
    provider: str = ""
    model: str = ""
    is_fallback: bool = False
    primary_provider: str | None = None
    primary_model: str | None = None
    source: str = "none"

    def describe(self) -> str:
        return f"{self.provider}/{self.model}" if self.provider else (self.model or "unknown")


@dataclass
class CacheInfo:
    hit: bool = False
    hit_type: str | None = None
    similarity: float | None = None
    threshold: float | None = None
    source: str = "none"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Provider-side prompt cache, included in prompt_tokens. Bifrost reports
    # them as prompt_tokens_details.cached_read_tokens / cached_write_tokens
    # (OpenAI's cached_tokens is mapped to the read side).
    cached_read_tokens: int = 0
    cached_write_tokens: int = 0

    @staticmethod
    def from_payload(d: dict | None) -> "Usage":
        d = d or {}
        details = d.get("prompt_tokens_details") or {}
        return Usage(
            int(d.get("prompt_tokens") or 0),
            int(d.get("completion_tokens") or 0),
            int(d.get("total_tokens") or 0),
            int(details.get("cached_read_tokens") or details.get("cached_tokens") or 0),
            int(details.get("cached_write_tokens") or 0),
        )


def _lower_headers(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(k).lower(): str(v) for k, v in items}


def extract(body: dict | None, headers: Any) -> tuple[RoutingInfo, CacheInfo, str]:
    h = _lower_headers(headers)
    request_id = h.get(H_REQUEST_ID, "")
    body = body or {}
    ef = body.get("extra_fields") or {}

    routing = RoutingInfo()
    ri = ef.get("routing_info") or {}
    if ri:
        routing = RoutingInfo(
            provider=ri.get("provider", "") or ef.get("provider", ""),
            model=ri.get("model", "") or ef.get("resolved_model_used", ""),
            is_fallback=bool(ri.get("is_fallback")),
            primary_provider=ri.get("primary_provider"),
            primary_model=ri.get("primary_model"),
            source="body",
        )
    if not routing.provider and h.get(H_PROVIDER):
        routing = RoutingInfo(
            provider=h.get(H_PROVIDER, ""),
            model=h.get(H_RESOLVED_MODEL, "") or h.get(H_ORIGINAL_MODEL, ""),
            is_fallback=_truthy(h.get(H_IS_FALLBACK, "")) or bool(h.get(H_FALLBACK_INDEX)),
            primary_provider=h.get(H_PRIMARY_PROVIDER),
            primary_model=h.get(H_PRIMARY_MODEL),
            source="header",
        )

    cache = CacheInfo()
    cd = ef.get("cache_debug")
    if isinstance(cd, dict) and cd:
        cache = CacheInfo(
            hit=bool(cd.get("cache_hit")),
            hit_type=cd.get("hit_type"),
            similarity=cd.get("similarity"),
            threshold=cd.get("threshold"),
            source="body",
        )
    elif H_CACHE_HIT in h:
        cache = CacheInfo(
            hit=_truthy(h[H_CACHE_HIT]),
            hit_type=h.get(H_CACHE_HIT_TYPE),
            similarity=_float_or_none(h.get(H_CACHE_SIMILARITY)),
            threshold=_float_or_none(h.get(H_CACHE_THRESHOLD)),
            source="header",
        )
    return routing, cache, request_id


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_log(request_id: str, base_url: str, timeout: float = 10.0,
              setup_token: str = "") -> dict:
    """Tier 3. The independent record, used by the evidence harness.

    Needs the /api surface unlocked (setup_token configured), otherwise it
    returns an error dict rather than raising: telemetry must never be able
    to break an agent run.
    """
    import json
    import urllib.error
    import urllib.request

    if not request_id:
        return {"error": "no request id"}
    url = f"{base_url.rstrip('/')}/api/logs/{request_id}"
    headers = {"X-Bifrost-Setup-Token": setup_token} if setup_token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "detail": exc.reason, "url": url}
    except Exception as exc:                      # noqa: BLE001
        return {"error": str(exc), "url": url}


def session_cost(request_ids: list[str], base_url: str, setup_token: str,
                 settle_seconds: float = 2.0) -> tuple[float, int] | None:
    """Sum the gateway's own per-request cost records.

    Returns (dollars, requests_found), or None when the lookup is not
    configured. Logs are written asynchronously, hence the short settle.
    Cost is whatever the gateway priced: $0 for local models unless a
    pricing override says otherwise.
    """
    import time

    if not setup_token or not request_ids:
        return None
    total, found = 0.0, 0
    pending = list(dict.fromkeys(request_ids))
    # Logs land asynchronously; measured lag is 1-5 s. Retry what is missing.
    for wait in (settle_seconds, 3.0, 5.0):
        if not pending:
            break
        time.sleep(wait)
        still = []
        for rid in pending:
            log = fetch_log(rid, base_url, timeout=5.0, setup_token=setup_token)
            if "error" in log:
                still.append(rid)
            else:
                total += float(log.get("cost") or 0.0)
                found += 1
        pending = still
    return total, found
