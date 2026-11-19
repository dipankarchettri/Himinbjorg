"""Shell tool. GATED, and argv-only.

Never shell=True. The argument validator has already rejected pipes,
redirects, command substitution and the refusal list before this runs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..security import sandbox as sandbox_mod

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


# The install directory: its .env holds the provider keys.
_INSTALL_DIR = Path(__file__).resolve().parents[2]


def sandbox_status(mode: str, network: bool) -> str:
    """One line for the session banner: how approved commands will run."""
    if mode == "off":
        return "off (AGENT_SANDBOX=off): commands run with your full rights"
    abi = sandbox_mod.abi_version()
    if abi < 1:
        if mode == "required":
            return "required but unavailable: commands will be refused"
        return "unavailable on this system: commands run with your full rights"
    net = "network on" if network or abi < 4 else "no network"
    return f"Landlock ABI {abi}: writes only in the repository and temp, {net}"


def sandboxed_argv(argv: list[str], repo_root: Path, network: bool) -> list[str]:
    """Wrap argv in the Landlock launcher.

    The launcher is run as a file under `python -I`: no PYTHONPATH, no user
    site, and not the current directory, so a repository cannot shadow
    the modules it imports. It imports only the standard library.
    """
    write = [Path(repo_root), Path(tempfile.gettempdir())]
    cache = Path.home() / ".cache"
    if cache.is_dir():
        write.append(cache)
    wrapped = [sys.executable, "-I", str(Path(sandbox_mod.__file__).resolve())]
    for w in write:
        wrapped += ["--write", str(w)]
    for d in sandbox_mod.default_deny(_INSTALL_DIR):
        wrapped += ["--deny", str(d)]
    if network:
        wrapped.append("--net")
    return wrapped + ["--", *argv]


def run_command(command, repo_root: Path, sandbox: str = "off", network: bool = False,
                **_) -> str:
    argv = command if isinstance(command, list) else [str(command)]
    if argv and argv[0] in ("python", "python3"):
        argv = [project_python(repo_root), *argv[1:]]
    elif argv and argv[0] in ("pytest", "py.test"):
        argv = [project_python(repo_root), "-m", "pytest", *argv[1:]]
    shown = " ".join(argv)        # what the model asked for, not the launcher
    if sandbox != "off":
        if sandbox_mod.abi_version() >= 1:
            argv = sandboxed_argv(argv, repo_root, network)
        elif sandbox == "required":
            return ("ERROR: AGENT_SANDBOX=required, but Landlock is not available on this "
                    "system, so the command was not run.")
    try:
        proc = subprocess.run(
            argv, cwd=str(repo_root), capture_output=True, stdin=subprocess.DEVNULL, text=True,
            timeout=TIMEOUT_SECONDS, shell=False,
            # No .pyc files: CPython trusts a cached .pyc when the source's
            # mtime (whole seconds) and size match, so a same-length edit made
            # within a second of the last run would test stale code.
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {TIMEOUT_SECONDS}s: {shown}"
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
