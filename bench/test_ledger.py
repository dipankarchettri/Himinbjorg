"""The ledger decides what the model sees and what it is told was left out,
and the router which model runs. Both are pure, so they are tested here
directly rather than only through the loop."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.config import Settings
from agent.memory.ledger import Ledger, LedgerEntry
from agent.router import Tier, route


def _call(i: str) -> dict:
    return {"id": i, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}


def _batch(ledger: Ledger, turn: int, n: int, size: int) -> None:
    ledger.append(LedgerEntry(turn, "assistant", "", tool_calls=[_call(f"t{turn}c{i}") for i in range(n)]))
    for i in range(n):
        ledger.append(LedgerEntry(turn, "tool", f"file {turn}.{i} " + "x" * size, kind="tool_result",
                                  tool_call_id=f"t{turn}c{i}", name="read_file"))


def test_results_the_model_has_not_seen_are_never_elided():
    # Found when the agent reviewed this repository: five files read in one
    # turn, all but the last replaced by "[elided to fit the context
    # window]" before the model saw any of them.
    ledger = Ledger(context_budget=2000)
    ledger.append(LedgerEntry(0, "user", "review the code", pinned=True, kind="task"))
    _batch(ledger, 0, 1, 3000)
    _batch(ledger, 1, 4, 3000)
    out = ledger.render(2)
    fresh = [m["content"] for m in out[-4:]]
    assert out[2]["content"] == "[elided to fit the context window]"      # the old one goes first
    assert all(c.startswith(f"file 1.{i} ") for i, c in enumerate(fresh))   # every new one is there
    assert all("cut to fit the context window" in c for c in fresh)          # shortened, and says so
    assert len({len(c) for c in fresh}) == 1                                 # an equal share each


def test_unseen_results_that_fit_are_left_whole():
    ledger = Ledger(context_budget=5000)
    ledger.append(LedgerEntry(0, "user", "review", pinned=True, kind="task"))
    _batch(ledger, 0, 2, 6000)
    _batch(ledger, 1, 3, 1000)
    out = ledger.render(2)
    assert all("cut to fit" not in m["content"] and len(m["content"]) > 1000 for m in out[-3:])


def test_elided_tokens_are_counted_once_per_request_and_never_for_estimates():
    ledger = Ledger(context_budget=100_000, keep_recent_turns=1, prune_step=1)
    ledger.append(LedgerEntry(0, "user", "task", pinned=True, kind="task"))
    _batch(ledger, 0, 1, 4000)
    for turn in range(1, 6):
        ledger.append(LedgerEntry(turn, "user", "go on"))
    ledger.render(5)
    first = ledger.elided_tokens
    for _ in range(20):
        ledger.estimate(5)                       # not requests: count nothing
    for _ in range(2):
        ledger.render(5)                         # two more requests
    stats = ledger.stats()
    assert first > 900 and stats["elided_last_request_estimate"] == first
    assert stats["elided_tokens_estimate"] == 3 * first


def test_the_ledger_prunes_in_the_measured_steps_by_default():
    assert Ledger().prune_step == Settings.__dataclass_fields__["prune_step"].default == 4


def test_routing_reads_the_task_only():
    assert route("refactor the payment module", confidential=False, routing_enabled=True).tier is Tier.T2_CAPABLE
    assert route("fix the typo in the README", confidential=False, routing_enabled=True).tier is Tier.T0_LOCAL
    assert route("add a discount code", confidential=False, routing_enabled=True).tier is Tier.T1_CHEAP
    assert route("x " * 400, confidential=False, routing_enabled=True).tier is Tier.T2_CAPABLE
    assert route("fix the typo", confidential=False, routing_enabled=False).tier is Tier.T2_CAPABLE


def _tool(ledger: Ledger, turn: int, name: str, body: str) -> LedgerEntry:
    i = f"t{turn}{name}"
    ledger.append(LedgerEntry(turn, "assistant", "", tool_calls=[
        {"id": i, "type": "function", "function": {"name": name, "arguments": "{}"}}]))
    e = LedgerEntry(turn, "tool", body, kind="tool_result", tool_call_id=i, name=name)
    ledger.append(e)
    return e


def test_an_elided_command_output_can_be_recalled_whole_and_a_read_cannot():
    # Test output cannot be had again for free; a file can be read again,
    # and an old copy of it may be stale.
    ledger = Ledger()
    run = _tool(ledger, 1, "run_command", "exit=1\n" + "FAILED test_x\n" * 50)
    read = _tool(ledger, 1, "read_file", "calc.py (5 lines): ...")
    out = {m.get("tool_call_id"): m["content"] for m in ledger.render(9) if m["role"] == "tool"}
    assert out[run.tool_call_id].endswith(f"; recall {run.ref} to see it whole]")
    assert "recall" not in out[read.tool_call_id]
    assert ledger.recall(run.ref).content == run.content
    assert (run.ref, read.ref) == ("r1", "r2")


def test_recall_survives_compaction_and_refs_never_repeat():
    ledger = Ledger()
    old = _tool(ledger, 1, "grep", "a.py:1: x")
    ledger.compact(5, "summary")
    assert ledger.recall(old.ref).content == "a.py:1: x"
    assert _tool(ledger, 6, "grep", "b.py:2: y").ref == "r2"


def test_a_restored_session_continues_the_refs():
    ledger = Ledger()
    _tool(ledger, 1, "grep", "a")
    restored = Ledger(entries=list(ledger.entries))
    assert _tool(restored, 2, "grep", "b").ref == "r2"


def test_a_superseded_output_is_not_offered_for_recall():
    ledger = Ledger()
    e = _tool(ledger, 1, "grep", "calc.py:4: return a / b")
    e.superseded = True
    out = [m["content"] for m in ledger.render(9) if m["role"] == "tool"]
    assert "recall" not in out[0] and not Ledger.recallable(e)


def test_output_elided_to_fit_the_budget_names_its_ref():
    ledger = Ledger(context_budget=500)
    e = _tool(ledger, 1, "run_command", "y" * 4000)
    _tool(ledger, 2, "grep", "z")
    out = ledger.render(2)
    assert out[1]["content"] == f"[elided to fit the context window; recall {e.ref} to see it whole]"
