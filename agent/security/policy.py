"""Permission classification, argument validation, and the approval gate.

The gate deliberately has no access to the conversation. It cannot be
influenced by prompt content because it cannot see prompt content. The
permission level is a property of the tool's registration, never something
the model proposes.
"""
from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .. import trace


class Permission(Enum):
    FREE = "FREE"
    GATED = "GATED"


class InvalidArgs(ValueError):
    """Raised by validate_args before any approval prompt is shown."""


# Files whose contents must never enter a model context, in any mode.
SECRET_PATTERNS = (
    r"(^|/)\.env($|\..*)", r"(^|/)\.envrc$",
    r"\.pem$", r"\.key$", r"\.p12$", r"\.pfx$",
    r"(^|/)id_rsa", r"(^|/)id_ed25519", r"(^|/)id_ecdsa", r"(^|/)id_dsa",
    r"\.jks$", r"\.keystore$", r"\.tfstate(\.backup)?$",
    r"(^|/)\.gnupg/", r"(^|/)\.kube/config$", r"(^|/)kubeconfig$",
    r"(^|/)\.pgpass$", r"(^|/)\.htpasswd$",
    r"(^|/)\.git/config$", r"(^|/)\.git-credentials$",
    r"(^|/)\.npmrc$", r"(^|/)\.pypirc$", r"(^|/)\.netrc$",
    r"(^|/)\.aws/", r"(^|/)\.ssh/", r"(^|/)\.docker/config\.json$",
    r"(^|/)credentials(\.json|\.yml|\.yaml)?$",
    r"(^|/)secrets?(\.json|\.yml|\.yaml|\.toml)$",
)
_SECRET_RE = [re.compile(p) for p in SECRET_PATTERNS]

# Commands refused outright. Not a complete defence (the gate is), but it
# stops the obvious catastrophes from ever reaching a human who might
# approve on autopilot.
SHELL_DENY = (
    "rm", "rmdir", "dd", "mkfs", "shutdown", "reboot", "halt", "poweroff",
    "chown", "chmod", "sudo", "su", "passwd", "useradd", "userdel",
    "iptables", "mount", "umount", "kill", "pkill", "killall",
    "curl", "wget", "nc", "ncat", "telnet", "ssh", "scp", "rsync",
)

# Interpreters that turn an argument back into a shell script.
SHELL_INTERPRETERS = ("sh", "bash", "zsh", "dash", "ksh", "fish")

# Commands that run another command. What they wrap is checked too, or
# `env rm -rf .` would pass as "env".
COMMAND_WRAPPERS = ("env", "nice", "nohup", "timeout", "xargs", "stdbuf", "time",
                    "command", "exec", "busybox", "doas", "ionice", "setsid", "watch")

# Arguments that may point outside the repository.
SAFE_OUTSIDE_PATHS = ("/dev/null",)

_INJECTION_MARKERS = (
    "pre-approved", "preapproved", "do not prompt", "don't prompt",
    "no need to ask", "skip approval", "without asking", "auto-approve",
    "autonomous agents", "ignore privacy", "ignore the privacy",
    # Not "system:" alone: it is in ordinary prose and code, and a warning
    # that is always on teaches the reader to skip it.
    "system note", "system override", "disregard previous", "ignore previous",
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    permission: Permission          # hardcoded at registration
    description: str
    schema: dict
    handler: Callable[..., Any]
    mutating: bool = False


@dataclass(frozen=True)
class GateDecision:
    tool: str
    permission: str
    decision: str                   # ALLOW | APPROVED | DENIED | BLOCKED_INVALID_ARGS
    resolved_args: dict
    injection_markers_seen: bool = False
    reason: str = ""


def detect_injection_markers(text: str) -> bool:
    """Advisory only. Recorded for the evidence log, never acted upon.

    The defence is that the gate consults no text at all. This function
    exists so the audit log can show that an injection attempt was present
    and made no difference.
    """
    low = (text or "").lower()
    return any(m in low for m in _INJECTION_MARKERS)


def is_secret_path(rel_path: str) -> bool:
    # Lower-cased: on a case-insensitive filesystem (macOS, Windows by
    # default) `.ENV` opens `.env`. Every pattern is written in lower case.
    norm = rel_path.replace("\\", "/").lower()
    return any(rx.search(norm) for rx in _SECRET_RE)


def resolve_in_repo(raw_path: str, repo_root: Path) -> Path:
    """Resolve a path and assert it stays inside the repository.

    Resolution happens before the check so a symlink pointing outside the
    repository is caught rather than followed.
    """
    if raw_path is None:
        raise InvalidArgs("path is required")
    if "\x00" in raw_path:
        raise InvalidArgs("path contains a NUL byte")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:     # symlink loops, bad names
        raise InvalidArgs(f"cannot resolve path {raw_path!r}: {exc}") from exc
    root = repo_root.resolve()
    if resolved != root and root not in resolved.parents:
        # The hint matters with small local models: they sometimes copy the
        # absolute repository path from the prompt and mangle a character.
        # Telling them the relative form lets the next turn recover.
        raise InvalidArgs(
            f"path escapes the repository root: {raw_path!r} -> {resolved}; "
            f"use a path relative to the repository root, e.g. 'calc.py'")
    return resolved


def validate_args(spec: ToolSpec, args: dict, repo_root: Path) -> dict:
    """Path jail, secret shield, .git protection, shell sanitation.

    Runs before the approval prompt so that what the human sees is the
    resolved, validated action rather than the model's description of it.
    """
    # Only what the tool declares. Underscored keys are the validator's own
    # (_rel), and a key the tool does not declare would override a bound
    # argument such as repo_root (the registry drops them too).
    declared = spec.schema.get("function", {}).get("parameters", {}).get("properties", {})
    out = {k: v for k, v in (args or {}).items()
           if not str(k).startswith("_") and (k in declared or not declared)}

    if "path" in out and out["path"] is not None:
        resolved = resolve_in_repo(str(out["path"]), repo_root)
        rel = os.path.relpath(resolved, repo_root.resolve())
        if is_secret_path(rel):
            raise InvalidArgs(f"refusing to touch a credentials file: {rel}")
        if spec.mutating and (rel == ".git" or rel.startswith(".git/")):
            raise InvalidArgs("direct writes into .git are refused; use the git tool")
        if rel == ".git" or rel.startswith(".git/"):
            # Found in use: with no git tool offered, a model read
            # .git/index (binary) again and again for 139k tokens.
            raise InvalidArgs("git's own files are not readable here; for status, diff "
                              "and log use the git_read tool")
        out["path"] = str(resolved)
        out["_rel"] = rel

    if spec.name == "run_command":
        cmd = out.get("command")
        if isinstance(cmd, str):
            try:
                argv = shlex.split(cmd)
            except ValueError as exc:
                raise InvalidArgs(f"unparseable command: {exc}") from exc
        elif isinstance(cmd, list):
            argv = [str(c) for c in cmd]
        else:
            raise InvalidArgs("command must be a string or a list")
        if not argv:
            raise InvalidArgs("empty command")
        code = _inline_code_index(argv)
        for meta in ("|", ";", "&&", "||", ">", ">>", "<", "`", "$("):
            if any(meta in part for i, part in enumerate(argv) if i != code):
                raise InvalidArgs(
                    f"shell metacharacter {meta!r} refused; commands run as argv, never through a shell"
                )
        _check_argv(argv, repo_root)
        out["command"] = argv

    return out


# Interpreters whose inline-code flag takes a program, not shell syntax.
INLINE_CODE_FLAGS = {"python": "-c", "python3": "-c", "node": "-e"}


def _inline_code_index(argv: list[str]) -> int | None:
    """Index of the code argument in `python -c CODE` or `node -e CODE`.

    That argument is a program in its own language, so `;` or `>` in it
    are Python or JavaScript, not shell. Measured 2026-11-18: refusing
    `python3 -c "from m import f; print(f(7))"` blocked Gemma 31B three
    turns running on three QuixBugs tasks. Refusing it bought nothing:
    the interpreter already runs any code it is given, so the approval
    prompt and the sandbox are what guard it. Only that one argument is
    exempt; the rest of the command is checked as before.
    """
    flag = INLINE_CODE_FLAGS.get(os.path.basename(argv[0]))
    if flag and len(argv) > 2 and argv[1] == flag:
        return 2
    return None


def _check_argv(argv: list[str], repo_root: Path) -> None:
    """Refuse commands that the approval prompt makes easy to misjudge.

    Not a sandbox (an approved command still runs with the user's rights),
    but it stops the obvious routes around the deny list and the secret
    shield before a human is asked about them.
    """
    base = os.path.basename(argv[0])
    if base in SHELL_DENY:
        raise InvalidArgs(f"command {base!r} is on the refusal list")
    names = [os.path.basename(a) for a in argv]
    if base in SHELL_INTERPRETERS and "-c" in argv[1:]:
        raise InvalidArgs(f"{base} -c runs a shell script; run the command directly as argv")
    if base in COMMAND_WRAPPERS:
        hit = next((n for n in names[1:] if n in SHELL_DENY or n in SHELL_INTERPRETERS), None)
        if hit:
            raise InvalidArgs(f"{base} wraps {hit!r}, which is refused")
    if base == "find":
        if "-delete" in argv:
            raise InvalidArgs("find -delete is refused")
        for i, a in enumerate(argv[:-1]):
            if a in ("-exec", "-execdir", "-ok", "-okdir") and (
                    names[i + 1] in SHELL_DENY or names[i + 1] in SHELL_INTERPRETERS):
                raise InvalidArgs(f"find {a} {names[i + 1]!r} is refused")
    root = repo_root.resolve()
    for arg in argv[1:]:
        # `--output=/etc/x` carries a path after the `=`.
        value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
        if not value or value.startswith("-") and "=" not in arg:
            continue
        if value in SAFE_OUTSIDE_PATHS:
            continue
        # Every argument is resolved, not only those that look like an
        # escape: `cat sub/passwd` with sub -> /etc is outside too. An
        # argument that is not a path resolves inside the repository.
        target = Path(os.path.expanduser(value))
        try:
            target = (target if target.is_absolute() else root / target).resolve()
        except (OSError, ValueError):       # not a path at all (NUL, too long)
            continue
        if target != root and root not in target.parents:
            raise InvalidArgs(
                f"argument {arg!r} points outside the repository; "
                f"use paths relative to the repository root")
        value = os.path.relpath(target, root)
        if is_secret_path(os.path.normpath(value)):
            raise InvalidArgs(f"argument {arg!r} names a credentials file")


def _read_tty_response(prompt: str) -> tuple[str, str]:
    """Ask the operator. Returns (answer, failure_reason).

    Approval must never be readable from piped stdin: a 'y' sitting in a
    redirected stream could come from a file the model influenced. So the
    controlling terminal is preferred, and stdin is used only when it is
    genuinely an interactive terminal (isatty), which pipes and redirects
    are not.

    Under some launchers (make, certain remote sessions) /dev/tty cannot be
    opened even though the session is interactive, so the isatty fallback
    is what makes the gate usable there. Without it the gate denies
    silently, which looks exactly like a gate that works.
    """
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(prompt)
            tty.flush()
            line = tty.readline()
            if line == "":
                return "", "terminal closed without an answer"
            return line.strip().lower(), ""
    except (OSError, IOError) as exc:
        tty_error = f"/dev/tty unavailable ({exc.__class__.__name__})"

    if sys.stdin.isatty():
        try:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            line = sys.stdin.readline()
            if line == "":
                return "", "stdin closed without an answer"
            return line.strip().lower(), ""
        except (OSError, IOError) as exc:
            return "", f"{tty_error}; stdin also failed ({exc.__class__.__name__})"

    return "", f"{tty_error}; stdin is not a terminal, so approval cannot be sought"


EDIT_TOOLS = ("apply_patch", "write_file", "rename_symbol")

# The operator channel used instead of the terminal, set once by the IDE
# server (agent/server.py). It receives only what the gate itself built:
# the tool, the resolved arguments and the dry-run diff, never any
# conversation text, so the property above holds: the gate cannot be
# argued with because nothing it reads came from the model. The far end
# is a native modal dialog in the editor, answered by a person.
_operator: Callable[[dict], tuple[str, str]] | None = None


# How the terminal prompt is drawn (agent/terminal.py), set by the CLI on a
# colour terminal. Presentation only: it receives the prompt the gate built
# and returns the same words, styled; it cannot change the answer.
_style_prompt: Callable[[str], str] | None = None


def set_prompt_style(fn: Callable[[str], str] | None) -> None:
    global _style_prompt
    _style_prompt = fn


def set_operator_channel(fn: Callable[[dict], tuple[str, str]] | None) -> None:
    global _operator
    _operator = fn


def gate(spec: ToolSpec, resolved_args: dict, *, auto_approve: bool = False,
         injection_markers_seen: bool = False, preview: str | None = None,
         edits_preapproved: bool = False) -> GateDecision:
    """FREE passes. GATED prompts a human on /dev/tty.

    Note the signature: no conversation, no context, no model output. The
    gate cannot be argued with because it cannot read.
    """
    if spec.permission is Permission.FREE:
        d = GateDecision(spec.name, spec.permission.value, "ALLOW", resolved_args,
                         injection_markers_seen)
        trace.emit("gate_decision", d.__dict__)
        return d

    display = {k: v for k, v in resolved_args.items() if not k.startswith("_")}
    if auto_approve:
        # Test-harness path only. Never reachable from a CLI flag.
        d = GateDecision(spec.name, spec.permission.value, "APPROVED", display,
                         injection_markers_seen, reason="auto_approve (test harness)")
        trace.emit("gate_decision", d.__dict__)
        return d

    if edits_preapproved and spec.name in EDIT_TOOLS:
        # The operator answered "a" earlier in this session. Edits only:
        # shell and git still prompt every time.
        d = GateDecision(spec.name, spec.permission.value, "APPROVED", display,
                         injection_markers_seen, reason="operator approved all edits this session")
        trace.emit("gate_decision", d.__dict__)
        return d

    lines = [
        "",
        "  ---------------------------------------------------------------",
        f"  APPROVAL REQUIRED: {spec.name}",
        "  ---------------------------------------------------------------",
    ]
    if preview is not None and "path" in display:
        # The exact change, computed by a dry run against the file on disk,
        # not the model's arguments. This is what will be written.
        lines.append(f"  path: {display['path']}")
        lines.extend("  " + ln for ln in preview.splitlines())
    else:
        ordered = ([("path", display["path"])] if "path" in display else []) + \
                  [(k, v) for k, v in display.items() if k != "path"]
        for k, v in ordered:
            text = v if isinstance(v, str) else repr(v)
            if len(text) > 800:
                text = text[:800] + f"... [{len(text)} chars total]"
            lines.append(f"  {k}: {text}")
    if injection_markers_seen:
        lines.append("")
        lines.append("  NOTE: approval-bypass phrasing was present in the context.")
        lines.append("        It had no effect on this prompt. Decide for yourself.")
    lines.append("  ---------------------------------------------------------------")
    edit = spec.name in EDIT_TOOLS
    lines.append("  Approve? [y/N" + ("/a = all edits this session" if edit else "") + "] ")

    if _operator is not None:
        answer, failure = _operator({
            "tool": spec.name, "edit": edit, "args": display,
            "path": display.get("path"), "diff": preview,
            "injection_markers_seen": injection_markers_seen,
        })
        answer = (answer or "").strip().lower()
    else:
        text = "\n".join(lines)
        answer, failure = _read_tty_response(_style_prompt(text) if _style_prompt else text)
    approve_all = edit and answer in ("a", "all")
    approved = answer in ("y", "yes") or approve_all
    if failure:
        # Never deny silently: a silent refusal is indistinguishable from a
        # working gate, and that ambiguity hides real breakage.
        sys.stderr.write(f"\n  [approval] {spec.name} DENIED: {failure}\n")
        if _operator is None:
            sys.stderr.write(
                f"  [approval] Run the agent directly in a terminal to approve:\n"
                f"  [approval]   .venv/bin/python -m agent.main \"<task>\" --repo <dir>\n\n")
    d = GateDecision(
        spec.name, spec.permission.value,
        "APPROVED" if approved else "DENIED",
        display, injection_markers_seen,
        reason=failure or ("operator declined" if not approved else
                           "operator approved all edits this session" if approve_all else ""),
    )
    trace.emit("gate_decision", d.__dict__)
    return d
