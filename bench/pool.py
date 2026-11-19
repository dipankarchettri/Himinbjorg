"""make pool - re-summarise archived scenario-1 packs as one table.

The README headline pools two 5-repeat runs (70 runs per arm), so no single
`make proof` pack reproduces it exactly: `make proof` runs a fresh 5-repeat
benchmark, which shows the same effect at smaller n. This reads any number
of archived `scenario1-benchmark-*.json` packs and prints the same summary
the benchmark printed at the end of a run.

Usage:
    make pool
    python bench/pool.py evidence/scenario1-benchmark-*.json
    python bench/pool.py --latest-wins FIRST.json RERUN.json

--latest-wins: a task's runs come only from the last pack that has it. For
a rerun of tasks whose runs were void for an infrastructure reason (a rate
limit, a gateway restart); both packs stay in evidence/.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.benchmark import RunResult, summarise   # noqa: E402

EVIDENCE = ROOT / "evidence"

# The two packs behind the README's 84% headline. Kept explicit so `make
# pool` means "reproduce the published number", not "whatever happens to be
# in evidence/". Fall back to every pack when they are absent.
HEADLINE = [
    EVIDENCE / "scenario1-benchmark-20261115-034421.json",
    EVIDENCE / "20261115-050124" / "scenario1-benchmark-20261115-050130.json",
]

KNOWN = {f.name for f in fields(RunResult)}


def load(path: Path) -> list[RunResult]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [RunResult(**{k: v for k, v in row.items() if k in KNOWN})
            for row in data.get("results", [])]


def default_inputs() -> list[Path]:
    if all(p.exists() for p in HEADLINE):
        return HEADLINE
    return sorted(EVIDENCE.glob("scenario1-benchmark-*.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Pool scenario-1 evidence packs into one table")
    ap.add_argument("packs", nargs="*", type=Path,
                    help="scenario1-benchmark-*.json files (default: the headline packs)")
    ap.add_argument("--latest-wins", action="store_true",
                    help="take each task's runs only from the last pack that has it")
    args = ap.parse_args()

    paths = args.packs or default_inputs()
    if not paths:
        print("no scenario1-benchmark-*.json packs found; run `make bench` or pass paths")
        return 2

    results: list[RunResult] = []
    for path in paths:
        rows = load(path)
        if not rows:
            print(f"  {path}: no results", file=sys.stderr)
            continue
        if args.latest_wins:
            replaced = {r.task for r in rows}
            dropped = [r for r in results if r.task in replaced]
            results = [r for r in results if r.task not in replaced]
            if dropped:
                print(f"  replaced {len(dropped):3} earlier runs of "
                      f"{', '.join(sorted({r.task for r in dropped}))}")
        print(f"  pooled {len(rows):3} runs from {path}")
        results.extend(rows)
    if not results:
        return 2

    print(f"\n  {len(results)} runs total "
          f"({', '.join(sorted({r.arm for r in results}))})")
    print(summarise(results, detail=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
