"""Git tools. GATED.

Direct writes into .git are refused by the validator; state changes go
through here so the exact command is displayed at the approval prompt.

A repository's own git config is untrusted input, and git runs commands
it names: core.fsmonitor on status and diff, hooks on commit, filter
drivers on add, external diff and textconv programs on diff, gpg.program
when signing. Approving "git status" must not run a command the
repository chose. HARDEN overrides the ones that can be overridden by
name (command-line -c wins over the repository's config), and the
command runs in the same Landlock sandbox as run_command, which also
confines the rest (filter drivers cannot be disabled without knowing
their names): writes only inside the repository and temp, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..security import sandbox as sandbox_mod

HARDEN = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
          "-c", "commit.gpgSign=false", "-c", "core.pager=cat"]

ALLOWED = {
    "status": ["status", "--short"],
    "diff": ["diff", "--no-ext-diff", "--no-textconv"],
    "add": ["add", "-A", "--", "."],
    "log": ["log", "--oneline", "-10"],
}


def _toplevel(repo_root: Path) -> Path | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(repo_root),
                              capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


def git_op(operation: str, repo_root: Path, message: str | None = None,
           sandbox: str = "off", **_) -> str:
    # Refuse unless the working directory IS the repository. Measured
    # 2026-11-14: benchmark repos sat inside the project's own repository,
    # so the agent's commits landed in the parent project's history.
    top = _toplevel(repo_root)
    if top is None:
        return f"ERROR: {repo_root} is not a git repository"
    if top != Path(repo_root).resolve():
        return (f"ERROR: refusing git {operation}: the working directory is inside another "
                f"repository ({top}), not a repository of its own")
    if operation == "commit":
        if not message:
            return "ERROR: commit requires a message"
        argv = ["git", *HARDEN, "commit", "-m", message]
    elif operation in ALLOWED:
        argv = ["git", *HARDEN] + ALLOWED[operation]
    else:
        return f"ERROR: unsupported git operation {operation!r}. Allowed: {sorted(ALLOWED) + ['commit']}"
    if sandbox != "off":
        if sandbox_mod.abi_version() >= 1:
            from .shell import sandboxed_argv
            argv = sandboxed_argv(argv, repo_root, network=False)
        elif sandbox == "required":
            return ("ERROR: AGENT_SANDBOX=required, but Landlock is not available on this "
                    "system, so git was not run.")
    try:
        proc = subprocess.run(argv, cwd=str(repo_root), capture_output=True, stdin=subprocess.DEVNULL,
                              text=True, timeout=30, shell=False)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return f"ERROR: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"exit={proc.returncode}\n{out.strip()[:3000] or '(no output)'}"
