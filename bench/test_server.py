"""Editor server protocol, against a scripted model. No gateway, no editor."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import server as server_mod                          # noqa: E402
from agent.loop import Agent                                    # noqa: E402
from agent.security import policy                               # noqa: E402
from agent.security.privacy import PrivacyViolation             # noqa: E402
from bench.test_loop import (FakeClient, GatewayErrorClient, call, make_agent,  # noqa: E402,F401
                             no_gateway, repo)


class FakeChannel:
    def __init__(self):
        self.events: queue.Queue = queue.Queue()

    def send(self, kind, **data):
        self.events.put({"type": kind, **data})

    def wait(self, kind, timeout=10):
        while True:
            ev = self.events.get(timeout=timeout)
            if ev["type"] == kind:
                return ev


class Feed:
    """stdin stand-in: lines pushed by the test, None for EOF."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()

    def push(self, obj):
        self.q.put(json.dumps(obj) + "\n" if obj is not None else None)

    def __iter__(self):
        while (line := self.q.get()) is not None:
            yield line


@pytest.fixture
def served(repo, monkeypatch):
    """A running server whose agent talks to a scripted client."""
    started = []

    def start(turns, client=None):
        base, _ = make_agent(repo, [], auto_approve=False)
        settings = base.settings
        monkeypatch.setattr(server_mod.Server, "__init__", lambda self, *a, **k: None)
        srv = server_mod.Server()
        srv.settings, srv.ch, srv.stdin = settings, FakeChannel(), Feed()
        srv.privacy, srv.registry = base.privacy, base.registry
        srv.client = client or FakeClient(turns)
        srv.agent = Agent(settings, srv.privacy, srv.client, srv.registry, printer=srv._print)
        srv.tasks, srv._pending = queue.Queue(), {}
        srv._pending_lock, srv._busy, srv._closed = threading.Lock(), False, threading.Event()
        policy.set_operator_channel(srv._ask_operator)
        t = threading.Thread(target=srv.serve, daemon=True)
        t.start()
        started.append((srv, t))
        srv.ch.wait("ready")
        return srv

    yield start
    for srv, t in started:
        srv.stdin.push(None)
        t.join(timeout=10)
    policy.set_operator_channel(None)


PATCH = [([call("apply_patch", path="calc.py", find="return a / b", replace="return None if b == 0 else a / b")], ""),
         ([], "fixed")]


def test_approved_edit_is_shown_as_a_diff_then_written(served, repo):
    srv = served(PATCH)
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    req = srv.ch.wait("approval_request")
    assert req["tool"] == "apply_patch" and req["edit"] is True
    assert "+" in req["diff"] and "return None if b == 0" in req["diff"]
    srv.stdin.push({"type": "approval", "id": req["id"], "answer": "y"})
    res = srv.ch.wait("result")
    assert res["text"] == "fixed"
    assert "return None if b == 0" in (repo / "calc.py").read_text()


def test_denied_edit_writes_nothing(served, repo):
    before = (repo / "calc.py").read_text()
    srv = served(PATCH)
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    req = srv.ch.wait("approval_request")
    srv.stdin.push({"type": "approval", "id": req["id"], "answer": "n"})
    srv.ch.wait("result")
    assert (repo / "calc.py").read_text() == before


def test_an_answer_for_another_id_is_ignored(served, repo):
    srv = served(PATCH)
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    req = srv.ch.wait("approval_request")
    srv.stdin.push({"type": "approval", "id": "forged", "answer": "y"})
    with pytest.raises(queue.Empty):
        srv.ch.wait("result", timeout=1)
    assert "return None" not in (repo / "calc.py").read_text()
    srv.stdin.push({"type": "approval", "id": req["id"], "answer": "n"})
    srv.ch.wait("result")


def test_editor_disconnect_during_approval_denies(served, repo):
    before = (repo / "calc.py").read_text()
    srv = served(PATCH)
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    srv.ch.wait("approval_request")
    srv.stdin.push(None)                            # editor closed
    srv.ch.wait("result")
    assert (repo / "calc.py").read_text() == before


def test_commands_answer_on_the_channel(served):
    srv = served([])
    srv.stdin.push({"type": "undo"})
    assert srv.ch.wait("info")["command"] == "undo"
    srv.stdin.push({"type": "reset"})
    assert srv.ch.wait("info")["command"] == "reset"
    srv.stdin.push({"type": "cost"})
    assert "total_tokens" in srv.ch.wait("cost")["stats"]
    srv.stdin.push({"type": "nonsense"})
    assert "unknown request" in srv.ch.wait("error")["text"]


def test_a_gateway_error_ends_the_task_but_not_the_server(served):
    srv = served([], client=GatewayErrorClient([]))
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    res = srv.ch.wait("result")
    assert res["text"].startswith("stopped: gateway error:")
    assert "502" in res["text"]
    # The session is still alive: another task runs and answers.
    srv.agent.client = FakeClient([([], "still here")])
    srv.stdin.push({"type": "task", "text": "now explain divide"})
    assert srv.ch.wait("result")["text"] == "still here"


def test_an_unexpected_error_is_reported_and_the_server_keeps_serving(served):
    class CrashClient(FakeClient):
        def call(self, spec):
            raise RuntimeError("boom")

    srv = served([], client=CrashClient([]))
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    err = srv.ch.wait("error")
    assert "RuntimeError: boom" in err["text"]
    assert not err.get("fatal")
    srv.agent.client = FakeClient([([], "still here")])
    srv.stdin.push({"type": "task", "text": "now explain divide"})
    assert srv.ch.wait("result")["text"] == "still here"


def test_a_privacy_violation_is_still_fatal(served):
    """The generic guard must not swallow the fail-closed path."""
    class LeakClient(FakeClient):
        def call(self, spec):
            raise PrivacyViolation("cloud model blocked in confidential mode")

    srv = served([], client=LeakClient([]))
    srv.stdin.push({"type": "task", "text": "fix divide in calc.py"})
    err = srv.ch.wait("error")
    assert err.get("fatal") is True
    assert "ABORT" in err["text"]
    assert "blocked" in err["text"]


def test_stdout_carries_only_protocol():
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from agent.server import Channel\n"
            "ch = Channel(); print('stray model text'); ch.send('log', text='ok')\n") % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    lines = [ln for ln in out.stdout.splitlines() if ln]
    assert lines == [json.dumps({"type": "log", "text": "ok"})]
    assert "stray model text" in out.stderr


def test_commands_the_agent_runs_cannot_read_its_stdin():
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "from agent.tools.shell import run_command\n"
            "print(run_command(['python3', '-c', 'import sys; print(repr(sys.stdin.read()))'],"
            " repo_root=Path('.')))\n") % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], input='{"type":"approval","answer":"y"}',
                         capture_output=True, text=True, timeout=30)
    assert "approval" not in out.stdout
    assert "''" in out.stdout
