# 05 - Reliability and Budget

Two mandatory requirements, handled together because they share a code path:
both are failures returned by the gateway that the agent must survive without
losing the session.

## Session durability

The requirement is to *continue working through provider outages without losing
the session*. That means session state cannot live only in the message list.

The context ledger is written to an append-only JSONL trace after every turn.
On an unrecoverable error, the agent prints the trace path and exits non-zero,
and can be resumed from it. The trace is also the evidence artifact, so this
costs nothing extra. See [07-EVIDENCE.md](07-EVIDENCE.md).

---

## Fallback chains

Every request carries a `fallbacks` array appropriate to its tier.

| Tier | Primary | Fallbacks |
| --- | --- | --- |
| T2 capable | `anthropic/claude-sonnet-5-5` | `openai/gpt-4o`, `ollama/qwen2.5-coder:7b` |
| T1 cheap | `openai/gpt-4o-mini` | `anthropic/claude-haiku-5-5`, `ollama/qwen2.5-coder:7b` |
| T0 local | `ollama/qwen2.5-coder:7b` | none |
| Any tier, confidential | `ollama/qwen2.5-coder:7b` | **none, enforced** |

Design notes:

- Each chain crosses providers. A chain of two OpenAI models does not survive an
  OpenAI outage, which is the scenario being demonstrated.
- Each cloud chain ends at the local model. The local model is slower and
  weaker, but it is the only tier that cannot be rate-limited or run out of
  budget, so it is the floor.
- The confidential chain is empty by construction. See
  [04-SECURITY.md](04-SECURITY.md) layer 3 for why this is a security control
  and not a configuration preference.

### Telling the user

`routing_info.is_fallback` is true when a fallback served the request, alongside
`primary_provider` and `primary_model`. On the first such response in a session,
print a clear line:

```
Provider changed: openai/gpt-4o-mini unavailable, now served by anthropic/claude-haiku-5-5
```

Print it once per transition, not once per call, or a sustained outage produces
one line per request and buries everything else. Track the current serving
provider and announce only on change.

Reading `routing_info` depends on the unresolved telemetry question in
[01-RESEARCH.md](01-RESEARCH.md) §2. The three-tier read in
[02-ARCHITECTURE.md](02-ARCHITECTURE.md) covers every case, with the gateway log
lookup as the guaranteed floor.

### When every fallback fails

Bifrost returns **the primary's error, not the last fallback's**. The user-facing
message must therefore not claim the last model in the chain is the one that
failed. Word it as a chain failure:

```
All providers failed. Primary openai/gpt-4o-mini reported: <error>.
Fallbacks attempted: anthropic/claude-haiku-5-5, ollama/qwen2.5-coder:7b
```

---

## Status code handling

| Code | Meaning | Action |
| --- | --- | --- |
| 402 | Budget exceeded | Degradation ladder below. **Never retry the same tier** |
| 429 | Rate limit | Exponential backoff with jitter, then let the fallback chain handle it. Cap retries |
| 403 | Disallowed model, or expired key | **Do not retry.** This is a router bug or a provisioning problem. Print which model was requested under which key |
| 401 | Invalid key | Configuration error. Exit with setup guidance |
| 5xx | Gateway or provider fault | Fallback chain handles it. Backoff on the chain as a whole |

Branch on the status code only. The exact `error.type` string for budget
failures is unverified and message text is not a stable interface.

The 403 case deserves care: it covers both "this key may not use this model" and
"this key has expired", distinguished only by message text. Print the message
verbatim rather than interpreting it, and say which of the two it is likely to
be based on whether other calls on the same key have succeeded this session.

---

## Budget enforcement

Enforcement is the gateway's job and it is authoritative. The agent's job is to
be well-behaved before the limit and graceful at it.

### Pre-flight check

`GET /api/governance/virtual-keys/quota`, authenticated with the virtual key
itself. Returns `budgets[]` with `max_limit` and `current_usage`.

- Polled at session start and then at most once every N seconds, cached in
  between. The check is not free and must not become a per-call cost.
- Headroom is computed as `max_limit - current_usage`.
- Before a T2 call, if headroom is below an estimated cost threshold, downgrade
  to T1 pre-emptively rather than issuing a call that will be refused.

This is an optimisation and a UX nicety. **It is not the enforcement.** The
agent never assumes its local accounting is correct; the 402 is the truth.

### The degradation ladder

On a 402, the agent walks down rather than dying:

1. **Downgrade tier.** If the call was T2, retry at T1. If T1, retry at T0.
2. **T0 is free.** The local model has no gateway budget, so a budget-exhausted
   session can continue at reduced capability indefinitely. Announce this
   clearly: the user needs to know quality has dropped.
3. **If T0 is also unavailable**, stop cleanly: print remaining budget, the
   reset window from `reset_duration` and `last_reset`, what was completed, and
   where the trace is. Exit non-zero.

Degrading to a free local tier rather than refusing outright is the behaviour
the requirement's phrase *clearly defined behaviour when a limit is reached*
is asking for, and it is more useful than a hard stop.

### The pricing trap

Bifrost computes spend from its internal pricing table. **If a model ID is not
in that table, its cost may be counted as zero and the budget will never
trigger.** Our chain uses recent model IDs.

Day-0 verification: one call per model, then read `current_usage`. If it does
not move, populate `governance.pricing_overrides`. Without this check, the
budget demo can fail in the most confusing possible way, by appearing to work
while nothing is being counted. See [01-RESEARCH.md](01-RESEARCH.md) §4.

### Provider-level versus key-level budgets

A provider-level overrun excludes **only that provider** from routing while the
rest of the key stays usable, so traffic falls back. A virtual-key-level overrun
blocks everything. These produce different user-visible behaviour and the demo
uses the key-level budget, since that is the one that visibly stops the agent.
