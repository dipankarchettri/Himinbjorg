"""Session persistence: the conversation and the undo journal on disk.

One session per repository and mode, in the user's state directory
(~/.local/state/himinbjorg/sessions). The conversation is saved after
every turn, so a crash, a closed terminal or a restarted editor loses at
most the turn in flight; `himin --resume` continues it. The undo journal
is saved by the write tools before each file is written, so an approved
edit can always be undone, also after a restart.

Confidential and standard sessions of one repository are separate files.
A confidential conversation holds code that must not leave the machine,
so it cannot be resumed in standard mode, where cloud models may answer.

Files are written with mode 0600: they hold the repository's code.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from pathlib import Path

from .memory.ledger import LedgerEntry
from .tools import write as write_mod

VERSION = 1
_FIELDS = {f.name for f in dataclasses.fields(LedgerEntry)}


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "himinbjorg"


def _key(repo_root: Path, confidential: bool) -> str:
    digest = hashlib.sha256(str(Path(repo_root).resolve()).encode()).hexdigest()[:16]
    return digest + ("-confidential" if confidential else "")


def session_path(repo_root: Path, confidential: bool) -> Path:
    return state_dir() / "sessions" / f"{_key(repo_root, confidential)}.json"


def journal_path(repo_root: Path, confidential: bool) -> Path:
    return state_dir() / "undo" / f"{_key(repo_root, confidential)}.json"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)          # never a half-written session


def save(agent) -> None:
    s = agent.settings
    _write(session_path(s.repo_root, s.confidential), {
        "version": VERSION,
        "repo": str(s.repo_root),
        "confidential": s.confidential,
        "saved": time.time(),
        "turn": agent._last_turn,
        "manifest": getattr(agent, "_manifest_text", ""),
        "entries": [dataclasses.asdict(e) for e in agent.ledger.entries],
    })


def load(repo_root: Path, confidential: bool) -> dict | None:
    path = session_path(repo_root, confidential)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("version") != VERSION or data.get("confidential") != confidential:
        return None
    return data


def restore(agent, data: dict) -> int:
    """Put a saved conversation into a fresh agent. Returns its turn count."""
    agent.ledger.entries = [LedgerEntry(**{k: v for k, v in e.items() if k in _FIELDS})
                            for e in data["entries"]]
    agent._last_turn = agent._turn_base = int(data.get("turn") or 0)
    agent._manifest_text = data.get("manifest") or ""
    # A session saved mid-turn can end in tool calls that never ran.
    agent.ledger.close_dangling_tool_calls(agent._last_turn)
    # Files may have changed since: read them again rather than trust memory.
    agent.files.records.clear()
    return agent._last_turn


def forget(repo_root: Path, confidential: bool) -> None:
    session_path(repo_root, confidential).unlink(missing_ok=True)


def describe_age(saved: float) -> str:
    minutes = max(0, int((time.time() - saved) / 60))
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def attach(agent, resume: bool, out=print) -> None:
    """Persist this agent's session; with resume, continue the saved one."""
    s = agent.settings
    write_mod.use_journal(journal_path(s.repo_root, s.confidential))
    agent.checkpoint = lambda: save(agent)
    saved = load(s.repo_root, s.confidential)
    if resume:
        if saved and saved.get("entries"):
            turns = restore(agent, saved)
            out(f"  Resumed the session from {describe_age(saved['saved'])} "
                f"({turns} turns).\n")
        else:
            out("  No saved session for this repository; starting a new one.\n")
    elif saved and saved.get("entries"):
        out(f"  A session from {describe_age(saved['saved'])} is saved for this repository; "
            "`himin --resume` continues it. Starting a new one replaces it.\n")
