# Results

Every number here is reported by the Bifrost gateway (token usage and
per-request cost from its logs), not estimated by the agent. Each row
names its evidence pack in [`evidence/`](../evidence/); the conditions,
the runs that went the other way and what was changed after them are in
[`VERIFIED.md`](VERIFIED.md).

**How to read them.** A benchmark task is run several times in each arm
on a fresh copy of its repository, and the task's own tests decide pass
or fail. *naive* is the same agent with every token lever off; the other
arms turn them on. *Tokens per completed task* is all tokens spent
divided by the runs that passed, so failed runs count against the arm
that paid for them.

## Token efficiency

| Benchmark | Passed | Tokens per completed task |
| --- | --- | --- |
| 7 tasks x 10 runs, Qwen 2.5 32B, T=0.7 (headline) | 57/70 vs 31/70 naive | 13,913 vs 88,196 (84% fewer) |
| Same 7 tasks x 10 runs, Gemma 31B (local vLLM) | 63/70 vs 64/70 naive | 23,513 vs 38,536 (39% fewer) |
| Same 7 tasks x 10 runs, Claude Haiku 5.5 (OpenRouter), pruning old turns | 67/70 vs 68/70 naive | 34,676 vs 60,068 (42% fewer) |
| Same, keeping the context whole (`AGENT_PRUNE_MODE=budget`) | 69/70 | 36,940 (38% fewer) |
| Same 7 tasks x 10 runs, Qwen 3.8 27B (local vLLM) | 70/70 vs 70/70 naive | 22,499 vs 35,046 (36% fewer) |
| Same, keeping the context whole (`AGENT_PRUNE_MODE=budget`) | 70/70 | 36,048 (3% more) |
| QuixBugs, 28 outside programs x 3 runs, Gemma 31B | 84/84 vs 82/84 naive | 15,339 vs 20,078 (24% fewer) |
| Identical re-run of a task | | 4,402 -> 0 provider tokens, every call served from the gateway cache |

On the 21-file repository alone the headline run passed 20/30 against
3/30 naive.

- **Keeping the context whole pays only with a prompt cache.** On Haiku
  it was the cheapest arm; on a local model, which bills every token
  alike, it used more tokens than naive on the longest task. Pruning
  old turns is the default.
- **The saving shrinks as the model gets stronger.** Gemma, Haiku and
  Qwen 3.8 pass nearly every task with the levers off, so there are few
  failed naive runs to charge; the levers then save tokens but add no
  passes.
- **Where every tier is the same model** (Gemma, QuixBugs, Haiku, Qwen 3.8),
  routing contributes nothing.

## Real issues: SWE-bench Verified subset

20 real GitHub issues (Django, SymPy, Sphinx, scikit-learn, Matplotlib,
xarray, pytest), chosen by a fixed seed before any run from Verified's
easiest, single-file fixes, graded by the official harness. Qwen 3.8
27B on every tier, 3 runs per issue per arm.

| Arm | Resolved | Tokens per resolved issue |
| --- | --- | --- |
| naive | 30/60 | 475,929 |
| levers on | 37/60 | 165,409 (65% fewer) |
| levers on, repeated on a later build | 35/60 | 193,501 (59% fewer) |

Corrected 2026-11-21: the checkouts kept the upstream history, and three
resolved runs (two naive, one in the repeat) had read the fix with `git
show`; they count as not resolved here. The harness now gives the agent
only the history up to the issue's commit.

Five naive runs outgrew the model's 32k window and were refused; no
levers-on run did. The repeat changed the opening listing of large
repositories and did not rerun the naive arm; it took 1.8 more turns
per run, and whether from that change or from chance is not separated. This is a subset of the easiest issues with a 27B
model, not comparable with full-benchmark leaderboard numbers. The per
issue table is in `VERIFIED.md`.

## A real project: pallets/click

Six bugs the [click](https://github.com/pallets/click) maintainers fixed
in 2026. Click is 12,800 lines of source and 16,500 of tests. Each run
starts at the commit before the fix, with the bug report as the prompt.
Afterwards the maintainers' tests from the fix commit are added and run,
then the rest of click's suite (1,494 to 2,111 tests): a bug counts as
solved only if both pass. Claude Haiku 5.5 through OpenRouter on every
tier, 5 runs per bug per arm, 30-turn limit, 60 runs in all.

| Bug (maintainers' fix) | Solved, naive vs levers on | Tokens per solved bug | Cost per solved bug |
| --- | --- | --- | --- |
| `write_usage` prints nothing without args (`0551bf5`) | 5/5 vs 5/5 | 127,298 → 30,829 (76% fewer) | $0.0165 → $0.0022 |
| `echo(b"")` raises `TypeError` (`4d3db84`) | 4/5 vs 5/5 | 94,112 → 38,546 (59% fewer) | $0.0116 → $0.0029 |
| `FuncParamType` hides the `ValueError` message (`fc6c7c4`) | 5/5 vs 5/5 | 58,446 → 14,290 (76% fewer) | $0.0071 → $0.0010 |
| Double brackets for an optional `Choice` (`762c97e`) | 5/5 vs 5/5 | 86,507 → 42,446 (51% fewer) | $0.0110 → $0.0036 |
| `show_envvar` with a list or empty `envvar` (`c53d373`) | 5/5 vs 5/5 | 93,721 → 95,887 (2% more) | $0.0115 → $0.0072 |
| `Argument.get_help_record` returns `None` (`05f6fd0`) | 5/5 vs 5/5 | 68,020 → 37,046 (46% fewer) | $0.0082 → $0.0026 |
| **All** | **29/30 vs 30/30** | **87,807 → 43,174 (51% fewer)** | **$0.0110 → $0.0032 (70% cheaper)** |

The median run took 6.5 turns and 25 s with the levers on, against 11
turns and 41 s without; the 60 runs cost $0.42 in all. Before any run,
`realrepo_run.py check` confirmed that each bug fails its tests and that
the maintainers' fix passes the whole suite.

- **Prompts.** Three come from an issue, as the user wrote it; where the
  report included a suggested patch, the patch was cut. Three come from
  the fix's pull request, rewritten as a bug report, so they describe
  the expected behaviour more fully than a user would. One prompt adds a
  sentence for a case the hidden test checks (an empty `ValueError`
  message). Every prompt is in `bench/realrepo_run.py`.
- **Rate limit.** Repeats 4 and 5 first hit the dev gateway's limit of
  2 million tokens an hour, so 24 runs stopped before any model reply.
  They say nothing about the agent and were run again
  (`--resume`); `results.json` records the count.
- **The one regression.** On `envvar-display` the levers-on arm used 2%
  more tokens per solved bug, though it cost 37% less: more of its
  prompt was read from the provider's cache.
- **Found on the way.** Working on click, Haiku said its first searches
  had returned nothing. The grep tool ran in basic regex syntax, where
  `a|b` is not alternation and `\{` is an error that was reported as a
  timeout; about a fifth of recorded grep calls used `|`. It was fixed
  before this run; the other results on this page were measured with
  the bug.

## Against another agent: opencode

opencode 2.3.2 on the same 7 tasks, through the same gateway to the same
Qwen 3.8 model, 5 runs per task, its tokens read from the gateway's log.

| Agent | Passed | Tokens per completed task |
| --- | --- | --- |
| opencode | 35/35 | 126,759 |
| Himinbjorg | 70/70 | 22,499 (82% fewer) |

Fewer tokens on every task, by 2.7 to 6.9 times. opencode ran on its own
defaults, so this is not a controlled comparison, and it counts tokens,
not dollars: with a provider prompt cache, much of opencode's repeated
prompt would be billed at a tenth.

## Cost on a paid provider

Claude Haiku 5.5 through OpenRouter, 7 tasks x 10 runs per arm, priced by
the gateway. $1.06 for all 210 runs.

| Arm | Passed | Dollars per completed task |
| --- | --- | --- |
| naive | 68/70 | $0.00978 |
| levers on, pruning old turns | 67/70 | $0.00287 (71% cheaper) |
| levers on, context kept whole | 69/70 | $0.00171 (82% cheaper) |

Both levers-on arms are cheaper than naive on every task. The two save
about the same tokens, but keeping the context whole leaves more of each
request to be read from the provider's prompt cache, billed at a tenth.

## Reliability, budgets, privacy, approval

| Scenario | Result |
| --- | --- |
| Failover | PASS: broken OpenAI -> Anthropic -> local, each switch announced with the model now serving |
| Failover with real provider errors | Gemini Pro 429 -> Groq gpt-oss-120b, attributed |
| Budget | PASS: usage reset to $0, 6 calls served, then 402 before any provider |
| Budget at real prices (Gemini 3.5 Flash-Lite) | PASS: $0.005 budget, 8 calls served, 402 on call 9; overshoots by at most one call (14%) |
| T2 -> T1 -> T0 ladder under real budgets | PASS in 2 of 3 runs: each cloud provider skipped for `budget_exceeded` in the gateway's log, all three tiers served. The third failed when Groq rejected malformed output while still under budget |
| Privacy | PASS: 6 structural checks under an adversarial prompt |
| Approval | PASS automated; the interactive half verified by a human |

## Earlier cloud runs

| Run | Result |
| --- | --- |
| Gemini, 7 tasks x 3 runs per arm (Flash-Lite cheap, 3.5 Flash capable, free tier) | 18/21 vs 16/21 passed; 67% fewer tokens and 76% lower cost per completed task than everything on 3.5 Flash |
| Claude Haiku, prompt caching | 59-81% of prompt tokens read from cache; input cost 31-57% of uncached. A stable prefix (`AGENT_PRUNE_STEP=4`, now the default) cut the rest by about a quarter |

## Caveats

- The budget scenario in the original evidence pack uses synthetic
  pricing for local models; the real-price runs are in the table above.
- `shop_freeship` is solved by neither arm on Qwen 2.5 32B; Gemma 31B
  and Claude Haiku solve it in 7 to 9 runs of 10. It was left untuned on
  purpose rather than fitted to the benchmark.
- The first run of the 21-file tasks went the other way (optimized 1/15,
  naive 8/15). What changed after it, and why, is in `VERIFIED.md`.
- The `docstring` task's grader wants a docstring of 15 characters or
  more, so a correct "Divide a by b." (14) fails, in both arms.

## Reproduce

```sh
make pool    # the headline table from the committed packs, no gateway needed
make bench   # a fresh benchmark, naive vs optimized
make proof   # every scenario into evidence/<timestamp>/ (about an hour)
python bench/pool.py evidence/scenario1-benchmark-20261119-224023.json    # the Qwen 3.8 table
python bench/pool.py --latest-wins evidence/scenario1-benchmark-20261119-204844.json \
    evidence/scenario1-benchmark-20261119-221735.json \
    evidence/scenario1-benchmark-20261119-222203.json    # the Haiku table
```

To benchmark another model, set every tier in the environment. The Haiku
run used the same model on all of them:

```sh
M=openrouter/anthropic/claude-haiku-5.5
BENCH_MODEL_CHEAP=$M BENCH_MODEL_CAPABLE=$M AGENT_MODEL_LOCAL_CODE=$M AGENT_MODEL_LOCAL_SMALL=$M \
    python bench/benchmark.py --tasks all --arms naive,optimized,keepctx --repeats 10
```

SWE-bench needs Docker and its own environment, since the harness pins
its dependencies:

```sh
python3 -m venv .venv-swebench && .venv-swebench/bin/pip install swebench==5.0.2 pyarrow
.venv-swebench/bin/python bench/swebench_run.py select --n 20     # writes the issue texts; the subset is committed
.venv/bin/python bench/swebench_run.py run --arms naive,optimized --repeats 3
.venv-swebench/bin/python bench/swebench_run.py grade evidence/swebench-<stamp>
```

The click run clones click into `bench/.realrepo` and needs only
pytest, which it installs there:

```sh
python bench/realrepo_run.py check        # each bug fails its tests; each real fix passes the suite
M=openrouter/anthropic/claude-haiku-5.5
BENCH_MODEL_CHEAP=$M BENCH_MODEL_CAPABLE=$M AGENT_MODEL_LOCAL_CODE=$M AGENT_MODEL_LOCAL_SMALL=$M \
    python bench/realrepo_run.py run --arms naive,optimized --repeats 5
```

opencode, pointed at the gateway by an `opencode.json` in its own
directory (the script's docstring has the setup):

```sh
OPENCODE_BIN=... OPENCODE_HOME=... BF_VK=... python bench/opencode_compare.py --tasks all --repeats 5
```

