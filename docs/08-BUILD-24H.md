# 08 - 24 Hour Build Schedule

Assumes one developer. With two, the parallel track is marked.

Hour 0 is whenever you start. Times are elapsed, not clock.

## Priority tiers

| Tier | Meaning |
| --- | --- |
| **P0** | A mandatory requirement. Without it the submission does not qualify |
| **P1** | The scored differentiator, or the evidence that proves a P0 |
| **P2** | Build only if P0 and P1 are done and verified |

The cut lines below are decisions made in advance, in a calm state, about what
to abandon. Honour them.

---

## Hours 0-1: Resolve the unknowns (P0, blocking)

Answer all five open questions in [01-RESEARCH.md](01-RESEARCH.md) §7 with curl
alone. **Write no agent code during this hour.** Every answer changes an interface.

1. `docker compose up`, confirm the dashboard loads and shows providers, virtual
   keys, and the semantic cache plugin.
2. Smoke test a chat completion with `x-bf-vk`. Settles the key prefix question.
3. `curl -i` the same call. Record every `x-bifrost-*` response header verbatim.
   Settles telemetry tier 2.
4. Send `fallbacks` in a raw body with a broken primary. Confirm `routing_info`
   location. Then confirm the OpenAI SDK forwards it via `extra_body`.
5. Call an Ollama model, with and without `/v1` on the base URL.
6. Call each chain model once, then read the quota endpoint. **Confirm
   `current_usage` moves.** If it does not, write `pricing_overrides` now.

Fix `infra/` per [09-CONFIG-FIXES.md](09-CONFIG-FIXES.md) before step 1; the
current config will not load correctly.

**Exit criterion.** A one-page `docs/VERIFIED.md` with six answers, each with the
curl that produced it. If you cannot answer all six by hour 1, take the slower
path for whatever is unresolved: raw `httpx` instead of the SDK, gateway log
lookup instead of headers. Do not keep debugging.

---

## Hours 1-3: Skeleton and chokepoints (P0)

`config.py`, `privacy.py`, `trace.py`, `llm.py`, `telemetry.py`.

Build in this order because everything else depends on them, and because the
egress assertion in `privacy.py` needs to exist before any code can route around
it.

**Exit criterion.** A script that makes one gateway call through `llm.py`, prints
which provider served it, and writes a trace line. Confidential mode flips the
key and the assertion rejects a deliberately wrong model.

---

## Hours 3-6: Tools and the policy gate (P0)

`registry.py`, `policy.py`, `read.py`, `write.py`, `shell.py`, `git.py`.

Argument validation before the gate. Gate reads `/dev/tty`. Every decision to
the trace.

**Parallel track:** with a second developer, this block and hours 1-3 run
simultaneously. The policy gate has no gateway dependency at all, which is why
the interfaces are drawn so it does not need one.

**Exit criterion.** Each tool callable from a test harness. A GATED tool prompts.
Path jail rejects `../`. Secret shield rejects `.env`. Piped `y` on stdin does
not approve.

---

## Hours 6-10: The agent loop (P0)

`loop.py`, `ledger.py`, `main.py`.

Plan, call, dispatch tools, observe, repeat. Turn cap and no-progress detection
from the start; they are three lines each and prevent a runaway spending the
budget at hour 7.

**Exit criterion.** The agent completes benchmark task 1 end to end, with an
approval prompt on the write, and the test suite passes afterwards.

**This is the critical milestone.** If hour 10 passes without it, stop adding
features and cut: drop the router to a fixed tier, drop retrieval, drop pruning.
A working agent with two levers beats a half-built agent with eight.

---

## Hours 10-13: Requirements 2, 3, 4 completed (P0)

- `budget.py`: quota polling, 402 handling, degradation ladder.
- Confidential mode wired end to end: key swap, registry without search, empty
  fallbacks, egress assertion armed, fail-closed on Ollama down.
- Fallback announcement on provider change, printed once per transition.

**Exit criterion.** All four mandatory requirements demonstrable by hand.
Everything after this point is score, not qualification.

---

## Hours 13-17: Token levers (P1)

In descending order of expected value. Stop when hour 17 arrives, wherever you are.

1. File memory, lever 3. Highest ratio of saving to effort.
2. Stateless sub-calls and cache keys, lever 1. Highest ceiling, most config risk.
3. Tiered routing, lever 2.
4. Retrieval windows, lever 4.
5. Progressive tool disclosure, lever 6. Nearly free to implement.
6. Context pruning, lever 5. Most complex, most likely to introduce a
   correctness bug. Attempt only with real time remaining.

Levers 7 and 8 should already be in the loop from hour 6.

**Exit criterion.** `LeverFlags` toggles each one and the agent still completes
task 1 with every combination you intend to benchmark.

---

## Hours 17-20: Evidence harness (P1)

`bench/` and the five scenarios. This is what converts work already done into
something a judge can verify, so it ranks above any further lever.

Order: scenario 1 first since it needs the most runtime, then 4 and 5, which are
the adversarial ones and the most memorable, then 2 and 3.

**Exit criterion.** `make proof` runs clean and writes `evidence/`.

---

## Hours 20-22: Demo and recording (P1)

Rehearse [10-DEMO.md](10-DEMO.md) three times with a timer. Record a full run.
Prepare the fallback video.

Fix only bugs that break the demo. A bug nobody will see is not a bug today.

---

## Hours 22-24: Buffer

Reserved. It will be consumed by something unforeseen, and if it is not, use it
to re-run `make proof` and write `SUMMARY.md` properly.

**Do not start a new feature after hour 22.** The failure mode of hackathons is
a half-finished addition breaking a working demo at hour 23.

---

## Cut lines, decided now

| If behind at | Cut |
| --- | --- |
| Hour 6 | Git tool. Shell plus manual git is enough |
| Hour 10 | Levers 4, 5, 6. Ship with file memory, caching, routing |
| Hour 13 | Tiered routing collapses to local-for-classification, capable-for-everything |
| Hour 17 | Benchmark drops to two tasks instead of four |
| Hour 20 | Scenarios 2 and 3 become manual demos with screenshots instead of harness runs |

Never cut: the policy gate, confidential mode enforcement, the 402 path, or the
fallback announcement. Those are the qualification criteria.

---

## Standing rules

- Commit after every exit criterion. A green state you can return to is worth
  more than an hour of forward progress.
- The seed repository for the benchmark is built at hour 3, not hour 17. Tasks
  need to be fixed before anything is tuned against them, or the benchmark is
  measuring what you tuned for.
- If a component fails twice, take the documented slower path rather than
  attempting a third fix.
