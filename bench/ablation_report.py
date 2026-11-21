"""Ablation report: each lever off against all on.

Per configuration: pass rate, tokens per completed task (all tokens spent
divided by passes), and a 95% bootstrap interval for the change against
all-on, resampling runs within each task so the task mix stays fixed.

usage: ablation_report.py DIR   (DIR holds bench/ablate.py's CONFIG.json files)
"""
import json, random, statistics, sys
from collections import defaultdict
from pathlib import Path

d = Path(sys.argv[1])
configs = ["all", "file_memory", "retrieval", "pruning", "tool_disclosure", "diff_writes", "loop_limits",
           "subagent", "plan"]
ADDED = {"subagent", "plan"}   # every lever on, plus this
runs = {c: json.loads((d / f"{c}.json").read_text())["runs"] for c in configs if (d / f"{c}.json").exists()}
tasks = sorted({r["task"] for rs in runs.values() for r in rs})
# Only repetitions every configuration finished, all tasks: the deadline
# stops the configurations at different points.
def complete(rs):
    reps = defaultdict(set)
    for r in rs:
        reps[r["repeat"]].add(r["task"])
    k = 0
    while len(reps.get(k + 1, ())) == len(tasks):
        k += 1
    return k
# Each comparison with all-on uses the repetitions both finished.
done = {c: complete(rs) for c, rs in runs.items()}
upto = lambda rs, k: [r for r in rs if r["repeat"] <= k]
print(f"complete repetitions of {len(tasks)} tasks: " + ", ".join(f"{c} {k}" for c, k in done.items()) + "\n")


def by_task(rs):
    out = defaultdict(list)
    for r in rs:
        out[r["task"]].append(r)
    return out


def per_pass(rs):
    passed = sum(r["success"] for r in rs)
    return sum(r["total_tokens"] for r in rs) / passed if passed else float("inf")


def boot(base, other, n=4000, seed=1):
    """95% interval of tokens-per-pass(other)/tokens-per-pass(base) - 1, and of the pass-rate change."""
    rnd = random.Random(seed)
    bt, ot = by_task(base), by_task(other)
    common = [t for t in tasks if bt.get(t) and ot.get(t)]
    ratios, dpass = [], []
    for _ in range(n):
        b = [rnd.choice(bt[t]) for t in common for _ in bt[t]]
        o = [rnd.choice(ot[t]) for t in common for _ in ot[t]]
        pb, po = per_pass(b), per_pass(o)
        if pb != float("inf") and po != float("inf"):
            ratios.append(po / pb - 1)
        dpass.append(sum(r["success"] for r in o) / len(o) - sum(r["success"] for r in b) / len(b))
    ratios.sort(); dpass.sort()
    q = lambda xs, p: xs[int(p * (len(xs) - 1))] if xs else float("nan")
    return (q(ratios, .025), q(ratios, .975)), (q(dpass, .025), q(dpass, .975))


print(f"{'config':18} {'runs':>5} {'passed':>9} {'tokens/pass':>12} {'vs all on':>10}   95% interval        pass-rate change, 95% interval")
for c, rs in runs.items():
    k = min(done["all"], done[c])
    rs, base = upto(rs, k), upto(runs["all"], k)
    n, p = len(rs), sum(r["success"] for r in rs)
    tp = per_pass(rs)
    if c == "all":
        print(f"{'all levers on':18} {n:5} {p:4}/{n:<4} {tp:12,.0f}")
        continue
    (lo, hi), (plo, phi) = boot(base, rs)
    delta = tp / per_pass(base) - 1
    sig = "  *" if lo > 0 or hi < 0 else ""
    psig = "  *" if plo > 0 or phi < 0 else ""
    print(f"{('with ' if c in ADDED else 'no ') + c:18} {n:5} {p:4}/{n:<4} {tp:12,.0f} {delta:+9.1%}   [{lo:+.1%}, {hi:+.1%}]{sig:3}   "
          f"{p / n - sum(r['success'] for r in base) / len(base):+.1%} [{plo:+.1%}, {phi:+.1%}]{psig}")

print("\nper task, passed/runs and median tokens of passing runs")
print(f"{'task':15}" + "".join(f"{c[:12]:>16}" for c in runs))
for t in tasks:
    row = f"{t:15}"
    for c, rs in runs.items():
        tr = [r for r in upto(rs, done[c]) if r["task"] == t]
        ok = [r["total_tokens"] for r in tr if r["success"]]
        med = f"{statistics.median(ok):,.0f}" if ok else "-"
        row += f"{f'{len(ok)}/{len(tr)} {med}':>16}"
    print(row)
print("\n* the interval excludes zero")
