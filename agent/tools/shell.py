"""Shell tool. GATED, and argv-only.

Never shell=True. The argument validator has already rejected pipes,
redirects, command substitution and the refusal list before this runs.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 90
MAX_OUTPUT = 6000


def project_python(repo_root: Path) -> str:
    """The interpreter the PROJECT uses, not the agent's.

    A project's tests need the project's dependencies, so its own virtualenv
    comes first. `python` alone does not exist on every host (it does not
    on the machine this was built on), hence the fallbacks.
    """
    for venv in (".venv", "venv", "env"):
        cand = Path(repo_root) / venv / "bin" / "python"
        if cand.exists():
            return str(cand)
    return shutil.which("python3") or shutil.which("python") or sys.executable


def run_command(command, repo_root: Path, **_) -> str:
    argv = command if isinstance(command, list) else [str(command)]
    if argv and argv[0] in ("python", "python3"):
        argv = [project_python(repo_root), *argv[1:]]
    elif argv and argv[0] in ("pytest", "py.test"):
        argv = [project_python(repo_root), "-m", "pytest", *argv[1:]]
    try:
        proc = subprocess.run(
            argv, cwd=str(repo_root), capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, shell=False,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {TIMEOUT_SECONDS}s: {' '.join(argv)}"
    except FileNotFoundError:
        return f"ERROR: command not found: {argv[0]}"
    except OSError as exc:
        return f"ERROR: {exc}"

    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    if len(out) > MAX_OUTPUT:
        # Keep both ends: test runners and compilers put the summary last.
        head, tail = MAX_OUTPUT // 3, MAX_OUTPUT - MAX_OUTPUT // 3
        out = (out[:head] + f"\n[... {len(out) - MAX_OUTPUT} chars omitted ...]\n"
               + out[-tail:])
    return f"exit={proc.returncode}\n{out.strip() or '(no output)'}"
