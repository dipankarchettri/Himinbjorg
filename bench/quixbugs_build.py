"""Vendor a subset of QuixBugs as outside benchmark tasks.

QuixBugs (Lin, Koppel, Chen, Solar-Lezama, 2017; MIT licence) is 40
classic algorithms, each with a one-line bug and test cases. It was not
written for this project, so results on it say more than results on our
own seven tasks.

    python bench/quixbugs_build.py /path/to/QuixBugs

Kept: programs whose tests follow the plain JSON pattern, where the buggy
version fails and the correct version passes within the time limit. Each
lands in bench/fixtures/quixbugs/<name>/ as the buggy program, its test
cases and a test file rewritten to import the program directly.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures" / "quixbugs"
TIMEOUT = 30

# Per test case. The correct versions finish each case in well under a
# second (slowest measured below). Without a limit, a buggy program that
# loops forever (bitcount, sqrt, find_first_in_sorted) held every test
# run until the agent's 90 s shell timeout, and the agent saw only
# "timed out", not which case hung. With it, each hanging case fails in
# CASE_SECONDS with a message naming the input.
CASE_SECONDS = 2
CONFTEST = f'''"""Puts the repo root on sys.path, and fails a test case that runs
longer than {CASE_SECONDS} s instead of letting the whole run hang."""
import signal

import pytest

CASE_SECONDS = {CASE_SECONDS}


@pytest.fixture(autouse=True)
def _case_time_limit(request):
    def stop(signum, frame):
        raise TimeoutError(f"{{request.node.name}} ran longer than {{CASE_SECONDS}} s "
                           "(probably an infinite loop)")
    previous = signal.signal(signal.SIGALRM, stop)
    signal.alarm(CASE_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
'''

TEST_TEMPLATE_HEAD = '''import json
from pathlib import Path

import pytest

from {name} import {name}

CASES = [json.loads(line) for line in
         (Path(__file__).parent / "{name}.jsonl").read_text().splitlines() if line.strip()]

'''


def rewrite_test(src: str, name: str) -> str | None:
    """Keep the original test function; replace only the imports and loader."""
    m = re.search(r"(@pytest\.mark\.parametrize\(\"input_data,expected\", testdata\)\n"
                  r"def test_\w+\(.*?\):\n(?:    .*\n?)+)", src)
    if not m:
        return None
    body = m.group(1).replace("testdata)", "CASES)")
    return TEST_TEMPLATE_HEAD.format(name=name) + body.rstrip() + "\n"


def passes(program: Path, test: str, cases: Path, name: str) -> bool | None:
    """True/False for pass/fail, None on timeout."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        shutil.copy(program, d / f"{name}.py")
        (d / "tests").mkdir()
        (d / "tests" / f"test_{name}.py").write_text(test)
        shutil.copy(cases, d / "tests" / f"{name}.jsonl")
        (d / "conftest.py").write_text(CONFTEST)
        try:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x",
                                   "-p", "no:cacheprovider"],
                                  cwd=d, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return None
        return proc.returncode == 0


def main(src_dir: str) -> int:
    src = Path(src_dir)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copy(src / "LICENSE", OUT / "LICENSE")
    kept = []
    for test_file in sorted((src / "python_testcases").glob("test_*.py")):
        name = test_file.stem[len("test_"):]
        cases = src / "json_testcases" / f"{name}.json"
        buggy = src / "python_programs" / f"{name}.py"
        fixed = src / "correct_python_programs" / f"{name}.py"
        if not (cases.exists() and buggy.exists() and fixed.exists()):
            continue
        original = test_file.read_text()
        if "run_slow" in original or original.count("def test_") != 1:
            continue
        test = rewrite_test(original, name)
        if test is None:
            continue
        if passes(fixed, test, cases, name) is not True:
            print(f"skip {name}: correct version does not pass")
            continue
        if passes(buggy, test, cases, name) is True:
            print(f"skip {name}: buggy version already passes")
            continue
        d = OUT / name
        (d / "tests").mkdir(parents=True)
        shutil.copy(buggy, d / f"{name}.py")
        (d / "tests" / f"test_{name}.py").write_text(test)
        shutil.copy(cases, d / "tests" / f"{name}.jsonl")
        (d / "conftest.py").write_text(CONFTEST)
        kept.append(name)
    (OUT / "README.md").write_text(
        "# QuixBugs subset\n\n"
        "From https://github.com/jkoppel/QuixBugs (MIT, see LICENSE), commit "
        f"{_head(src)}. Built by `bench/quixbugs_build.py`: each program's buggy\n"
        "version, its JSON test cases, and its test rewritten to import the program\n"
        "directly, plus a conftest.py that fails any test case running longer than\n"
        f"{CASE_SECONDS} s (some buggy versions loop forever). Kept where the buggy version\n"
        "fails and the correct one passes.\n\n"
        + "\n".join(f"- {n}" for n in kept) + "\n")
    print(f"kept {len(kept)}: {' '.join(kept)}")
    return 0


def _head(src: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
