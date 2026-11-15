"""make proof - run every scenario and write one evidence directory.

Numbers in the evidence pack come from the gateway (response bodies,
x-bifrost-* headers, /api/logs, /api/governance quota), not from the
agent's own report. The agent is not a neutral witness about itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")

SCENARIOS = [
    ("0-security-tests", [PY, "-m", "pytest", "bench/test_security.py", "bench/test_write.py", "bench/test_tasks.py", "bench/test_loop.py", "bench/test_server.py", "-q"],
     "Unit tests: security, edit tools, agent loop, task checkers (no gateway needed)"),
    ("1-token-efficiency", [PY, "bench/benchmark.py", "--tasks", "all", "--repeats", "5"],
     "Naive baseline vs all levers, 7 tasks x 5 repeats at T=0.7: pass rate and tokens per completed task"),
    ("1b-cache-rerun", [PY, "bench/cache_scenario.py", "add_power"],
     "Identical re-run served from the gateway cache"),
    ("2-failover", [PY, "bench/failover_scenario.py"],
     "Provider outage, failover with attribution"),
    ("3-budget", [PY, "bench/budget_scenario.py"],
     "402 before the provider call (synthetic pricing, disclosed)"),
    ("4-privacy", [PY, "bench/privacy_scenario.py"],
     "Confidential repo under an adversarial prompt"),
    ("5-approval", [PY, "bench/approval_scenario.py"],
     "Approval gate versus prompt injection"),
]

VERDICT = {0: "PASS", 2: "INCONCLUSIVE"}


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    started_at = time.time()
    out = ROOT / "evidence" / stamp
    out.mkdir(parents=True, exist_ok=True)
    results = []

    print(f"Writing evidence to {out}\n")
    for name, cmd, desc in SCENARIOS:
        print(f"=== {name}: {desc}")
        started = time.time()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        elapsed = round(time.time() - started, 1)
        log = out / f"{name}.log"
        log.write_text((proc.stdout or "") + "\n[stderr]\n" + (proc.stderr or ""))
        verdict = VERDICT.get(proc.returncode, "FAIL")
        print(f"    {verdict}  ({elapsed}s)  -> {log.name}\n")
        results.append({"scenario": name, "description": desc, "verdict": verdict,
                        "exit_code": proc.returncode, "seconds": elapsed,
                        "log": log.name})

    # Scenario-specific JSON artifacts written by the individual scripts.
    # Only artifacts written during THIS run. Older ones would silently mix
    # results from earlier code into the pack.
    for art in sorted((ROOT / "evidence").glob("scenario*")):
        if art.is_file() and art.stat().st_mtime >= started_at:
            (out / art.name).write_text(art.read_text())

    summary = [
        "# Evidence Pack",
        "",
        f"Generated {stamp}. Re-run with `make proof`.",
        "",
        "Every number here is read from the Bifrost gateway (response bodies,",
        "x-bifrost-* headers, /api/logs, /api/governance quota), not from the",
        "agent's own output.",
        "",
        "## Disclosure",
        "",
        "Local inference is genuinely free, so Bifrost meters it at $0 and no",
        "budget could ever trip. Scenario 3 uses a SYNTHETIC pricing override",
        "scoped to a dedicated virtual key to make the limit reachable. The",
        "price is fake; the 402, the block before the provider, and the",
        "degradation ladder are real.",
        "",
        "Scenario 1 gives every run a fresh cache scope so repeats stay",
        "independent; cross-run reuse is measured separately in 1b. Bifrost",
        "reports the original usage on a cache hit, so hit tokens are counted",
        "as served from cache rather than provider-processed.",
        "",
        "Scenario 5 runs its non-interactive half here. The interactive half,",
        "a human seeing the prompt and answering it, must be run at a terminal.",
        "",
        "## Results",
        "",
        "| Scenario | Verdict | Seconds |",
        "| --- | --- | --- |",
    ]
    for r in results:
        summary.append(f"| {r['scenario']} - {r['description']} | **{r['verdict']}** | {r['seconds']} |")
    summary += ["", "## Notes", "",
                "- INCONCLUSIVE is a real outcome, not a soft failure. Scenario 5",
                "  reports it when the model never attempted a gated action, because",
                "  a non-attempt tests nothing.",
                "- Token counts are gateway-reported. Both benchmark arms are the",
                "  same binary; only the lever flags differ. The naive arm has every",
                "  lever off, which includes diff writes: it edits by rewriting whole",
                "  files, as a simple agent does.",
                "- Three of the seven tasks run on a 21-file repository. They were",
                "  written before any results, but the agent was improved after their",
                "  first run, so they are no longer held out. See docs/VERIFIED.md.",
                "- Budget cost is synthetic for local models (a pricing override);",
                "  the 402 and the pre-provider block are real.",
                ]
    (out / "SUMMARY.md").write_text("\n".join(summary) + "\n")
    (out / "results.json").write_text(json.dumps(
        {"generated": stamp, "results": results}, indent=2))

    print("\n".join(summary[-(len(results) + 4):]))
    print(f"\nEvidence pack: {out}")
    failures = [r for r in results if r["verdict"] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
