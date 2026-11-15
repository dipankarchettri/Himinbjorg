"""Scenario 1b: gateway cache on an identical re-run.

Runs the same task twice under the same cache scope, as happens when a
session is retried after a crash or a CI job is re-run. Conversation calls
use DIRECT (exact-hash) matching only, so a hit means the model is being
asked precisely what it was asked before, with identical file contents.

Accounting: Bifrost reports the ORIGINAL usage on a cache hit, so a naive
token sum shows no saving at all. Tokens of hit calls are counted as
served from cache, not processed by any provider.

This is a separate measurement from scenario 1 on purpose. Scenario 1 gives
each run a fresh cache scope so repeats stay independent.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import LeverFlags, load_dotenv   # noqa: E402
from bench.benchmark import run_one                 # noqa: E402
from bench.tasks import TASKS_BY_ID                 # noqa: E402

TASK_ID = sys.argv[1] if len(sys.argv) > 1 else "add_power"


def main() -> int:
    env = load_dotenv(ROOT / ".env")
    task = TASKS_BY_ID[TASK_ID]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    scope = f"cachedemo-{stamp}-{uuid.uuid4().hex[:6]}"

    print("=" * 68)
    print(f"SCENARIO 1b: identical re-run of '{task.id}' under one cache scope")
    print("=" * 68)
    runs = []
    for label in ("first run (cold cache)", "re-run (same task, same repo)"):
        r = run_one(task, "optimized", LeverFlags(), env, stamp, cache_scope=scope)
        runs.append(r)
        print(f"  {label:32} ok={r.success}  calls={r.llm_calls}  hits={r.cache_hits}  "
              f"processed_by_provider={r.provider_tokens}  from_cache={r.cached_tokens}")

    first, second = runs
    saved = first.provider_tokens - second.provider_tokens
    ok = first.success and second.success and second.cache_hits > 0
    print()
    if first.provider_tokens:
        print(f"  provider tokens: {first.provider_tokens} -> {second.provider_tokens} "
              f"({saved / first.provider_tokens * 100:.0f}% fewer on the re-run)")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'} - "
          + ("re-run served from cache and still completed the task"
             if ok else "no cache hits on the re-run, or a run failed"))

    out = ROOT / "evidence"; out.mkdir(exist_ok=True)
    path = out / f"scenario1b-cache-{stamp}.json"
    path.write_text(json.dumps({
        "scenario": "gateway_cache_identical_rerun", "task": task.id,
        "cache_scope": scope, "match": "direct (exact hash) only",
        "note": "Bifrost reports original usage on a hit; hit tokens are counted as "
                "served from cache, not provider-processed.",
        "runs": [r.__dict__ for r in runs], "passed": ok,
    }, indent=2, default=str))
    print(f"  evidence: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
