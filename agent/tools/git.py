"""Git tools. GATED.

Direct writes into .git are refused by the validator; state changes go
through here so the exact command is displayed at the approval prompt.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ALLOWED = {
    "status": ["status", "--short"],
    "diff": ["diff"],
    "add": ["add", "-A", "--", "."],
    "log": ["log", "--oneline", "-10"],
}


def _toplevel(repo_root: Path) -> Path | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(repo_root),
                              capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


def git_op(operation: str, repo_root: Path, message: str | None = None, **_) -> str:
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
        argv = ["git", "commit", "-m", message]
    elif operation in ALLOWED:
        argv = ["git"] + ALLOWED[operation]
    else:
        return f"ERROR: unsupported git operation {operation!r}. Allowed: {sorted(ALLOWED) + ['commit']}"
    try:
        proc = subprocess.run(argv, cwd=str(repo_root), capture_output=True,
                              text=True, timeout=30, shell=False)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return f"ERROR: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return f"exit={proc.returncode}\n{out.strip()[:3000] or '(no output)'}"
