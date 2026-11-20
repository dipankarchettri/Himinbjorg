"""The task checkers themselves: each must reject the starting repository
and accept a reference solution, or the benchmark measures nothing."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.tasks import TASKS_BY_ID, materialise                # noqa: E402


def _sub(repo: Path, rel: str, old: str, new: str, regex: bool = False) -> None:
    p = repo / rel
    text = p.read_text()
    p.write_text(re.sub(old, new, text) if regex else text.replace(old, new))


def solve_bulk(repo):
    _sub(repo, "shop/discounts.py", "quantity > config", "quantity >= config")


def solve_rename(repo):
    for p in (repo / "shop").glob("*.py"):
        _sub(repo, str(p.relative_to(repo)), r"\bline_total\b", "line_total_cents", regex=True)


def solve_freeship(repo):
    _sub(repo, "shop/discounts.py", '"SPRING10": ("rate", 0.10),',
         '"SPRING10": ("rate", 0.10),\n    "FREESHIP": ("shipping", 0),')
    _sub(repo, "shop/orders.py", "        net = self.subtotal() - self.discounts()\n",
         "        if (self.coupon or \"\").upper() == \"FREESHIP\":\n            return 0\n"
         "        net = self.subtotal() - self.discounts()\n")
    _sub(repo, "shop/orders.py", '("WELCOME5", "SPRING10")', '("WELCOME5", "SPRING10", "FREESHIP")')


def cheat_bulk_threshold(repo):
    _sub(repo, "shop/config.py", "BULK_THRESHOLD = 10", "BULK_THRESHOLD = 9")


def cheat_bulk_test(repo):
    _sub(repo, "tests/test_orders.py", "4_250 - 425", "4_250")


@pytest.mark.parametrize("task_id,solve", [
    ("shop_bulk_bug", solve_bulk),
    ("shop_rename", solve_rename),
    ("shop_freeship", solve_freeship),
])
def test_checker_rejects_start_accepts_solution(tmp_path, task_id, solve):
    task = TASKS_BY_ID[task_id]
    repo = materialise(task, tmp_path / "start")
    assert not task.verify(repo)
    repo = materialise(task, tmp_path / "solved")
    solve(repo)
    assert task.verify(repo)


@pytest.mark.parametrize("cheat", [cheat_bulk_threshold, cheat_bulk_test])
def test_bulk_checker_rejects_cheats(tmp_path, cheat):
    task = TASKS_BY_ID["shop_bulk_bug"]
    repo = materialise(task, tmp_path / "r")
    cheat(repo)
    assert not task.verify(repo)


def test_materialise_clears_previous_run(tmp_path):
    task = TASKS_BY_ID["shop_bulk_bug"]
    repo = materialise(task, tmp_path / "r")
    (repo / "shop" / "stray.py").write_text("x = 1\n")
    repo = materialise(task, tmp_path / "r")
    assert not (repo / "shop" / "stray.py").exists()


def test_task_repo_is_its_own_git_repo(tmp_path):
    import subprocess
    repo = materialise(TASKS_BY_ID["zero_check"], tmp_path / "r")
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    assert Path(top).resolve() == repo.resolve()


def test_git_op_refuses_nested_working_dir(tmp_path):
    import subprocess
    from agent.tools.git import git_op
    repo = materialise(TASKS_BY_ID["zero_check"], tmp_path / "outer")
    inner = repo / "sub"
    inner.mkdir()
    out = git_op("commit", inner, message="x")
    assert out.startswith("ERROR") and "inside another repository" in out
    assert git_op("status", repo).startswith("exit=0")
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
    assert len(log.splitlines()) == 1


def test_adding_a_test_is_allowed_but_editing_one_is_not(tmp_path):
    task = TASKS_BY_ID["shop_bulk_bug"]
    repo = materialise(task, tmp_path / "add")
    solve_bulk(repo)
    with open(repo / "tests/test_orders.py", "a") as f:
        f.write("\n\ndef test_extra():\n    assert True\n")
    assert task.verify(repo)
    repo = materialise(task, tmp_path / "edit")
    solve_bulk(repo)
    _sub(repo, "tests/test_orders.py", "assert inv.available(\"PEN-01\") == 100", "assert True")
    assert not task.verify(repo)


def test_quixbugs_tasks_load_and_grade_against_the_original_tests(tmp_path):
    from bench.tasks import QUIXBUGS_TASKS
    assert len(QUIXBUGS_TASKS) >= 20
    assert len({t.id for t in QUIXBUGS_TASKS}) == len(QUIXBUGS_TASKS)
    task = TASKS_BY_ID["qb_to_base"]
    repo = materialise(task, tmp_path / "r")
    assert not task.verify(repo)
    src = repo / "to_base.py"
    buggy, fixed = "result = result + alphabet[i]", "result = alphabet[i] + result"
    # The known one-line fix passes, even if the agent also weakened the test.
    src.write_text(src.read_text().replace(buggy, fixed))
    (repo / "tests" / "test_to_base.py").write_text("def test_nothing():\n    pass\n")
    assert task.verify(repo)
    # A weakened test does not hide the bug: grading restores the original.
    src.write_text(src.read_text().replace(fixed, buggy))
    (repo / "tests" / "test_to_base.py").write_text("def test_nothing():\n    pass\n")
    assert not task.verify(repo)


def test_a_quixbugs_program_that_loops_forever_fails_fast_and_says_where(tmp_path):
    # Measured 2026-11-18: buggy bitcount loops forever, so every test run
    # held the agent until the 90 s shell timeout with no hint which case
    # hung. The fixture's conftest fails each case after CASE_SECONDS.
    import subprocess
    import time
    from bench.quixbugs_build import CASE_SECONDS
    task = TASKS_BY_ID["qb_bitcount"]
    repo = materialise(task, tmp_path / "r")
    (repo / "tests" / "bitcount.jsonl").write_text("[[127], 7]\n")
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                          cwd=repo, capture_output=True, text=True, timeout=60)
    assert time.time() - started < CASE_SECONDS + 10
    assert proc.returncode != 0
    assert "TimeoutError" in proc.stdout and "infinite loop" in proc.stdout


def test_a_real_repository_checkout_holds_no_later_commit(tmp_path):
    # Measured: the agent ran `git show` on the upstream fix in a clone that
    # kept the source's branches and tags.
    import subprocess

    from bench.benchmark import history_to

    def git(*args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                              text=True).stdout.strip()
    src = tmp_path / "src"
    src.mkdir()
    git("init", "-q", cwd=src)
    for i in (1, 2):
        (src / "f.txt").write_text(f"{i}\n")
        git("add", "f.txt", cwd=src)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", f"c{i}", cwd=src)
    git("tag", "v2", cwd=src)
    first, fix = git("rev-parse", "HEAD~1", cwd=src), git("rev-parse", "HEAD", cwd=src)

    dest = history_to(src, "HEAD~1", tmp_path / "work")
    assert git("rev-parse", "HEAD", cwd=dest) == first
    assert (dest / "f.txt").read_text() == "1\n"
    assert git("for-each-ref", "--format=%(refname)", cwd=dest) == "refs/heads/main"
    assert git("log", "--all", "--format=%H", cwd=dest) == first
    assert subprocess.run(["git", "cat-file", "-e", fix], cwd=dest).returncode != 0
    assert git("remote", cwd=dest) == ""
