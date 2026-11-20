"""Context ledger and pruning.

The message list is derived from this ledger rather than accumulated
blindly. Pruning writes a visible marker where content was elided, because
silent truncation is how agents start inventing file contents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .. import trace

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
    path: str | None = None     # a read_file result's file, relative to the repository
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
    prune_step: int = 4         # as Settings.prune_step, measured (VERIFIED.md)
    # Hard ceiling on the rendered context, in estimated tokens. Above it
    # the oldest unpinned tool results are elided first, then old assistant
    # text. A local model silently drops the START of an over-long prompt
    # (the system prompt and the task), which is far worse. Applies whether
    # or not the pruning lever is on.
    context_budget: int = 28_000
    # "turns": elide old tool results (above). "budget": send everything
    # verbatim, so each request extends the last one's prefix; the agent
    # replaces old turns with a summary near the budget (Agent._compact).
    mode: str = "turns"
    entries: list[LedgerEntry] = field(default_factory=list)
    # While a read-only task runs: its first turn and the files it names,
    # whose reads are kept to the answer. Eliding them after three turns
    # saves tokens on an edit, whose old reads are stale anyway, but on a
    # review of named files they are the work: DeepSeek re-read ledger.py
    # six times in one, each a whole call with the context resent. Keeping
    # every read instead grew the context with each file it wandered into
    # (326k tokens against 230k), so only the named ones are kept.
    keep_reads_from: int | None = None
    keep_paths: tuple[str, ...] = ()
    elided_tokens: int = 0      # left out of the latest request
    elided_total: int = 0       # left out, summed over the requests sent
    compacted_tokens: int = 0   # replaced by summaries, over the session

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

    def keeps(self, path: str | None) -> bool:
        return (self.keep_reads_from is not None and bool(path)
                and any(path == p or path.endswith("/" + p) for p in self.keep_paths))

    def read_horizon(self, current_turn: int, path: str | None = None) -> int:
        """Reads of `path` from turns before this are elided."""
        h = self.horizon(current_turn)
        return min(h, self.keep_reads_from) if self.keeps(path) else h

    def render(self, current_turn: int, fit: bool = True) -> list[dict[str, Any]]:
        """The messages of the next request. With fit, it is the request
        itself (one render per request), and what it leaves out is recorded;
        estimates render without fit and count nothing. They used to, so the
        run total grew with every estimate as well."""
        out: list[dict[str, Any]] = []
        elided = 0
        horizon = self.horizon(current_turn)
        for e in self.entries:
            if not self.pruning_enabled or self.mode == "budget":
                out.append(self._as_message(e, e.content))
                continue

            age = current_turn - e.turn
            content = e.content

            if e.superseded and not e.pinned:
                # A tool result still needs a body, or the tool_call it
                # answers is left dangling and the provider rejects the
                # message list.
                replacement = f"[superseded: {e.summary or 'newer version provided later'}]"
                elided += max(0, e.tokens - estimate_tokens(replacement))
                out.append(self._as_message(e, replacement))
                continue

            if (not e.pinned and e.kind == "tool_result"
                    and e.turn < (self.read_horizon(current_turn, e.path)
                                  if e.name == "read_file" else horizon)):
                replacement = f"[elided, turn {e.turn}: {e.summary or self._one_line(content)}]"
                elided += max(0, e.tokens - estimate_tokens(replacement))
                out.append(self._as_message(e, replacement))
                continue

            out.append(self._as_message(e, content))
        if not fit:
            return out
        out, cut = self._fit_budget(out)
        self.elided_tokens = elided + cut
        self.elided_total += self.elided_tokens
        return out

    def estimate(self, current_turn: int) -> int:
        """Estimated tokens of the next request, before any fitting."""
        return sum(estimate_tokens(m.get("content") or "")
                   for m in self.render(current_turn, fit=False))

    def compact(self, before_turn: int, summary: str) -> int:
        """Replace unpinned entries from turns before `before_turn` with one
        pinned summary. Cut by turn, so a tool call and its results always
        go together. Returns how many entries were replaced."""
        old = [e for e in self.entries if not e.pinned and e.turn < before_turn]
        if not old:
            return 0
        keep_pinned = [e for e in self.entries if e.pinned and e.turn < before_turn]
        recent = [e for e in self.entries if e.turn >= before_turn]
        note = LedgerEntry(before_turn - 1, "user",
                           "Summary of the earlier work in this session (the older turns "
                           "were replaced by it):\n\n" + summary,
                           pinned=True, kind="summary")
        self.compacted_tokens += max(0, sum(e.tokens for e in old) - note.tokens)
        self.entries = keep_pinned + [note] + recent
        return len(old)

    def _fit_budget(self, out: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Elide the oldest unpinned tool results, then old assistant text,
        until the request fits. What follows the last assistant message is
        what the model has not seen yet, and is never elided: it once was,
        so a review that read five files in one turn saw only the last one
        ("every read_file on these was elided"). If that alone is over the
        budget, its tool results are shortened to share the room left."""
        sizes = [estimate_tokens(m.get("content") or "") for m in out]
        total = sum(sizes)
        if total <= self.context_budget:
            return out, 0
        # The latest assistant message and everything after it stay.
        last = max((i for i, m in enumerate(out) if m["role"] == "assistant"), default=-1)
        unseen = last if last >= 0 else max(0, len(out) - 2)
        pinned = {i for i, e in enumerate(self.entries[:len(out)]) if e.pinned}
        stub = "[elided to fit the context window]"
        cut = 0
        for role in ("tool", "assistant"):
            for i in range(unseen):
                if total <= self.context_budget:
                    return out, cut
                m = out[i]
                if i in pinned or m["role"] != role or len(m.get("content") or "") <= len(stub):
                    continue
                saved = sizes[i] - estimate_tokens(stub)
                out[i] = {**m, "content": stub}
                sizes[i] -= saved
                total -= saved
                cut += saved
        fresh = [i for i in range(unseen, len(out)) if out[i]["role"] == "tool"]
        if total > self.context_budget and fresh:
            room = self.context_budget - (total - sum(sizes[i] for i in fresh))
            share = max(200, room // len(fresh)) * 4       # characters each
            for i in fresh:
                body = out[i].get("content") or ""
                if len(body) > share:
                    note = (f"\n[cut to fit the context window: {share:,} of {len(body):,} "
                            "characters shown; read a narrower range with start and end]")
                    out[i] = {**out[i], "content": body[:share] + note}
                    saved = sizes[i] - estimate_tokens(out[i]["content"])
                    sizes[i] -= saved
                    total -= saved
                    cut += saved
        if total > self.context_budget:
            trace.emit("context_over_budget", {"estimate": total, "budget": self.context_budget})
        return out, cut

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
            "elided_tokens_estimate": self.elided_total,
            "elided_last_request_estimate": self.elided_tokens,
            "compacted_tokens_estimate": self.compacted_tokens,
            "raw_tokens_estimate": sum(e.tokens for e in self.entries),
        }
