# Himinbjorg

**A coding agent that gets the job done on the fewest tokens.**

[![CI](https://github.com/dipankarchettri/Himinbjorg/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dipankarchettri/Himinbjorg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Gateway: Bifrost](https://img.shields.io/badge/gateway-Bifrost-6E40C9)](https://github.com/maximhq/bifrost)
[![VS Code extension](https://img.shields.io/badge/VS%20Code-extension-007ACC?logo=visualstudiocode&logoColor=white)](#vs-code)

Himinbjorg fixes bugs, adds features and refactors real repositories from
your terminal or VS Code. Every model call goes through the
[Bifrost](https://github.com/maximhq/bifrost) LLM gateway, and the agent
spends as few tokens as it can: it reads what it needs, edits with small
diffs, keeps the context lean and routes to the cheapest model that can
do the job. Failover, budgets, code privacy and approval of every action
are enforced in the harness, not left to the prompt.

- **Fewer tokens, same results.** 84% fewer tokens per completed task
  than the same agent with its levers off, and 71% to 82% lower cost on a
  paid provider ([results](#results)).
- **You approve every change.** Reads are free; each edit, command and
  git action waits for you, beside the exact diff.
- **Keeps working when providers fail.** Fallback across OpenAI,
  Anthropic, Gemini, Groq and OpenRouter to a local model, saying which
  model took over.
- **Stays within budget.** Per-user budgets are enforced by the gateway
  before a call reaches any provider.
- **Keeps confidential code local.** A confidential repository is served
  only by local models, whatever the prompt says.

## Results

Gateway-reported, on 7 benchmark tasks, 10 runs per task in each arm.
*naive* is the same agent with every token lever off.

| Model | Passed (levers on vs naive) | Per completed task |
| --- | --- | --- |
| Qwen 2.5 32B (local) | 57/70 vs 31/70 | 84% fewer tokens (13,913 vs 88,196) |
| Gemma 31B (local) | 63/70 vs 64/70 | 39% fewer tokens |
| Claude Haiku 5.5 (OpenRouter) | 67/70 vs 68/70 | 71% lower cost ($0.00287 vs $0.00978) |
| Claude Haiku 5.5, context kept whole (`AGENT_PRUNE_MODE=budget`) | 69/70 vs 68/70 | 82% lower cost ($0.00171) |

On 28 outside programs (QuixBugs) it passed 84/84 against 82/84 with 24%
fewer tokens. Every table, its conditions and the runs that went the
other way are in [docs/RESULTS.md](docs/RESULTS.md), backed by the
evidence packs in [`evidence/`](evidence/).

## Quick start

You need Docker with Compose, Python 3.11+, and either
[Ollama](https://ollama.com) with a local model or a cloud provider key
(Gemini, Groq or OpenRouter).

```sh
git clone https://github.com/dipankarchettri/Himinbjorg.git himinbjorg && cd himinbjorg
ollama pull qwen2.5:32b-instruct     # local coding model, about 20 GB (skip with a cloud key)
cp .env.example .env                 # add provider keys here, all optional
make venv                            # .venv with the agent installed
make up                              # Bifrost gateway on :8099 + Redis Stack
make agent TASK="what does divide() do wrong?" REPO=bench/seed
```

To use cloud models instead of a local one, set the tiers in `.env`, for
example `AGENT_MODEL_CHEAP=gemini/gemini-3.1-flash-lite`. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Usage

Put `himin` on your `PATH` and your settings in the user config
directory, then run it from any repository. The gateway must be up
(`make up`).

```sh
mkdir -p ~/.local/bin ~/.config/himinbjorg
ln -s "$PWD/.venv/bin/himin" ~/.local/bin/himin
cp .env ~/.config/himinbjorg/env

cd ~/code/some-project
himin                                                  # interactive session
himin "fix the failing test in tests/test_orders.py"   # one task, then exit
himin --confidential "explain the auth flow"           # local models only
```

In a session each task continues the same conversation, so "now add a
test for it" has the context. The answer streams as it is written, and
each task ends with its tokens and gateway-priced cost.

| Command | Effect |
| --- | --- |
| `/cost` | Session token and cost totals |
| `/undo` | Revert the agent's most recent edit (a whole rename counts as one) |
| `/reset` | Start a fresh conversation |
| `/exit`, Ctrl-D | Quit; Ctrl-C interrupts a task and keeps the session |

At each approval you see the exact diff, computed by a dry run against
the file on disk. Answer `y`, `n`, or `a` to approve every file edit for
the rest of the session; commands and git still ask each time.
`himin --help` lists every flag.

### VS Code

```sh
make extension                                   # builds extension/himinbjorg.vsix
code --install-extension extension/himinbjorg.vsix
```

Or install `himinbjorg.vsix` from the
[releases](https://github.com/dipankarchettri/Himinbjorg/releases). Set
**Himinbjorg: Command** (a User setting) to the full path of `himin`,
open and trust a repository, then click the Himinbjorg icon in the
activity bar.

![An edit waiting for approval: the exact diff beside a modal dialog](docs/images/vscode-approval.png)

Approvals come only from native modal dialogs, never from the chat view
that shows model text. Failover and budget changes appear as
notifications, and the status bar shows the session's cost. More in
[extension/README.md](extension/README.md).

## How it works

The agent owns the loop, the tools, the context and every security
decision; Bifrost owns routing, caching, provider fallback and budget
enforcement. The token savings come from eight levers, each switchable
so it can be measured against the baseline:

| Lever | What it does |
| --- | --- |
| Caching | Gateway cache for repeated calls, plus provider prompt-cache breakpoints |
| Tiered routing | The cheapest capable model first; escalate only when a run is stuck |
| File memory | A file is sent once; a re-read of an unchanged file is not repeated |
| Targeted retrieval | grep and line ranges widened to the enclosing function |
| Context pruning | Old tool output is dropped, or kept whole and summarised near the budget |
| Tool disclosure | git and rename tools are offered only when the task needs them |
| Diff writes | Small patches with tolerant matching instead of whole-file rewrites |
| Loop discipline | Limits and nudges for failed edits, stalls and unanswered tasks |

Read more: [architecture](docs/02-ARCHITECTURE.md),
[token strategy](docs/03-TOKEN-STRATEGY.md),
[security model](docs/04-SECURITY.md),
[reliability and budgets](docs/05-RELIABILITY-BUDGET.md).

## Documentation

| Document | Contents |
| --- | --- |
| [Results](docs/RESULTS.md) | Every benchmark and scenario result, with how to reproduce it |
| [Configuration](docs/CONFIGURATION.md) | Every setting, and the gateway configuration |
| [Verification log](docs/VERIFIED.md) | Facts checked against a live gateway, run by run; it overrides the other docs where they disagree |
| [Architecture](docs/02-ARCHITECTURE.md) | Components, request path, telemetry |
| [Token strategy](docs/03-TOKEN-STRATEGY.md) | The eight levers and how each is measured |
| [Security](docs/04-SECURITY.md) | Threat model, approval gate, sandbox, confidential mode |
| [Reliability and budgets](docs/05-RELIABILITY-BUDGET.md) | Fallback chains, 402/429/403 handling, the degradation ladder |
| [Evidence](docs/07-EVIDENCE.md) | Benchmark design and scenario specifications |
| [Bifrost research](docs/01-RESEARCH.md) | Gateway API facts from its documentation, with citations |

## Known limitations

- **Local tool calling needs an instruct model.** The `qwen2.5-coder`
  family returns tool calls as plain text; use `qwen2.5:32b-instruct`, a
  model of similar strength, or a cloud tier.
- **Benchmarks are small.** The own tasks are small repositories, and the
  10-runs-per-task cloud check covers one paid provider.
- **The sandbox is Linux-only** and does not yet cover the git tool or
  UDP.
- **The extension needs the agent installed.** It is a front end for
  `himin --serve` and the gateway, not a self-contained package.

## Contributing

Bug reports, results on other models and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests and the benchmark,
and [SECURITY.md](SECURITY.md) to report a vulnerability privately.

## Acknowledgements

Built by Essential Engineers (Anusha A, J Bhuvanesh and Dipankar Chettri)
for the Geek Room AI Global Hackathon, theme *Build a Coding Agent That
Gets the Job Done on the Fewest Tokens*, with
[Bifrost](https://github.com/maximhq/bifrost) by Maxim AI and
[Tavily](https://tavily.com) web search. The name is Heimdall's hall at
the end of the Bifrost bridge, where the gate is kept.

## License

[MIT](LICENSE). Copyright (c) 2026 Essential Engineers.
