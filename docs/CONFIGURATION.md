# Configuration

Settings are layered: the install directory's `.env`, then
`~/.config/himinbjorg/env`, then the process environment, each overriding
the one before. They are never read from the repository the agent works
on, which is treated as untrusted input. [`.env.example`](../.env.example)
documents every key.

## Gateway and keys

| Key | Purpose |
| --- | --- |
| `BIFROST_BASE_URL` | Gateway URL, default `http://localhost:8099` |
| `BIFROST_SETUP_TOKEN` | Unlocks the gateway's log API, used for the per-session cost |
| `BIFROST_VK_DEMO`, `BIFROST_VK_CONFIDENTIAL` | Per-user virtual keys; budgets live on these |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY` | Optional cloud providers, read by the gateway container. Gemini, OpenRouter and Groq are verified end to end ([`VERIFIED.md`](VERIFIED.md)) |
| `VLLM_API_KEY` | Optional local vLLM server (provider `vllm`, for example `vllm/gemma-4-31b-it-qat-w4a16`). Allowed on the standard key only: confidential mode stays on Ollama, since a provider name does not prove the server is local |
| `TAVILY_API_KEY` | Optional. Enables the `web_search` tool for library docs and error messages. Without it the tool is not offered; in confidential mode it is never built |

## Models

| Key | Purpose |
| --- | --- |
| `AGENT_MODEL_LOCAL_CODE` | Local coding model, default `ollama/qwen2.5:32b-instruct` |
| `AGENT_MODEL_LOCAL_SMALL` | Small local model, default `ollama/qwen3:1.7b` |
| `AGENT_MODEL_CHEAP`, `AGENT_MODEL_CAPABLE` | Cloud tiers, for example `gemini/gemini-3.1-flash-lite` and `gemini/gemini-3.5-flash`, or `openrouter/anthropic/claude-haiku-5.5` and `openrouter/anthropic/claude-sonnet-5.5` |

Local tool calling needs an instruct model: the `qwen2.5-coder` family
returns tool calls as plain text and cannot drive the agent.

## Context

| Key | Purpose |
| --- | --- |
| `AGENT_CONTEXT_TOKENS` | Context budget in estimated tokens, default 28000 (for a 32k window). Keep it below the local model's window. A file up to a quarter of it is sent whole |
| `AGENT_PRUNE_MODE` | `turns` (default): old tool output is pruned every few turns. `budget`: the context is kept verbatim and older turns are replaced by a structured summary only near `AGENT_CONTEXT_TOKENS`. Use `budget` with a provider that has prompt caching: on Claude Haiku it was 82% cheaper than the naive baseline against 71% for `turns`. On a local model it used 60% more tokens than `turns` (Qwen 3.8), since nothing discounts the repeated context |
| `AGENT_PRUNE_STEP` | Default `4`: with `turns`, the pruning boundary moves in steps, so the prompt prefix stays stable and provider prompt caching hits. On Claude it cut input cost by about a quarter against `1` |

## Sandbox

| Key | Purpose |
| --- | --- |
| `AGENT_SANDBOX` | `auto` (default): approved commands run in a Landlock sandbox on Linux, writing only inside the repository and temp, with no TCP. `required` refuses commands where Landlock is missing; `off` disables it |
| `AGENT_SANDBOX_NETWORK` | `1` lets sandboxed commands open TCP connections, for example for `pip install` |

## Gateway configuration

Providers, virtual keys and budgets are in
[`infra/data/config.json`](../infra/data/config.json), loaded by the
gateway container at start; restart it after a change
(`docker compose --env-file .env -f infra/docker-compose.yml restart bifrost`).
`infra/bifrost/config.json` is the original configuration, kept only as
the "before" for [`09-CONFIG-FIXES.md`](09-CONFIG-FIXES.md).
