"""CLI entry point."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from . import budget as budget_mod
from . import config as config_mod
from . import gateway as gateway_mod
from . import llm as llm_mod
from . import telemetry
from .memory.ledger import LedgerEntry
from . import terminal
from . import trace
from .security import policy
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
    args = sys.argv[1:] if argv is None else argv
    if "--serve" in args:
        from .server import main as serve_main
        return serve_main(args)
    if args[:1] == ["doctor"]:
        from .doctor import main as doctor_main
        return doctor_main(args[1:])
    from . import setup as setup_mod
    if args[:1] == ["setup"]:
        return setup_mod.main(args[1:])
    if args[:1] == ["gateway"]:
        return setup_mod.gateway_main(args[1:])
    if setup_mod.needs_setup() and not {"--version", "-h", "--help"} & set(args):
        # The first run: nothing says where the gateway or the key is.
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print("  himin is not set up yet. Run `himin setup` in a terminal first.")
            return 1
        code = setup_mod.main([])
        if code:
            return code
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

    task = settings_task(argv)
    styled = terminal.enabled()
    if not styled:
        print(banner(settings, privacy, registry))
    down = budget_mod.gateway_error(settings)
    if down and gateway_mod.owns(settings.bifrost_base_url):
        # The gateway `himin setup` made: start it rather than send the user off.
        problem = gateway_mod.up(settings.bifrost_base_url, config_mod.user_config_path(),
                                 out=(lambda m: print(terminal.dim(m))) if styled else print)
        down = problem or budget_mod.gateway_error(settings)
    if down:
        print(f"  The gateway at {settings.bifrost_base_url} is {down}.\n"
              "  Start it with `himin gateway up` (or `make up` in a clone); "
              "`himin doctor` checks the rest.\n")
        return 1
    quota = budget_mod.poll(settings, privacy.virtual_key)
    if not styled:
        print(f"  {quota.describe()}\n")

    live = LiveText(styled=styled) if sys.stdout.isatty() else None
    notes: list[str] = []
    agent = attach_session(new_agent(settings, privacy, client, registry, live), settings.resume,
                           (lambda *a: notes.append(" ".join(map(str, a)).strip())) if styled
                           else print)
    if styled:
        policy.set_prompt_style(terminal.style_approval)
        print(welcome(settings, privacy, quota, "  ".join(notes)) if not task
              else terminal.dim(f"\n  Himinbjorg  ·  {settings.repo_root}  ·  {privacy.describe()}\n"))

    if task:
        return run_one(agent, settings, task)
    if not sys.stdin.isatty():
        print("  No task given, and stdin is not a terminal. Pass a task as an argument.")
        return 1
    return repl(agent, settings, live)


class LiveText:
    """The model's text as it arrives, kept apart from the activity lines.

    On a terminal only: piped output gets the finished answer alone.
    `styled` draws the activity and the answer as agent/terminal.py does.
    """

    def __init__(self, out=None, styled: bool = False):
        self.out = out or sys.stdout
        self.mid_line = False
        self.styled = styled
        self.answering = False

    def text(self, piece: str) -> None:
        if self.styled and not self.answering:
            self.out.write("\n" + terminal.bold("●") + " ")
            self.answering = True
        self.out.write(piece)
        self.out.flush()
        self.mid_line = not piece.endswith("\n")

    def line(self, *args, **kwargs) -> None:
        if self.mid_line:
            self.out.write("\n")
            self.mid_line = False
        self.answering = False
        if self.styled:
            args = tuple(terminal.style_line(str(a)) for a in args)
        print(*args, file=self.out, **kwargs)


def welcome(settings, privacy, quota, resumed: str = "") -> str:
    models = list(dict.fromkeys(m.split("/", 1)[-1] for m in (
        [settings.model_local_code] if settings.confidential else
        [settings.model_local_code, settings.model_cheap, settings.model_capable])))
    mode = ("confidential: local models only" if settings.confidential
            else "standard")
    sandbox = shell_mod.sandbox_status(settings.sandbox, settings.sandbox_network)
    budget = (f"${quota.current_usage:.2f} of ${quota.max_limit:.2f} used"
              if quota.available and quota.max_limit else
              "no limit on this key" if quota.available else "unknown")
    return terminal.welcome(config_mod._version(), str(settings.repo_root), mode,
                            sandbox.split(":")[0] if sandbox.startswith("Landlock") else sandbox,
                            models, budget, resumed)


def new_agent(settings, privacy, client, registry, live: LiveText | None):
    from .loop import Agent
    if live is None:
        return Agent(settings, privacy, client, registry)
    return Agent(settings, privacy, client, registry, printer=live.line, on_text=live.text)


def attach_session(agent, resume: bool, out=print):
    from . import session as session_mod
    session_mod.attach(agent, resume, out)
    return agent


def _delta(after: dict, before: dict) -> dict:
    return {k: v - before.get(k, 0) for k, v in after.items() if isinstance(v, (int, float))}


def report(agent, settings, before: dict | None = None, label: str = "") -> dict:
    """Print the stats of the last task (or the whole session)."""
    full = agent.stats.as_dict()
    s = _delta(full, before) if before else full
    ids = full["request_ids"][len(before.get("request_ids", [])) if before else 0:]
    if terminal.enabled():
        cost = telemetry.session_cost(ids, settings.bifrost_base_url, settings.bifrost_setup_token)
        print(terminal.summary(s, cost[0] if cost else None, label))
        if cost is not None:
            s["cost_usd"] = cost[0]
        return s
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

    if terminal.enabled():
        print("" if agent.answer_streamed else "\n" + terminal.bold("●") + " " + answer)
    elif agent.answer_streamed:
        print("\n" + "=" * 62)          # the answer is above, as it arrived
    else:
        print("\n" + "=" * 62)
        print(answer)
        print("=" * 62)
    s = report(agent, settings)
    if not terminal.enabled():
        print(f"  trace: {trace.path()}")
    trace.emit("session_end", {"stats": {k: v for k, v in s.items() if k != "request_ids"},
                               "files": agent.files.stats(), "ledger": agent.ledger.stats()})
    return 0


REPL_HELP = """  Commands:
    /cost    totals for the whole session
    /undo    revert the agent's most recent file edit
    /reset   start a fresh conversation (same repository); the saved one is dropped
    /help    this text
    /exit    quit (or Ctrl-D)
  Ctrl-C interrupts the running task and returns to the prompt.
  The conversation is saved after every turn: `himin --resume` continues it."""


def repl(agent, settings, live: LiveText | None = None) -> int:
    try:
        import readline  # noqa: F401  - line editing and history for input()
    except ImportError:
        pass

    styled = terminal.enabled()
    if not styled:
        print("  Interactive session. Each task continues the same conversation.")
        print(REPL_HELP + "\n")
    history = None
    if styled:
        try:
            from prompt_toolkit.history import InMemoryHistory
            history = InMemoryHistory()       # this session only: tasks can name secrets
        except ImportError:
            pass
    while True:
        try:
            if history is not None:
                print()
                line = terminal.input_box(history).strip()
                if line:
                    print(terminal.echo_task(line))
            else:
                line = input(terminal.prompt() if styled else "agent> ").strip()
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
            report(agent, settings, label="session")
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
            from . import session as session_mod
            session_mod.forget(agent.settings.repo_root, agent.settings.confidential)
            agent = attach_session(new_agent(agent.settings, agent.privacy, agent.client,
                                             agent.registry, live), False, lambda *a: None)
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
        if styled:
            print("" if agent.answer_streamed else "\n" + terminal.bold("●") + " " + answer)
        else:
            print("\n" if agent.answer_streamed else "\n" + answer + "\n")
        if not styled:
            report(agent, settings, before)
            print()
        elif agent.stats.llm_calls != before["llm_calls"]:     # not after a greeting
            report(agent, settings, before)
    s = report(agent, settings, label="session" if styled else "")
    trace.emit("session_end", {"stats": {k: v for k, v in s.items() if k != "request_ids"},
                               "files": agent.files.stats(), "ledger": agent.ledger.stats()})
    print(terminal.dim(f"  trace: {trace.path()}") if styled else f"  trace: {trace.path()}")
    return 0


def settings_task(argv: list[str] | None) -> str:
    import argparse
    parser = config_mod.build_parser()
    args, _ = parser.parse_known_args(argv)
    return args.task or ""


if __name__ == "__main__":
    raise SystemExit(main())
