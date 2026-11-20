# Configuration

Settings are layered: the install directory's `.env`, then
`~/.config/himinbjorg/env`, then the process environment, each overriding
the one before. They are never read from the repository the agent works
on, which is treated as untrusted input. [`.env.example`](../.env.example)
documents every key. After changing them, `himin doctor` checks the
result: gateway, keys, each model tier and the sandbox.

`himin setup` writes `~/.config/himinbjorg/env` for you (mode 0600) and
the gateway it runs under `~/.local/share/himinbjorg/gateway`: a compose
file for Bifrost and Redis Stack (compose project `himinbjorg`, bound to
127.0.0.1, the first free port from 8099), and a Bifrost config with only
the providers you chose, your monthly budget on the standard key, and a
local-only key when Ollama was running. Both virtual keys and the setup
token are random per install. Rerunning it rewrites both and restarts the
gateway on the same port. Edit the env file by hand for anything setup
does not ask, such as the tiers below.

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

The agent picks a tier per step: local for simple steps, cheap by
default, capable for hard tasks or when a run is stuck. The cheap and capable tiers fail
over to each other and then to the local model; the local tier has no
fallback. So without Ollama, `AGENT_MODEL_LOCAL_CODE` must be a cloud
model too (see `.env.example`, "Cloud only"), and confidential mode,
which uses only local models, is unavailable.

Local tool calling needs an instruct model: the `qwen2.5-coder` family
returns tool calls as plain text and cannot drive the agent.

## Context

| Key | Purpose |
| --- | --- |
| `AGENT_CONTEXT_TOKENS` | Context budget for local models (`ollama/`, `vllm/`) in estimated tokens, default 28000 (for a 32k window). Keep it below the local model's window. A file up to a quarter of it is sent whole |
| `AGENT_CLOUD_CONTEXT_TOKENS` | Context budget for cloud models, default 64000. At 28000 a DeepSeek review of three files sat at the ceiling and re-read what was elided. A cloud call whose context is over the local budget does not fall back to the local model |
| `AGENT_PRUNE_MODE` | `turns` (default): old tool output is pruned every few turns. `budget`: the context is kept verbatim and older turns are replaced by a structured summary only near `AGENT_CONTEXT_TOKENS`. Use `budget` with a provider that has prompt caching: on Claude Haiku it was 82% cheaper than the naive baseline against 71% for `turns`. On a local model it used 60% more tokens than `turns` (Qwen 3.8), since nothing discounts the repeated context. On DeepSeek V4.1 Flash a three-file review used 436k tokens ($0.060) in `budget` against 230k ($0.028) in `turns`: with everything kept, it read more widely |
| `AGENT_PRUNE_STEP` | Default `4`: with `turns`, the pruning boundary moves in steps, so the prompt prefix stays stable and provider prompt caching hits. On Claude it cut input cost by about a quarter against `1` |

## Sandbox

| Key | Purpose |
| --- | --- |
| `AGENT_SANDBOX` | `auto` (default): approved commands run in a Landlock sandbox on Linux, writing only inside the repository and temp, with no TCP. `required` refuses commands where Landlock is missing; `off` disables it |
| `AGENT_SANDBOX_NETWORK` | `1` lets sandboxed commands open TCP connections, for example for `pip install` |

## Files the agent keeps

| Path | Contents |
| --- | --- |
| `~/.local/state/himinbjorg/sessions/` | The conversation per repository and mode, saved after every turn; `himin --resume` continues it, `/reset` deletes it |
| `~/.local/state/himinbjorg/undo/` | The last 20 edits per repository, saved before each file is written, so `/undo` works after a restart |
| `~/.local/state/himinbjorg/traces/` | One JSON-lines trace per session (in `traces/` of the clone when run from source) |

`XDG_STATE_HOME` moves all three. The files are mode 0600: they contain
the repository's code.

## Gateway configuration

Providers, virtual keys and budgets are in
[`infra/data/config.json`](../infra/data/config.json), loaded by the
gateway container at start; restart it after a change
(`docker compose --env-file .env -f infra/docker-compose.yml restart bifrost`).
`infra/bifrost/config.json` is the original configuration, kept only as
the "before" for [`09-CONFIG-FIXES.md`](09-CONFIG-FIXES.md).
