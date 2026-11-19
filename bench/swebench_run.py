"""SWE-bench Verified subset: real repositories, graded by the official harness.

Three stages, each a subcommand:

    select  (.venv-swebench)  pick the subset with a fixed seed; writes the
            instance list (committed, bench/swebench_subset.json) and a
            git-ignored cache with the issue texts (bench/.swebench/).
    run     (.venv)           check out each instance at its base commit,
            run the agent once per arm and repeat, and record the diff and
            the gateway-reported tokens.
    grade   (.venv-swebench)  grade every recorded diff with the official
            harness (swebench.harness.run_evaluation) and print pass rate
            and tokens per completed task per arm.

The subset is fixed by the seed and written to the repository before any
run, so it cannot be re-picked after seeing results. Only small fixes are
eligible: Verified's "<15 min fix" difficulty and a gold patch that touches
one file. The gold patch is never shown to the agent.

The repositories' dependencies are not installed on the host, so the
agent usually cannot run their tests; both arms are told so and share the
handicap. The official harness runs the tests in its own containers.

    .venv-swebench/bin/python bench/swebench_run.py select --n 20
    .venv/bin/python bench/swebench_run.py run --arms naive,optimized --repeats 3
    .venv-swebench/bin/python bench/swebench_run.py grade RUN_DIR
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = "SWE-bench/SWE-bench_Verified"
PARQUET = (f"https://huggingface.co/datasets/{DATASET}/resolve/main/"
           "data/test-00000-of-00001.parquet")
CACHE = ROOT / "bench" / ".swebench"
SUBSET = ROOT / "bench" / "swebench_subset.json"
SEED = 20261119

PROMPT = """Resolve this GitHub issue in the repository.

{problem}

Change only the library code needed to resolve the issue; do not edit or
add tests. The repository's dependencies are not installed here, so its
test suite may not run; if a command fails for that reason, rely on
reading the code."""


# ------------------------------------------------------------------ select

def _files_in_patch(patch: str) -> set[str]:
    return {line[6:] for line in patch.splitlines() if line.startswith("+++ b/")}


def select(n: int, seed: int) -> int:
    import pyarrow.parquet as pq

    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / "verified.parquet"
    if not local.exists():
        urllib.request.urlretrieve(PARQUET, local)
    rows = pq.read_table(local).to_pylist()
    eligible = sorted((r for r in rows
                       if r.get("difficulty") == "<15 min fix"
                       and len(_files_in_patch(r["patch"])) == 1),
                      key=lambda r: r["instance_id"])
    picked = random.Random(seed).sample(eligible, n)
    picked.sort(key=lambda r: r["instance_id"])
    SUBSET.write_text(json.dumps({
        "dataset": DATASET, "seed": seed, "n": n,
        "rule": "difficulty '<15 min fix' and a gold patch touching one file, "
                "sorted by instance_id, then random.Random(seed).sample(n)",
        "eligible": len(eligible),
        "instances": [{"instance_id": r["instance_id"], "repo": r["repo"],
                       "base_commit": r["base_commit"]} for r in picked],
    }, indent=2) + "\n")
    # Issue texts stay out of git: the run stage reads them from here.
    (CACHE / "problems.json").write_text(json.dumps(
        {r["instance_id"]: r["problem_statement"] for r in picked}, indent=2))
    print(f"{len(eligible)} eligible of {len(rows)}; picked {n} with seed {seed}")
    for r in picked:
        print(f"  {r['instance_id']}")
    return 0


# --------------------------------------------------------------------- run

def _git(*args, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout


def checkout(repo: str, commit: str, dest: Path) -> Path:
    """A clean working tree of `repo` at `commit`, from a shared mirror."""
    mirror = CACHE / "mirrors" / repo.replace("/", "__")
    if not mirror.exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "-q", "--mirror", f"https://github.com/{repo}.git", str(mirror))
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=True)
    _git("clone", "-q", "--shared", "--no-checkout", str(mirror), str(dest))
    _git("checkout", "-q", commit, cwd=dest)
    # The agent's git tool and the diff below must see only this commit.
    _git("remote", "remove", "origin", cwd=dest)
    return dest


def run(arms: list[str], repeats: int, limit: int | None, only: list[str] | None = None) -> int:
    sys.path.insert(0, str(ROOT))
    from agent.config import load_dotenv
    from bench.benchmark import ARMS, run_one
    from bench.tasks import Task

    subset = json.loads(SUBSET.read_text())["instances"]
    if only:
        subset = [i for i in subset if i["instance_id"] in only]
    subset = subset[:limit]
    problems = json.loads((CACHE / "problems.json").read_text())
    env = {**load_dotenv(ROOT / ".env"),
           **{k: v for k, v in os.environ.items()
              if k.startswith(("BENCH_", "AGENT_", "BIFROST_VK_"))}}
    env.setdefault("BENCH_MAX_TURNS", "30")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "evidence" / f"swebench-{stamp}"
    out_dir.mkdir(parents=True)
    work = Path(os.environ.get("SWEBENCH_WORK", "/tmp/himin-swebench"))
    rows = []
    for inst in subset:
        iid = inst["instance_id"]
        task = Task(id=iid, prompt=PROMPT.format(problem=problems[iid]), files={},
                    verify=lambda repo: False)
        for arm in arms:
            make_levers, prune_mode = ARMS[arm]
            for rep in range(repeats):
                repo = checkout(inst["repo"], inst["base_commit"], work / f"{iid}-{arm}")
                print(f"  {iid:40} [{arm}#{rep + 1}] ...", flush=True)
                r = run_one(task, arm, make_levers(), env, stamp,
                            prune_mode=prune_mode, repo=repo)
                if r.stop.startswith("stopped: configuration error"):
                    # A wrong key or model voids every run after it too.
                    sys.exit(f"configuration error on {iid}; check BIFROST_VK_DEMO "
                             "and the model settings")
                diff = _git("diff", cwd=repo)
                row = {**asdict(r), "repeat": rep + 1, "model_patch": diff}
                rows.append(row)
                print(f"    -> tokens={r.total_tokens} turns={r.turns} {r.wall_seconds}s "
                      f"diff={len(diff.splitlines())} lines {r.stop[:60]} {r.error[:80]}",
                      flush=True)
                (out_dir / "runs.json").write_text(json.dumps({
                    "dataset": DATASET, "subset": str(SUBSET.relative_to(ROOT)),
                    "models": {k: env.get(k) for k in ("BENCH_MODEL_CHEAP", "BENCH_MODEL_CAPABLE",
                                                         "AGENT_MODEL_LOCAL_CODE")},
                    "max_turns": env["BENCH_MAX_TURNS"], "runs": rows}, indent=2))
    print(f"  runs: {out_dir / 'runs.json'}")
    return 0


# ------------------------------------------------------------------- grade

def _eval_images() -> set[str]:
    out = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                         capture_output=True, text=True, check=False).stdout
    return {line for line in out.splitlines() if "sweb.eval" in line}


def grade(run_dir: Path, workers: int) -> int:
    """One instance at a time, every arm and repeat of it, then its image
    is removed: the harness keeps every image it pulls (about 3 GB each),
    and the disk does not hold twenty of them at once."""
    data = json.loads((run_dir / "runs.json").read_text())
    runs = data["runs"]
    for iid in dict.fromkeys(r["task"] for r in runs):
        before = _eval_images()
        for r in (r for r in runs if r["task"] == iid):
            run_id = f"{r['arm']}-{r['repeat']}-{iid}"
            model = f"himinbjorg-{r['arm']}"
            preds = run_dir / "predictions" / f"{run_id}.jsonl"
            preds.parent.mkdir(exist_ok=True)
            preds.write_text(json.dumps({"instance_id": iid, "model_name_or_path": model,
                                         "model_patch": r["model_patch"]}) + "\n")
            report = run_dir / "reports" / f"{model}.{run_id}.json"
            if not report.exists():
                print(f"  grading {iid} {r['arm']} #{r['repeat']}", flush=True)
                subprocess.run([sys.executable, "-m", "swebench.harness.run_evaluation",
                                "--dataset_name", DATASET, "--predictions_path", str(preds),
                                "--max_workers", str(workers), "--run_id", run_id,
                                "--instance_ids", iid,
                                "--report_dir", str(run_dir / "reports")],
                               cwd=run_dir, check=False, capture_output=True)
            ids = (set(json.loads(report.read_text()).get("resolved_ids", []))
                   if report.exists() else set())
            r["success"] = iid in ids
            r["graded"] = report.exists()
        for image in _eval_images() - before:
            subprocess.run(["docker", "rmi", "-f", image], check=False, capture_output=True)
    (run_dir / "graded.json").write_text(json.dumps(data, indent=2))
    arms = list(dict.fromkeys(r["arm"] for r in runs))
    print(f"\n{'arm':10} {'resolved':>9} {'ungraded':>9} {'tokens':>11} {'tokens/resolved':>16}")
    for arm in arms:
        rs = [r for r in runs if r["arm"] == arm]
        ok = sum(r["success"] for r in rs)
        spent = sum(r["total_tokens"] for r in rs)
        per = f"{spent / ok:,.0f}" if ok else "n/a"
        print(f"{arm:10} {ok:>4}/{len(rs):<4} {sum(not r['graded'] for r in rs):>9} "
              f"{spent:>11,} {per:>16}")
    print(f"  graded: {run_dir / 'graded.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--seed", type=int, default=SEED)
    r = sub.add_parser("run")
    r.add_argument("--arms", default="naive,optimized")
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--limit", type=int, default=None, help="first N instances only (pilot)")
    r.add_argument("--only", default="", help="comma-separated instance ids (pilot)")
    g = sub.add_parser("grade")
    g.add_argument("run_dir", type=Path)
    g.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()
    if args.cmd == "select":
        return select(args.n, args.seed)
    if args.cmd == "run":
        return run(args.arms.split(","), args.repeats, args.limit,
                   [i for i in args.only.split(",") if i] or None)
    return grade(args.run_dir.resolve(), args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
