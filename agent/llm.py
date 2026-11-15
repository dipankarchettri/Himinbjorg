"""The only module that talks to Bifrost.

Verified against a live gateway on 2026-11-14:
  - `fallbacks` reaches Bifrost through the OpenAI SDK's `extra_body`.
  - `with_raw_response` exposes both the x-bifrost-* headers and the body's
    `extra_fields` (via `model_extra`), so routing and cache attribution
    are available without a second HTTP call.

Every request passes the privacy egress assertion before leaving.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import telemetry, trace
from .security.privacy import PrivacyMode


_TOOLCALL_TEXT = re.compile(
    r'\{\s*"name"\s*:\s*"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"\s*,\s*'
    r'"(?:arguments|parameters)"\s*:\s*(?P<args>\{.*?\})\s*\}', re.S)


def recover_text_tool_calls(text: str) -> list[dict]:
    """Some local models emit tool calls as plain text instead of in the
    tool_calls field, even when they advertise tool support.

    Verified on 2026-11-14: qwen2.5-coder:7b does this through Ollama's
    OpenAI-compatible endpoint, while qwen2.5:7b-instruct and qwen3:1.7b
    return proper tool_calls. Recovering the text form keeps the agent
    working across local models rather than silently treating a tool call
    as a final answer.
    """
    if not text or '"name"' not in text:
        return []
    out = []
    for i, m in enumerate(_TOOLCALL_TEXT.finditer(text)):
        try:
            json.loads(m.group("args"))
        except json.JSONDecodeError:
            continue
        out.append({
            "id": f"recovered_{i}",
            "type": "function",
            "function": {"name": m.group("name"), "arguments": m.group("args")},
        })
    return out


class GatewayError(RuntimeError):
    """Base for mapped gateway failures."""


class BudgetExceeded(GatewayError):
    """HTTP 402. The gateway refused before reaching a provider."""


class ProviderPaymentRequired(GatewayError):
    """HTTP 402 from a provider (for example, its account has no credit).

    Not the user's budget. Measured 2026-11-15: OpenRouter returned 402
    "requires more credits" through the gateway; before this class, the
    agent would have reported it as the user's budget being exhausted.
    """


class RateLimited(GatewayError):
    """HTTP 429."""


class ModelNotAllowed(GatewayError):
    """HTTP 403: model blocked for this virtual key, or key expired."""


class AuthFailed(GatewayError):
    """HTTP 401."""


class AllProvidersFailed(GatewayError):
    """Primary and every fallback failed. Carries the primary's error."""


@dataclass
class CallSpec:
    messages: list[dict]
    model: str
    fallbacks: list[str] = field(default_factory=list)
    tools: list[dict] | None = None
    cache_key: str | None = None
    # 'direct' = exact-hash only. Conversation calls must never use
    # semantic matching: a near-identical prompt with different file
    # contents would be served someone else's answer.
    cache_type: str = "direct"
    max_tokens: int = 1200
    temperature: float = 0.0


@dataclass
class CallResult:
    text: str
    tool_calls: list[dict]
    usage: telemetry.Usage
    routing: telemetry.RoutingInfo
    cache: telemetry.CacheInfo
    request_id: str
    latency_s: float
    finish_reason: str = ""


_EPHEMERAL = {"type": "ephemeral"}


def mark_prompt_cache(messages: list[dict]) -> list[dict]:
    """Provider prompt-cache breakpoints, as Bifrost message-level markers.

    Two breakpoints: the system message, which caches tools + system (the
    providers place tool schemas before the system prompt in the prefix),
    and the last message, so each turn's prefix is written once and read
    on the next turn. Anthropic bills a read at a tenth of the input
    price; OpenAI caches stable prefixes on its own and ignores the
    marker; Ollama ignores it (verified 2026-11-15, HTTP 200). Inputs are
    copied, never mutated: the ledger stays provider-neutral.
    """
    if not messages:
        return messages
    out = [dict(m) for m in messages]
    if out[0].get("role") == "system":
        out[0]["cache_control"] = _EPHEMERAL
    if len(out) > 1:
        out[-1]["cache_control"] = _EPHEMERAL
    return out


class LLMClient:
    def __init__(self, settings, privacy: PrivacyMode):
        from openai import OpenAI

        self.settings = settings
        self.privacy = privacy
        self.vk = privacy.virtual_key
        self._client = OpenAI(
            base_url=f"{settings.bifrost_base_url}/openai",
            api_key=self.vk,
            default_headers={"x-bf-vk": self.vk},
            timeout=300.0,
            max_retries=1,
        )

    def call(self, spec: CallSpec) -> CallResult:
        # Last chokepoint. Raises and aborts rather than returning False.
        self.privacy.assert_egress(spec.model, spec.fallbacks, self.vk)

        extra_body: dict[str, Any] = {}
        if spec.fallbacks:
            extra_body["fallbacks"] = spec.fallbacks

        extra_headers: dict[str, str] = {}
        # Caching is opt-in per request: without x-bf-cache-key the gateway
        # bypasses the cache silently. It is never sent in confidential
        # mode, because a semantic lookup would embed the request through a
        # cloud embedding provider.
        if spec.cache_key and self.privacy.cache_enabled and self.settings.levers.cache:
            extra_headers["x-bf-cache-key"] = spec.cache_key
            extra_headers["x-bf-cache-type"] = spec.cache_type

        messages = spec.messages
        if self.settings.levers.cache:
            messages = mark_prompt_cache(messages)

        kwargs: dict[str, Any] = {
            "model": spec.model,
            "messages": messages,
            "max_tokens": spec.max_tokens,
            "temperature": spec.temperature,
        }
        if spec.tools:
            kwargs["tools"] = spec.tools
            kwargs["tool_choice"] = "auto"
        if extra_body:
            kwargs["extra_body"] = extra_body
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        started = time.time()
        try:
            raw = self._client.chat.completions.with_raw_response.create(**kwargs)
        except Exception as exc:                    # noqa: BLE001
            self._raise_mapped(exc, spec)
            raise
        elapsed = time.time() - started

        parsed = raw.parse()
        body = getattr(parsed, "model_extra", None) or {}
        routing, cache, request_id = telemetry.extract(body, raw.headers)

        choice = parsed.choices[0] if parsed.choices else None
        message = getattr(choice, "message", None)
        text = (getattr(message, "content", None) or "") if message else ""
        tool_calls = []
        for tc in (getattr(message, "tool_calls", None) or []) if message else []:
            tool_calls.append({
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            })

        if not tool_calls and text:
            recovered = recover_text_tool_calls(text)
            if recovered:
                tool_calls = recovered
                text = ""
                trace.emit("tool_calls_recovered", {
                    "model": spec.model, "count": len(recovered),
                    "names": [t["function"]["name"] for t in recovered]})

        usage = telemetry.Usage.from_payload(
            parsed.usage.model_dump() if getattr(parsed, "usage", None) else None)
        if usage.total_tokens == 0:
            # Measured: a successful response arrived without a usage block.
            # Estimate (chars / 4) rather than record a free call; flagged
            # in the trace as estimated.
            sent = sum(len(str(m.get("content") or "")) for m in messages)
            sent += len(json.dumps(spec.tools)) if spec.tools else 0
            got = len(text) + sum(len(t["function"]["arguments"] or "") for t in tool_calls)
            usage = telemetry.Usage(max(1, sent // 4), max(1, got // 4), max(2, (sent + got) // 4))
            usage_estimated = True
        else:
            usage_estimated = False

        result = CallResult(
            text=text, tool_calls=tool_calls, usage=usage, routing=routing,
            cache=cache, request_id=request_id, latency_s=elapsed,
            finish_reason=getattr(choice, "finish_reason", "") or "",
        )
        trace.emit("llm_call", {
            "model_requested": spec.model,
            "served_by": result.routing.describe(),
            "is_fallback": result.routing.is_fallback,
            "primary": f"{routing.primary_provider}/{routing.primary_model}"
                       if routing.is_fallback else None,
            "cache_hit": result.cache.hit,
            "cache_hit_type": result.cache.hit_type,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cached_read_tokens": usage.cached_read_tokens,
            "cached_write_tokens": usage.cached_write_tokens,
            "latency_s": round(elapsed, 3),
            "request_id": request_id,
            "telemetry_source": result.routing.source,
            "usage_estimated": usage_estimated,
            "finish_reason": getattr(choice, "finish_reason", "") or "",
            "tool_calls": [t["function"]["name"] for t in tool_calls],
        })
        return result

    @staticmethod
    def _is_budget_refusal(exc: Exception) -> bool:
        """Was this 402 the gateway's budget, or a provider's payment error?

        Both arrive as 402 with is_bifrost_error false (verified
        2026-11-15), so that flag cannot tell them apart. The governance
        refusal carries type "budget_exceeded" and an error_type naming
        the budget; a provider's own 402 carries neither. The SDK keeps
        only the inner "error" object, so the full body is read from the
        response. Unreadable body: assume the budget, as before.
        """
        try:
            full = exc.response.json()          # type: ignore[attr-defined]
        except Exception:                       # noqa: BLE001
            return True
        if not isinstance(full, dict):
            return True
        kind = str(full.get("type") or "")
        etype = str((full.get("extra_fields") or {}).get("error_type") or "")
        return "budget" in kind or "budget" in etype

    @staticmethod
    def _raise_mapped(exc: Exception, spec: CallSpec) -> None:
        """Branch on status code only.

        The exact error.type string for budget failures is not a stable
        interface; the status code is.
        """
        status = getattr(exc, "status_code", None)
        detail = str(exc)[:400]
        if status == 402:
            if LLMClient._is_budget_refusal(exc):
                raise BudgetExceeded(detail) from exc
            raise ProviderPaymentRequired(detail) from exc
        if status == 429:
            raise RateLimited(detail) from exc
        if status == 403:
            raise ModelNotAllowed(detail) from exc
        if status == 401:
            raise AuthFailed(detail) from exc
        if spec.fallbacks:
            # Bifrost returns the PRIMARY's error when every fallback also
            # fails, so the message must not blame the last model tried.
            raise AllProvidersFailed(
                f"primary {spec.model} reported: {detail}. "
                f"Fallbacks attempted: {', '.join(spec.fallbacks)}") from exc
        raise GatewayError(detail) from exc
