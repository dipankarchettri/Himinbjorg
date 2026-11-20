"""Read tools. FREE: no approval required.

Default verbosity is budgeted (lever 4: retrieval instead of whole-file
dumps), but availability is not. An explicit line range always returns
exactly what was asked for, so the model can always get more.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from ..security.policy import is_secret_path

MAX_DEFAULT_LINES = 240
# A longer file is still sent whole when it is this small (about 3,000
# tokens). Measured 2026-11-19, shop_freeship on Gemma 31B, 10 runs: the
# 297-line, 10,486-character orders.py came back as an outline and 80
# lines; the model then read three ranges, 11,621 characters in all, more
# than the whole file, and passed 4/10 against 9/10 for the naive arm,
# which saw the whole file.
MAX_DEFAULT_CHARS = 12_000


def whole_file_chars(context_budget_tokens: int) -> int:
    """The size up to which a file is sent whole: a quarter of the context
    budget (about 4 characters a token), never under MAX_DEFAULT_CHARS.

    opencode sends up to 2,000 lines or 50 KB whole, for windows of about
    200k tokens. A local model here has 32k, so the limit follows the
    budget: 28,000 characters at the default budget of 28,000 tokens.
    """
    return max(MAX_DEFAULT_CHARS, context_budget_tokens)
MAX_GREP_MATCHES = 60
GREP_SCAN_CAP = 2_000       # lines read from grep before it is stopped
GREP_TIMEOUT = 20.0


def read_file(path: str, repo_root: Path, retrieval: bool = True,
              start: int | None = None, end: int | None = None,
              max_chars: int = MAX_DEFAULT_CHARS, **_) -> str:
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

    if retrieval and len(lines) > MAX_DEFAULT_LINES and len(text) > max_chars:
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


def _run_grep(pattern: str, target: str, mode: str) -> tuple[list[str], bool, bool, str]:
    """(lines, cut, timed_out, error). mode is "-E" (regex) or "-F" (plain text)."""
    cmd = ["grep", "-rnIs", mode, "--color=never"]
    for d in SKIP_DIRS:
        cmd += ["--exclude-dir", d]
    # -e and -- keep a pattern or path that starts with "-" from being
    # read as a grep option.
    cmd += ["-e", pattern, "--", target]
    # Streamed and cut at GREP_SCAN_CAP lines: on a large repository a
    # common pattern can match hundreds of thousands of lines, which were
    # all held in memory, or ran past the timeout and returned nothing.
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, errors="replace")
    except FileNotFoundError as exc:
        return [], False, False, f"grep failed: {exc}"
    fired = threading.Event()

    def kill() -> None:
        fired.set()
        proc.kill()
    timer = threading.Timer(GREP_TIMEOUT, kill)
    timer.start()
    out, cut = [], False
    try:
        for line in proc.stdout:
            out.append(line.rstrip("\n"))
            if len(out) >= GREP_SCAN_CAP:
                cut = True
                proc.kill()
                break
    finally:
        timer.cancel()
        proc.stdout.close()
        err = proc.stderr.read(2000)    # -s leaves only real errors, such as a bad pattern
        proc.stderr.close()
        proc.wait()
    error = err.strip() if proc.returncode == 2 and not cut and not fired.is_set() else ""
    return out, cut, fired.is_set(), error


def grep(pattern: str, repo_root: Path, path: str | None = None, **_) -> str:
    target = path or str(repo_root)
    # Extended syntax, which is what models write: in grep's default basic
    # syntax "a|b" matches a literal "|", and "\{" is an error.
    out, cut, timed_out, error = _run_grep(pattern, target, "-E")
    note = ""
    if error and not out:
        # Not a valid regex (say "def f(" ): search for the text itself.
        out, cut, timed_out, error2 = _run_grep(pattern, target, "-F")
        if error2:
            return f"ERROR: grep failed: {error2[:200]}"
        note = f"\n[not a valid regex ({error[:120]}); searched for the plain text]"
    if timed_out and not out:
        return f"ERROR: grep found nothing within {GREP_TIMEOUT:.0f} s; narrow the path"
    # The read tool refuses credentials files; grep must not show their
    # lines either, since its output goes to the model without approval.
    root = str(repo_root) + os.sep
    kept = [l for l in out
            if not is_secret_path(l.split(":", 1)[0].replace(root, "", 1))]
    hidden = len(out) - len(kept)
    out = kept
    if not out:
        hidden_note = f" ({hidden} in credentials files withheld)" if hidden else ""
        return f"no matches for {pattern!r}{hidden_note}{note}"
    trimmed = [l[:300] for l in out[:MAX_GREP_MATCHES]]
    rel = [l.replace(str(repo_root) + os.sep, "") for l in trimmed]
    more = f"\n[{len(out) - len(trimmed)} more matches not shown]" if len(out) > len(trimmed) else ""
    if cut or timed_out:
        more = (f"\n[more than {len(out)} matches; only the first {len(trimmed)} are shown. "
                "Narrow the pattern or the path]")
    if hidden:
        more += f"\n[{hidden} matches in credentials files withheld]"
    return "\n".join(rel) + more + note
