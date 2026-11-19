"""Run opencode on the benchmark tasks, through the same gateway and model.

Each run gets a fresh copy of the task repository, `opencode run --auto`
with the task's own prompt, and the task's own checker. Tokens come from
the gateway's request log (requests whose user agent is opencode's, in the
run's time window), the same source the benchmark's cost column uses.

opencode is pointed at the gateway by an opencode.json in OPENCODE_HOME,
and its config, data and cache live there too (XDG_* variables), so a
user's own opencode setup is neither read nor touched:

    OPENCODE_BIN=.../opencode OPENCODE_HOME=.../oc-home BF_VK=sk-bf-... \\
        python bench/opencode_compare.py --tasks all --repeats 5

with OPENCODE_HOME/config/opencode/opencode.json (opencode's documented
custom OpenAI-compatible provider; the key comes from BF_VK):

    {"model": "bifrost/qwen",
     "providers": {"bifrost": {
       "package": "@opencode/ai/providers/openai-compatible",
       "settings": {"baseURL": "http://localhost:8099/openai", "apiKey": "{env:BF_VK}"},
       "headers": {"x-bf-vk": "{env:BF_VK}"},
       "models": {"qwen": {"modelID": "vllm/qwen3.8-27b-fp8",
                           "capabilities": {"tools": true, "input": ["text"], "output": ["text"]},
                           "limit": {"context": 32768, "output": 8192}}}}}}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.tasks import TASKS, TASKS_BY_ID, WORK_ROOT, materialise  # noqa: E402

EVIDENCE = ROOT / "evidence"
WORK = WORK_ROOT.parent / "bifrost-opencode-work"
GATEWAY = os.environ.get("BIFROST_BASE_URL", "http://localhost:8099").rstrip("/")
SETUP_TOKEN = os.environ.get("BIFROST_SETUP_TOKEN", "bifrost-local-setup-token")
TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "900"))


@dataclass
class Run:
    task: str
    success: bool
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    wall_seconds: float = 0.0
    exit_code: int | None = None
    error: str = ""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def gateway_logs(since: str, until: str) -> list[dict]:
    """opencode's requests logged between since and until (UTC, seconds).
    Newest first, paged until the log is older than the window."""
    found, offset = [], 0
    while True:
        req = urllib.request.Request(f"{GATEWAY}/api/logs?limit=100&offset={offset}",
                                     headers={"X-Bifrost-Setup-Token": SETUP_TOKEN})
        with urllib.request.urlopen(req, timeout=30) as resp:
            logs = json.load(resp).get("logs") or []
        for log in logs:
            ts = log["timestamp"][:19]
            if since <= ts <= until and (log.get("user_agent") or "").startswith("opencode/"):
                found.append(log)
        if not logs or logs[-1]["timestamp"][:19] < since:
            return found
        offset += len(logs)


def checkout_state() -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                          capture_output=True, text=True).stdout


def run_one(task, opencode: str, env: dict, stamp: str, rep: int) -> Run:
    # Its own work root, away from the benchmark's task copies: opencode
    # explores parent directories when a model wanders.
    repo = materialise(task, WORK / task.id)
    before = checkout_state()
    started, since = time.time(), utc_now()
    res = Run(task=task.id, success=False)
    try:
        proc = subprocess.run(
            [opencode, "run", "--standalone", "--archived", "--auto",
             "--title", f"bench {stamp} {task.id} {rep}", task.prompt],
            # opencode takes its project directory from $PWD, not the
            # process's working directory: without this it works on
            # whatever directory launched the benchmark.
            cwd=repo, env={**env, "PWD": str(repo)}, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=TIMEOUT)
        res.exit_code = proc.returncode
        log = ROOT / "traces" / f"opencode-{stamp}-{task.id}-{rep}.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
        if proc.returncode:
            res.error = proc.stderr.strip()[-300:]
    except subprocess.TimeoutExpired:
        res.error = f"timeout after {TIMEOUT}s"
    res.wall_seconds = round(time.time() - started, 1)
    if checkout_state() != before:
        sys.exit(f"opencode changed files in {ROOT} during {task.id}; stopping")
    time.sleep(3)          # the gateway writes its log asynchronously
    for log in gateway_logs(since, utc_now()):
        usage = log.get("token_usage") or {}
        res.llm_calls += 1
        res.prompt_tokens += usage.get("prompt_tokens") or 0
        res.completion_tokens += usage.get("completion_tokens") or 0
    res.total_tokens = res.prompt_tokens + res.completion_tokens
    try:
        res.success = bool(task.verify(repo))
    except Exception as exc:                                # noqa: BLE001
        res.error = res.error or f"verify failed: {exc}"[:200]
    return res


def summarise(runs: list[Run]) -> str:
    lines = [f"{'task':16} {'passed':>7} {'tokens/completed':>17} {'calls/run':>9} {'secs/run':>8}"]
    groups = {}
    for r in runs:
        groups.setdefault(r.task, []).append(r)
    for task, rs in [*groups.items(), ("ALL", runs)]:
        ok = sum(r.success for r in rs)
        per = sum(r.total_tokens for r in rs) // ok if ok else 0
        lines.append(f"{task:16} {ok:>3}/{len(rs):<3} {per:>17,} "
                     f"{sum(r.llm_calls for r in rs) / len(rs):>9.1f} "
                     f"{sum(r.wall_seconds for r in rs) / len(rs):>8.1f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="opencode on the benchmark tasks")
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    opencode = os.environ["OPENCODE_BIN"]
    home = Path(os.environ["OPENCODE_HOME"])
    if "BF_VK" not in os.environ:
        sys.exit("BF_VK must name the virtual key opencode.json refers to")
    env = {**os.environ,
           **{f"XDG_{k}_HOME": str(home / k.lower()) for k in ("CONFIG", "DATA", "STATE", "CACHE")}}
    version = subprocess.run([opencode, "--version"], capture_output=True, text=True,
                             env=env).stdout.strip()
    config = json.loads((home / "config" / "opencode" / "opencode.json").read_text())
    tasks = TASKS if args.tasks == "all" else [TASKS_BY_ID[t] for t in args.tasks.split(",")]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    WORK.mkdir(parents=True, exist_ok=True)

    runs = []
    for task in tasks:
        for rep in range(args.repeats):
            print(f"  running {task.id:16} [opencode#{rep + 1}] ...", flush=True)
            r = run_one(task, opencode, env, stamp, rep + 1)
            print(f"    -> ok={r.success} tokens={r.total_tokens} calls={r.llm_calls} "
                  f"{r.wall_seconds}s {r.error[:120]}", flush=True)
            runs.append(r)

    text = summarise(runs)
    print(text)
    EVIDENCE.mkdir(exist_ok=True)
    out = EVIDENCE / f"opencode-compare-{stamp}.json"
    out.write_text(json.dumps({
        "opencode_version": version,
        "model": config["providers"]["bifrost"]["models"]["qwen"]["modelID"],
        "repeats": args.repeats,
        "runs": [asdict(r) for r in runs],
        "summary": text,
    }, indent=2))
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
