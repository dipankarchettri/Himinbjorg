"""One ablation configuration: all levers on, or one off. Repeats outermost,
so a deadline leaves every task with about the same number of runs.

usage: ablate.py ROOT CONFIG STAMP REPEATS DEADLINE_EPOCH OUT_JSON [TASKS]
CONFIG: all | file_memory | retrieval | pruning | tool_disclosure |
        diff_writes | loop_limits (that lever off) | subagent | plan (every
        lever on, plus AGENT_SUBAGENT or AGENT_PLAN)
ROOT is the checkout the agent runs from; BENCH_GATEWAY_DIR (default ROOT)
is where `make up` brings the gateway back. Run one process per CONFIG,
in parallel, and compare them with bench/ablation_report.py.
"""
import json, os, sys, time
from dataclasses import asdict
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from agent.config import LeverFlags, load_dotenv          # noqa: E402
from bench.benchmark import run_one                       # noqa: E402
from bench.tasks import TASKS, TASKS_BY_ID                # noqa: E402

config, stamp, reps, deadline, out = sys.argv[2], sys.argv[3], int(sys.argv[4]), float(sys.argv[5]), Path(sys.argv[6])
tasks = [TASKS_BY_ID[t] for t in sys.argv[7].split(",")] if len(sys.argv) > 7 else TASKS
env = {**load_dotenv(root / ".env"),
       **{k: v for k, v in os.environ.items() if k.startswith(("BENCH_", "AGENT_", "BIFROST_VK_"))}}
# "subagent", "plan": every lever on, plus that addition.
EXTRA = {"subagent": "AGENT_SUBAGENT", "plan": "AGENT_PLAN"}
if config in EXTRA:
    flags, arm = LeverFlags(), f"with_{config}"
    env[EXTRA[config]] = "1"
else:
    flags = LeverFlags() if config == "all" else LeverFlags(**{**LeverFlags().as_dict(), config: False})
    arm = "all_on" if config == "all" else f"no_{config}"
rows = []
GATEWAY = Path(os.environ.get("BENCH_GATEWAY_DIR", root))


def healthy() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:8099/health", timeout=3)
        return True
    except OSError:
        return False


def up() -> None:
    import subprocess
    if healthy():
        return
    subprocess.run(["make", "up"], cwd=GATEWAY, capture_output=True)
    for _ in range(60):
        if healthy():
            return
        time.sleep(2)


for rep in range(reps):
    for t in tasks:
        if time.time() > deadline:
            break
        # The nudge run brings the gateway down when it ends: bring it back
        # up, and run again a run that lost it.
        for attempt in range(3):
            up()
            r = run_one(t, arm, flags, env, stamp)
            if "unreachable" not in (r.stop + r.error):
                break
        rows.append({**asdict(r), "repeat": rep + 1})
        out.write_text(json.dumps({"config": config, "levers": flags.as_dict(), "runs": rows}, indent=1))
        print(f"{arm:20} rep{rep + 1} {t.id:15} ok={r.success} tokens={r.total_tokens} "
              f"turns={r.turns} {r.wall_seconds}s {r.error[:60]}", flush=True)
