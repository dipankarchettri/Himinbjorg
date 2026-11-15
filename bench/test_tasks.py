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
