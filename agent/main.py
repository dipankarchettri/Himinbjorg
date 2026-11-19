"""CLI entry point."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from . import budget as budget_mod
from . import config as config_mod
from . import llm as llm_mod
from . import telemetry
from .memory.ledger import LedgerEntry
from . import trace
from .security.privacy import PrivacyMode, PrivacyViolation
from .tools import shell as shell_mod
from .tools.registry import Registry


def build(settings):
    privacy = PrivacyMode.build(
        settings.confidential, settings.vk_standard, settings.vk_confidential)
    registry = Registry(privacy, settings)
    client = llm_mod.LLMClient(settings, privacy)
    return privacy, registry, client


def banner(settings, privacy, registry) -> str:
    tools = ", ".join(sorted(registry.specs))
    levers = ", ".join(k for k, v in settings.levers.as_dict().items() if v) or "none"
    return "\n".join([
        "",
        f"  repo      : {settings.repo_root}",
        f"  gateway   : {settings.bifrost_base_url}",
        f"  mode      : {privacy.describe()}",
        f"  sandbox   : {shell_mod.sandbox_status(settings.sandbox, settings.sandbox_network)}",
        f"  tools     : {tools}",
        f"  levers on : {levers}",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    if "--serve" in (sys.argv[1:] if argv is None else argv):
        from .server import main as serve_main
        return serve_main(sys.argv[1:] if argv is None else argv)
    settings = config_mod.load(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trace.init(settings.trace_dir / f"session-{stamp}.jsonl")

    try:
        privacy, registry, client = build(settings)
    except PrivacyViolation as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 2

    trace.emit("session_start", {
        "repo": str(settings.repo_root),
        "confidential": settings.confidential,
        "levers": settings.levers.as_dict(),
        "tools": sorted(registry.specs),
    })

    print(banner(settings, privacy, registry))
    quota = budget_mod.poll(settings, privacy.virtual_key)
    print(f"  {quota.describe()}\n")

    live = LiveText() if sys.stdout.isatty() else None
    agent = new_agent(settings, privacy, client, registry, live)

    task = settings_task(argv)
    if task:
        return run_one(agent, settings, task)
    if not sys.stdin.isatty():
        print("  No task given, and stdin is not a terminal. Pass a task as an argument.")
        return 1
    return repl(agent, settings, live)


class LiveText:
    """The model's text as it arrives, kept apart from the activity lines.

    On a terminal only: piped output gets the finished answer alone.
    """

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.mid_line = False

    def text(self, piece: str) -> None:
        self.out.write(piece)
        self.out.flush()
        self.mid_line = not piece.endswith("\n")

    def line(self, *args, **kwargs) -> None:
        if self.mid_line:
            self.out.write("\n")
            self.mid_line = False
        print(*args, file=self.out, **kwargs)


def new_agent(settings, privacy, client, registry, live: LiveText | None):
    from .loop import Agent
    if live is None:
        return Agent(settings, privacy, client, registry)
    return Agent(settings, privacy, client, registry, printer=live.line, on_text=live.text)


def _delta(after: dict, before: dict) -> dict:
    return {k: v - before.get(k, 0) for k, v in after.items() if isinstance(v, (int, float))}


def report(agent, settings, before: dict | None = None) -> dict:
    """Print the stats of the last task (or the whole session)."""
    full = agent.stats.as_dict()
    s = _delta(full, before) if before else full
    ids = full["request_ids"][len(before.get("request_ids", [])) if before else 0:]
    print(f"  turns={s['turns']} llm_calls={s['llm_calls']} tokens={s['total_tokens']} "
          f"(prompt {s['prompt_tokens']}, completion {s['completion_tokens']})")
    print(f"  provider_tokens={s['provider_tokens']} (served from gateway cache: "
          f"{s['cached_tokens']}; provider prompt cache read {s['prompt_cache_read']}, "
          f"write {s['prompt_cache_write']})")
    print(f"  tool_calls={s['tool_calls']} approvals={s['approvals_granted']}/"
          f"{s['approvals_requested']}")
    cost = telemetry.session_cost(ids, settings.bifrost_base_url, settings.bifrost_setup_token)
    if cost is not None:
        dollars, found = cost
        print(f"  cost=${dollars:.6f} (gateway-priced, {found}/{len(set(ids))} requests in logs)")
        s["cost_usd"] = dollars
    return s


def run_one(agent, settings, task: str) -> int:
    try:
        answer = agent.run(task)
    except PrivacyViolation as exc:
        # Fail closed. An outage is a better outcome than a privacy breach.
        print(f"\nABORT: {exc}", file=sys.stderr)
        trace.emit("error", {"kind": "privacy_violation", "detail": str(exc)})
        return 2
    except KeyboardInterrupt:
        print("\n  Interrupted. Session trace:", trace.path())
        return 130

    if agent.answer_streamed:
        print("\n" + "=" * 62)          # the answer is above, as it arrived
    else:
        print("\n" + "=" * 62)
        print(answer)
        print("=" * 62)
    s = report(agent, settings)
    print(f"  trace: {trace.path()}")
    trace.emit("session_end", {"stats": {k: v for k, v in s.items() if k != "request_ids"},
                               "files": agent.files.stats(), "ledger": agent.ledger.stats()})
    return 0


REPL_HELP = """  Commands:
    /cost    totals for the whole session
    /undo    revert the agent's most recent file edit
    /reset   start a fresh conversation (same repository)
    /help    this text
    /exit    quit (or Ctrl-D)
  Ctrl-C interrupts the running task and returns to the prompt."""


def repl(agent, settings, live: LiveText | None = None) -> int:
    try:
        import readline  # noqa: F401  - line editing and history for input()
    except ImportError:
        pass

    print("  Interactive session. Each task continues the same conversation.")
    print(REPL_HELP + "\n")
    while True:
        try:
            line = input("agent> ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            continue
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/help":
            print(REPL_HELP)
            continue
        if line == "/cost":
            report(agent, settings)
            continue
        if line == "/undo":
            from .tools import write as write_mod
            result = write_mod.undo_last()
            print(f"  {result}")
            if result != "nothing to undo":
                agent.files.records.clear()
                agent.ledger.append(LedgerEntry(
                    agent._last_turn, "user", f"[The operator undid the last edit: {result}.]"))
            continue
        if line == "/reset":
            agent = new_agent(agent.settings, agent.privacy, agent.client, agent.registry, live)
            print("  New conversation.")
            continue
        before = agent.stats.as_dict()
        try:
            answer = agent.run(line)
        except PrivacyViolation as exc:
            print(f"\nABORT: {exc}", file=sys.stderr)
            trace.emit("error", {"kind": "privacy_violation", "detail": str(exc)})
            return 2
        except KeyboardInterrupt:
            print("\n  Interrupted. The conversation so far is kept.")
            continue
        print("\n" if agent.answer_streamed else "\n" + answer + "\n")
        report(agent, settings, before)
        print()
    s = report(agent, settings)
    trace.emit("session_end", {"stats": {k: v for k, v in s.items() if k != "request_ids"},
                               "files": agent.files.stats(), "ledger": agent.ledger.stats()})
    print(f"  trace: {trace.path()}")
    return 0


def settings_task(argv: list[str] | None) -> str:
    import argparse
    parser = config_mod.build_parser()
    args, _ = parser.parse_known_args(argv)
    return args.task or ""


if __name__ == "__main__":
    raise SystemExit(main())
