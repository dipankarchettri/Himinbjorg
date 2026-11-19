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
zero_check run: 197,305 tokens, 18 turns). The published table
(`docs/07-EVIDENCE.md`) pools both packs to 10 runs per cell, with
medians as the typical-run figure.

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

## Cloud providers through Bifrost, checked 2026-11-15

Three keys added: Gemini (Google AI Studio, free tier), OpenRouter and
Groq (free tier). Every model below was probed through the gateway with
a tool-calling request, and priced by the gateway's own log record.

| Model (as the agent names it) | Tool call | Gateway cost of the probe |
| --- | --- | --- |
| `gemini/gemini-3.1-flash-lite` | yes | $0.000043 |
| `gemini/gemini-3.5-flash-lite` | yes | $0.000064 |
| `gemini/gemini-3.5-flash` | yes (thinking tokens billed as output) | $0.000591 |
| `openrouter/anthropic/claude-haiku-5.5` | yes | $0.000064 (equals OpenRouter's billed amount) |
| `openrouter/anthropic/claude-sonnet-5.5` | yes | $0.001282 |
| `openrouter/deepseek/deepseek-v4.1-flash` | yes | $0.000094 |
| `groq/openai/gpt-oss-120b` | yes | $0.000046 |
| `groq/openai/gpt-oss-20b` | yes | $0.000042 |

Not usable on this key: `gemini-3.8-flash` (no response in 150 s, direct
to Google as well), `gemini-2.5-flash` / `gemini-2.5-pro` (404, closed to
new users), the Pro models (429, no free-tier quota), Groq
`qwen/qwen3.8-27b` (400, tool-call validation failed).

**Prompt caching survives the gateway.** The agent's message-level
`cache_control` markers, sent through Bifrost to
`openrouter/anthropic/claude-haiku-5.5` with a 10,421-token prompt:
call 1 wrote 10,417 cached tokens, call 2 read 10,417. The same prompt
without markers cached nothing. Bifrost's OpenRouter page says both that
the markers pass through and that they are removed; the first is what
happens on chat completions.

**Failover with real provider failures**, `bench/failover_scenario.py`
with `FAILOVER_PRIMARY` / `FAILOVER_CLOUD`:

- `gemini/gemini-pro-latest` (real 429, quota) -> `gemini/gemini-3.1-flash-lite`, fallback index 1. PASS.
- `gemini/gemini-pro-latest` (real 429) -> `groq/openai/gpt-oss-120b` -> `openrouter/anthropic/claude-haiku-5.5` -> local: served by Groq at index 1, cross-provider. PASS.

The scenario's `max_tokens` went from 16 to 200: gpt-oss spends tokens
reasoning first, and at 16 it returned an empty reply.

## Benchmark on Gemini, 2026-11-15

`BENCH_MODEL_CHEAP=gemini/gemini-3.1-flash-lite`,
`BENCH_MODEL_CAPABLE=gemini/gemini-3.5-flash`, local tier
`qwen2.5:32b-instruct`, 7 tasks x 3 runs per arm, T=0.7. Evidence:
`evidence/scenario1-benchmark-20261115-161010.json`.

| | naive (everything on 3.5 Flash) | optimized |
| --- | --- | --- |
| Passed | 16/21 | 18/21 |
| Tokens per completed task | 58,942 | 19,395 (67% fewer) |
| Gateway cost per completed task | $0.01098 | $0.00260 (76% cheaper) |

Per task, the optimized arm was cheaper on every task both arms solved;
it used more tokens on `shop_bulk_bug` (routed to the free local tier,
$0). The naive arm failed `docstring` 0/3; the optimized arm failed
`shop_freeship` 0/3, which the naive arm solved 2/3.

Conditions, disclosed: the key is on Gemini's free tier, so the dollar
figures are the gateway's list-price accounting, not a bill. 30 of 336
calls hit a real 429 and were absorbed by the fallback chain (3.5 Flash
to Flash-Lite 11 times in the naive arm, Flash-Lite to local 5 times);
no run failed because of it.

**Two defects found by this run, both fixed** (`agent/loop.py`, tests in
`bench/test_loop.py`):

1. Sticky failover was session-long. One 429 (retry_after 34 s) pinned
   the cheap tier to the local model for the rest of a `shop_freeship`
   run. The pin now lasts `STICKY_SECONDS = 60`, then the primary is
   tried again, and the return is announced ("Provider restored").
2. Escalation fired only when stuck (3 turns without progress, or 5
   failed edits). On `shop_freeship` the cheap model kept making
   progress that did not converge, and ran out of turns. A mutating task
   now escalates once when half the turn budget is spent. It never stops
   a run: with no different model above (local-only, confidential), it
   continues. With all tiers on one local model, as in the published
   local benchmark, neither change has any effect.

**Privacy re-checked with all three cloud providers enabled** on the
gateway: `bench/privacy_scenario.py` PASS (6 structural checks, 8 calls,
all served by `ollama/qwen2.5:32b-instruct`), and the confidential
virtual key gets HTTP 403 from the gateway for
`gemini/gemini-3.1-flash-lite`, `openrouter/anthropic/claude-haiku-5.5`
and `groq/openai/gpt-oss-20b`. The new providers are allowed on the demo
key only.

**`shop_freeship` rerun with both fixes** (optimized arm, 3 runs, same
Gemini models; `evidence/scenario1-benchmark-20261115-171309.json`):
1/3 passed, up from 0/3. The half-budget escalation fired at turn 9 in
all three runs, but the escalation target was not available: every one
of the 5 calls that asked for `gemini-3.5-flash` got a 429 and was
served by Flash-Lite (3) or local (2), and the 60 s pins sent the other
T2 calls to the same fallbacks. After the full benchmark the free-tier
quota for 3.5 Flash was spent. The mechanism is verified; whether a
reachable capable tier solves this task is measured on Claude below.

## VS Code extension, checked 2026-11-15

`extension/` (TypeScript, VS Code 1.95+) drives `himin --serve`
(`agent/server.py`, JSON lines on stdio).

- `bench/test_server.py`, 7 tests against a scripted model: approve
  writes the dry-run diff that was shown; deny writes nothing; an answer
  for another id is ignored; an editor that disconnects mid-approval is a
  denial; commands answer on the channel; a stray `print` goes to stderr,
  not the protocol stream; a command the agent runs reads `''` from
  stdin even when the server's stdin holds an approval.
- `extension/src/test/protocol.test.ts`, end to end against the real
  agent and gateway: ready, a read-only answer with stats, an edit that
  arrives as an approval request with the diff, a forged id ignored, a
  denial that leaves the file byte-identical, undo. PASS.
- `extension/src/test/vscode/`, inside a downloaded VS Code: activation,
  all commands registered, the agent view opens, the machine-scoped
  `himinbjorg.command` starts the agent, `cost` and `undo` answered. PASS.

Found by the end-to-end test: after one denial the model retried the
edit, then tried `run_command` and `write_file`, six prompts in all. The
denial message now says not to retry or work around it, and a second
denial in a task ends the task (`MAX_DENIALS = 2`): two prompts.

Also fixed for the server, and good for the CLI: child processes
(`run_command`, git, grep) inherited the agent's stdin. They now get
`/dev/null`.

**Installed and run in VS Code 1.146** (the `.vsix` installed with
`code --install-extension`, then `npm run test:flow` loading that
installed copy; the input box and the modal answer are intercepted at
the vscode API, everything else is the real editor, agent and gateway):

| Flow | Observed |
| --- | --- |
| Read-only question | answer in the view; "2,817 tokens (0 cached) · $0.0008 · gemini/gemini-3.1-flash-lite"; status bar "Himinbjorg $0.0008"; no dialog |
| Edit, denied | modal "Himinbjorg wants to run apply_patch on calc.py" with Approve / Approve all edits this session; the dry-run diff open beside it as a `diff` document; file byte-identical; second denial ended the task |
| Edit, approved | one dialog; `calc.py` changed; "approved" logged in the view |
| Undo | "reverted calc.py"; file byte-identical to the original |
| Cost | session tokens and $ from the gateway |
| Confidential setting + restart | CONFIDENTIAL mode, no `web_search`, lock in the status bar |

Two bugs found this way, both fixed: per-task cost was never shown in
the REPL or the editor (`Stats.as_dict` returned the live
`request_ids` list, so the before/after slice was always empty;
regression test in `bench/test_loop.py`), and the diff tab could stay
open after the last answer (now closed before the answer is sent).
Not checked by automation: how the webview and the modal look.

**Checked by eye** in a normally launched VS Code 1.146 (nested Xephyr
display, driven with keystrokes and clicks; screenshots in
`docs/images/`): the Agent view, the command-palette Ask, the real
approval dialog with the diff beside it, Approve, Cancel, two denials
ending the task, Undo restoring the file byte-for-byte, and a repeated
task replayed entirely from the gateway cache at $0. Found and fixed on
the way: an approval whose dialog fails was never answered (now a
denial, with a flow-test step); an Ask from the palette did not reveal
the Agent view; New did not clear the transcript; commands showed as a
JSON array; the ready line used the alert style and a full path; the
secondary buttons had no border in the default theme.

## Claude via OpenRouter: prompt caching and the prune step, 2026-11-15

Optimized arm only, `BENCH_MODEL_CHEAP=openrouter/anthropic/claude-haiku-5.5`,
`BENCH_MODEL_CAPABLE=openrouter/anthropic/claude-sonnet-5.5`, local tier
`qwen2.5:32b-instruct`, 7 tasks x 3 runs, once with `AGENT_PRUNE_STEP=1`
(`evidence/scenario1-benchmark-20261115-174318.json`) and once with `4`
(`...-175123.json`).

**Contamination, found in the gateway logs.** Part-way through the
second pass, OpenRouter answered 402 "This request requires more
credits": the account had no purchased credit (`total_credits: 0`) and
had used its small free allowance ($0.199 in all). The gateway's
fallback chain absorbed it (Claude -> local), so no run crashed, but
`shop_freeship` (all 3 runs) and one `shop_rename` call in the second
pass ran on the local model. Most other tasks were routed to the free
local tier by design and never touched Claude. Only `zero_check` and
`add_power` ran fully on Claude in both passes; they are the comparison.

Per Claude call, from the trace (`cached_read_tokens`,
`cached_write_tokens`), with Anthropic's price shape (read 0.1x, write
1.25x of input):

| Task | Prune step | Claude calls | Prompt tokens | Read from cache | Written to cache | Input cost vs no caching |
| --- | --- | --- | --- | --- | --- | --- |
| zero_check | 1 | 14 | 35,842 | 71.0% | 28.9% | 43.3% |
| zero_check | 4 | 12 | 29,368 | 81.4% | 18.5% | 31.3% |
| add_power | 1 | 20 | 66,034 | 58.8% | 41.2% | 57.4% |
| add_power | 4 | 28 | 103,640 | 71.2% | 28.7% | 43.1% |

Prompt caching through the gateway roughly halves Claude input cost; a
stable prefix (step 4) removes about another quarter of what is left,
because fewer turns rewrite the cache. Gateway cost per completed task:
zero_check $0.00105 -> $0.00075 (3/3 both); add_power $0.00251 ->
$0.00292 (3/3 both; one step-4 run escalated to Sonnet at half budget,
5 calls). The earlier local calibration showed no difference between 1
and 4. So the default is now 4 (`agent/config.py`, and the benchmark,
so it measures the product as shipped); the published local numbers
used 1.

**Found by this run, fixed:** a provider's 402 and the gateway's budget
402 were treated alike. Both arrive with `is_bifrost_error: false`; the
budget refusal carries `type: "budget_exceeded"` and
`error_type: "policy_budget_exceeded"`, the provider's does not. The
agent now raises `ProviderPaymentRequired` for the second, says "the
provider has no credit; your budget is fine", and drops a tier
(`agent/llm.py`, `agent/loop.py`; tests with both real bodies in
`bench/test_loop.py`). Before, an exhausted provider with no working
fallback would have been reported as the user's budget.

---

## Budget at real provider prices (2026-11-18)

`BUDGET_PRICING=real python bench/budget_scenario.py`. Key
`vk-user-budget-real`, Gemini only, $0.005 limit, no pricing override:
the gateway prices `gemini-3.5-flash-lite` from its own model catalogue.
Each call is a 2,355-token prompt (the first 8,000 characters of
`agent/loop.py` plus the question), so the limit is reached in a handful
of calls instead of thousands.

```
call  1: HTTP 200  used=$0.000000  tokens=2355
call  2: HTTP 200  used=$0.000709  tokens=2355
...
call  8: HTTP 200  used=$0.004961  tokens=2355
call  9: HTTP 402  budget_exceeded: 0.0057 >= 0.0050 dollars
provider actually invoked: NO
```

- Gateway price per call: $0.000709 (2,355 tokens), in line with the
  $0.0007045 the log API reported for a 2,341-token call earlier the
  same day.
- The free tier means these dollars are the gateway's accounting, not a
  bill. The price table is Bifrost's, which is the point: no synthetic
  number is involved.
- **Overshoot of at most one call, seen again.** Call 8 was served at
  $0.004961 used and brought the total to $0.0057. The check runs before
  the call against usage so far, not against usage plus the call's cost,
  so the last call can cross the limit. Here that is 14% over a tiny
  limit; on a realistic limit it is one call's cost.
- `used` lags by one call (call 6 shows the same total as call 5): the
  usage write is asynchronous, as recorded above.

Evidence: `evidence/scenario3-budget-20261118-230106.json`.

---

## Degradation ladder under real budgets (2026-11-18)

`python bench/ladder_scenario.py`. One key, `sk-bf-ladder-key`, three
providers, each cloud provider with its own budget on its provider
config (Bifrost `provider_config_id` budgets):

| Tier | Model | Budget |
| --- | --- | --- |
| T2 | `gemini/gemini-3.5-flash-lite` | $0.0008 |
| T1 | `groq/openai/gpt-oss-120b` | $0.0006 |
| T0 | `vllm/gemma-4-31b-it-qat-w4a16` (local) | none |

Task `shop_bulk_bug`, routing lever off so the run starts on T2:

```
served by gemini/gemini-3.5-flash-lite: 2
served by groq/openai/gpt-oss-120b: 3
served by vllm/gemma-4-31b-it-qat-w4a16: 2
gemini budget: $0.000899 of $0.0008
groq   budget: $0.000868 of $0.0006
gateway log a33e8d63: gemini budget_exceeded ... 0.0009 >= 0.0008
gateway log f20e50ff: groq   budget_exceeded ... 0.0009 >= 0.0006
task   : PASS (13.9s)
```

What this shows, and what it does not:

- **The gateway walks the ladder, not the agent.** A provider over its
  own budget is skipped and the request's fallback chain
  (T2 -> T1 -> local) continues, so no 402 reaches the agent. The agent's
  402 handler (drop a tier, then stop) is for requests with no chain:
  T0 and confidential mode.
- **Proof is the gateway's log**, not the agent's wording: the primary
  attempt of each fallback request carries `402 budget_exceeded`.
- **The agent's live message lags.** It now reads the provider budgets
  from the quota endpoint on every fallback and says "budget used up"
  when it can see it. But usage reaches that endpoint seconds after the
  gateway enforces it, so on this 14-second task both transitions were
  announced as "unavailable". On a longer run, the next call prints
  "that fallback was its budget ($x of $y used)". Waiting was rejected:
  it would add seconds to every fallback.
- **Overshoot of one call per rung**, as with the key budget: Groq ended
  45% over a tiny limit, which is one gpt-oss-120b call.
- Model choices were forced by the day's conditions: Gemini 3.5 Flash
  returned 503 "high demand" for 30 s, and gpt-oss-20b called a tool that
  does not exist (`open_file`), which Groq rejects with 400, so the chain
  moved on for a reason other than the budget. Both are recorded in
  `bench/ladder_scenario.py`.

Evidence: `evidence/scenario3b-ladder-20261118-232320.json`.

## Found by the QuixBugs and ladder runs, fixed (2026-11-18)

- **A rate limit burned every turn.** On a 429 the loop printed
  "retrying once" and retried on every turn with no wait. When the demo
  key hit its 2M tokens/hour limit mid-benchmark, each task used all 18
  turns in 8 s and was graded a failure. Now: wait 5 s, retry once, then
  `stopped: rate limited`. The benchmark has its own key now
  (`sk-bf-bench-local-key`, local vLLM only, no limits), selected with
  `BIFROST_VK_DEMO`.
- **The duplicate-call stub was a refusal.** A repeated identical read
  got "[identical call already made this session]" and counted as no
  progress. Gemma 31B re-read files it could still see, got the stub
  three times and the run stopped: 0/3 on `qb_lis` and `qb_get_factors`
  where the naive arm passed 3/3. Now the first repeat gets the stub,
  a second repeat gets the real result.
- **Stale bytecode graded a correct fix as failing.** CPython checks its
  `.pyc` cache by mtime in whole seconds plus size, so a same-length
  one-line fix made in the same second as the previous test run reused
  the old bytecode. Grading and the agent's own commands now run with
  `-B` and `PYTHONDONTWRITEBYTECODE=1`. Earlier published runs could have
  counted correct fixes as failures this way, which would understate
  their pass rates, not inflate them.

## QuixBugs run 2: what still failed, and why (2026-11-18)

28 QuixBugs programs, 3 runs per arm, every tier on Gemma 31B (local
vLLM, `sk-bf-bench-local-key`), on the code with the three fixes above.
Evidence: `evidence/scenario1-benchmark-20261118-232419.json`.

| | naive | optimized |
|---|---|---|
| passed | 76/84 | 71/84 |
| tokens per completed task (all tokens / runs passed) | 24,389 | 18,400 (24.6% fewer) |

Optimized lost on 5 programs. The traces show four agent faults and no
model limit:

- **`;` inside `python -c` was refused** (bitcount 0/3, sqrt 0/3,
  subsequences 1 of 3, and 1 of 3 lis runs lost two turns). The model
  ran `python3 -c "print(bitcount(127))"`, got a NameError, then sent
  `python3 -c "from bitcount import bitcount; print(...)"` three times.
  The shell check refuses `;` in any argument, so three turns in a row
  were refused and the run stopped for no progress. The naive arm has
  no stop, so it carried on and solved bitcount.
- **A copied line number was ignored** (get_factors 0/3, naive 3/3).
  The model sent `find="    10      return []"`, copied with its line
  number from `read_file`. The number was stripped, the rest occurs on
  lines 4 and 10, and every attempt was refused as ambiguous until 5
  edits had failed. Earlier this was blamed on the duplicate-call stub;
  the traces show this was the cause.
- **Buggy programs that loop forever held every test run for 90 s**
  (bitcount, sqrt, find_first_in_sorted). The agent saw only "command
  timed out after 90s", never which case hung. find_first_in_sorted
  failed on both arms, 0/3 each.
- **The duplicate-call stub ignored pruning.** It says the result is
  "earlier in this conversation", which is false once pruning has elided
  it, so the model had to ask a third time.

Optimized also used more tokens than naive on lis (82% more), wrap (8%)
and max_sublist_sum (9%), from repeated reads of a file Gemma could
still see.

## Fixed after run 2 (2026-11-19)

- **Inline code may use `;`.** In `python -c CODE`, `python3 -c CODE`
  and `node -e CODE`, the code argument is exempt from the
  shell-metacharacter check. It is Python or JavaScript, not shell, and
  the interpreter already runs any code it is given, so refusing `;`
  protected nothing; the approval prompt and the sandbox are what guard
  it. Every other argument is checked as before, including
  `python3 -c X ; rm y` and `python3 -B -c ...` (tests in
  `bench/test_security.py`).
- **A copied line number picks the occurrence.** When `find` carries
  read_file line numbers and the stripped text occurs more than once,
  `apply_patch` uses the occurrence starting on that line. If no
  occurrence starts there, it is still refused as ambiguous. The traced
  get_factors edit now applies on its first try.
- **A per-case time limit in the QuixBugs fixtures.** Each fixture's
  `conftest.py` fails a test case after 2 s with `TimeoutError: <case>
  ran longer than 2 s (probably an infinite loop)`. The correct versions
  run each whole suite in 0.02 s or less. The buggy bitcount, sqrt and
  find_first_in_sorted suites now fail in 18, 12 and 4 s, naming each
  case, instead of hitting the 90 s timeout. Grading restores this
  conftest with the tests, so the agent cannot remove it. This changes
  the task set for both arms equally; earlier QuixBugs numbers were
  taken without it.
- **The duplicate-call stub only appears while the earlier result is
  still in the context.** After pruning has elided it, the call just
  runs.
- Not changed: the no-progress stop. In every run it cut short, it
  fired after three refused commands, which are no longer refused.
  Whether it still stops Gemma too early is measured below, not assumed.

## QuixBugs run 3, on the fixed code (2026-11-19)

Same conditions as run 2: 28 programs, 3 runs per arm, temperature 0.7,
every tier on Gemma 31B (`vllm/gemma-4-31b-it-qat-w4a16`, local vLLM
through a relay, key `sk-bf-bench-local-key`), with the per-case time
limit. Evidence: `evidence/scenario1-benchmark-20261119-012654.json`.

| | naive | optimized |
|---|---|---|
| passed | 82/84 | 84/84 |
| tokens per completed task (all tokens / runs passed) | 20,078 | 15,339 (23.6% fewer) |
| median turns per run | 7 | 7 |
| median wall time per run | 18.7 s | 14.0 s |
| wall time, all 84 runs | 27.9 min | 22.7 min |

- Optimized passed every program. Naive failed 2 of 3
  longest_common_subsequence runs at the 18-turn limit, which is also
  where most of its extra tokens went (192,188 tokens per completed
  task there, against 14,642).
- No optimized run was stopped for no progress, so the stop was left as
  it is.
- **Optimized used more tokens on 7 of 28 programs**: lis 77% more,
  next_permutation 16%, rpn_eval 13%, bitcount 7%, max_sublist_sum 6%,
  shunting_yard 4%. On lis, Gemma re-reads `lis.py` while it can still
  see it; each re-read is a turn. Left open for later work on wasted
  turns rather than tuned here.
- **What this does not measure.** Every tier is the same model, so
  routing saves nothing here (the naive arm sends everything to T2, the
  optimized arm to T0, both Gemma). The 23.6% comes from the other
  levers: file memory, retrieval, pruning, tool disclosure, diff writes
  and loop limits. Each program is a one-line bug in one file, so these
  are short sessions, where those levers have least to work with.
- Compared with run 2, the fixes moved optimized from 71/84 to 84/84;
  naive moved from 76/84 to 82/84, mostly from the per-case time limit
  (find_first_in_sorted 0/3 to 3/3, sqrt 1/3 to 3/3).
- One cell is 3 runs. Gemma under vLLM is low-entropy, so repeats often
  use the same number of tokens even at temperature 0.7; three repeats
  are closer to one sample than to three.

## Budget and ladder rerun on the final code (2026-11-19)

- Budget at real prices: PASS again, 8 calls served, 402 at call 9
  (`evidence/scenario3-budget-20261119-021900.json`).
- Ladder: 2 of 3 runs PASS over both days
  (`scenario3b-ladder-20261118-232320.json`,
  `scenario3b-ladder-20261119-021956.json`). The third
  (`scenario3b-ladder-20261119-021902.json`) FAILED on the T1 -> T0 step.
  Groq left the chain with $0.000259 of its $0.0006 still unspent,
  because Groq rejected gpt-oss-120b's own output ("Parsing failed. The
  model generated output that could not be parsed"). The gateway fell
  back to Gemma and the task passed, but the step was not caused by a
  budget, and the check refused to count it. That is what it is for.
  Kept as evidence, not rerun until it passed.
- In all three runs the agent's live message said "unavailable", not
  "budget used up", for the lag described above; the runs last 8 to 14 s.

## Own tasks, 10 runs per task, on Gemma 31B (2026-11-19)

The headline (84% fewer tokens, 57/70 vs 31/70) was measured on one
model family, Qwen 2.5 32B. The paid cloud runs planned to check it are
blocked: the OpenRouter key has almost no credit left ("You requested up
to 1200 tokens, but can only afford 376"), and the Groq free tier allows
8,000 tokens per minute and 1,000 requests per day (measured from its
response headers through the gateway), less than one naive call on the
shop tasks. So the same 7 tasks ran 10 times per arm on a second local
model: Gemma 31B (`vllm/gemma-4-31b-it-qat-w4a16`) on every tier,
temperature 0.7, key `sk-bf-bench-local-key`.

Run 1, `evidence/scenario1-benchmark-20261119-104400.json`: naive 64/70,
optimized 60/70, 45% fewer tokens per completed task. Optimized lost
shop_freeship 4/10 against 9/10. **Cause: retrieval.** `read_file` sent
files over 240 lines as an outline and the first 80 lines. For the
297-line, 10,486-character `shop/orders.py`, the model then read three
ranges, 11,621 characters in all, more than the whole file, without ever
seeing the whole order-total pipeline. **Fix:** a file of 12,000
characters or less (about 3,000 tokens) is sent whole whatever its line
count (`MAX_DEFAULT_CHARS` in `agent/tools/read.py`). Only the
optimized arm uses retrieval, and only `orders.py` crosses 240 lines, so
the optimized arm of the three shop tasks was rerun:
`evidence/scenario1-benchmark-20261119-121511.json`.

Pooled (`python bench/pool.py --latest-wins 104400.json 121511.json`,
which takes each task and arm from the last pack that has it):

```
                    passed            tokens per completed task
task             naive  optimized       naive    optimized   saving
zero_check       10/10    10/10         7,419        6,751      9%
add_power        10/10    10/10         9,927       14,116    42% MORE
rename_symbol    10/10    10/10        24,832       16,866     32%
docstring         5/10     6/10        10,088        9,042     10%
shop_bulk_bug    10/10    10/10        16,884       14,778     13%
shop_rename      10/10    10/10        90,358        5,551     94%
shop_freeship     9/10     7/10       102,409      120,920    18% MORE
ALL              64/70    63/70        38,536       23,513     39%
```

What this says:

- **The saving holds on a second model, at about half the size.** 39%
  fewer tokens per completed task with the pass rate unchanged (63 vs
  64), against 84% on Qwen. The difference is mostly the naive arm:
  Gemma passes 64/70 naive where Qwen passed 31/70, so there are far
  fewer failed naive runs to charge. On a model strong enough to cope
  without the levers, they save tokens but no longer add passes.
- **Routing saves nothing here**, as on QuixBugs: every tier is Gemma.
- **Where optimized costs more.** add_power: on 30-line files, diff
  edits take three small patches where the naive arm rewrites two files
  whole, and one patch guessed at text it had not re-read (10 turns
  against 7). shop_freeship: pruning elides `orders.py` after three
  turns and the model reads it again, four times in a run, about 3,000
  tokens each, so optimized runs take 17 or 18 turns where naive often
  takes 7. Keeping the file being edited in context is a later change,
  measured separately, not tuned inside this measurement.
- Gemma under vLLM is low-entropy: naive add_power used between 9,876
  and 9,956 tokens in all 10 runs. Ten repeats are fewer independent
  samples than the count suggests.

---

## Wasted turns, streaming, and cost on a caching provider (2026-11-19)

### What changed

From the traces of the 10-run Gemma benchmark above (140 runs):

- **A successful `apply_patch` returns the file as it is now** (whole up
  to the read limit, else the changed lines and 10 either side), with
  the file-memory lever on. Before, the patch superseded the earlier
  read and showed nothing, so the model guessed its next `find` (failed
  in 8 of 10 add_power runs) or read the file again: every patch of
  `orders.py` on shop_freeship was followed by a re-read.
- **Superseding matches the path as a whole name.** A patch of `calc.py`
  also hid the read of `test_calc.py`, a substring.
- **The listing says it is complete**, and the prompt asks for several
  reads in one reply: 83 of about 140 runs opened with `list_dir` of a
  root already listed in the prompt.
- **Not built: running reads in parallel.** Gemma asked for more than
  one tool in 4 of 2,670 turns, and a local read takes milliseconds
  against a median 0.85 s per model call.
- **Streaming** in the CLI (on a terminal) and the VS Code panel
  (`delta` events). Checked against the gateway first: routing headers,
  the final chunk's `usage`, and `cache_debug` (a direct-cache hit
  included) all arrive on a streamed call. The benchmark does not
  stream.

From the opencode review (github.com/anomalyco/opencode at 0a297bf):

- **A handover at the turn limit:** one last call with tools declared
  but `tool_choice: "none"`, asking what was done, what is left, and
  what to do next. Checked live: Gemma through the gateway answers in
  text with tool calls in the history. On Haiku, all 12 runs that hit
  the 18-turn limit ended with that summary.
- **Edit matching reads typographic quotes and dashes as plain ones.**
- **The whole-file limit follows the context budget** (a quarter of it,
  28,000 characters by default). No own task has a file over the old
  12,000, so this is measured on SWE-bench, not here.
- **A third arm, `keepctx`:** every lever on, but the pruning lever keeps
  the context verbatim (`AGENT_PRUNE_MODE=budget`), so each request
  extends the cached prefix of the last, and replaces older turns with a
  structured summary only near the budget. On the own tasks the context
  never got there: no compaction fired.

### Gemma 31B, before and after (5 of 7 tasks)

`evidence/scenario1-benchmark-20261119-200132.json`, 10 runs per arm,
against the pooled 10-run Gemma results above. The other two tasks are void: the model server
went down mid-run, outside the benchmark, so all shop_freeship runs and
12 shop_rename runs got no answer (recorded as 0 tokens), and a rerun
was cut short the same way.

```
                 passed         median turns          tokens per completed task
                 (each column: before > after)
zero_check       naive 10>10    6 > 4                 7,419 >  5,516
                 opt   10>10    5 > 4                 6,751 >  6,189
add_power        naive 10>10    7 > 6                 9,926 >  7,921
                 opt   10>10   10 > 7                14,116 >  9,930
rename_symbol    naive 10>10   14 > 7                24,832 > 16,620
                 opt   10>10   11 > 6.5              16,866 >  8,668
docstring        naive  5>7     4 > 3                10,088 >  5,575
                 opt    6>4     4 > 3                 9,042 >  9,107
shop_bulk_bug    naive 10>10    7 > 7                16,884 > 17,105
                 opt   10>10    7 > 7                14,778 > 16,119
```

Median turns fell on 9 of the 10 cells and held on the tenth; median
wall time fell on 8. The prompt changes apply to both arms, so the
baseline improved too. docstring is a grading artifact: the grader wants
a docstring of 15 characters or more, and "Divide a by b." has 14.

### Claude Haiku 5.5, three arms, 10 runs per task

Every tier `openrouter/anthropic/claude-haiku-5.5`, temperature 0.7, key
`sk-bf-bench-cloud-key` (OpenRouter only, so no call can fall back to a
local model). `evidence/scenario1-benchmark-20261119-204844.json`, with
add_power and rename_symbol for the two levers-on arms rerun after the
two fixes below (`...-221735.json`, `...-222203.json`); pooled with
`python bench/pool.py --latest-wins` over the three. Every request was
priced by the gateway (no run had an unpriced request); $1.06 in all.

```
                    passed              tokens per completed task         dollars per completed task
task          naive  opt  keepctx     naive     opt   keepctx        naive     opt      keepctx
zero_check    10/10  9/10  10/10      8,233  13,260    11,319      0.00111  0.00086   0.00053
add_power     10/10 10/10  10/10     11,677  13,358    13,518      0.00161  0.00117   0.00090
rename_symbol 10/10 10/10  10/10     15,309  21,823    18,945      0.00219  0.00187   0.00124
docstring     10/10 10/10  10/10     10,009  10,159     8,952      0.00143  0.00083   0.00062
shop_bulk_bug 10/10 10/10  10/10     11,756  12,626    18,546      0.00157  0.00085   0.00077
shop_rename   10/10 10/10  10/10     92,945  15,441    11,638      0.01429  0.00091   0.00048
shop_freeship  8/10  8/10   9/10    323,169 183,738   191,080      0.05537  0.01600   0.00809
ALL           68/70 67/70  69/70     60,068  34,676    36,940      0.00978  0.00287   0.00171
```

- **On a provider with a prompt cache, tokens and dollars disagree.**
  optimized and keepctx save about the same tokens (42% and 38%), but
  keepctx is 82.5% cheaper than naive and optimized 70.7%. Pruning
  rewrites earlier messages, so less of each request is a cache read
  (billed at a tenth). In the first pack, keepctx read 2.2M of its 2.6M
  prompt tokens from cache, optimized 1.5M of 2.4M. Both levers-on
  arms are cheaper than naive on every task, and keepctx is cheapest on
  every task.
- **On small tasks the levers cost tokens on Haiku.** A strong model
  needs a median 4 turns naive; optimized uses more tokens than naive
  on five of the seven tasks and keepctx on four. Where those tokens go
  is not yet broken down. They still cost less in dollars, because the naive arm
  has no prompt caching (the cache lever is off).
- The tokens-only headline would have hidden this. The README's
  headline is still tokens on a local model; dollars are what a cloud
  user pays.

### Found by the Haiku run, fixed

- **A new top-level function was nested by the indentation fix-up**
  (`agent/tools/write.py`, `_match_indent`). The model patched
  `return a / b` with the same line plus `def power` at column 0; the
  fix-up shifted every line to the indentation of `return`, so `power`
  landed inside `divide`. It parsed, so the model saw it only on reading
  back, and spent up to 11 more turns. With the block already at the
  file's indentation, the first line got a second indent and the patch
  was refused. Now a block already at the right indentation is left
  alone, and a `def`/`class`/decorator at column 0 after the first line
  stays there. add_power, optimized: 30,577 -> 13,358 tokens per
  completed task, median turns 6.5 -> 4, $0.00290 -> $0.00117.
- **`rename_symbol` reported only counts**, so the model grepped for the
  old name, found `test_multiply` (not a whole-word match), and then
  re-read every file to check. The result now shows the changed lines
  and any text that still contains the old name. rename_symbol,
  optimized: 28,264 -> 21,823 tokens, median turns 7 -> 5.5; keepctx
  25,660 -> 18,945.

## Own tasks, 10 runs per task, on Qwen 3.8 27B (2026-11-20)

Every tier `vllm/qwen3.8-27b-fp8` (FP8, non-thinking, on a local vLLM
server with a 32k window), temperature 0.7, key `sk-bf-bench-local-key`,
three arms, on the same code as the Haiku run above.
`evidence/scenario1-benchmark-20261119-224023.json`; `python bench/pool.py`
over it reproduces the table.

```
                    passed               tokens per completed task
task          naive  opt  keepctx      naive      opt    keepctx
zero_check    10/10 10/10  10/10      10,528   10,037     10,362
add_power     10/10 10/10  10/10       9,650    7,095      7,885
rename_symbol 10/10 10/10  10/10      21,490   17,554     21,852
docstring     10/10 10/10  10/10       9,836    7,283      5,405
shop_bulk_bug 10/10 10/10  10/10      28,070   22,176     24,908
shop_rename   10/10 10/10  10/10      55,406   10,233     11,632
shop_freeship 10/10 10/10  10/10     110,344   83,114    170,291
ALL           70/70 70/70  70/70      35,046   22,499     36,048
```

- **Every run passed, in every arm.** Qwen 2.5 32B solved shop_freeship
  in neither arm; Qwen 3.8 solves it every time. As on Gemma and Haiku,
  the levers can only save tokens here, not add passes.
- **optimized: 36% fewer tokens per completed task, fewer on every
  task** (5% on zero_check to 82% on shop_rename), and less than half
  the time: 25.8 s per run against 60.0 s naive.
- **keepctx: 3% more tokens than naive overall, 54% more on
  shop_freeship.** It never compacted (no run came near 85% of the
  28,000-token budget), so its context only grew: on shop_freeship its
  requests averaged 13,940 prompt tokens, against 6,171 for optimized
  and 8,980 for naive. On Haiku the same growth was cheap, because the
  repeated prefix is read from the provider's prompt cache at a tenth of
  the price; a local server bills nothing either way, so the tokens are
  the cost.
- **So `AGENT_PRUNE_MODE` stays `turns` by default**, and `budget` is
  the setting for a provider with prompt caching (Anthropic, through
  OpenRouter or directly).
- 12 of the 210 runs ended on a stop (no progress for three turns, or
  the turn limit) and still passed: the fix was in, and the handover
  says what was done.
- About two minutes of opencode test runs shared the GPU with a few
  rename_symbol and docstring runs, which may have slowed their wall
  time; tokens are unaffected.
