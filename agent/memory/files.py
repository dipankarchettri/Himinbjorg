"""Read-file memory keyed by path and content hash.

Rereading the same file is the most common waste in an agent loop, and it
compounds: the reread lands in the context and is then resent on every
subsequent turn.
"""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class FileRecord:
    path: str
    digest: str
    first_sent_turn: int
    content: str
    summary: str | None = None


@dataclass
class FileMemory:
    enabled: bool = True
    # How many turns an earlier copy stays visible to the model. Set to the
    # ledger's pruning window; None when nothing is ever pruned. Without
    # it, a re-read after the original was elided was answered "already
    # provided above" while the model could no longer see it.
    visible_turns: int | None = None
    # Preferred over visible_turns: the ledger's own horizon function, so
    # the two can never disagree about what the model can still see.
    horizon: "Callable[[int], int] | None" = None
    records: dict[str, FileRecord] = field(default_factory=dict)
    suppressed_rereads: int = 0
    bytes_saved: int = 0

    def decide(self, path: str, content: str) -> str:
        """full | diff | skip. The caller renders accordingly."""
        if not self.enabled:
            return "full"
        rec = self.records.get(path)
        if rec is None:
            return "full"
        if rec.digest == sha256(content):
            return "skip"
        return "diff"

    def render(self, path: str, content: str, turn: int, force_full: bool = False) -> str:
        """Return what should actually be put in front of the model.

        force_full: the model already got the "unchanged" stub for this
        call and asked again, so it gets the file.
        """
        mode = "full" if force_full else self.decide(path, content)
        rec = self.records.get(path)
        if mode != "full" and rec is not None and self._pruned(rec.first_sent_turn, turn):
            # The earlier copy has been pruned from what the model sees.
            mode = "full"
        digest = sha256(content)

        if mode == "skip":
            self.suppressed_rereads += 1
            self.bytes_saved += len(content)
            rec = self.records[path]
            return (f"[{path} unchanged since turn {rec.first_sent_turn}; "
                    f"contents already provided above, not repeated]")

        if mode == "diff":
            old = self.records[path].content
            delta = "\n".join(difflib.unified_diff(
                old.splitlines(), content.splitlines(),
                fromfile=f"{path}@earlier", tofile=f"{path}@now", lineterm="", n=2))
            self.records[path] = FileRecord(path, digest, turn, content)
            if len(delta) < len(content):
                self.bytes_saved += len(content) - len(delta)
                return f"[{path} changed since it was last shown; diff only]\n{delta}"
            return content

        self.records[path] = FileRecord(path, digest, turn, content)
        return content

    def _pruned(self, first_turn: int, turn: int) -> bool:
        """Will the copy sent at first_turn be elided in the next request?"""
        if self.horizon is not None:
            return first_turn < self.horizon(turn + 1)
        if self.visible_turns is not None:
            return (turn + 1) - first_turn > self.visible_turns
        return False

    def invalidate(self, path: str) -> None:
        """Called synchronously by the write tools.

        A cached hash must never survive a write the agent itself performed.
        """
        self.records.pop(path, None)

    def stats(self) -> dict:
        return {
            "tracked_files": len(self.records),
            "suppressed_rereads": self.suppressed_rereads,
            "bytes_saved": self.bytes_saved,
        }
