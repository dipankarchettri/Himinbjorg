# Contributing

Thanks for taking a look. Bug reports, benchmark results on other models
and pull requests are all welcome.

## Set up

```sh
git clone https://github.com/dipankarchettri/Himinbjorg.git himinbjorg && cd himinbjorg
cp .env.example .env     # provider keys optional
make venv                # .venv with the agent installed in editable mode
make up                  # Bifrost on :8099 + Redis Stack (Docker)
make doctor              # checks the setup and says what to fix
```

Unit tests need neither the gateway nor a model; everything else does.

## Test

```sh
make test            # unit tests: security, edit tools, agent loop, task checkers
make gate-check      # the approval prompt on a real terminal, no model involved
make extension-test  # extension: end to end against the agent, then inside VS Code
```

A change to the agent's behaviour should come with a test in
`bench/test_*.py`, and a security fix with a case in
`bench/test_security.py`.

## Measure

Claims about tokens or cost are measured, not estimated:

```sh
make bench           # token benchmark, naive vs optimized
make pool            # re-summarise the committed evidence packs, no gateway
make proof           # every scenario into evidence/<timestamp>/ (about an hour)
make scenario-failover   # also: scenario-budget, -privacy, -approval, -cache
```

The failover and budget scenarios also use a small local model
(`ollama pull qwen2.5:7b-instruct`). Benchmark task repositories are
created in the system temp directory, each as its own git repository, so
runs never touch the checkout. Report results with the model, the number
of runs per arm, and the evidence pack, as in
[`docs/RESULTS.md`](docs/RESULTS.md).

## Continuous integration

GitHub Actions runs the Python suite on 3.11 to 3.14 on Linux and 3.14 on
macOS, re-summarises the committed evidence, builds and smoke-installs the
wheel, compiles and packages the extension, and validates the Compose
file, on every push and pull request. Checks that need a live gateway and
a model (`make extension-test`, `make bench`, `make proof`) run locally.
Pushing a `v*` tag builds the wheel, sdist and VSIX and attaches them to a
GitHub Release. The agent and the extension share one version: set it in
`pyproject.toml` and with `npm version <x.y.z> --no-git-tag-version` in
`extension/`, then tag `v<x.y.z>`. `bench/check_versions.py`, run by CI
and the release workflow, fails when they disagree. Dependabot keeps Python, npm, Actions and Compose
dependencies current.

## Project layout

```text
agent/            the agent
  main.py         CLI entry point and interactive session
  server.py       editor server for the VS Code extension (JSON lines)
  loop.py         turn loop, prompts, escalation, nudges
  llm.py          gateway client, streaming, failover attribution, prompt caching
  config.py       settings and confidential-mode resolution
  telemetry.py    usage and gateway-priced cost
  memory/         history ledger and file memory
  security/       approval gate, egress policy, redaction, sandbox
  tools/          read, write, shell and git tools, tool registry
bench/            benchmark, scenarios, unit tests, fixture repositories
extension/        VS Code extension (TypeScript)
infra/            Docker Compose and Bifrost configuration
docs/             design, research, results and verification notes
evidence/         committed evidence packs
```

## Style

Match the code around you. Comments explain why, and a fix for something
that was measured says what was measured. Keep prompts and tool
descriptions short: they are billed on every turn.
