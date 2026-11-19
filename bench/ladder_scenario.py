"""Scenario 3b: the T2 -> T1 -> T0 degradation ladder under real budgets.

One virtual key (sk-bf-ladder-key) with three providers, each budgeted on
its own provider config:

    T2 capable  gemini/gemini-3.5-flash-lite  Gemini budget  $0.0008
    T1 cheap    groq/openai/gpt-oss-120b      Groq budget    $0.0006
    T0 local    vllm/gemma-4-31b-it-qat...    no budget (local inference)

Bifrost prices both cloud models from its own catalogue; nothing here is
synthetic. A provider over its budget gets HTTP 402 while the key's other
providers keep working (checked 2026-11-18). The request carries the
fallback chain T2 -> T1 -> T0, so the GATEWAY walks the ladder: a provider
over budget is skipped and the next one answers, with no 402 reaching the
agent. The agent's own 402 ladder (agent/loop.py) is for requests without
a chain. PASS needs: the gateway's own log shows each cloud rung left
because of its budget (402 budget_exceeded on the primary attempt), each
of the three providers served at least one call, and the task solved.
Whether the agent's live message already said "budget" is reported too;
the quota endpoint lags the gateway, so on a short task it may not.

    python bench/ladder_scenario.py [task_id]

The models can be changed with LADDER_T2, LADDER_T1, LADDER_T0; they must
be allowlisted on vk-user-ladder in infra/data/config.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import telemetry, trace                         # noqa: E402
from agent.config import LeverFlags, load_dotenv           # noqa: E402
from agent.llm import LLMClient                             # noqa: E402
from agent.loop import Agent                                # noqa: E402
from agent.security.privacy import PrivacyMode              # noqa: E402
from agent.tools.registry import Registry                   # noqa: E402
from bench.benchmark import build_settings                  # noqa: E402
from bench.tasks import TASKS_BY_ID, WORK_ROOT, materialise  # noqa: E402

BASE = "http://localhost:8099"
SETUP_TOKEN = os.environ.get("BIFROST_SETUP_TOKEN", "bifrost-local-setup-token")
VK = "sk-bf-ladder-key"
VK_ID = "vk-user-ladder"
# 3.5-flash-lite, not 3.5-flash: on 2026-11-18 the free tier answered
# 3.5-flash with 503 "high demand" after 30 s, so the run fell back
# before any budget was spent.
T2 = os.environ.get("LADDER_T2", "gemini/gemini-3.5-flash-lite")
# 120b, not 20b: gpt-oss-20b called a tool that does not exist (open_file)
# and Groq rejected the request with 400, so the chain moved on for a
# reason other than the budget.
T1 = os.environ.get("LADDER_T1", "groq/openai/gpt-oss-120b")
T0 = os.environ.get("LADDER_T0", "vllm/gemma-4-31b-it-qat-w4a16")

# Provider config ids and budgets, matching infra/data/config.json. The
# reset below resubmits them, so these limits are what the run uses.
# Small enough that each cloud rung runs out within a few calls of a
# short task (about $0.0003 per 1,000 tokens on Flash-Lite).
PROVIDER_CONFIGS = [
    {"id": 901, "provider": "gemini", "weight": 1.0, "key_ids": ["*"],
     "allowed_models": ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
     "budgets": [{"max_limit": 0.0008, "reset_duration": "1M"}]},
    {"id": 903, "provider": "groq", "weight": 1.0, "key_ids": ["*"],
     "allowed_models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
     "budgets": [{"max_limit": 0.0006, "reset_duration": "1M"}]},
    {"id": 902, "provider": "vllm", "weight": 1.0, "key_ids": ["*"],
     "allowed_models": ["gemma-4-31b-it-qat-w4a16"]},
]


def _put_vk(payload: dict) -> None:
    req = urllib.request.Request(
        f"{BASE}/api/governance/virtual-keys/{VK_ID}", data=json.dumps(payload).encode(),
        method="PUT", headers={"Content-Type": "application/json",
                               "X-Bifrost-Setup-Token": SETUP_TOKEN})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def reset_budgets() -> None:
    """Zero both provider budgets. As with the key-level budget, Bifrost
    applies the reset only when the budgets are resubmitted with it."""
    _put_vk({"reset_budget_usage": True, "provider_configs": PROVIDER_CONFIGS})


def provider_budgets() -> dict:
    req = urllib.request.Request(f"{BASE}/api/governance/virtual-keys/quota",
                                 headers={"x-bf-vk": VK})
    with urllib.request.urlopen(req, timeout=10) as r:
        q = json.loads(r.read().decode())
    return {p["provider"]: {"max_limit": b["max_limit"], "current_usage": b["current_usage"]}
            for p in q.get("provider_configs") or [] for b in (p.get("budgets") or [])[:1]}


class RecordingClient(LLMClient):
    """Records which tier asked and which provider actually answered."""

    calls: list

    def call(self, spec):
        try:
            result = super().call(spec)
        except Exception as exc:
            self.calls.append({"model": spec.model, "error": type(exc).__name__})
            raise
        self.calls.append({"model": spec.model, "served": result.routing.describe(),
                           "fallback": result.routing.is_fallback,
                           "primary": result.routing.primary_provider,
                           "request_id": result.request_id,
                           "tokens": result.usage.total_tokens})
        return result


def primary_attempt(request_id: str) -> dict:
    """The gateway's log of the first attempt of a request, which carries
    the request id. Logs are written asynchronously, so retry."""
    for _ in range(6):
        req = urllib.request.Request(f"{BASE}/api/logs/{request_id}",
                                     headers={"X-Bifrost-Setup-Token": SETUP_TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode())
            d = d.get("log", d)
            err = d.get("error_details") or {}
            return {"provider": d.get("provider"), "status": d.get("status"),
                    "error_type": err.get("type"),
                    "message": str((err.get("error") or {}).get("message", ""))[:160]}
        except Exception:                                  # noqa: BLE001
            time.sleep(5)
    return {}


def main() -> int:
    task = TASKS_BY_ID[sys.argv[1] if len(sys.argv) > 1 else "shop_bulk_bug"]
    print("=" * 68)
    print("SCENARIO 3b: T2 -> T1 -> T0 degradation ladder under real budgets")
    print("=" * 68)
    print(f"  key  : {VK}")
    print(f"  T2   : {T2}  (Gemini budget ${PROVIDER_CONFIGS[0]['budgets'][0]['max_limit']})")
    print(f"  T1   : {T1}  (Groq budget ${PROVIDER_CONFIGS[1]['budgets'][0]['max_limit']})")
    print(f"  T0   : {T0}  (local, no budget)")
    print(f"  task : {task.id}\n")

    reset_budgets()
    time.sleep(2)
    before = provider_budgets()
    if any(b["current_usage"] > 0 for b in before.values()):
        print(f"  FAIL: budgets did not reset: {before}")
        return 1

    env = {**load_dotenv(ROOT / ".env"), "BIFROST_VK_DEMO": VK,
           "BENCH_MODEL_CAPABLE": T2, "BENCH_MODEL_CHEAP": T1,
           "AGENT_MODEL_LOCAL_CODE": T0, "AGENT_MODEL_LOCAL_SMALL": T0}
    repo = materialise(task, WORK_ROOT / f"ladder-{task.id}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trace.init(ROOT / "traces" / f"ladder-{stamp}.jsonl")
    # Routing off so the run starts on T2: the ladder is the subject here,
    # not the router. Every other lever is on, as shipped.
    settings = build_settings(repo, LeverFlags(routing=False), env,
                              f"ladder-{stamp}-{uuid.uuid4().hex[:8]}")
    privacy = PrivacyMode.build(False, settings.vk_standard, settings.vk_confidential)
    client = RecordingClient(settings, privacy)
    client.calls = []
    lines: list[str] = []

    def printer(*a, **_k):
        text = " ".join(map(str, a))
        lines.append(text)
        if any(w in text for w in ("Budget", "budget", "Downgrading", "Provider")):
            print(text)

    agent = Agent(settings, privacy, client, Registry(privacy, settings), printer=printer)
    started = time.time()
    answer = agent.run(task.prompt)
    wall = round(time.time() - started, 1)
    ok = bool(task.verify(repo))
    time.sleep(15)                      # usage is written asynchronously
    after = provider_budgets()

    # The gateway's record of why each fallback happened.
    attempts = {c["request_id"]: primary_attempt(c["request_id"])
                for c in client.calls if c.get("fallback")}

    def left_for_budget(provider: str, tier: str) -> bool:
        # Either path counts: the gateway's chain skipping a provider over
        # budget, or a 402 the agent turned into a downgrade.
        return (any(a.get("provider") == provider and a.get("error_type") == "budget_exceeded"
                    for a in attempts.values())
                or any(f"Downgrading to {tier}" in ln for ln in lines))

    def announced(provider: str) -> bool:
        return any((f"Provider changed: {provider}/" in ln and "budget used up" in ln)
                   or (ln.lstrip().startswith(f"{provider}/")
                       and "that fallback was its budget" in ln)
                   or "Budget exceeded on" in ln for ln in lines)

    p2, p1 = T2.split("/")[0], T1.split("/")[0]
    steps = {"T2->T1": left_for_budget(p2, "T1_CHEAP"),
             "T1->T0": left_for_budget(p1, "T0_LOCAL")}
    agent_said_budget = {"T2->T1": announced(p2), "T1->T0": announced(p1)}
    served = {}
    for c in client.calls:
        if "served" in c:
            served[c["served"]] = served.get(c["served"], 0) + 1
    every_rung = all(any(name.startswith(m.split("/")[0] + "/") for name in served)
                     for m in (T2, T1, T0))
    refused = [c for c in client.calls if c.get("error") == "BudgetExceeded"]

    print(f"\n  answer : {answer[:100]}")
    print(f"  calls  : {len(client.calls)}, refused with 402: {len(refused)}")
    for name, n in served.items():
        print(f"    served by {name}: {n}")
    for prov, b in after.items():
        print(f"  {prov:6} budget: ${b['current_usage']:.6f} of ${b['max_limit']}")
    for rid, a in attempts.items():
        print(f"  gateway log {rid[:8]}: {a.get('provider')} {a.get('error_type')} "
              f"{a.get('message', '')[:70]}")
    print(f"  steps (gateway record) : {steps}")
    print(f"  agent said 'budget'    : {agent_said_budget}")
    print(f"  every rung served      : {every_rung}")
    print(f"  task   : {'PASS' if ok else 'FAIL'} ({wall}s)")

    passed = all(steps.values()) and every_rung and ok and not answer.startswith("stopped:")
    print(f"\n  RESULT: {'PASS' if passed else 'FAIL'}")

    cost = telemetry.session_cost(agent.stats.request_ids, BASE, SETUP_TOKEN)
    out = ROOT / "evidence" / f"scenario3b-ladder-{stamp}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "scenario": "degradation_ladder_real_budgets",
        "task": task.id, "tiers": {"T2": T2, "T1": T1, "T0": T0},
        "budgets_before": before, "budgets_after": after,
        "steps": steps, "agent_said_budget": agent_said_budget,
        "fallback_attempts": attempts, "every_rung_served": every_rung,
        "calls": client.calls, "served": served,
        "answer": answer, "task_passed": ok, "wall_seconds": wall,
        "gateway_cost_usd": round(cost[0], 6) if cost else None,
        "agent_output": lines, "result": "PASS" if passed else "FAIL",
    }, indent=2))
    print(f"  evidence: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
