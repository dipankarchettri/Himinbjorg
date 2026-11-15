"""Read tools. FREE: no approval required.

Default verbosity is budgeted (lever 4: retrieval instead of whole-file
dumps), but availability is not. An explicit line range always returns
exactly what was asked for, so the model can always get more.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

MAX_DEFAULT_LINES = 240
MAX_GREP_MATCHES = 60


def read_file(path: str, repo_root: Path, retrieval: bool = True,
              start: int | None = None, end: int | None = None, **_) -> str:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return f"ERROR: cannot read {path}: {exc}"
    lines = text.splitlines()
    rel = os.path.relpath(p, repo_root)

    if start is not None or end is not None:
        s = max(1, int(start or 1))
        e = min(len(lines), int(end or len(lines)))
        if retrieval and p.suffix == ".py" and s <= e:
            s, e = _enclosing_units(text, s, e)
        if s > len(lines) or s > e:
            # Measured: the model asked for lines 37-45 of 15- and 35-line
            # files, got an empty body, and then patched code it had guessed.
            if len(lines) <= MAX_DEFAULT_LINES:
                body = "\n".join(f"{i+1:>5}  {l}" for i, l in enumerate(lines))
                return (f"{rel} has only {len(lines)} lines (requested {start}-{end}); "
                        f"whole file:\n{body}")
            return f"ERROR: {rel} has only {len(lines)} lines; requested {start}-{end}."
        body = "\n".join(f"{i:>5}  {lines[i-1]}" for i in range(s, e + 1))
        return f"{rel} lines {s}-{e} of {len(lines)}:\n{body}"

    if retrieval and len(lines) > MAX_DEFAULT_LINES:
        outline = _outline(lines)
        head = "\n".join(f"{i+1:>5}  {l}" for i, l in enumerate(lines[:80]))
        return (f"{rel}: {len(lines)} lines, too long to send whole.\n"
                f"Symbol outline:\n{outline}\n\nFirst 80 lines:\n{head}\n"
                f"[Request a specific range with start/end to see more.]")

    body = "\n".join(f"{i+1:>5}  {l}" for i, l in enumerate(lines))
    return f"{rel} ({len(lines)} lines):\n{body}"


MAX_EXPANDED_LINES = 120


def _enclosing_units(text: str, s: int, e: int) -> tuple[int, int]:
    """Widen [s, e] to the functions or classes it overlaps.

    Measured: the model paged one 297-line file in 10-line windows, 15
    reads in one task, each read a full turn. A whole function per read is
    a coherent unit and usually the last read needed. Capped, and left
    alone when the code does not parse.
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return s, e
    units = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            units.append((first, node.end_lineno or node.lineno))
    ns, ne = s, e
    # The innermost unit containing the whole range: a method, not its class.
    containing = [u for u in units if u[0] <= s and u[1] >= e]
    if containing:
        first, last = min(containing, key=lambda u: u[1] - u[0])
        if last - first + 1 <= MAX_EXPANDED_LINES:
            ns, ne = first, last
    # Units cut by the edges of the range are completed, innermost first.
    for first, last in sorted(units, key=lambda u: u[1] - u[0]):
        cut = (first < ns <= last) or (first <= ne < last)
        if cut and not (first <= s and last >= e):
            cand_s, cand_e = min(ns, first), max(ne, last)
            if cand_e - cand_s + 1 <= MAX_EXPANDED_LINES:
                ns, ne = cand_s, cand_e
    return ns, ne


_DEF_RE = re.compile(
    r"^\s*(?:(?:async\s+)?def\s+\w+|class\s+\w+|"
    r"(?:export\s+)?(?:async\s+)?function\s+\w+|"
    r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(|"
    r"func\s+\w+|type\s+\w+|interface\s+\w+)")


def _outline(lines: list[str]) -> str:
    hits = [f"{i+1:>5}  {l.rstrip()}" for i, l in enumerate(lines) if _DEF_RE.match(l)]
    return "\n".join(hits[:120]) or "  (no top-level definitions detected)"


def symbols(path: str, repo_root: Path, **_) -> str:
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"ERROR: cannot read {path}: {exc}"
    return f"{os.path.relpath(p, repo_root)} symbols:\n{_outline(lines)}"


SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".mypy_cache", ".pytest_cache", "traces", "evidence"}


def list_dir(path: str, repo_root: Path, **_) -> str:
    p = Path(path)
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    rows = []
    for entry in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name)):
        if entry.name in SKIP_DIRS:
            continue
        if entry.is_dir():
            rows.append(f"  {entry.name}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            rows.append(f"  {entry.name}  ({size}b)")
    return f"{os.path.relpath(p, repo_root)}/\n" + "\n".join(rows[:200])


def grep(pattern: str, repo_root: Path, path: str | None = None, **_) -> str:
    target = path or str(repo_root)
    cmd = ["grep", "-rnI", "--color=never"]
    for d in SKIP_DIRS:
        cmd += ["--exclude-dir", d]
    cmd += [pattern, target]
    try:
        proc = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=20)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"ERROR: grep failed: {exc}"
    out = proc.stdout.splitlines()
    if not out:
        return f"no matches for {pattern!r}"
    trimmed = [l[:300] for l in out[:MAX_GREP_MATCHES]]
    rel = [l.replace(str(repo_root) + os.sep, "") for l in trimmed]
    more = f"\n[{len(out) - len(trimmed)} more matches not shown]" if len(out) > len(trimmed) else ""
    return "\n".join(rel) + more
