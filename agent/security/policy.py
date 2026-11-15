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
    r"(^|/)id_rsa", r"(^|/)id_ed25519", r"(^|/)id_ecdsa",
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

_INJECTION_MARKERS = (
    "pre-approved", "preapproved", "do not prompt", "don't prompt",
    "no need to ask", "skip approval", "without asking", "auto-approve",
    "autonomous agents", "ignore privacy", "ignore the privacy",
    "system note", "system:", "disregard previous", "ignore previous",
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
    norm = rel_path.replace("\\", "/")
    return any(rx.search(norm) for rx in _SECRET_RE)


def resolve_in_repo(raw_path: str, repo_root: Path) -> Path:
    """Resolve a path and assert it stays inside the repository.

    Resolution happens before the check so a symlink pointing outside the
    repository is caught rather than followed.
    """
    if raw_path is None:
        raise InvalidArgs("path is required")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    root = repo_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise InvalidArgs(f"path escapes the repository root: {raw_path!r} -> {resolved}")
    return resolved


def validate_args(spec: ToolSpec, args: dict, repo_root: Path) -> dict:
    """Path jail, secret shield, .git protection, shell sanitation.

    Runs before the approval prompt so that what the human sees is the
    resolved, validated action rather than the model's description of it.
    """
    out = dict(args or {})

    if "path" in out and out["path"] is not None:
        resolved = resolve_in_repo(str(out["path"]), repo_root)
        rel = os.path.relpath(resolved, repo_root.resolve())
        if is_secret_path(rel):
            raise InvalidArgs(f"refusing to touch a credentials file: {rel}")
        if spec.mutating and (rel == ".git" or rel.startswith(".git/")):
            raise InvalidArgs("direct writes into .git are refused; use the git tool")
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
        for meta in ("|", ";", "&&", "||", ">", ">>", "<", "`", "$("):
            if any(meta in part for part in argv):
                raise InvalidArgs(
                    f"shell metacharacter {meta!r} refused; commands run as argv, never through a shell"
                )
        base = os.path.basename(argv[0])
        if base in SHELL_DENY:
            raise InvalidArgs(f"command {base!r} is on the refusal list")
        out["command"] = argv

    return out


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
        answer, failure = _read_tty_response("\n".join(lines))
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
