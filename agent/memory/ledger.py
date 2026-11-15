"""Context ledger and pruning.

The message list is derived from this ledger rather than accumulated
blindly. Pruning writes a visible marker where content was elided, because
silent truncation is how agents start inventing file contents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


def estimate_tokens(text: str) -> int:
    """Rough character-based estimate.

    Only used for pruning decisions and progress display. Billed numbers
    always come from the gateway, never from this function.
    """
    return max(1, len(text or "") // 4)


@dataclass
class LedgerEntry:
    turn: int
    role: Role
    content: str
    pinned: bool = False
    kind: str = "message"          # message | tool_result | plan | task
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None
    name: str | None = None
    summary: str | None = None
    superseded: bool = False

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)


@dataclass
class Ledger:
    pruning_enabled: bool = True
    keep_recent_turns: int = 3
    # Move the pruning boundary in steps of this many turns rather than
    # every turn. A boundary that moves every turn rewrites one old message
    # per turn, which breaks the provider's prompt-prefix cache (cloud
    # cost) and Ollama's prefix KV reuse (local latency) at that point.
    prune_step: int = 1
    # Hard ceiling on the rendered context, in estimated tokens. Above it
    # the oldest unpinned tool results are elided first, then old assistant
    # text. A local model silently drops the START of an over-long prompt
    # (the system prompt and the task), which is far worse. Applies whether
    # or not the pruning lever is on.
    context_budget: int = 28_000
    entries: list[LedgerEntry] = field(default_factory=list)
    elided_tokens: int = 0

    def append(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)

    def supersede(self, predicate) -> None:
        """Mark earlier entries obsolete, e.g. a file re-read after a write."""
        for e in self.entries:
            if not e.pinned and predicate(e):
                e.superseded = True

    def horizon(self, current_turn: int) -> int:
        """Tool results from turns before this are elided."""
        h = current_turn - self.keep_recent_turns
        step = max(1, self.prune_step)
        return (h // step) * step

    def render(self, current_turn: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        horizon = self.horizon(current_turn)
        for e in self.entries:
            if not self.pruning_enabled:
                out.append(self._as_message(e, e.content))
                continue

            age = current_turn - e.turn
            content = e.content

            if e.superseded and not e.pinned:
                # A tool result still needs a body, or the tool_call it
                # answers is left dangling and the provider rejects the
                # message list.
                replacement = f"[superseded: {e.summary or 'newer version provided later'}]"
                self.elided_tokens += max(0, e.tokens - estimate_tokens(replacement))
                out.append(self._as_message(e, replacement))
                continue

            if not e.pinned and e.kind == "tool_result" and e.turn < horizon:
                replacement = f"[elided, turn {e.turn}: {e.summary or self._one_line(content)}]"
                self.elided_tokens += max(0, e.tokens - estimate_tokens(replacement))
                out.append(self._as_message(e, replacement))
                continue

            out.append(self._as_message(e, content))
        return self._fit_budget(out)

    def _fit_budget(self, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        total = sum(estimate_tokens(m.get("content") or "") for m in out)
        if total <= self.context_budget:
            return out
        pinned = {id(m) for m, e in zip(out, self.entries) if e.pinned}
        for role in ("tool", "assistant"):
            for m in out[:-2]:                 # never the latest exchange
                if total <= self.context_budget:
                    return out
                if id(m) in pinned or m["role"] != role:
                    continue
                body = m.get("content") or ""
                stub = "[elided to fit the context window]"
                if len(body) > len(stub):
                    total -= estimate_tokens(body) - estimate_tokens(stub)
                    self.elided_tokens += estimate_tokens(body) - estimate_tokens(stub)
                    m["content"] = stub
        return out

    @staticmethod
    def _one_line(text: str, limit: int = 110) -> str:
        flat = " ".join((text or "").split())
        return flat[:limit] + ("..." if len(flat) > limit else "")

    @staticmethod
    def _as_message(e: LedgerEntry, content: str) -> dict[str, Any]:
        if e.role == "tool":
            return {"role": "tool", "tool_call_id": e.tool_call_id,
                    "name": e.name, "content": content}
        msg: dict[str, Any] = {"role": e.role, "content": content}
        if e.role == "assistant" and e.tool_calls:
            msg["tool_calls"] = e.tool_calls
            # An assistant turn carrying tool_calls must not also carry a
            # null content field for some providers.
            msg["content"] = content or ""
        return msg

    def close_dangling_tool_calls(self, turn: int) -> int:
        """Answer tool calls that never got a result (an interrupted turn).

        Providers reject a message list in which an assistant tool call has
        no matching tool message. Returns how many were closed.
        """
        answered = {e.tool_call_id for e in self.entries if e.role == "tool"}
        closed = 0
        for e in list(self.entries):
            for tc in (e.tool_calls or []) if e.role == "assistant" else []:
                if tc.get("id") not in answered:
                    self.entries.append(LedgerEntry(
                        turn, "tool", "[interrupted by the operator; not executed]",
                        kind="tool_result", tool_call_id=tc.get("id"),
                        name=(tc.get("function") or {}).get("name")))
                    answered.add(tc.get("id"))
                    closed += 1
        return closed

    def stats(self) -> dict:
        return {
            "entries": len(self.entries),
            "elided_tokens_estimate": self.elided_tokens,
            "raw_tokens_estimate": sum(e.tokens for e in self.entries),
        }
