"""Scenario 3: budget enforced before the provider is reached.

Drives a dedicated virtual key with a $0.05 limit until Bifrost returns
402, then reports what the gateway recorded.

IMPORTANT, and stated out loud in the demo: local inference is genuinely
free, so Bifrost meters it at $0 and no budget could ever trip. A
SYNTHETIC pricing override (governance.pricing_overrides, scoped to this
key only) assigns a fake price to the local models so the limit is
reachable. The price is fake; the 402, the pre-provider block and the
degradation ladder are the real mechanism.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://localhost:8099"
VK = "sk-bf-budget-demo-key"
VK_ID = "vk-user-budget-demo"
MODEL = os.environ.get("BUDGET_MODEL", "ollama/qwen2.5:7b-instruct")
# BUDGET_MODEL must be allowlisted for the budget virtual key in
# infra/data/config.json (e.g. BUDGET_MODEL=ollama/qwen2.5:3b for a smoke test).
SETUP_TOKEN = "bifrost-local-setup-token"
MAX_CALLS = 40
SETTLE_SECONDS = 45   # usage is flushed asynchronously; see note in main()


def quota() -> dict:
    req = urllib.request.Request(
        BASE + "/api/governance/virtual-keys/quota", headers={"x-bf-vk": VK})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def call(n: int):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": f"Say the number {n} and nothing else."}],
        "max_tokens": 40,
    }
    req = urllib.request.Request(
        BASE + "/openai/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-bf-vk": VK})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        return e.code, body, dict(e.headers)


def gateway_log(request_id: str) -> dict:
    if not request_id:
        return {}
    req = urllib.request.Request(
        f"{BASE}/api/logs/{request_id}", headers={"X-Bifrost-Setup-Token": SETUP_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:                                  # noqa: BLE001
        return {}


def reset_usage() -> None:
    """Zero the demo budget so the run starts from $0, not from leftovers.

    Without this the key stays exhausted from earlier runs and the 402
    arrives at call 1, which proves nothing about enforcement. Bifrost only
    applies reset_budget_usage when the budgets are resubmitted with it
    (verified 2026-11-15 against PUT /api/governance/virtual-keys/{id}).
    """
    payload = {"reset_budget_usage": True,
               "budgets": [{"max_limit": 0.05, "reset_duration": "1M"}]}
    req = urllib.request.Request(
        f"{BASE}/api/governance/virtual-keys/{VK_ID}", data=json.dumps(payload).encode(),
        method="PUT", headers={"Content-Type": "application/json",
                               "X-Bifrost-Setup-Token": SETUP_TOKEN})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def budget_of(q: dict) -> dict:
    for b in q.get("budgets") or []:
        if b.get("id") == "bud-budget-demo":
            return b
    return (q.get("budgets") or [{}])[0]


def main() -> int:
    print("=" * 68)
    print("SCENARIO 3: budget enforced BEFORE the provider call")
    print("=" * 68)
    reset_usage()
    time.sleep(2)
    b = budget_of(quota())
    print(f"  key    : {VK}")
    print(f"  limit  : ${float(b.get('max_limit', 0)):.4f}")
    print(f"  used   : ${float(b.get('current_usage', 0)):.6f}")
    print("  NOTE   : local inference is free; a SYNTHETIC pricing override")
    print("           makes the limit reachable. The 402 path is real.")
    print()

    results = []
    blocked_at = None
    for n in range(1, MAX_CALLS + 1):
        status, body, headers = call(n)
        rid = {k.lower(): v for k, v in headers.items()}.get("x-bifrost-request-id", "")
        b = budget_of(quota())
        used = float(b.get("current_usage", 0))
        usage = (body.get("usage") or {}) if isinstance(body, dict) else {}
        print(f"  call {n:>2}: HTTP {status}  used=${used:.6f}  "
              f"tokens={usage.get('total_tokens', '-')}  rid={rid[:8]}")
        results.append({"n": n, "status": status, "used": used, "request_id": rid})

        if status == 402:
            blocked_at = n
            err = body.get("error", {}) if isinstance(body, dict) else {}
            print()
            print("  BLOCKED. Gateway response:")
            print(f"    type    : {body.get('type') if isinstance(body, dict) else '?'}")
            print(f"    message : {err.get('message') or err.get('error')}")
            print()
            # Logs are written asynchronously, like usage. Give the write a
            # moment or the lookup 404s on a request that did happen.
            time.sleep(5)
            log = gateway_log(rid)
            if log:
                print("  Independent gateway log record for the blocked request:")
                for k in ("provider", "model", "status", "object",
                          "virtual_key_name", "cost", "total_tokens"):
                    if k in log:
                        print(f"    {k:18}: {log[k]}")
                served = log.get("provider") and log.get("total_tokens")
                print()
                print(f"    provider actually invoked: {'YES' if served else 'NO'}")
                print("    (NO is the requirement: blocked before reaching a provider)")
            break

    if not blocked_at:
        # Budget usage is flushed asynchronously: current_usage lags real
        # spend by tens of seconds. Verified 2026-11-14, where 25 calls
        # showed $0.0048 live and settled to $0.02592 afterwards. Wait for
        # the flush before issuing the verdict, and try once more.
        print(f"\n  No 402 yet. Waiting {SETTLE_SECONDS}s for asynchronous usage flush...")
        time.sleep(SETTLE_SECONDS)
        b = budget_of(quota())
        print(f"  settled usage: ${float(b.get('current_usage', 0)):.6f} "
              f"of ${float(b.get('max_limit', 0)):.4f}")
        status, body, headers = call(MAX_CALLS + 1)
        rid = {k.lower(): v for k, v in headers.items()}.get("x-bifrost-request-id", "")
        print(f"  post-settle call: HTTP {status}")
        results.append({"n": MAX_CALLS + 1, "status": status,
                        "used": float(b.get("current_usage", 0)), "request_id": rid})
        if status == 402:
            blocked_at = MAX_CALLS + 1
            err = body.get("error", {}) if isinstance(body, dict) else {}
            print(f"    message : {err.get('message') or err.get('error')}")

    print()
    served_before_block = sum(1 for r in results if r["status"] == 200)
    if blocked_at and served_before_block == 0:
        print("  RESULT: FAIL - 402 at call 1 with no prior spend in this run;")
        print("          the budget was already exhausted, so this proves nothing.")
        blocked_at = None
    elif blocked_at:
        print(f"  RESULT: PASS - {served_before_block} calls served, then 402 at call {blocked_at}")
    else:
        print(f"  RESULT: INCONCLUSIVE - no 402 within {MAX_CALLS} calls.")
        print("          Check that the pricing override is loaded and usage is moving.")

    out = Path(__file__).resolve().parent.parent / "evidence"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"scenario3-budget-{stamp}.json"
    path.write_text(json.dumps({
        "scenario": "budget_402_before_provider",
        "virtual_key": VK,
        "synthetic_pricing": True,
        "blocked_at_call": blocked_at,
        "calls": results,
    }, indent=2))
    print(f"  evidence: {path}")
    return 0 if blocked_at else 1


if __name__ == "__main__":
    raise SystemExit(main())
