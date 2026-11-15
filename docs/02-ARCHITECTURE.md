# 02 - Architecture

## Principle

Bifrost owns infrastructure concerns: routing, caching, failover, spend
accounting. The agent owns task completion, context economy, and security. The
boundary is deliberate, and so is the overlap: **anything that must be true
regardless of what the model says is enforced in the agent, in code, before the
request is built.** Anything that is an optimisation is pushed to the gateway.

## Component map

```
CLI (main)
  |
  v
Session  ---------------------------------------------------.
  |                                                          |
  v                                                          v
Loop  -> Router -> LLMClient -> Bifrost -> openai | anthropic | ollama
  |                   ^                      + semantic cache (redis)
  |                   |                      + virtual key budget
  |              Telemetry                   + fallback chain
  |              (3-tier read)
  v
ToolRegistry -> PolicyGate -> Tool impl
  |                 ^
  |                 |
  |            PrivacyMode (frozen at startup)
  v
Memory (file hashes, context ledger)
```

## Request path, step by step

1. **Loop** decides a model call is needed and assembles a minimal message list
   from the context ledger.
2. **Router** classifies the step into a tier (local / cheap / capable) using
   heuristics first, an LLM classifier only when heuristics are inconclusive.
3. **PrivacyMode** is consulted. In confidential mode the router's answer is
   overridden to the local tier unconditionally.
4. **LLMClient** builds the request: model string with explicit `provider/`
   prefix, `fallbacks` list appropriate to the tier and privacy mode, headers
   `x-bf-vk` and, for cacheable calls, `x-bf-cache-key`.
5. **Egress assertion** runs immediately before the HTTP call. See
   [04-SECURITY.md](04-SECURITY.md). It is the last chokepoint and it fails closed.
6. **Bifrost** enforces budget, checks cache, routes, falls back if needed.
7. **Telemetry** extracts which provider actually served, whether it was a
   fallback, whether it was a cache hit, and the token counts.
8. **Loop** reports a provider change to the user, appends the result to the
   ledger, and continues.

## Telemetry: the three-tier read

The docs are inconsistent about whether the `/openai` drop-in route exposes
`extra_fields` in the body or `x-bifrost-*` headers (see
[01-RESEARCH.md](01-RESEARCH.md) §2). Rather than bet on one, read in this order
and stop at the first that yields data:

1. **Response body `extra_fields`** - `routing_info`, `cache_debug`. Free if present.
2. **Response headers** - whatever `x-bifrost-*` keys the live gateway actually
   sets. Discovered once at startup, not hardcoded.
3. **`GET /api/logs/{id}`**, keyed by `x-bifrost-request-id`.

Tier 3 is the important one. `x-bifrost-request-id` is documented, always
present, and *is* the log entry ID. That guarantees we can always recover the
full record of what happened, including provider, model, cost, and cache status,
even if tiers 1 and 2 both come back empty.

Tier 3 costs an extra HTTP call, so the agent uses it only when the first two
fail. The **evidence harness always uses tier 3**, because a number read from
the gateway's own log is independent attestation, while a number the agent
prints about itself is not. See [07-EVIDENCE.md](07-EVIDENCE.md).

A startup probe performs one cheap call and records which tier works, so the
rest of the session takes the fast path.

## Why the OpenAI SDK, and the exit if it fails

The drop-in route is documented and lets us use a battle-tested client with
retry and timeout handling we do not have time to write. The one risk is
`fallbacks`, which is not an OpenAI parameter and must travel via `extra_body`.

If hour-1 testing shows that does not work, `LLMClient` switches to raw `httpx`
against `/openai/v1/chat/completions`. **Nothing above `LLMClient` changes.**
This is why the module boundary is drawn where it is: the SDK choice is an
implementation detail of one file.

## Two model-call shapes

This distinction drives the whole caching strategy and is the least obvious
decision in the design.

**Shape A, the conversation call.** The main agent loop. Long message list,
grows every turn, contains tool calls. Cannot be meaningfully cached: Bifrost
skips caching above `conversation_history_threshold` messages (default 3) and
skips tool-call responses by default. Routed to the capable tier. Low volume,
high value per call.

**Shape B, the stateless sub-call.** A single self-contained question with no
history: classify this task, summarise this file, extract the symbols from this
chunk, is this diff plausible. One or two messages, no tool calls, deterministic
prompt template.

Shape B calls cache almost perfectly, route to the local or cheap tier, and
across a session there are many more of them than Shape A calls. **The agent is
therefore designed to push as much work as possible out of Shape A and into
Shape B.** That is not a caching trick bolted on afterwards; it is the reason
the module boundaries fall where they do.

## Confidential mode as a startup fork

Confidential mode is not a runtime flag that functions check. It is resolved
once, before the first model call, and produces a different object graph:

- a different virtual key,
- a tool registry built **without** the web search tool,
- a fallback chain containing only local models,
- an egress assertion armed to reject any non-local provider.

A function cannot "forget" to check the flag, because the unsafe option is not
present in the graph to be chosen. Absence beats refusal.

## Process and state

Single process, single repository, synchronous loop. Session state is in memory
with an append-only JSONL trace on disk for the evidence harness. No database,
no server, no concurrency. Every one of those would be a reasonable production
choice and all are wrong for 24 hours.
