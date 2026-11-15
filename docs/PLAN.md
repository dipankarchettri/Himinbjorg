# PS01 - Token-Efficient Coding Agent: Overview

> This file previously contained a build plan written from assumption, before
> the Bifrost documentation had been read. Several of those assumptions were
> wrong. It has been replaced with this overview; the verified facts live in
> [01-RESEARCH.md](01-RESEARCH.md) and the corrections in
> [09-CONFIG-FIXES.md](09-CONFIG-FIXES.md).

## The problem

Build a coding agent that completes real development tasks correctly while
minimising token consumption, with four mandatory properties: reliability
through provider outages, per-user budget enforcement before model calls, code
privacy for confidential repositories, and approval gating on mutating actions.
The last two must hold *regardless of instructions contained in prompts*.

Bifrost (LLM gateway) and Tavily (web research) are mandatory integrations.

## Shape of the solution

A Python CLI agent. Bifrost owns routing, semantic caching, provider failover,
and spend accounting. The agent owns the loop, the tool surface, context
economy, and every security decision.

```
user -> CLI loop -> OpenAI SDK (base_url /openai, header x-bf-vk)
                 -> Bifrost -> openai | anthropic | ollama
                               + semantic cache (redis)
                               + per-key budget, 402 before provider call
                               + fallback chain
     tools: read (free) | write, shell, git (gated) | web_search (absent if confidential)
```

## Requirement to component map

| Requirement | Enforced by | Verified by |
| --- | --- | --- |
| Reliability through outages | `fallbacks` array per request, cross-provider, ending at the local model. `routing_info.is_fallback` read back and announced once per transition. Session state in an append-only trace | [07-EVIDENCE.md](07-EVIDENCE.md) scenario 2 |
| Budget before model calls | Bifrost virtual key with an attached budget. 402 returned before the provider is reached. Agent walks a degradation ladder T2 to T1 to T0, then exits cleanly | Scenario 3 |
| Code privacy | Startup fork: confidential virtual key (ollama-only at the gateway), registry built without the search tool, empty fallback chain, egress assertion before every call, fail-closed when the local model is down | Scenario 4 |
| Action approval | Permission enum fixed at tool registration. Gate reads `/dev/tty`, takes no conversation parameter, cannot be influenced by prompt content. Argument validation before the prompt | Scenario 5 |
| Fewest tokens | Eight stacked levers, each independently switchable and separately attributed against a naive baseline | Scenario 1 |

## The two design decisions worth knowing up front

**Caching cannot be bolted on.** Bifrost skips caching for conversations longer
than three messages and for tool-call responses, both by default. An agent loop
violates both immediately. So model work is split into conversation calls, which
are not cacheable, and stateless single-message sub-calls, which are. The agent
is built to push as much work as possible into the second shape. This is the
reason the module boundaries fall where they do.

**The requirements conflict with each other, and the conflicts are the
interesting part.** Reliability wants a cloud fallback available on every
request; privacy needs the chain empty, because that fallback fires exactly when
the local model is unavailable. Caching wants to embed every request through
OpenAI; privacy needs that disabled for confidential repositories. Both resolve
in favour of the guarantee over the optimisation, and both are easy to miss.

## Scope

In: the agent, the policy gate, the privacy fork, the token levers, the evidence
harness, a four-task benchmark.

Out: IDE extension, server mode, multi-user, persistent database, concurrency,
repository-wide embedding retrieval, fine-tuning.

## Where to go next

Start with [01-RESEARCH.md](01-RESEARCH.md) §7, the five open questions. They are
answerable in an hour with curl and every one of them changes an interface.
Then [08-BUILD-24H.md](08-BUILD-24H.md).
