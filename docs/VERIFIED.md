# VERIFIED - Live Gateway Findings

Every answer below was produced against a running Bifrost gateway on
**2026-11-14**, not read from documentation. Where the documentation
disagrees with what the gateway does, the gateway wins and the
disagreement is noted.

Environment: Bifrost in Docker on host port **8099**, Redis Stack, native
host Ollama 0.30.10 reached at `host.docker.internal:11434`. No valid cloud
provider keys were present, so cloud-path items are marked accordingly.

---

## The five unknowns

### Q1. Does `fallbacks` work on the `/openai` drop-in route? YES

Sent as a body field. Confirmed with a deliberately broken OpenAI primary
falling back to local Ollama:

```json
{"model": "openai/gpt-4o-mini", "messages": [...],
 "fallbacks": ["ollama/qwen2.5-coder:7b"]}
```

Response was HTTP 200, served by Ollama, with full attribution in the body:

```json
"routing_info": {
  "provider": "ollama", "model": "qwen2.5-coder:7b",
  "is_fallback": true,
  "primary_provider": "openai", "primary_model": "gpt-4o-mini",
  "requested_provider": "openai", "requested_model": "gpt-4o-mini"
}
```

and in headers: `x-bifrost-routing-info-is-fallback: true`,
`x-bifrost-fallback-index: 1`, `x-bifrost-routing-info-primary-provider: openai`.

**The reliability requirement is implementable exactly as designed.**

### Q2. Which response headers carry routing and cache info? ALL OF THEM

The documentation states drop-in routes have no `extra_fields` and does not
name the headers. **Both claims are wrong.** The `/openai` route returns
`extra_fields` in the body *and* sets `x-bifrost-*` headers.

The gateway advertises the full set in `Access-Control-Expose-Headers`.
The ones that matter:

| Header | Carries |
| --- | --- |
| `x-bifrost-request-id` | Always present. Also the log entry ID |
| `x-bifrost-provider` / `-resolved-model` / `-original-model` | Who actually served |
| `x-bifrost-routing-info-is-fallback` | Fallback flag |
| `x-bifrost-routing-info-primary-provider` / `-primary-model` | What was tried first |
| `x-bifrost-fallback-index` | Position in the chain |
| `x-bifrost-cache-hit` / `-cache-hit-type` / `-cache-similarity` / `-cache-threshold` / `-cache-id` | Cache outcome |
| `x-bifrost-upstream-latency-ms` / `-latency-ms` | Timing |
| `x-bifrost-error-type` / `-retry-after-ms` | Failure classification |

Telemetry tiers 1 and 2 both work. Tier 3 is retained for the evidence
harness only.

### Q3. Does Bifrost price our models, so budgets trigger? YES

`GET /api/governance/virtual-keys/quota` works with **only the virtual key**
in `x-bf-vk`, no admin credential, exactly as documented. It returns live
`current_usage` and a `per_model_usage` breakdown.

**Pricing is confirmed present for every cloud model we configured**, read
from the running gateway via `GET /api/models/details`:

| Model | Provider | Input $/MTok | Output $/MTok |
| --- | --- | --- | --- |
| `claude-haiku-5-5` | anthropic | 0.10 | 0.50 |
| `claude-sonnet-5-5` | anthropic | 2.00 | 10.00 |
| `gpt-4o-mini` | openai | 0.15 | 0.60 |
| `gpt-4o` | openai | 2.50 | 10.00 |
| `text-embedding-3-small` | openai | 0.02 | - |
| `qwen*` (all) | ollama | - | - |

The Anthropic rates match Anthropic's published first-party rates exactly.
**No `governance.pricing_overrides` entry is needed**, and the budget
scenario will meter correctly the moment a valid cloud key is present.

The gateway's catalog holds 3398 models; all four of ours are in it.
Querying it needs `?limit=` raised, since `/api/models/base` pages at 20.

Ollama models carry no cost, which is correct and is why **a local-only
session can never trigger a 402**. The budget demo requires a cloud key.

**Still open:** a single live cloud call to confirm `current_usage`
actually increments. The pricing data being present makes this a
formality rather than a risk.

### Q4. Virtual key prefix: `sk-bf-` CONFIRMED

`sk-bf-demo-user-key` in `x-bf-vk` authenticates. The `vk-*` prefix
mentioned in one docs passage is not required here. Auth succeeded even on
requests that were later refused on policy grounds, which is how we know
the key itself was accepted.

### Q5. Ollama configuration: needs BOTH settings

Neither alone is sufficient, and the failure modes are different and
misleading:

```json
"ollama": {
  "keys": [{
    "name": "ollama-local", "value": "",
    "models": ["qwen3:1.7b", "qwen2.5-coder:7b"],
    "ollama_key_config": { "url": "http://host.docker.internal:11434" }
  }],
  "network_config": {
    "base_url": "http://host.docker.internal:11434",
    "allow_private_network": true
  }
}
```

- Without `ollama_key_config.url` on the key: `400 no_key_supports_model`,
  "no keys found that support model". The error blames the model, not the
  missing URL.
- Without `network_config.allow_private_network`: `502
  provider_connection_failed`, "connection to private IP 172.17.0.1 is not
  allowed". It defaults to `false` and blocks all RFC1918 addresses, which
  is every realistic local Ollama.

**No `/v1` suffix is needed.** The base URL is the bare Ollama root.

---

## Defects found live, beyond those in 09-CONFIG-FIXES.md

| # | Defect | Symptom | Fix applied |
| --- | --- | --- | --- |
| 16 | Host port 8080 and 8081 already in use by an unrelated service | Gateway cannot bind | Host port **8099** |
| 17 | `redis:7-alpine` cannot serve the semantic cache | `plugin status: semantic_cache - error`, "search module not available ... FT.* commands required" | **`redis/redis-stack-server`** |
| 18 | `allowed_models: ["*"]` is not honoured as a wildcard | `403 model_blocked`, "Model 'x' is not allowed for virtual key" | Enumerate every model explicitly |
| 19 | `allow_private_network` defaults to false | `502`, private IP refused | Set true on the Ollama provider |
| 20 | `/api` surface locked with no admin account | `401`, "send the setup token in the X-Bifrost-Setup-Token header" | `setup_token` in config and env; send the header for log reads |
| 21 | Host shell `OPENAI_API_KEY` overrides `.env` | Gateway reports "Incorrect API key provided" using a key that is not in `.env` | Compose gives the shell environment precedence over `--env-file`. Unset it or overwrite it deliberately |

Defect 17 is the one that would have cost the most time: the cache plugin
reports `error` in a single log line at startup and otherwise behaves as if
it were simply never hit.

Defect 21 is worth knowing about: there is an `OPENAI_API_KEY` in this
machine's environment that the OpenAI API rejects. It silently wins over
whatever is written in `.env`.

---

## Confirmed working, end to end

- Inference through `/openai/v1/chat/completions` to local Ollama, with
  `x-bf-vk` governance applied.
- **Semantic cache**, after the Redis Stack fix. Two identical requests with
  the same `x-bf-cache-key`:
  - call 1: `{"cache_hit": false, "cache_id": "ea812716-..."}`
  - call 2: `{"cache_hit": true, "hit_type": "direct", "cache_hit_latency": 0}`
  Confirms the cache key is mandatory and that direct-hash mode works with
  `dimension: 1` and no embedding provider. **This means caching is
  available even with no OpenAI key**, which materially de-risks the token
  strategy.
- **Fallback chain**, cross-provider, with full attribution.
- **Quota endpoint**, VK-authenticated.
- **Log lookup** (`GET /api/logs/{request_id}`) with the setup-token header,
  returning provider, model, `fallback_index`, `selected_key_name`,
  `virtual_key_name`, and cost fields. This is the evidence harness's
  independent record.

## Performance note

`qwen2.5-coder:7b` took ~17.7s upstream for a trivial completion. A cache
hit on the same request took **0ms**. That contrast is the demo.

`qwen3:1.7b` is a thinking model: it spent its entire `max_tokens` budget on
reasoning and returned empty content. **Do not use it for tool-calling work
without disabling thinking.** `qwen2.5-coder:7b` is the right local default.

## Still open

One item, and it is no longer a design risk:

1. A live cloud call to confirm `current_usage` increments and a 402 fires
   at the limit. Pricing data is confirmed present for every configured
   model, so this is a formality awaiting a valid provider key.

Resolved since first writing: the OpenAI Python SDK **does** forward
`fallbacks` via `extra_body`, and `with_raw_response` exposes both the
headers and the body's `extra_fields`. The agent uses the SDK.

## Useful API routes on this build

| Route | Auth | Returns |
| --- | --- | --- |
| `/openai/v1/chat/completions` | `x-bf-vk` | Inference, drop-in |
| `/api/governance/virtual-keys/quota` | `x-bf-vk` | Live budget usage |
| `/api/logs/{request_id}` | `X-Bifrost-Setup-Token` | Independent per-request record |
| `/api/models/details` | `X-Bifrost-Setup-Token` | Configured models with pricing |
| `/api/models/base?limit=5000` | `X-Bifrost-Setup-Token` | Full 3398-model catalog |

`/api/models/datasheet/entry` does not exist on this image, though it is in
the `dev` branch source. The released image lags the repository.

---

## Budget enforcement, proven end to end (2026-11-14)

Run with `./.venv/bin/python bench/budget_scenario.py`.

### Making the limit reachable without spending money

Local inference is genuinely free, so Bifrost meters it at `total_cost: 0`
and **no budget can ever trip on a local-only setup.** A synthetic pricing
override makes the limit reachable through the real governance path:

```json
{"id": "po-local-synthetic",
 "name": "SYNTHETIC price for local models (budget demo only)",
 "scope_kind": "virtual_key", "virtual_key_id": "vk-user-budget-demo",
 "match_type": "wildcard", "pattern": "qwen*",
 "request_types": ["chat_completion"],
 "pricing_patch": "{\"input_cost_per_token\":0.0002,\"output_cost_per_token\":0.001}"}
```

`scope_kind` accepts `global`, `provider`, `provider_key`, `virtual_key`,
`virtual_key_provider`, `virtual_key_provider_key`. `match_type` is `exact`
or `wildcard`. It is scoped to a dedicated key so the tiny limit never
affects ordinary development.

**The price is fake; nothing else is.** This must be stated in the demo.

### What the gateway did

```
call  3: HTTP 200
call  4: HTTP 402
  type    : budget_exceeded
  message : Budget exceeded: vk_model_config * budget exceeded:
            0.0547 >= 0.0500 dollars
```

The gateway's own log for the blocked request:

```
status           : "error"
provider         : "ollama"
virtual_key_name : "Budget Demo (tiny limit)"
error_details    : {"type": "budget_exceeded", "status_code": 402}
(no input_tokens, output_tokens or cost fields at all)
```

**No provider was invoked.** That absence is the evidence the requirement
asks for, and it comes from Bifrost's log rather than from the agent.

### Usage accounting is asynchronous

This is the find worth knowing about. `current_usage` **lags real spend by
tens of seconds**:

| Moment | `current_usage` |
| --- | --- |
| During a 25-call burst | $0.0048, frozen |
| ~10s after the burst | $0.02592 (all 25 calls) |

Consequences:

1. **A burst can overshoot the limit.** The 402 fired at $0.0547 against a
   $0.0500 limit, roughly 9% over. The limit is enforced, but not to the cent.
2. A pre-flight quota check is inherently approximate. The agent's 20-second
   poll cache is appropriate; tightening it would not help, because the lag
   is on the gateway's side.
3. **Request logs are flushed asynchronously too.** `GET /api/logs/{id}`
   returns 404 immediately after a request and succeeds seconds later. Any
   evidence harness reading the log must wait or retry.

None of this is documented.

### Agent behaviour on 402

```
  budget: $0.0547 of $0.05 used, $0.0000 left, resets every 1M
  Low budget headroom; pre-emptively using T0_LOCAL.
  Budget exhausted at the gateway (HTTP 402).
  The request was refused before it reached any provider.
  stopped: budget exhausted and no cheaper tier available
```

Pre-flight downgrade, 402 caught, ladder walked, clean stop with the reset
window. **The full T2 -> T1 -> T0 ladder still needs a cloud key**: with a
local-only key, T0 is both the first and last rung.

---

## Approval gate: interactive half, verified by a human (2026-11-14)

A defect found only by a person at a keyboard, invisible to the test suite.

**Symptom.** A real run reported `approvals=0/3`: three `apply_patch`
attempts, all refused, **no prompt ever shown**. The trace gave the reason:

```
tool=apply_patch perm=GATED decision=DENIED reason='no controlling terminal; refused'
```

**Cause.** `open("/dev/tty")` fails under some launchers (`make` here, and
likely remote sessions), and the gate treated any failure as a silent
refusal. A silently-denying gate is indistinguishable from a working one,
so the bug hides itself. The unit tests could not catch it: they have no
terminal either, so "denied" was the expected result there.

**Fix.** Fall back to `sys.stdin` when `sys.stdin.isatty()`. The original
security property is preserved: the threat was an approval read from
*piped* stdin that file contents could influence, and `isatty()` is false
for pipes and redirects. Every prompt failure is now reported on stderr
rather than swallowed.

**Verified by hand** via `make gate-check`, which exercises the gate with
no model and no gateway:

| Answer | decision | reason |
| --- | --- | --- |
| `n` | `DENIED` | `operator declined` |
| `y` | `APPROVED` | - |

`stdin.isatty()` was `True` under `make`, confirming the stdin fallback is
what makes the gate usable there.

**Lesson worth keeping.** Two of the four mandatory requirements are about
refusing things. A component whose job is to say no fails silently by
default, because its failure mode and its success mode look the same. It
needs a positive test that a human performs.

---

## Local model choice and three write-path defects (2026-11-15)

### qwen2.5:32b-instruct is the local tier

| Model | Tool calls | Warm latency |
| --- | --- | --- |
| `qwen2.5-coder:7b` | **as plain text** | - |
| `qwen2.5-coder:32b` | **as plain text** | 61 s (GPU shared with vLLM) |
| `qwen2.5:7b-instruct` | structured | 2-5 s |
| `qwen2.5:32b-instruct` | structured | **0.95 s** (29 s cold load) |

The coder family returns tool calls as text through Ollama's
OpenAI-compatible endpoint at both sizes, so a bigger *coder* model was
never going to fix tool calling. Use the instruct family. New models must
be added to every `allowed_models` list explicitly, since the wildcard is
not honoured.

### Defects the stronger model exposed

Each produced valid-looking success while leaving the file wrong.

1. **Literal escape sequences.** The model sent `replace` containing a
   backslash-n *pair* instead of newlines. The whole fix landed on one line
   and the file stopped parsing; the agent then reported "Fixed".
   Decoded now, but only when the string contains no real newline, so
   genuine `"x\ny"` string literals in multi-line code are left alone.
2. **No syntax guard.** Nothing stopped a write that broke the file.
   `apply_patch` and `write_file` now `ast.parse` any `.py` result and
   refuse an unparseable one, returning the error to the model.
3. **Indentation on the exact-match path.** `find="    return a / b"`
   (indented) with an unindented `replace` matched exactly and swapped an
   indented line for column-0 code, emptying the function. The replacement
   is now re-based to the indentation of the text it replaces, keeping its
   internal structure.

### Benchmark fairness fix

With `loop_limits` off, the naive arm had a 60-turn ceiling against the
optimized arm's 18. One naive run used 51 turns and 227k tokens. That made
the baseline artificially bad, which this project committed not to do.
Both arms now share `max_turns`; the lever controls only no-progress
detection and call deduplication.

---

## Cache hits report the original usage (2026-11-15)

Two identical requests with the same `x-bf-cache-key`:

```
call 1: hit=False                usage={'prompt_tokens': 34, 'completion_tokens': 2}
call 2: hit=True  type=direct    usage={'prompt_tokens': 34, 'completion_tokens': 2}
```

A hit returns the **cached response's original usage**. Summing `usage`
therefore shows no saving from the cache at all. The agent counts tokens of
hit calls as `cached_tokens` and reports `provider_tokens` as total minus
cached. Undocumented, and the reason a naive cache benchmark reads as a
wash.

---

## Benchmark repeats at temperature 0 are not independent (2026-11-15)

The first 32B benchmark reported cells like "3/3 passed, identical tokens
every run". That looked like reliability. It was not.

`zero_check` with all levers on produced two fixed trajectories:
**8,304 tokens and pass** in one batch, **6,916 tokens and fail** in
another, with identical configuration. At temperature 0, consecutive runs of
the same input are replays, not samples: Ollama reuses its prompt cache, so
repeat 2 and 3 reproduce repeat 1, and which trajectory a batch lands on
depends on numerical state that the configuration does not control.

So "3/3" meant one sample, shown three times. Success rates derived from
it overstated confidence, and the per-lever attribution built on it was
noise. The flawed attribution run was stopped and discarded.

**Fix.** The benchmark samples at `BENCH_TEMPERATURE` (default 0.7), so
repeats are independent draws. Confirmed: four runs of the same cell now
give 5,073 / 2,848 / 2,848 / 2,857 tokens. The agent itself still defaults
to temperature 0 for interactive use; only the measurement samples.

All scenario-1 numbers dated before this note were produced at temperature
0 and are superseded by the evidence pack generated after it.

## Budget reset before the demo (2026-11-15)

The budget scenario had been passing trivially: `bud-budget-demo` was left
at $0.05472 from earlier runs, so the 402 arrived at call 1 and proved
nothing. `PUT /api/governance/virtual-keys/{id}` with only
`{"reset_budget_usage": true}` returns success but does NOT zero usage. It
works only when the budgets are resubmitted alongside it:
`{"reset_budget_usage": true, "budgets": [{"max_limit": 0.05, "reset_duration": "1M"}]}`.
`bench/budget_scenario.py` now does this first, and reports FAIL if the 402
arrives before any call was served. Verified run: 6 calls served, 402 at
call 7 (`0.0576 >= 0.0500`), gateway log shows no provider invocation.

The quota endpoint's `current_usage` still reads $0 during the run while
enforcement has already counted the spend (the async flush noted above).
Quote the 402 message for the figure, not the quota column.

## Per-lever attribution is inconclusive (2026-11-15)

Ablation, one lever off at a time, on zero_check and add_power, 3 repeats
at temperature 0.7. No lever separated from noise. add_power runs are
bimodal (3 turns ~2,850 tokens or 5 turns ~5,100) in every configuration,
including all levers on; zero_check with all levers on passed only 2/3.
Evidence: `evidence/scenario1-benchmark-20261115-003350.json`. A per-lever
ranking would need far more repeats than the time allows; the levers are
claimed only jointly.

## Run-to-run variance in scenario 1 (2026-11-15)

Two identical 5-repeat proof runs gave 39% and 75% fewer tokens per
completed task overall. The gap is single runaway runs (one naive
zero_check run: 197,305 tokens, 18 turns). The demo quotes both packs
pooled to 10 runs per cell, with medians as the typical-run figure.

## Runaway runs were failed edits (2026-11-15)

Traces of the three expensive optimized runs (29,557 / 21,805 / 14,375
tokens) show the same thing: `apply_patch` failing 8 to 16 times in a row.
Shapes found, all now handled in `agent/tools/write.py` with a regression
test each in `bench/test_write.py`:

1. `find` omitted a comment-only line inside the target block. Fuzzy match
   now has a second pass that skips comment lines in the file; a pure
   insertion keeps the comment.
2. `find` started mid-line (`return a / b`) while `replace` spanned lines
   with the first line unindented. The match is widened to the whole line
   and the block re-indented.
3. `find` carried read_file line-number prefixes. Stripped when every line
   has one.
4. A rename retried edits already applied, answered "not found" each time.
   Now answered "already applied". `replace_all` added for renames.

The loop limit never fired because each failed patch was followed by a
successful read, which counts as progress. Under the loop_limits lever the
agent now hints after 2 consecutive failed edits and stops after 5.

## Agent commits landed in the project's own history (found 2026-11-15)

All 8 commits in this repository dated 2026-11-14 ("Rename multiply to
times...", "Add power function...") were made by the agent during
benchmark runs, not by a person. Benchmark task repos lived under
`.bench-work/` inside the project, so they had no git repository of their
own and `git_op commit` acted on the parent. Nothing was committed on
2026-11-15 only because `.bench-work` had since been gitignored, but
`git add -A` from a task directory would still have staged the whole
project.

Fixed three ways:
1. `git_op` refuses unless the working directory is itself the top level
   of a git repository (`git rev-parse --show-toplevel`), and `add` is
   scoped to `-- .`. Test: `bench/test_tasks.py::test_git_op_refuses_nested_working_dir`.
2. Task repos are materialised under the system temp directory, each as
   its own git repository with one commit.
3. `git_op` is only offered when the task mentions git. Offered on every
   task, the model committed unprompted and, in one completion, repeated
   status/diff/commit four times (34,200 tokens in 4 turns). Identical tool
   calls within one completion now run once, capped at 8 per turn.

## Edits of unseen code (2026-11-15)

Held-out task shop_bulk_bug, first run: the optimized arm patched text it
had never read (`if quantity > 10:`), requested lines 37-45 of 15- and
35-line files and received an empty body, and once sent exactly the right
`find` to the wrong file. Fixes: an out-of-range read of a small file
returns the whole file; a not-found patch names the other files that do
contain the text (never edits them); the system prompt forbids patching
text not seen in a tool result.

## File memory claimed pruned content was visible (2026-11-15)

A re-read of an unchanged file was answered "contents already provided
above" even when the earlier copy had been elided by pruning, so the model
was told it could see something it could not. File memory now consults the
ledger's pruning horizon and resends when the earlier copy is gone.

## Held-out shop tasks: the levers hurt (2026-11-15)

First run of the three shop tasks (21-file repository, written before any
results were seen), 5 repeats per cell, code as of 01:58. Evidence:
`evidence/scenario1-benchmark-20261115-015810.json`.

| | naive | optimized |
| --- | --- | --- |
| 4 small calc tasks, passed | 18/20 | 20/20 |
| 3 shop tasks, passed | 8/15 | 1/15 |
| all 35 runs, tokens per completed task | 18,144 | 21,527 (19% more) |

On the small tasks the optimized arm was 9-18% cheaper per run. On the
realistic repository it mostly failed: it edited code it had not read,
paged one file in 10-line windows, and patched imports one at a time
during a rename. The levers had been tuned on tasks too small to need
context. Fixes since (see entries above) were made after this run, so
the next run is no longer a held-out measurement of the shop tasks; it
is reported as such.

Checker correction made after this run: the shop checkers rejected any
change to a test file, which failed a run that had only ADDED a test.
They now require every original test function to be unchanged and allow
additions.

## Shop ablation: no lever is the culprit; n=3 cannot rank them (2026-11-15)

One lever off at a time, shop_bulk_bug and shop_freeship, 3 repeats.
Evidence: `evidence/scenario1-benchmark-20261115-024233.json`.

shop_bulk_bug passed 3/3 with cache, file memory, retrieval, pruning or
loop limits off, 2/3 without tool disclosure, and 1/3 both with all levers
on and with routing off. Routing off changes nothing in the benchmark
(every tier is the same local model), so 1/3 to 3/3 is the spread of one
configuration: sampling noise dominates at this sample size. Diff writes
off scored 0/3, but that cell ran with a 1,200-token output cap that made
whole-file rewrites of large files impossible; the cap is now 4,096 when
diff writes are off.

shop_freeship scored 0/27 in every configuration, which pointed at the
tools rather than the levers. The cause: the model double-escapes
arguments (`\"WELCOME5\"`, `\"\"\"`), and only `\n` was being decoded, so
`find` never matched and `replace` failed to parse. All standard escapes
are now decoded, with the arguments as sent tried first.

## Replies that promise instead of act (2026-11-15)

Two shapes, both from local-model traces, both now answered with a push
back (at most two per task): a plan with no edit at all ("We will add
FREESHIP... Let's make these changes."), and a reply after a partial edit
that ends by announcing the next step ("Let's execute the tests."). A
final answer of "no change is needed, because..." is accepted after one
push.

## Baseline arm fairness (2026-11-15)

With diff writes actually gated, the naive arm's system prompt still told
the model to use apply_patch, which it did not have: it scored 0/20 on the
small tasks, describing edits instead of making them. That run was
discarded. The system prompt now names only the editing tools that are
offered.

## Pruning step calibration (2026-11-15)

Optimized arm, 5 runs each, `AGENT_PRUNE_STEP` 1 vs 4:

| task | step | passed | tokens / completion | s / turn |
| --- | --- | --- | --- | --- |
| shop_bulk_bug | 1 | 5/5 | 29,230 | 5.02 |
| shop_bulk_bug | 4 | 5/5 | 24,845 | 4.82 |
| rename_symbol | 1 | 5/5 | 8,647 | 3.34 |
| rename_symbol | 4 | 5/5 | 4,712 | 3.50 |

No loss in success or tokens (the differences are within run-to-run noise),
and no measurable local latency gain. The default stays 1, which is what
every published number used. Set `AGENT_PRUNE_STEP=4` with cloud models:
a prompt prefix that stays byte-identical for four turns is what lets a
provider prompt cache hit; that benefit is unmeasured here for lack of a
cloud key.

## Tavily web search, checked 2026-11-15

Live against `POST https://api.tavily.com/search`. The key in the request
body still returns 200, but the current API reference documents only
`Authorization: Bearer <key>`, so the tool now sends the header and keeps
the key out of the body. `chunks_per_source: 1` cuts the returned content
from about 4,100 to 1,400 characters for 4 results at the same 1 credit
(`include_usage` reports it); results are cut to 400 characters each
anyway, so the extra chunks were only latency. Results are labelled as
third-party text. The tool is registered only when `TAVILY_API_KEY` is set,
so a keyless install no longer sends a schema the model can only get an
error from. No trace before this date shows a `web_search` call: the
benchmark tasks never needed it.
