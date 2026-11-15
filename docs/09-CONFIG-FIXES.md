# 09 - Infrastructure Config Defects and Corrections

The files in `infra/` were written from assumption before the docs were read.
This document lists what is wrong, why it matters, and the corrected versions.

**The corrected files below are not yet applied.** Apply them at hour 0, before
the first smoke test.

---

## Defects in `infra/bifrost/config.json`

| # | Defect | Consequence | Severity |
| --- | --- | --- | --- |
| 1 | `budgets` is inline on each virtual key | Budgets are a separate top-level array referenced by `virtual_key_id`. Inline budgets are not loaded, so **no budget is enforced at all** | **Critical** |
| 2 | `rate_limit` is inline on the virtual key | Same: `governance.rate_limits[]` plus `rate_limit_id` on the key | **Critical** |
| 3 | Ollama uses `network_config.base_url` with a guessed shape | Documented form is `ollama_key_config.url` on the key entry. Wrong shape means no base URL, and the docs state requests fail without one | **Critical** |
| 4 | `conversation_history_threshold` not set, defaults to 3 | Caching is skipped for any conversation over 3 messages. The primary token lever silently contributes nothing | **Critical** |
| 5 | No `x-bf-cache-key` strategy and `default_cache_key` unset | Caching does not engage at all without a key | **Critical** |
| 6 | `source_of_truth` not set | Config store may take precedence over the file, so edits appear not to apply | High |
| 7 | Model IDs `claude-3-5-sonnet-latest`, `claude-3-5-haiku-latest` | Previous-generation aliases. Current IDs are `claude-sonnet-5-5`, `claude-haiku-5-5` | High |
| 8 | No `pricing_overrides` and no verification that models are priced | If Bifrost cannot price a model, spend is not counted and the budget never trips | High |
| 9 | Virtual key values `sk-bf-demo-user-key` | Prefix is inconsistent in the docs. Verify against the running gateway | Medium |
| 10 | `vector_store.config` has only `addr` | Confirm the redis config shape in the dashboard | Medium |

Defects 1, 2, 4, and 5 share a failure signature: **the feature appears
configured and silently does nothing.** None of them produce an error at
startup. All four would be discovered for the first time during the demo.

---

## Defects in `infra/docker-compose.yml`

| # | Defect | Consequence |
| --- | --- | --- |
| 11 | `./data:/app/data` writable and `./bifrost/config.json:/app/data/config.json:ro` read-only overlap | Bifrost persists config store state into `/app/data`. A read-only file inside a writable mount is a conflict waiting to surface at startup or on any dashboard edit |
| 12 | `${OPENAI_API_KEY}` with no `env_file` | Compose reads `.env` from the **compose file's** directory. `.env` is at the repository root, `docker-compose.yml` is in `infra/`. Variables resolve to empty strings, silently |
| 13 | `depends_on` without `condition: service_healthy` | Bifrost starts before Redis and Ollama are ready. The semantic cache plugin may fail to initialise on a cold start |
| 14 | No Ollama healthcheck | Nothing to depend on, and no signal that the model has been pulled |
| 15 | `TAVILY_API_KEY` absent from the bifrost service | Harmless, Tavily is called by the agent directly, but worth noting so nobody adds it to the gateway by mistake |

Defect 12 is the one that will waste an hour: every provider call fails
authentication and the cause is invisible.

---

## Corrected `infra/bifrost/config.json`

Model IDs per [01-RESEARCH.md](01-RESEARCH.md) §6. Verify the key prefix and
the redis config shape against the dashboard before relying on this.

```json
{
  "$schema": "https://www.getbifrost.ai/schema",
  "source_of_truth": "config.json",
  "client": {
    "drop_excess_requests": false,
    "enforce_auth_on_inference": true
  },
  "providers": {
    "openai": {
      "keys": [
        { "name": "openai-primary", "value": "env.OPENAI_API_KEY",
          "models": ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small"], "weight": 1.0 }
      ]
    },
    "anthropic": {
      "keys": [
        { "name": "anthropic-primary", "value": "env.ANTHROPIC_API_KEY",
          "models": ["claude-sonnet-5-5", "claude-haiku-5-5"], "weight": 1.0 }
      ]
    },
    "ollama": {
      "keys": [
        { "name": "ollama-local", "value": "", "models": ["*"], "weight": 1.0,
          "ollama_key_config": { "url": "http://ollama:11434" } }
      ]
    }
  },
  "vector_store": {
    "enabled": true,
    "type": "redis",
    "config": { "addr": "redis:6379" }
  },
  "plugins": [
    {
      "name": "semantic_cache",
      "enabled": true,
      "config": {
        "provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "dimension": 1536,
        "ttl": "24h",
        "threshold": 0.9,
        "conversation_history_threshold": 50,
        "cache_by_model": true,
        "cache_by_provider": true,
        "cache_tool_call_responses": false,
        "default_cache_key": ""
      }
    }
  ],
  "governance": {
    "virtual_keys": [
      {
        "id": "vk-user-demo",
        "name": "Demo User",
        "value": "sk-bf-demo-user-key",
        "is_active": true,
        "rate_limit_id": "rl-demo",
        "provider_configs": [
          { "provider": "openai", "weight": 0.5,
            "allowed_models": ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small"],
            "key_ids": ["*"] },
          { "provider": "anthropic", "weight": 0.5,
            "allowed_models": ["claude-sonnet-5-5", "claude-haiku-5-5"],
            "key_ids": ["*"] },
          { "provider": "ollama", "weight": 0.0,
            "allowed_models": ["*"], "key_ids": ["*"] }
        ]
      },
      {
        "id": "vk-user-confidential",
        "name": "Confidential User (local only)",
        "value": "sk-bf-confidential-key",
        "is_active": true,
        "provider_configs": [
          { "provider": "ollama", "weight": 1.0,
            "allowed_models": ["*"], "key_ids": ["*"] }
        ]
      }
    ],
    "budgets": [
      { "id": "bud-demo", "virtual_key_id": "vk-user-demo",
        "max_limit": 5.00, "reset_duration": "1M" }
    ],
    "rate_limits": [
      { "id": "rl-demo",
        "token_max_limit": 200000, "token_reset_duration": "1h",
        "request_max_limit": 200, "request_reset_duration": "1m" }
    ]
  }
}
```

### Rationale for the non-obvious values

- **`ollama` is on the demo key at weight 0.0.** The cloud fallback chains end
  at the local model, so the standard key must be permitted to reach Ollama.
  Weight 0 keeps it out of normal load balancing while leaving it reachable as
  an explicit fallback target. **Verify this interpretation of weight 0 against
  the running gateway**; if weight 0 excludes it from routing entirely, use a
  small positive weight instead.
- **`allowed_models: ["*"]` on the confidential key** with only the `ollama`
  provider listed. The provider restriction is what enforces privacy; listing
  models as well would break whenever the pulled model tag changes.
- **No budget on the confidential key.** Local inference has no marginal cost,
  and a budget there could block the agent for no reason. The previous
  `max_limit: 1000.00` was harmless but meaningless.
- **`text-embedding-3-small` is added to the allowed models.** The semantic
  cache calls the embedding provider through the gateway. If the virtual key
  does not permit the embedding model, semantic caching fails. Direct-hash
  caching would still work, making this another silent degradation.
- **`threshold: 0.9`, up from 0.85.** For code, a loose semantic threshold
  risks returning a cached answer about a different file. Prefer a stricter
  threshold and a lower hit rate over a wrong hit. Tune with measurement.
- **`ttl: 24h`** so the benchmark's second pass hits the cache from the first.
- **`conversation_history_threshold: 50`** so the value never silently excludes
  a borderline call. The Shape A / Shape B split, not this number, is what makes
  caching work. See [03-TOKEN-STRATEGY.md](03-TOKEN-STRATEGY.md) lever 1.

### Confidential mode and the embedding provider

The configured embedding model is OpenAI's. A semantic cache lookup embeds the
request, which means sending text to OpenAI.

**For the confidential key, semantic caching must be direct-only or disabled.**
Options, in order of preference:

1. Agent-side: never send `x-bf-cache-key` in confidential mode, so the cache is
   bypassed entirely. Simplest, and it is enforced where the rest of the privacy
   controls already live.
2. Send `x-bf-cache-type: direct` to force hash matching with no embedding call.
   Relies on the gateway honouring it, which is worth verifying before trusting.

Option 1 is the design decision. It costs a token lever in confidential mode and
that is the correct trade.

---

## Corrected `infra/docker-compose.yml`

```yaml
# Infra for the token-efficient coding agent (Himinbjorg).
# Usage from the repository root:
#   docker compose --env-file .env -f infra/docker-compose.yml up -d
services:
  bifrost:
    image: maximhq/bifrost
    ports:
      - "8080:8080"
    volumes:
      - bifrost-data:/app/data
      - ./bifrost/config.json:/app/config.json:ro
    environment:
      - LOG_LEVEL=info
      - BIFROST_CONFIG_PATH=/app/config.json
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    depends_on:
      redis:
        condition: service_healthy
      ollama:
        condition: service_healthy

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama-models:/root/.ollama
    healthcheck:
      test: ["CMD-SHELL", "ollama list || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 12

volumes:
  bifrost-data:
  ollama-models:
```

### Changes and their reasons

- **Config mounted outside `/app/data`.** Removes the read-only-inside-writable
  conflict. `BIFROST_CONFIG_PATH` points at it. **Verify this environment
  variable name against the container's documentation at hour 0**; if it is not
  supported, mount the config at the path the image expects and move the
  persistent data volume elsewhere instead. The principle is that the two must
  not overlap.
- **Named volumes** instead of bind mounts for state. Avoids root-owned
  directories appearing in the repository.
- **`--env-file .env`** in the usage line. This is the fix for defect 12 and is
  the reason the comment is on the first line rather than buried.
- **Healthchecks and conditional `depends_on`.** The semantic cache plugin needs
  the vector store at startup.

---

## Hour 0 verification sequence

Run in order. Each step gates the next.

1. `docker compose --env-file .env -f infra/docker-compose.yml up -d`
2. `docker compose -f infra/docker-compose.yml exec ollama ollama pull qwen2.5-coder:7b`
3. Dashboard at `http://localhost:8080`: confirm three providers, two virtual
   keys, one budget attached to the demo key, semantic cache plugin loaded.
   **If the budget does not appear attached, defect 1 is not fixed.**
4. Smoke test a chat completion with `x-bf-vk`. Settles the key prefix.
5. `curl -i` the same call, record every `x-bifrost-*` response header.
6. Repeat with `fallbacks` and a broken primary, confirm `routing_info`.
7. Repeat twice with an identical `x-bf-cache-key`, confirm `cache_debug.cache_hit`
   is true the second time. **If it is false, caching is not working and no
   amount of agent-side work will fix it.**
8. Call an Ollama model through the confidential key.
9. Call each chain model once, then read
   `GET /api/governance/virtual-keys/quota` and confirm `current_usage` moved.

Record the answers in `docs/VERIFIED.md` with the command that produced each.

---

## Addendum: defects found by running it

Doc [VERIFIED.md](VERIFIED.md) records six further defects (16-21) found
against a live gateway, including two that this document got wrong:

- The corrected `config.json` above specifies `redis:7-alpine`. **That cannot
  serve the semantic cache**, which needs the `FT.*` search commands. Use
  `redis/redis-stack-server`.
- It also specifies `allowed_models: ["*"]`. **The wildcard is not honoured**
  by the governance plugin; every model must be enumerated.

The live config in `infra/data/config.json` is the authoritative version.
