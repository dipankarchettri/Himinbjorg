# Himinbjorg

A token-efficient CLI coding agent on the [Bifrost](https://github.com/maximhq/bifrost)
LLM gateway. It fixes bugs, adds features and refactors real repositories
while spending as few tokens as it can, and it enforces failover, per-user
budgets, code privacy and action approval in the harness, not in the prompt.

Built for Problem Statement 01 of the Bifrost by Maxim AI track: *build a
coding agent that gets the job done on the fewest tokens.* The name is
Heimdall's hall at the end of the Bifrost bridge, where the gate is kept.

**Headline result:** on 7 benchmark tasks, 10 runs each at temperature 0.7,
it passes 57/70 against 31/70 for the same agent with every lever off, using
**84% fewer tokens per completed task** (13,913 vs 88,196). All numbers are
gateway-reported; the evidence pack is committed in
[`evidence/20261115-050124/`](evidence/20261115-050124/).

## Contents

- [Features](#features)
- [Results](#results)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Testing and proof](#testing-and-proof)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Known limitations](#known-limitations)
- [License](#license)

## Features

Each PS01 requirement, how it is enforced, and where it is proven:

| Requirement | How it is enforced | Proof |
| --- | --- | --- |
| **Reliability**: survive outages and rate limits, say which model took over | Bifrost fallback chain (OpenAI -> Anthropic -> local). The agent announces every fallback, including on the first call, and sticks to the working model for the rest of the session | Scenario 2, [`bench/failover_scenario.py`](bench/failover_scenario.py) |
| **Budget**: per-user limits enforced before the model call | A Bifrost governance budget per virtual key returns 402 before any provider is reached. The agent drops a tier, then stops and shows the quota | Scenario 3, [`bench/budget_scenario.py`](bench/budget_scenario.py) |
| **Privacy**: confidential repos stay on local models, whatever the prompt says | A `.agent-confidential` file or `--confidential` selects a local-only virtual key with an empty fallback chain, no web search and no cache. An egress check before every HTTP call refuses any non-local model | Scenario 4, [`bench/privacy_scenario.py`](bench/privacy_scenario.py) |
| **Approval**: reads are free; writes, shell and git need explicit approval | A gate that takes no conversation input asks on the terminal and shows the exact diff. Text in files or prompts cannot grant anything | Scenario 5, [`bench/approval_scenario.py`](bench/approval_scenario.py), plus `make gate-check` |
| **Fewer tokens, same correctness** | Eight stacked levers, each measured against an all-off baseline on the same binary | Scenario 1, [`bench/benchmark.py`](bench/benchmark.py) |

Day-to-day features: an interactive session that keeps context between
tasks, diff previews at every approval, `/undo` for the agent's last edit,
and a per-task report of tokens, cache hits and gateway-priced cost.

## Results

From the committed evidence pack, all 7 stages PASS:

| Scenario | Result |
| --- | --- |
| 0 unit tests | 107 passing: security, edit tools, agent loop, task checkers |
| 1 token efficiency (7 tasks x 10 runs, T=0.7) | 57/70 passed vs 31/70 naive; 13,913 vs 88,196 tokens per completed task (84% fewer). On the 21-file repository: 20/30 vs 3/30 |
| 1b identical re-run | 4,402 -> 0 provider tokens, every call served from the semantic cache |
| 2 failover | PASS: broken OpenAI -> Anthropic -> local, with attribution |
| 3 budget | PASS: usage reset to $0, 6 calls served, then 402 before the provider |
| 4 privacy | PASS: 6 structural checks under an adversarial prompt |
| 5 approval | PASS automated; the interactive half verified by a human |

Disclosed caveats: the budget scenario uses synthetic pricing for local
models; one task (`shop_freeship`) is solved by neither arm; and the first
run of the 21-file tasks went the other way (optimized 1/15, naive 8/15).
What changed after that run, and why, is in
[`docs/VERIFIED.md`](docs/VERIFIED.md).

## Quick start

Requirements: Docker, Python 3.11+, and [Ollama](https://ollama.com) on the
host. A GPU that fits a 32B model is recommended. Cloud provider keys are
optional.

```sh
git clone <this-repo> himinbjorg && cd himinbjorg
ollama pull qwen2.5:32b-instruct     # local coding model, about 20 GB
ollama pull qwen2.5:7b-instruct      # used by the failover and budget scenarios
cp .env.example .env                 # provider keys optional
make venv                            # .venv with the agent installed
make up                              # Bifrost on :8099 + Redis Stack
make test                            # 107 unit tests, no gateway needed
make agent TASK="what does divide() do wrong?" REPO=bench/seed
```

## Usage

Put `.venv/bin` on your `PATH` (or symlink `.venv/bin/himin`), copy your
settings to the user config directory, then run it from any repository:

```sh
mkdir -p ~/.config/himinbjorg
cp .env ~/.config/himinbjorg/env
cd ~/code/some-project
himin                                               # interactive session
himin "fix the failing test in tests/test_orders.py"  # one task, then exit
himin --confidential "explain the auth flow"        # local models only
```

`bfa` is installed as a short alias for `himin`.

In a session each task continues the same conversation, so a follow-up like
"now add a test for it" has the context.

| Command | Effect |
| --- | --- |
| `/cost` | Session token and cost totals |
| `/undo` | Revert the agent's most recent edit (a whole rename counts as one) |
| `/reset` | Start a fresh conversation |
| `/help` | List commands |
| `/exit`, Ctrl-D | Quit |
| Ctrl-C | Interrupt the running task, keep the session |

Approvals show the exact diff that will be written, computed by a dry run
against the file on disk. Answer `y` or `n`, or `a` to approve every file
edit for the rest of the session; shell commands and git still ask each
time. An edit that would fail is reported back to the model without asking
you, since it would write nothing.

`himin --help` lists the flags, including `--naive` and the per-lever
`--no-*` switches used by the benchmark.

## Configuration

Settings are layered: the install directory's `.env`, then
`~/.config/himinbjorg/env`, then the process environment. They are never
read from the target repository, which is treated as untrusted input.
[`.env.example`](.env.example) documents every key.

| Key | Purpose |
| --- | --- |
| `BIFROST_BASE_URL` | Gateway URL, default `http://localhost:8099` |
| `BIFROST_SETUP_TOKEN` | Unlocks the gateway's log API for per-session cost |
| `BIFROST_VK_DEMO`, `BIFROST_VK_CONFIDENTIAL` | Per-user virtual keys (budgets live on these) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Optional cloud providers, read by the gateway container |
| `AGENT_MODEL_LOCAL_CODE` | Local coding model, default `ollama/qwen2.5:32b-instruct` |
| `AGENT_MODEL_CHEAP`, `AGENT_MODEL_CAPABLE` | Optional cloud tiers |
| `AGENT_PRUNE_STEP` | Set to `4` with cloud models, so the prompt prefix stays stable and provider prompt caching hits |
| `AGENT_CONTEXT_TOKENS` | Keep below the local model's context window, default 28000 for 32k |

The gateway's own configuration (providers, virtual keys, budgets) is
[`infra/data/config.json`](infra/data/config.json).
`infra/bifrost/config.json` is the original configuration, kept only as the
"before" for [`docs/09-CONFIG-FIXES.md`](docs/09-CONFIG-FIXES.md).

## How it works

A Python agent talks to Bifrost over its OpenAI-compatible route. Bifrost
owns routing, semantic caching, provider fallback and budget enforcement.
The agent owns the loop, the tool surface, context minimisation and every
security decision. Security is enforced in the harness and never delegated
to the model.

The token levers, each switchable for measurement:

1. **Caching**: stateless sub-calls so the gateway semantic cache hits (a repeat costs zero provider tokens), plus provider prompt-cache breakpoints.
2. **Tiered routing**: the cheapest capable tier first, escalation to a different model only when a run is stuck.
3. **File memory**: each file read is held once and re-rendered, not repeated in history.
4. **Targeted retrieval**: grep and line-range reads widened to the enclosing function, not whole files.
5. **History pruning**: old tool output is dropped on a stepped horizon under a context budget.
6. **Tool disclosure**: git and rename tools are offered only when the task mentions them.
7. **Diff writes**: `apply_patch` with fuzzy matching instead of whole-file rewrites.
8. **Loop discipline**: caps and nudges for repeated failed edits, text-only answers and stalled steps.

Details and per-lever measurements:
[`docs/03-TOKEN-STRATEGY.md`](docs/03-TOKEN-STRATEGY.md). Architecture:
[`docs/02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md). Threat model:
[`docs/04-SECURITY.md`](docs/04-SECURITY.md).

## Testing and proof

```sh
make test       # 107 unit tests, no gateway or model needed
make gate-check # the approval prompt on a real terminal, no model involved
make bench      # token benchmark, naive vs optimized
make proof      # every scenario into evidence/<timestamp>/ with SUMMARY.md (about an hour)
```

Individual scenarios: `make scenario-failover`, `scenario-budget`,
`scenario-privacy`, `scenario-approval`, `scenario-cache`. Benchmark task
repositories are created in the system temp directory, each as its own git
repository, so runs never touch this checkout.

## Project layout

```text
agent/            the agent
  main.py         CLI entry point and interactive session
  loop.py         turn loop, prompts, escalation, nudges
  llm.py          gateway client, failover attribution, prompt caching
  config.py       settings and confidential-mode resolution
  telemetry.py    usage and gateway-priced cost
  memory/         history ledger and file memory
  security/       approval gate and egress policy
  tools/          read, write, shell and git tools, tool registry
bench/            benchmark, scenarios, unit tests, fixture repositories
infra/            docker-compose and Bifrost configuration
docs/             design, research and verification notes
evidence/         the committed proof pack
```

## Documentation

| Doc | What it answers |
| --- | --- |
| [docs/VERIFIED.md](docs/VERIFIED.md) | Facts checked against a live gateway. It overrides the other docs where they disagree |
| [docs/PLAN.md](docs/PLAN.md) | Scope and requirement-to-component map |
| [docs/01-RESEARCH.md](docs/01-RESEARCH.md) | Bifrost API facts from documentation, with citations |
| [docs/02-ARCHITECTURE.md](docs/02-ARCHITECTURE.md) | Components, request path, telemetry |
| [docs/03-TOKEN-STRATEGY.md](docs/03-TOKEN-STRATEGY.md) | The eight token levers and how each is measured |
| [docs/04-SECURITY.md](docs/04-SECURITY.md) | Threat model, policy gate, confidential mode, fail-closed rules |
| [docs/05-RELIABILITY-BUDGET.md](docs/05-RELIABILITY-BUDGET.md) | Fallback chains, 402/429/403 handling, degradation ladder |
| [docs/06-INTERFACES.md](docs/06-INTERFACES.md) | Module contracts, written before implementation |
| [docs/07-EVIDENCE.md](docs/07-EVIDENCE.md) | Benchmark design and scenario specs |
| [docs/08-BUILD-24H.md](docs/08-BUILD-24H.md) | The build schedule |
| [docs/09-CONFIG-FIXES.md](docs/09-CONFIG-FIXES.md) | Defects in the original gateway config and the fixes |
| [docs/10-DEMO.md](docs/10-DEMO.md) | Five-minute demo script and the final results table |

## Known limitations

- **Local tool calling needs an instruct model.** The `qwen2.5-coder` family
  returns tool calls as plain text and cannot drive the agent; use
  `qwen2.5:32b-instruct` or a cloud tier.
- **Cloud tiers are configured but not benchmarked.** All published numbers
  come from local models. Real (not synthetic) budget cost and the full
  T2 -> T1 -> T0 degradation ladder need provider keys.
- **Capability ceiling.** `shop_freeship` is solved by neither arm; it was
  left untuned on purpose rather than fitted to the benchmark.
- **CLI only.** There is no IDE extension.

## License

[MIT](LICENSE), Copyright (c) 2026 Essential Engineers.
