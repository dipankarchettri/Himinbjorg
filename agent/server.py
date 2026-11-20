"""Editor server: the agent behind the VS Code extension.

`himin --serve --repo <dir>` speaks JSON lines on stdin/stdout. Requests:

    {"type": "task", "text": "..."}
    {"type": "approval", "id": "...", "answer": "y" | "n" | "a"}
    {"type": "cancel"}       interrupt the running task, keep the session
    {"type": "cost"} | {"type": "undo"} | {"type": "reset"} | {"type": "shutdown"}

Events: ready, log, delta, approval_request, result, cost, info, error.
`ready` carries `gateway_error`, empty when the gateway's health check
passed.
`delta` is model text as it streams in, JSON-encoded like every event; the
`result` that follows carries the whole answer.

Security. The approval answer arrives on this channel, so the channel must
carry nothing the model can write:

- stdout is protocol only. Anything else the process prints goes to
  stderr, so model text can never be framed as a protocol message.
- Child processes get /dev/null as stdin (tools/shell.py, tools/git.py,
  tools/read.py), so a command the agent runs cannot read the channel.
- Requests are answered by id. An id is minted here, per approval, and an
  answer for any other id is ignored.
- The gate sends only what it built itself (tool, resolved arguments,
  dry-run diff). The extension shows the diff and asks with a native modal
  dialog; its webview, which renders model text, cannot send approvals.
"""
from __future__ import annotations

import _thread
import json
import os
import queue
import sys
import threading
import time
import uuid

from . import budget as budget_mod
from . import config as config_mod
from . import telemetry
from . import trace
from .memory.ledger import LedgerEntry
from .security import policy
from .security.privacy import PrivacyViolation
from .tools import shell as shell_mod


class Channel:
    """The protocol stream, isolated from everything else that prints."""

    def __init__(self):
        # Keep a private handle on the real stdout, then point fd 1 and
        # sys.stdout at stderr so a stray print cannot corrupt the stream.
        self._out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        self._lock = threading.Lock()

    def send(self, kind: str, **data) -> None:
        line = json.dumps({"type": kind, **data}, default=str)
        with self._lock:
            self._out.write(line + "\n")
            self._out.flush()


class Server:
    def __init__(self, settings, channel: Channel, stdin=None):
        from .loop import Agent
        from .main import build

        self.settings = settings
        self.ch = channel
        self.stdin = stdin or sys.stdin
        self.privacy, self.registry, self.client = build(settings)
        self.agent = Agent(settings, self.privacy, self.client, self.registry,
                           printer=self._print, on_text=self._text)
        self.tasks: queue.Queue = queue.Queue()
        self._pending: dict[str, dict] = {}
        self._pending_lock = threading.Lock()
        self._busy = False
        self._closed = threading.Event()
        policy.set_operator_channel(self._ask_operator)

    # ---------------------------------------------------------- outbound

    def _print(self, *args, **_) -> None:
        self.ch.send("log", text=" ".join(str(a) for a in args))

    def _text(self, piece: str) -> None:
        """Model text as it arrives; the "result" message still carries
        the whole answer."""
        self.ch.send("delta", text=piece)

    def _ask_operator(self, request: dict) -> tuple[str, str]:
        """Called by the gate on the agent thread; blocks until answered."""
        if self._closed.is_set():
            return "", "editor disconnected"
        rid = uuid.uuid4().hex
        slot = {"event": threading.Event(), "answer": ""}
        with self._pending_lock:
            self._pending[rid] = slot
        self.ch.send("approval_request", id=rid, **request)
        try:
            while not slot["event"].wait(0.25):
                if self._closed.is_set():
                    return "", "editor disconnected"
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)
        return slot["answer"], ""

    def _stats(self, before: dict | None = None) -> dict:
        full = self.agent.stats.as_dict()
        s = {k: v - (before or {}).get(k, 0) for k, v in full.items()
             if isinstance(v, (int, float))}
        ids = full["request_ids"][len((before or {}).get("request_ids", [])):]
        cost = telemetry.session_cost(ids, self.settings.bifrost_base_url,
                                      self.settings.bifrost_setup_token)
        if cost is not None:
            s["cost_usd"] = round(cost[0], 6)
        s["served_by"] = self.agent._last_provider
        return s

    # ----------------------------------------------------------- inbound

    def _reader(self) -> None:
        """Reads requests on a side thread; the agent runs on the main one."""
        for raw in self.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self.ch.send("error", text="unparseable request")
                continue
            kind = msg.get("type")
            if kind == "approval":
                with self._pending_lock:
                    slot = self._pending.get(str(msg.get("id")))
                if slot is not None:
                    slot["answer"] = str(msg.get("answer", ""))
                    slot["event"].set()
            elif kind == "cancel":
                if self._busy:
                    _thread.interrupt_main()
            elif kind == "shutdown":
                break
            else:
                self.tasks.put(msg)
        self._closed.set()
        self.tasks.put({"type": "shutdown"})

    def _handle(self, msg: dict) -> bool:
        from .loop import Agent
        from .tools import write as write_mod

        kind = msg.get("type")
        if kind == "shutdown":
            return False
        if kind == "cost":
            self.ch.send("cost", stats=self._stats())
        elif kind == "undo":
            result = write_mod.undo_last()
            if result != "nothing to undo":
                self.agent.files.records.clear()
                self.agent.ledger.append(LedgerEntry(
                    self.agent._last_turn, "user",
                    f"[The operator undid the last edit: {result}.]"))
            self.ch.send("info", text=result, command="undo")
        elif kind == "reset":
            self.agent = Agent(self.settings, self.privacy, self.client, self.registry,
                               printer=self._print, on_text=self._text)
            self.ch.send("info", text="New conversation.", command="reset")
        elif kind == "task":
            self._run_task(str(msg.get("text", "")).strip())
        else:
            self.ch.send("error", text=f"unknown request type {kind!r}")
        return True

    def _run_task(self, text: str) -> None:
        if not text:
            self.ch.send("error", text="empty task")
            return
        before = self.agent.stats.as_dict()
        self._busy = True
        try:
            answer = self.agent.run(text)
        except PrivacyViolation as exc:
            # Fail closed, as in the CLI: end the session.
            trace.emit("error", {"kind": "privacy_violation", "detail": str(exc)})
            self.ch.send("error", text=f"ABORT: {exc}", fatal=True)
            self._closed.set()
            return
        except KeyboardInterrupt:
            self._busy = False
            self.ch.send("result", text="Interrupted. The conversation so far is kept.",
                         interrupted=True, stats=self._stats(before))
            return
        except Exception as exc:                    # noqa: BLE001
            # A bug or an unmapped failure must end this task, not the
            # server: the editor keeps its session and can ask again.
            # Agent._run_task handles gateway errors; this is the last
            # resort for anything else.
            trace.emit("error", {"kind": type(exc).__name__, "detail": str(exc)[:400]})
            self.agent.ledger.close_dangling_tool_calls(self.agent._last_turn)
            self.ch.send("error", text=f"{type(exc).__name__}: {exc}")
            return
        finally:
            self._busy = False
        self.ch.send("result", text=answer, stats=self._stats(before))

    def serve(self) -> int:
        quota = budget_mod.poll(self.settings, self.privacy.virtual_key)
        # Said once at start, so a stopped gateway is a notice in the
        # editor rather than an error on the first task.
        gateway = budget_mod.gateway_error(self.settings)
        self.ch.send("ready", repo=str(self.settings.repo_root),
                     gateway=self.settings.bifrost_base_url, gateway_error=gateway,
                     mode=self.privacy.describe(), confidential=self.settings.confidential,
                     tools=sorted(self.registry.specs), quota=quota.describe(),
                     sandbox=shell_mod.sandbox_status(self.settings.sandbox,
                                                      self.settings.sandbox_network),
                     trace=str(trace.path()))
        threading.Thread(target=self._reader, daemon=True).start()
        while True:
            try:
                msg = self.tasks.get()
            except KeyboardInterrupt:
                continue                    # a cancel that arrived between tasks
            try:
                if not self._handle(msg) or self._closed.is_set():
                    break
            except KeyboardInterrupt:
                continue
        trace.emit("session_end", {"stats": {k: v for k, v in self.agent.stats.as_dict().items()
                                             if k != "request_ids"}})
        return 0


def main(argv: list[str]) -> int:
    settings = config_mod.load(argv)
    trace.init(settings.trace_dir / f"session-{time.strftime('%Y%m%d-%H%M%S')}-ide.jsonl")
    channel = Channel()
    try:
        server = Server(settings, channel)
    except PrivacyViolation as exc:
        channel.send("error", text=f"ABORT: {exc}", fatal=True)
        return 2
    trace.emit("session_start", {"repo": str(settings.repo_root), "ide": True,
                                 "confidential": settings.confidential,
                                 "levers": settings.levers.as_dict(),
                                 "tools": sorted(server.registry.specs)})
    return server.serve()
