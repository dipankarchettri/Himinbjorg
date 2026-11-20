"""Settings, lever flags, and confidential-mode resolution.

Confidential mode is resolved exactly once here, before any model call, and
the resulting Settings object is frozen. There is deliberately no way to
flip it at runtime.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIDENTIAL_MARKER = ".agent-confidential"


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / "himinbjorg" / "env"
    legacy = Path(base) / "bifrost-agent" / "env"
    return legacy if legacy.exists() and not path.exists() else path


def _strip_inline_comment(value: str) -> str:
    """Cut an unquoted `#` comment from a .env value.

    A `#` starts a comment when it is unquoted and either begins the value
    or is preceded by whitespace, so `pa#ss` and `"a # b"` survive. Quotes
    are only special at the START of a value, as in dotenv: an apostrophe
    inside a word (`don't`) is data, not an opening delimiter. This matters
    because `.env.example` documented optional keys as
    `GEMINI_API_KEY=   # Google AI Studio key`; read literally, the value
    was the comment text, and a non-empty key overrode the default.
    """
    value = value.lstrip()
    if not value:
        return ""
    if value[0] == "#":
        return ""
    if value[0] in ("'", '"'):
        quote = value[0]
        i = 1
        while i < len(value):
            ch = value[i]
            if quote == '"' and ch == "\\" and i + 1 < len(value):
                i += 2
                continue
            if ch == quote:
                # A comment may follow the closing quote, with or without
                # intervening whitespace.
                rest = value[i + 1:].strip()
                return value[:i + 1] if rest.startswith("#") else value
            i += 1
        return value          # unterminated quote: keep the value literally
    for i, ch in enumerate(value):
        if ch == "#" and value[i - 1].isspace():
            return value[:i]
    return value


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser. Avoids a dependency for ten lines of work."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = _strip_inline_comment(value).strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


@dataclass(frozen=True)
class LeverFlags:
    """Every token lever, individually switchable.

    The benchmark's `naive` arm is this dataclass with everything False.
    Designing it in from the start is what makes per-lever attribution
    possible without a second codebase.
    """
    cache: bool = True
    routing: bool = True
    file_memory: bool = True
    retrieval: bool = True
    pruning: bool = True
    tool_disclosure: bool = True
    diff_writes: bool = True
    loop_limits: bool = True

    @staticmethod
    def all_off() -> "LeverFlags":
        return LeverFlags(False, False, False, False, False, False, False, False)

    def as_dict(self) -> dict[str, bool]:
        return {
            "cache": self.cache, "routing": self.routing,
            "file_memory": self.file_memory, "retrieval": self.retrieval,
            "pruning": self.pruning, "tool_disclosure": self.tool_disclosure,
            "diff_writes": self.diff_writes, "loop_limits": self.loop_limits,
        }


@dataclass(frozen=True)
class Settings:
    bifrost_base_url: str
    vk_standard: str
    vk_confidential: str
    tavily_api_key: str | None
    repo_root: Path
    confidential: bool
    max_turns: int
    levers: LeverFlags
    model_local_small: str
    model_local_code: str
    model_cheap: str
    model_capable: str
    trace_dir: Path
    auto_approve: bool = False   # test harness only, never a CLI flag
    # Namespace for gateway cache keys. Defaults to the repository, so an
    # identical re-run on the same repo is served from cache. The
    # benchmark sets a per-run value so runs cannot feed each other.
    cache_scope: str = ""
    # 0.0 for normal use. The benchmark samples at a higher temperature,
    # because at 0.0 repeats are not independent: Ollama reuses its
    # prompt cache and repeat N replays repeat 1, so "3/3" is one sample.
    temperature: float = 0.0
    # Unlocks the gateway's /api surface for per-request log records (used
    # for the session cost line). Empty disables the lookup.
    bifrost_setup_token: str = ""
    prune_step: int = 4         # measured: stable prefix, fewer prompt-cache writes (VERIFIED.md)
    # Approved commands run in a Landlock sandbox (agent/security/sandbox.py):
    # "auto" where the kernel supports it, "required" to refuse commands
    # when it does not, "off" to run them with the user's full rights.
    sandbox: str = "auto"
    sandbox_network: bool = False
    context_budget: int = 28_000
    # How the pruning lever manages the context. "turns": elide tool
    # results older than a few turns (the measured default). "budget":
    # keep everything verbatim, so the prompt prefix stays cacheable, and
    # summarise the older turns once the context nears context_budget
    # (opencode's approach). Cheaper only with provider prompt caching;
    # on a local model it costs more tokens (docs/VERIFIED.md).
    prune_mode: str = "turns"

    @property
    def virtual_key(self) -> str:
        return self.vk_confidential if self.confidential else self.vk_standard


def is_confidential(repo_root: Path, flag: bool) -> bool:
    """Either trigger arms it. Neither can disarm the other."""
    return bool(flag) or (repo_root / CONFIDENTIAL_MARKER).exists()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="himin", description="Himinbjorg: token-efficient coding agent on Bifrost")
    p.add_argument("task", nargs="?", help="Task description. Omit for an interactive REPL. "
                                           "`himin doctor` checks the setup instead.")
    p.add_argument("--repo", default=".", help="Repository root the agent may touch")
    p.add_argument("--confidential", action="store_true",
                   help="Force local-only processing. Also triggered by a .agent-confidential file.")
    p.add_argument("--max-turns", type=int, default=25)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--no-routing", action="store_true")
    p.add_argument("--no-file-memory", action="store_true")
    p.add_argument("--no-retrieval", action="store_true")
    p.add_argument("--no-pruning", action="store_true")
    p.add_argument("--no-tool-disclosure", action="store_true")
    p.add_argument("--no-diff-writes", action="store_true")
    p.add_argument("--no-loop-limits", action="store_true")
    p.add_argument("--naive", action="store_true", help="Disable every token lever (benchmark baseline arm)")
    p.add_argument("--trace-dir", default=None)
    p.add_argument("--serve", action="store_true",
                   help="Run as the editor server (JSON lines on stdio), for the VS Code extension")
    return p


def _prune_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in ("turns", "budget"):
        raise ValueError(f"AGENT_PRUNE_MODE must be turns or budget, not {value!r}")
    return mode


def _sandbox_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in ("auto", "required", "off"):
        raise SystemExit(f"AGENT_SANDBOX must be auto, required or off, not {value!r}")
    return mode


def load(argv: list[str] | None = None) -> Settings:
    args = build_parser().parse_args(argv)
    project_root = Path(__file__).resolve().parent.parent
    # Layered, lowest first: the install directory's .env, the user's config
    # file, then the process environment. Deliberately NOT the target
    # repository: a repository is untrusted input, and one that set
    # BIFROST_BASE_URL could route your code to a server of its choosing.
    env = {**load_dotenv(project_root / ".env"), **load_dotenv(user_config_path()),
           **os.environ}

    repo_root = Path(args.repo).resolve()

    if args.naive:
        levers = LeverFlags.all_off()
    else:
        levers = LeverFlags(
            cache=not args.no_cache,
            routing=not args.no_routing,
            file_memory=not args.no_file_memory,
            retrieval=not args.no_retrieval,
            pruning=not args.no_pruning,
            tool_disclosure=not args.no_tool_disclosure,
            diff_writes=not args.no_diff_writes,
            loop_limits=not args.no_loop_limits,
        )

    trace_dir = Path(args.trace_dir).resolve() if args.trace_dir else project_root / "traces"

    return Settings(
        bifrost_base_url=env.get("BIFROST_BASE_URL", "http://localhost:8099").rstrip("/"),
        vk_standard=env.get("BIFROST_VK_DEMO", "sk-bf-demo-user-key"),
        vk_confidential=env.get("BIFROST_VK_CONFIDENTIAL", "sk-bf-confidential-key"),
        tavily_api_key=env.get("TAVILY_API_KEY") or None,
        bifrost_setup_token=env.get("BIFROST_SETUP_TOKEN", ""),
        prune_step=int(env.get("AGENT_PRUNE_STEP", "4")),
        sandbox=_sandbox_mode(env.get("AGENT_SANDBOX", "auto")),
        sandbox_network=env.get("AGENT_SANDBOX_NETWORK", "0").strip().lower() in ("1", "true", "yes"),
        context_budget=int(env.get("AGENT_CONTEXT_TOKENS", "28000")),
        prune_mode=_prune_mode(env.get("AGENT_PRUNE_MODE", "turns")),
        repo_root=repo_root,
        confidential=is_confidential(repo_root, args.confidential),
        max_turns=args.max_turns,
        levers=levers,
        model_local_small=env.get("AGENT_MODEL_LOCAL_SMALL", "ollama/qwen3:1.7b"),
        # Verified 2026-11-14: the qwen2.5-coder family returns tool calls as
        # plain text; the instruct models return structured tool_calls.
        model_local_code=env.get("AGENT_MODEL_LOCAL_CODE", "ollama/qwen2.5:32b-instruct"),
        model_cheap=env.get("AGENT_MODEL_CHEAP", "openai/gpt-4o-mini"),
        model_capable=env.get("AGENT_MODEL_CAPABLE", "anthropic/claude-sonnet-5-5"),
        trace_dir=trace_dir,
        cache_scope="repo-" + __import__("hashlib").sha256(
            str(repo_root).encode()).hexdigest()[:12],
    )
