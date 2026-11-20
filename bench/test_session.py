"""Session persistence and the on-disk undo journal (agent/session.py)."""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                                   # noqa: E402

from agent import session as session_mod                        # noqa: E402
from agent.config import build_parser                           # noqa: E402
from agent.tools import write as w                              # noqa: E402
from bench.tasks import TASKS_BY_ID, materialise                # noqa: E402
from bench.test_loop import call, make_agent                    # noqa: E402

FIX = [([call("apply_patch", path="calc.py", find="return a / b",
              replace="return None if b == 0 else a / b")], ""), ([], "fixed")]


@pytest.fixture
def repo(tmp_path):
    return materialise(TASKS_BY_ID["zero_check"], tmp_path / "repo")


def attached(repo, turns, resume=False):
    agent, client = make_agent(repo, turns)
    session_mod.attach(agent, resume, out=lambda *a: None)
    return agent, client


# ------------------------------------------------------------ undo journal

def test_undo_survives_a_restart(repo):
    before = (repo / "calc.py").read_text()
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    assert (repo / "calc.py").read_text() != before
    w.JOURNAL.clear()                       # the process is gone
    w.use_journal(session_mod.journal_path(repo, False))
    assert w.undo_last() == "reverted calc.py"
    assert (repo / "calc.py").read_text() == before


def test_the_journal_is_saved_before_the_file_is_written(tmp_path, monkeypatch):
    w.use_journal(tmp_path / "undo.json")
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    seen = []
    real = w._save_journal
    monkeypatch.setattr(w, "_save_journal", lambda: (seen.append(f.read_text()), real()))
    w.write_file(str(f), "x = 2\n", tmp_path)
    assert seen == ["x = 1\n"]              # saved while the file was unchanged
    saved = json.loads((tmp_path / "undo.json").read_text())
    assert saved["batches"][0][0]["before"] == "x = 1\n"


def test_undo_refuses_a_file_changed_after_the_agent(tmp_path):
    w.use_journal(tmp_path / "undo.json")
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    w.write_file(str(f), "x = 2\n", tmp_path)
    f.write_text("x = 3\n")                 # someone edited it afterwards
    assert w.undo_last().startswith("not undone: a.py changed")
    assert f.read_text() == "x = 3\n"
    assert len(w.JOURNAL) == 1              # still there, for a manual revert


def test_a_rename_is_one_undo_batch_on_disk(tmp_path):
    from agent.tools.write import rename_symbol
    w.use_journal(tmp_path / "undo.json")
    r = materialise(TASKS_BY_ID["rename_symbol"], tmp_path / "r")
    before = {p: p.read_text() for p in r.glob("*.py")}
    rename_symbol("multiply", "times", r)
    assert len(json.loads((tmp_path / "undo.json").read_text())["batches"]) == 1
    w.use_journal(tmp_path / "undo.json")   # reload, as after a restart
    w.undo_last()
    assert {p: p.read_text() for p in r.glob("*.py")} == before


def test_the_journal_keeps_only_recent_batches(tmp_path):
    w.use_journal(tmp_path / "undo.json")
    f = tmp_path / "a.py"
    for i in range(w.MAX_BATCHES + 5):
        w.write_file(str(f), f"x = {i}\n", tmp_path)
    assert len(json.loads((tmp_path / "undo.json").read_text())["batches"]) == w.MAX_BATCHES


def test_an_unreadable_journal_starts_empty(tmp_path):
    (tmp_path / "undo.json").write_text("{not json")
    w.use_journal(tmp_path / "undo.json")
    assert w.undo_last() == "nothing to undo"


# ----------------------------------------------------------------- session

def test_resume_continues_the_conversation(repo):
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    first = [(e.role, e.content) for e in agent.ledger.entries]
    turns = agent._last_turn

    resumed, client = attached(repo, [([], "it returns None for b == 0")], resume=True)
    assert [(e.role, e.content) for e in resumed.ledger.entries] == first
    resumed.run("what did you change?")
    sent = client.specs[0].messages
    assert any("fix divide" in str(m.get("content")) for m in sent)
    assert resumed._last_turn > turns       # turn numbers continue


def test_a_new_session_does_not_resume_by_default(repo):
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    fresh, _ = attached(repo, [])
    assert fresh.ledger.entries == []


def test_a_crash_mid_task_keeps_the_finished_turns(repo, monkeypatch):
    """A kill -9 runs no cleanup: what is on disk when the second call
    starts is all that survives, so it is read at that moment."""
    agent, client = attached(repo, FIX)
    real = client.call
    on_disk = []

    def crash_on_second(spec):
        if len(client.specs) == 1:
            on_disk.append(session_mod.load(repo, False))
            raise SystemExit("killed")
        return real(spec)
    monkeypatch.setattr(client, "call", crash_on_second)
    with pytest.raises(SystemExit):
        agent.run("fix divide")
    assert any(e["role"] == "tool" for e in on_disk[0]["entries"])  # the approved edit's result

    resumed, _ = attached(repo, [], resume=True)
    roles = [e.role for e in resumed.ledger.entries]
    assert roles.count("assistant") >= 1 and "tool" in roles


def test_a_session_cut_after_a_tool_call_is_repaired(repo):
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    data = session_mod.load(repo, False)
    data["entries"].append({"turn": 9, "role": "assistant", "content": "",
                            "tool_calls": [call("read_file", path="calc.py")]})
    session_mod._write(session_mod.session_path(repo, False), data)
    resumed, _ = attached(repo, [], resume=True)
    last = resumed.ledger.entries[-1]
    assert last.role == "tool" and "not executed" in last.content


def test_confidential_and_standard_sessions_are_separate(repo):
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    assert session_mod.load(repo, False) is not None
    assert session_mod.load(repo, True) is None
    assert session_mod.session_path(repo, True) != session_mod.session_path(repo, False)


def test_session_files_are_private(repo):
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    for p in (session_mod.session_path(repo, False), session_mod.journal_path(repo, False)):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_forget_drops_the_saved_session(repo):
    agent, _ = attached(repo, FIX)
    agent.run("fix divide")
    session_mod.forget(repo, False)
    assert session_mod.load(repo, False) is None


def test_resume_is_a_cli_flag():
    assert build_parser().parse_args(["--resume"]).resume is True


# ------------------------------------------------------ gateway lost mid-task

def test_a_refused_connection_is_gateway_unreachable_not_provider_failure(repo):
    from agent import llm as llm_mod
    from agent.security.privacy import PrivacyMode
    agent, _ = make_agent(repo, [])        # its settings point at localhost:1, nothing listens
    client = llm_mod.LLMClient(agent.settings, PrivacyMode.build(False, "k", "c"))
    client._client = client._client.with_options(max_retries=0, timeout=5)
    spec = llm_mod.CallSpec(messages=[{"role": "user", "content": "x"}], model="ollama/b",
                            fallbacks=["ollama/c"])
    with pytest.raises(llm_mod.GatewayUnreachable, match="cannot reach the gateway"):
        client.call(spec)


def test_losing_the_gateway_stops_the_task_and_says_it_can_resume(repo):
    from agent import llm as llm_mod
    printed = []
    agent, client = attached(repo, FIX)
    agent.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    real = client.call

    def drop_after_first(spec):
        if len(client.specs) == 1:
            raise llm_mod.GatewayUnreachable("cannot reach the gateway at http://gw")
        return real(spec)
    client.call = drop_after_first
    assert agent.run("fix divide") == "stopped: the gateway is unreachable"
    assert any("--resume" in line for line in printed)
    assert session_mod.load(repo, False)["entries"]      # saved, ready to resume


# ------------------------------------------------------ large repositories

def _git_repo(root, files, ignore=""):
    import subprocess
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    if ignore:
        (root / ".gitignore").write_text(ignore)
    return root


def test_a_small_repository_lists_every_file(repo):
    agent, _ = make_agent(repo, [])
    m = agent._manifest()
    assert "calc.py" in m and "test_calc.py" in m and "This is every file" in m


def test_a_large_repository_lists_the_top_level_with_counts(tmp_path):
    files = {f"pkg/m{i}.py": "x = 1\n" for i in range(80)}
    files.update({f"tests/t{i}.py": "x = 1\n" for i in range(30)})
    files["setup.py"] = ""
    root = _git_repo(tmp_path / "big", files)
    agent, _ = make_agent(root, [])
    m = agent._manifest()
    assert "pkg/  (80 files)" in m and "tests/  (30 files)" in m and "  setup.py" in m
    assert "m0.py" not in m and "The top level only (111 files in all)" in m


def test_ignored_files_are_not_listed(tmp_path):
    files = {"app.py": "", "venv2/lib/x.py": "", "out/bundle.js": ""}
    root = _git_repo(tmp_path / "r", files, ignore="venv2/\nout/\n")
    agent, _ = make_agent(root, [])
    m = agent._manifest()
    assert "app.py" in m and "venv2" not in m and "bundle.js" not in m


def test_a_repository_under_a_build_directory_is_still_listed(tmp_path):
    root = tmp_path / "build" / "proj"
    root.mkdir(parents=True)
    (root / "main.py").write_text("")         # not a git repository: the walk
    agent, _ = make_agent(root, [])
    assert "main.py" in agent._manifest()


def test_grep_stops_reading_at_its_cap(tmp_path, monkeypatch):
    from agent.tools import read as read_mod
    monkeypatch.setattr(read_mod, "GREP_SCAN_CAP", 100)
    (tmp_path / "many.py").write_text("hit\n" * 5000)
    out = read_mod.grep("hit", tmp_path)
    assert len(out.splitlines()) == read_mod.MAX_GREP_MATCHES + 1
    assert "more than 100 matches" in out and "Narrow" in out
