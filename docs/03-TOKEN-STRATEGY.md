# 03 - Token Strategy

The scored requirement is *fewest tokens, without compromising correctness*. A
token reduction that breaks the task scores zero. Every lever below therefore
carries a correctness guard and a measurement hook.

## Measurement contract

Each lever can be switched off independently by a flag. The benchmark runs the
full configuration, then re-runs with one lever disabled at a time, and
attributes the difference. A lever that cannot be shown to pay for itself gets
cut rather than defended. Savings estimates below are **hypotheses to be
measured**, not results.

---

## Lever 1: Stateless sub-calls, so the cache can actually work

> **As built (2026-11-15), this differs from the plan below.** The
> stateless sub-call design was never implemented; until 2026-11-15 the
> agent sent no cache key at all and lever 1 did nothing. What shipped is
> simpler: **direct-hash caching of the whole conversation request**, keyed
> per repository, with `cache_tool_call_responses: true` on the gateway.
> It is safe because the hash covers every message and file content the
> model saw, and it pays off on identical re-runs (2,848 -> 0 provider
> tokens, scenario 1b), not within a single session. Bifrost reports the
> original usage on a hit, so hit tokens are counted separately.
> The original plan follows, kept for the reasoning.


**The problem.** Bifrost's semantic cache skips any conversation longer than
`conversation_history_threshold` (default 3) and skips tool-call responses
(`cache_tool_call_responses: false`). An agent loop violates both within its
first turn. Configure the cache naively and it will report a 0% hit rate while
appearing to be enabled.

**The response.** Split model work into two shapes (see
[02-ARCHITECTURE.md](02-ARCHITECTURE.md)). Push every self-contained judgement
into a one-message stateless call from a fixed prompt template. Those cache on
exact-hash matching, and semantically for near-identical repeats.

Work that moves into Shape B:
- task tier classification
- file summarisation and symbol extraction
- commit message and patch description generation
- "is this error message a syntax error, a test failure, or an environment
  problem" triage

**Cache key design.** `x-bf-cache-key` is required or caching silently
no-ops. Use a structured key: `{repo_id}:{template_name}:{content_sha256}`.
This gives per-repository isolation, prevents a summarisation result leaking
into a classification lookup, and makes the key reproducible across runs so the
benchmark's second pass actually hits.

**Config changes required.** Raise `conversation_history_threshold` so it cannot
silently exclude borderline calls, keep `cache_tool_call_responses: false` since
Shape B calls carry no tool calls anyway, and set `ttl` long enough to span the
whole benchmark.

**Correctness guard.** Never cache anything whose result depends on mutable
repository state unless the file hash is in the key. The `content_sha256`
component enforces this structurally.

**Measure.** `cache_debug.cache_hit` rate over Shape B calls, and tokens saved
as the sum of prompt tokens that would have been billed on a miss.

---

## Lever 2: Tiered routing

| Tier | Model | Used for |
| --- | --- | --- |
| T0 local | `ollama/qwen2.5-coder:7b` | Classification, summarisation, symbol extraction, triage. Zero marginal cost |
| T1 cheap | `openai/gpt-4o-mini` or `anthropic/claude-haiku-5-5` | Single-file edits, tool selection on a narrow surface |
| T2 capable | `anthropic/claude-sonnet-5-5` | Multi-file reasoning, ambiguous specs, failed-retry escalation |

**The router must not cost more than it saves.** It runs heuristics first:
file count, diff size, whether tests are involved, whether the request contains
ambiguity markers. Only when heuristics are inconclusive does it make a T0
classification call, and that call is itself a cached Shape B call.

**Escalation.** A T0 or T1 attempt that fails its correctness guard escalates
one tier and retries once. Escalation is logged as a router miss. Two
escalations on the same step means the heuristic is wrong and should be tuned.

**Correctness guard.** Escalation on failure is the guard. The risk is a cheap
model producing confidently wrong output that passes no check, so T0 and T1 are
only given work whose output is verifiable: a patch that must apply, a JSON
object that must parse against a schema, a classification that must be one of
N enum values.

**Measure.** Share of calls and tokens per tier, escalation rate, and the cost
delta against routing everything to T2.

---

## Lever 3: File memory

Hash every file the agent reads. Maintain `path -> (sha256, turn_first_sent,
summary)`. On a re-read:

- unchanged and already in context: send nothing, reference it by name
- unchanged and pruned from context: send the stored summary, not the content
- changed: send a diff against the version already sent, not the whole file

**Why this matters more than it sounds.** Rereading the same file is the single
most common waste in agent loops, and it compounds, since the reread lands in
the context and is then resent on every subsequent turn.

**Correctness guard.** Hash on every access, never trust a cached hash across a
write performed by the agent itself. The write tools invalidate entries
synchronously.

**Measure.** Count of suppressed rereads and bytes not resent.

---

## Lever 4: Retrieval instead of whole-file dumps

A file over a threshold is never sent whole. Instead:

- a symbol outline (top-level definitions with line numbers),
- plus the specific windows matched by the current query,
- plus an explicit note that the rest exists and can be requested.

**Correctness guard.** The model must be able to ask for more. A `read_file`
call with an explicit line range always returns exactly what was asked for. The
budget is on *default* verbosity, not on availability.

**Measure.** Average tokens per read-tool result, and the rate at which the
model needs a follow-up read, which is the cost side of this lever.

---

## Lever 5: Context ledger with pruning

The message list is derived from a ledger, not accumulated blindly. Each entry
knows its turn, its token cost, and its decay policy:

- tool results older than K turns collapse to a one-line outcome summary
- superseded file contents drop entirely once a newer version is sent
- the task statement, the plan, and the last N turns are pinned

**Correctness guard.** Pruning writes a visible marker into the context so the
model knows information was elided and can re-request it. Silent truncation is
how agents start hallucinating file contents.

**Measure.** Prompt tokens per turn, plotted against turn number. The naive
baseline grows roughly quadratically over a session; the target is roughly flat.
This is the most visually compelling chart in the demo.

---

## Lever 6: Progressive tool disclosure

Tool schemas are resent on every single request, so they are pure recurring
overhead. Expose only the read tools by default. Inject the write, shell, and
git schemas only once the task has been classified as mutating.

Keep descriptions terse. A tool description is prompt text billed every turn.

**Correctness guard.** Misclassifying a task as read-only must be recoverable:
if the model indicates it needs to write, the registry expands and the turn is
retried once.

**Measure.** Tokens in the tool block per request, times request count.

---

## Lever 7: Diff-based writes

The model emits a patch, never a whole rewritten file. Output tokens are the
expensive ones, and a full-file rewrite of a 400-line file to change 3 lines is
the single largest avoidable output cost in a coding agent.

**Correctness guard.** The patch must apply cleanly. A failed application is an
error returned to the model, not a silent fallback to full rewrite.

**Measure.** Output tokens per successful edit.

---

## Lever 8: Loop discipline

- hard cap on turns per task
- no-progress detection: if three consecutive turns produce no file change and no
  new information, stop and report rather than spiral
- deduplicate identical tool calls within a session and return the prior result

**Measure.** Turns per completed task against the baseline.

---

## Interaction effects worth watching

Levers are not independent and the benchmark must not pretend they are.

- **Lever 5 weakens lever 1.** Pruning changes the message list, which changes
  cache keys for any Shape A call. Since Shape A is not cached anyway, this is
  acceptable, but it is the reason the split exists.
- **Lever 2 fragments the cache.** `cache_by_model` defaults to true, so each
  tier maintains a separate cache namespace. Routing the *same* Shape B template
  to different tiers on different runs will miss. Pin each template to a fixed
  tier.
- **Lever 4 can increase turns.** Sending less per read means more reads. If the
  follow-up read rate exceeds roughly a third, the threshold is too aggressive
  and the lever is costing tokens rather than saving them.

## Deliberately out of scope

Prompt compression by paraphrase, fine-tuning, and embedding-based retrieval
over the whole repository. Each is defensible and none fits in 24 hours
alongside the mandatory requirements.

## As built, 2026-11-15

Changes made after reading benchmark traces. Each is covered by a test in
`bench/test_write.py`, `bench/test_loop.py` or `bench/test_tasks.py`.

| Lever | Addition | Trace that motivated it |
| --- | --- | --- |
| 1 cache | Provider prompt-cache breakpoints (`cache_control`) on the system message and the last message, sent as Bifrost message-level markers. Reads and writes are reported separately from gateway cache hits | Anthropic bills cache reads at a tenth of input; the agent resends tools + system + history every turn |
| 2 routing | Escalate one tier when a run is stuck, instead of stopping, when the higher tier is a different model; sticky failover for the rest of the session | `escalate()` existed and was never called; a failed primary cost a round trip on every call |
| 3 file memory | Resend when the earlier copy has been pruned; uses the ledger's own horizon | "already provided above" was said about content the model could no longer see |
| 5 pruning | `AGENT_PRUNE_STEP`: the elision boundary moves in steps, keeping the prompt prefix byte-identical between steps | A boundary that moves every turn rewrites one old message per turn, breaking prefix caching |
| 5 pruning, `AGENT_PRUNE_MODE=budget` | No per-turn elision; older turns are replaced by one structured summary near the context budget (from opencode's compaction) | Claude Haiku: 82% cheaper than naive per completed task, against 71% for per-turn pruning, at similar token counts. Qwen 3.8 (local): 3% more tokens than naive, against 36% fewer for per-turn pruning. A setting for cached providers, not the default |
| 6 disclosure | `git_op` only when the task mentions git; `rename_symbol` only when it mentions renaming | Unprompted commits; schema tokens on every turn |
| 7 diff writes | Now actually gated: `apply_patch` and `rename_symbol` exist only with the lever on; off, the agent can only rewrite whole files. `rename_symbol` added (one approved call per rename) | The flag existed but changed nothing, so runs before 2026-11-15 did not measure it; a 4-file rename took 12 turns |
| 8 loop limits | Hint after 2 failed edits, stop (or escalate) after 5; identical calls in one completion run once; 8 calls per turn | read/patch/read/patch loops never tripped no-progress |

Shared by both benchmark arms (not levers, so both arms benefit):
apply_patch robustness (whole-line widening, comment-skipping match,
line-number stripping, empty find = append, "already applied",
`replace_all`, cross-file pointer on not-found), the system prompt's
verification and no-guessing rules, the dry-run approval
path, out-of-range reads returning small files whole, and command output
keeping both head and tail.
