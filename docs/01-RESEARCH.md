# 01 - Verified Bifrost Facts

Everything in this file was read from the official docs on 2026-11-14 and is
cited. Anything not cited is marked UNVERIFIED and must be confirmed against a
running gateway before code depends on it.

The previous version of `docs/PLAN.md` and the current `infra/bifrost/config.json`
were written from assumption. Several assumptions turned out to be wrong. See
[09-CONFIG-FIXES.md](09-CONFIG-FIXES.md) for the corrections.

---

## 1. Request path and SDK wiring

| Fact | Detail |
| --- | --- |
| OpenAI drop-in base URL | `http://localhost:8080/openai` |
| Raw HTTP path | `/openai/v1/chat/completions`, also works without `/v1` |
| Other drop-ins | `/anthropic/*`, `/genai/*` |
| Virtual key header | `x-bf-vk` (primary). Also accepted on `Authorization: Bearer`, `x-api-key`, `x-goog-api-key` |
| Model naming | `provider/model`, e.g. `anthropic/claude-haiku-5-5`, `ollama/qwen2.5-coder:7b`. A bare name defaults to OpenAI |

Source: [OpenAI SDK integration](https://docs.getbifrost.ai/integrations/openai-sdk/overview.md),
[request options](https://docs.getbifrost.ai/providers/request-options.md).

**Decision: always use the explicit `provider/` prefix.** Bare names silently
default to OpenAI, which in confidential mode would be a privacy breach rather
than an error. Explicit prefixes make a routing bug fail loudly.

### Virtual key prefix

Official docs examples use `sk-bf-*`. One passage in the request-options page
refers to a `vk-*` prefix. **The docs are internally inconsistent.** Treat the
prefix as deployment-specific and confirm against the running gateway on hour 1.
Do not hardcode a prefix check in the agent.

---

## 2. Fallbacks

Sent as a **body** field, not a header:

```json
{
  "model": "openai/gpt-4o-mini",
  "messages": [],
  "fallbacks": ["anthropic/claude-haiku-5-5", "ollama/qwen2.5-coder:7b"]
}
```

Each entry is a `"provider/model"` string, tried in order.

Triggering behaviour:

- Fires after the primary exhausts its retries, for any error, **except**:
  caller cancellation, a plugin setting `AllowFallbacks = false`, or a
  `caller_fault` error where the fallbacks came from the model catalog rather
  than from the caller.
- Provider and TTFT timeouts **do** trigger fallback. An expired request
  deadline does not.
- A fallback that is not configured, or cannot serve the request type, is skipped.
- **If every fallback fails, Bifrost returns the primary's error, not the last
  fallback's.** Error messages shown to the user must be worded with this in mind.

Reported in the response body as:

```json
"extra_fields": {
  "routing_info": {
    "provider": "anthropic",
    "model": "claude-haiku-5-5",
    "is_fallback": true,
    "primary_provider": "openai",
    "primary_model": "gpt-4o-mini"
  }
}
```

Source: [fallbacks](https://docs.getbifrost.ai/features/fallbacks.md).

### The problem this creates for us

The docs state that **the drop-in SDK routes have no `extra_fields`**, and that
the same information is available in response headers, without naming them. The
supported-headers page documents only three response headers
(`x-bifrost-request-id`, `x-bifrost-trace-id`, deprecated `x-request-id`) and
none of them carry provider, model, fallback, or cache status. A v1.5.1
changelog entry claims routed identity is exposed through `x-bifrost-*` response
headers, but the header names are not documented anywhere we could find.

**UNVERIFIED: which response headers, if any, report the serving provider on the
`/openai` route.** Resolve this on hour 1 with a single live request and
`curl -i`. See [02-ARCHITECTURE.md](02-ARCHITECTURE.md) for the three-tier
telemetry fallback that makes the agent correct either way.

Sources: [supported headers](https://docs.getbifrost.ai/providers/supported-headers.md),
[drop-in replacement](https://docs.getbifrost.ai/features/drop-in-replacement.md).

### Sending `fallbacks` through the OpenAI SDK

`fallbacks` is not an OpenAI parameter. The OpenAI Python SDK forwards unknown
body fields only when passed via `extra_body`. The Bifrost docs do not document
this combination.

**UNVERIFIED: that `extra_body={"fallbacks": [...]}` reaches Bifrost intact.**
Confirm on hour 1. If it does not, the fallback is raw `httpx` against
`/openai/v1/chat/completions`, which costs us the SDK but nothing else.

---

## 3. Semantic cache

A vector store must be enabled before the plugin will load.

| Field | Default | Note |
| --- | --- | --- |
| `dimension` | required | `1` for direct-only, real embedding size for semantic |
| `provider`, `embedding_model` | - | Semantic mode only, omit for direct-only |
| `ttl` | `5m` | |
| `threshold` | `0.8` | Minimum cosine similarity for a semantic hit |
| `conversation_history_threshold` | **`3`** | **Caching is skipped when a conversation has more messages than this** |
| `exclude_system_prompt` | `false` | |
| `cache_by_model`, `cache_by_provider` | `true` | |
| `vector_store_namespace` | `BifrostSemanticCachePlugin` | |
| `default_cache_key` | empty | Fallback when no header is sent |
| `cache_tool_call_responses` | **`false`** | |
| `disable_identity_scoping` | `false` | |

**Caching only engages when the request carries a cache key**, in the
`x-bf-cache-key` header. If the header is absent and `default_cache_key` is
empty, the request bypasses the cache entirely and silently.

Per-request headers: `x-bf-cache-key`, `x-bf-cache-ttl`, `x-bf-cache-threshold`
(can only make matching stricter), `x-bf-cache-type` (`direct` or `semantic`),
`x-bf-cache-no-store`.

Direct hash matching runs first; semantic runs only on a direct miss.

Hit detection: `extra_fields.cache_debug` with `cache_hit`, `hit_type`
(`direct` or `semantic`), and on semantic hits `similarity` and `threshold`.
On streamed responses only the final chunk carries it. The docs state the same
data is available as `x-bifrost-cache-*` response headers on inference routes,
which contradicts the supported-headers page. Same UNVERIFIED item as above.

Source: [semantic caching](https://docs.getbifrost.ai/features/semantic-caching.md).

### Three findings that change the design

1. **`conversation_history_threshold: 3` would disable caching for essentially
   every turn of an agent loop.** A coding agent's conversation passes three
   messages within the first tool call. Left at the default, the single
   highest-scoring token lever contributes nothing and does so silently.
2. **`cache_tool_call_responses: false`** means responses containing tool calls
   are not cached by default. An agent's main loop responses are almost all tool
   calls, so this compounds finding 1.
3. Together these mean the cache cannot be bolted onto the main loop. It has to
   be designed for: see the stateless sub-call pattern in
   [03-TOKEN-STRATEGY.md](03-TOKEN-STRATEGY.md), lever 1.

---

## 4. Governance, virtual keys, budgets

Governance objects are seeded under the top-level `governance` key and loaded
into the config store at startup. Under `source_of_truth: "config.json"`, only
the sections present in the file are authoritative.

**Budgets and rate limits are separate top-level arrays referenced by ID. They
are not inline on the virtual key.** This is the single largest error in the
current `config.json`.

| Object | Fields |
| --- | --- |
| `governance.virtual_keys[]` | `id`, `name`, `value`, `is_active`, `rate_limit_id`, `provider_configs[]` |
| `provider_configs[]` | `provider`, `allowed_models`, `key_ids`, `weight`, own `rate_limit_id` |
| `governance.budgets[]` | `id`, `max_limit`, `reset_duration`, plus **exactly one** owner field (`virtual_key_id`, `team_id`, `customer_id`) |
| `governance.rate_limits[]` | `id`, `request_max_limit`, `request_reset_duration`, `token_max_limit`, `token_reset_duration` |

Budget runtime fields: `current_usage`, `last_reset`, `calendar_aligned`,
`override_amount`, `override_cycles_remaining`, `reset_budget_usage`.

Sources: [config.json governance](https://docs.getbifrost.ai/deployment-guides/config-json/governance.md),
[budget and limits](https://docs.getbifrost.ai/features/governance/budget-and-limits.md),
[schema reference](https://docs.getbifrost.ai/deployment-guides/config-json/schema-reference.md).

### Status codes

| Code | Meaning | Agent response |
| --- | --- | --- |
| **402** | `budget_exceeded`. Checked at provider config, virtual key, team, and customer levels; any single failure blocks the request | Stop or degrade, never retry the same tier |
| **429** | Rate limit exceeded | Back off, then walk the fallback chain |
| **403** | Disallowed model, **or** expired virtual key. Distinguish by message text | Router bug or key expiry. Surface loudly, do not retry |
| **401** | Missing or invalid key | Configuration error |

Budgets stack. Importantly, **a provider-level budget overrun excludes only that
provider from routing** while other providers on the same virtual key stay
available, so traffic can still fall back. A virtual-key-level overrun blocks
everything.

**UNVERIFIED: the exact `error.type` / `error.code` string for budget failures.**
Branch on the HTTP status code, never on message text.

### Pre-call budget check

`GET /api/governance/virtual-keys/quota`

- Authenticated by the virtual key itself (`x-bf-vk`), no admin credential needed.
- Returns `virtual_key_name`, `is_active`, `budgets[]` (each with
  `per_model_usage`), `rate_limit`, `rate_limits[]`, `provider_configs[]`,
  `model_configs[]`.
- Budget objects carry `max_limit`, `current_usage`, `reset_duration`, `last_reset`.
- Errors: 401 invalid key, 403 expired key, 500 internal.
- An inactive key still returns its quota.

Source: [get virtual key quota](https://docs.getbifrost.ai/api-reference/governance/get-virtual-key-quota.md).

### Pricing overrides: the silent budget killer

The schema exposes `governance.pricing_overrides`. Bifrost computes spend from
its internal model pricing table. **If a model ID is absent from that table, its
cost is unknown and the budget may never trip.** Our fallback chain uses recent
model IDs that a given Bifrost build may predate.

**Day-0 check:** make one call on each model in the chain, then read
`current_usage` from the quota endpoint. If it does not move, populate
`pricing_overrides`. A budget demo that fails to trigger because the gateway
thinks every call is free is a visible, avoidable failure in front of judges.

---

## 5. Ollama provider

```json
{
  "providers": {
    "ollama": {
      "keys": [
        {
          "name": "ollama-local",
          "value": "",
          "models": ["*"],
          "weight": 1.0,
          "ollama_key_config": { "url": "http://localhost:11434" }
        }
      ]
    }
  }
}
```

- The base URL **must** be set explicitly via `ollama_key_config.url` or
  `network_config.base_url`. There is no default and requests fail without it.
- `"value": ""` is correct for a local server with no auth.
- Bifrost delegates to its OpenAI implementation internally.
- Docs show both bare (`llama3.1:latest`) and prefixed (`ollama/nimble`) model
  references. We use the prefixed form everywhere, per section 1.

Source: [Ollama provider](https://docs.getbifrost.ai/providers/supported-providers/ollama.md).

**UNVERIFIED: whether the URL needs a `/v1` suffix.** Other gateways differ.
Test both on hour 1.

---

## 6. Model IDs and pricing for the fallback chain

Current Anthropic model IDs and first-party API rates, per the bundled Claude
API reference (cached 2026-10-06):

| Model | ID | Input $/MTok | Output $/MTok |
| --- | --- | --- | --- |
| Claude Haiku 5.5 | `claude-haiku-5-5` | 0.10 | 0.50 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2.00 | 10.00 |
| Claude Opus 5.5 | `claude-opus-5-5` | 4.00 | 20.00 |

The existing config's `claude-3-5-sonnet-latest` and `claude-3-5-haiku-latest`
are previous-generation aliases. Use the exact IDs above with no date suffix.

For a hackathon budget, the cost-effective chain is Haiku 5.5 as the cheap cloud
tier and Sonnet 5.5 as the capable tier. Opus 5.5 is not needed for the task
sizes in our benchmark and would distort the cost numbers.

---

## 7. Open items, ranked by how much they can hurt

| # | Question | Blast radius if wrong | Resolve by |
| --- | --- | --- | --- |
| 1 | Does `extra_body={"fallbacks": [...]}` reach Bifrost through the OpenAI SDK? | Reliability requirement unimplementable as designed | One live call, hour 1 |
| 2 | Which response headers carry routing and cache info on `/openai`? | Cannot tell the user which model took over | `curl -i`, hour 1 |
| 3 | Does the gateway price our model IDs? | Budget demo silently never triggers | Quota endpoint delta, hour 1 |
| 4 | Virtual key prefix, `sk-bf-` or `vk-` | Every call 401s | First smoke test |
| 5 | Ollama URL `/v1` suffix | Confidential mode dead | Two smoke tests, hour 1 |

All five are answerable in the first hour with curl alone. **Do not write agent
code before they are answered**; every one of them changes an interface.
