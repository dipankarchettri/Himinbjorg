"""Write tools. GATED: every call requires explicit human approval.

Two correctness guards sit here, both found by running the benchmark:

1. Literal escape sequences. Models sometimes double-escape tool
   arguments, so a replacement arrives as `if b == 0:\\n    return None`
   with a backslash-n pair instead of a newline. Written as-is, the whole
   fix lands on one line.
2. Syntax guard. A write that would leave a Python file unparseable is
   refused and the error returned to the model, rather than written and
   reported as success. Before this guard the agent would break a file
   and then announce that it had fixed it.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import textwrap
from functools import partial
from pathlib import Path


# Undo journal: one batch per tool call, each entry (path, contents before
# the write or None if the file did not exist, hash of the contents the
# agent wrote). Saved to disk, when a path is set, before the file itself
# is written, so a crash or restart never leaves an edit that cannot be
# undone.
JOURNAL: list[list[tuple[Path, str | None, str]]] = []
JOURNAL_PATH: Path | None = None
MAX_BATCHES = 20       # each holds whole files; older edits are in git


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def use_journal(path: Path | None) -> None:
    """Keep the journal in this file, loading what a previous session left."""
    global JOURNAL_PATH
    JOURNAL_PATH = path
    JOURNAL.clear()
    if path is None or not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for batch in data["batches"]:
            JOURNAL.append([(Path(e["path"]), e["before"], e["after"]) for e in batch])
    except (OSError, ValueError, KeyError, TypeError):
        JOURNAL.clear()        # unreadable: start empty rather than undo wrongly


def _save_journal() -> None:
    if JOURNAL_PATH is None:
        return
    del JOURNAL[:-MAX_BATCHES]
    data = {"version": 1, "batches": [
        [{"path": str(p), "before": before, "after": after} for p, before, after in batch]
        for batch in JOURNAL]}
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOURNAL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o600)           # it holds file contents
    os.replace(tmp, JOURNAL_PATH)


def _journal_write(p: Path, new_text: str, batch: list | None = None) -> None:
    before = p.read_text(encoding="utf-8") if p.exists() else None
    entry = (p, before, _digest(new_text))
    if batch is None:
        JOURNAL.append([entry])
    else:
        batch.append(entry)
        if not JOURNAL or JOURNAL[-1] is not batch:
            JOURNAL.append(batch)
    _save_journal()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new_text, encoding="utf-8")


def undo_last() -> str:
    """Revert the most recent edit batch. Returns a description.

    Refuses when a file has changed since the agent wrote it: reverting
    would throw away an edit someone made after the agent's."""
    if not JOURNAL:
        return "nothing to undo"
    batch = JOURNAL[-1]
    changed = []
    for p, _, after in batch:
        now = p.read_text(encoding="utf-8") if p.exists() else None
        if now is None or _digest(now) != after:
            changed.append(p.name)
    if changed:
        return (f"not undone: {', '.join(dict.fromkeys(changed))} changed since the agent's edit. "
                "Revert by hand, or with git.")
    JOURNAL.pop()
    _save_journal()
    names = []
    for p, before, _ in reversed(batch):
        if before is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(before, encoding="utf-8")
        names.append(p.name)
    return "reverted " + ", ".join(dict.fromkeys(reversed(names)))


def _unescape_if_literal(text: str) -> str:
    """Decode a double-escaped argument, only when it has no real newline.

    A string that already contains real newlines is trusted as-is, so code
    that legitimately contains a backslash-n inside a string literal is
    left alone whenever it spans more than one line.

    Measured 2026-11-15: models also double-escape quotes, sending
    `\\"WELCOME5\\"` and `\\"\\"\\"` for a docstring. Decoding only \\n
    left the backslashes in, so `find` never matched and `replace` failed to
    parse. All standard escapes are decoded now; callers try the text as
    sent first, so a real `\\"` in single-line code still matches.
    """
    if not text or "\n" in text:
        return text
    if "\\n" not in text and '\\"' not in text and "\\t" not in text and "\\'" not in text:
        return text
    marker = "\x00BACKSLASH\x00"
    out = text.replace("\\\\", marker)
    for seq, ch in (("\\r\\n", "\n"), ("\\n", "\n"), ("\\t", "\t"),
                    ('\\"', '"'), ("\\'", "'")):
        out = out.replace(seq, ch)
    return out.replace(marker, "\\")


def _indent_of(block: str) -> str:
    """Leading whitespace of the first non-blank line."""
    for ln in block.splitlines():
        if ln.strip():
            return ln[: len(ln) - len(ln.lstrip())]
    return ""


_LINE_NO = re.compile(r"^ *\d+  ")


def _strip_line_numbers(block: str) -> str:
    """Remove read_file's `   18  ` prefixes when every line carries one.

    Measured: the model copied numbered lines from a read_file result into
    `find`, which can never match.
    """
    lines = block.splitlines()
    if lines and all(_LINE_NO.match(ln) for ln in lines if ln.strip()):
        return "\n".join(_LINE_NO.sub("", ln, count=1) for ln in lines)
    return block


def _numbered_from(block: str) -> int | None:
    """The line number on the first line of a block _strip_line_numbers
    would strip, else None."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if lines and all(_LINE_NO.match(ln) for ln in lines):
        return int(lines[0].split()[0])
    return None


def _occurrence_at(text: str, find: str, line_no: int) -> int | None:
    """Offset of the occurrence of `find` whose first non-blank line is
    line `line_no` of the file, if exactly one is."""
    lead = len(find) - len(find.lstrip("\n"))
    hits, start = [], text.find(find)
    while start != -1:
        if text.count("\n", 0, start + lead) + 1 == line_no:
            hits.append(start)
        start = text.find(find, start + 1)
    return hits[0] if len(hits) == 1 else None


def _normalise_block(replace: str) -> list[str]:
    """Dedent a replacement whose first line was written without indentation.

    Measured: with find='return a / b' (which starts mid-line), the model
    sends 'if b == 0:\\n            return None\\n        return a / b'. It
    assumed the first line continues the existing indentation and indented
    the rest absolutely. Treat the first line as sitting at the shallowest
    indentation of the others, then dedent the whole block.
    """
    lines = replace.splitlines()
    rest = [ln for ln in lines[1:] if ln.strip()]
    depth = [len(ln) - len(ln.lstrip()) for ln in rest]
    # Only when the next line is deeper than the shallowest one. A header
    # such as `def f():` followed by its body at the shallowest level is
    # already consistent and must not be moved.
    if lines and rest and not lines[0][:1].isspace() and depth[0] > min(depth):
        lines[0] = " " * min(depth) + lines[0]
    return textwrap.dedent("\n".join(lines)).splitlines()


def _match_indent(find: str, replace: str) -> str:
    """Make the replacement start at the indentation of what it replaces.

    Measured on this project with qwen2.5:32b-instruct: the model sent
    find='    return a / b' with an UNindented replacement. Swapping an
    indented line for column-0 code empties the enclosing function and the
    file stops parsing. Relative indentation inside the replacement is
    preserved; only its base level is moved.
    """
    target, current = _indent_of(find), _indent_of(replace)
    # Already at the indentation of what it replaces: the model wrote the
    # block in the file's own terms, so nothing moves. Measured 2026-11-19
    # (Haiku, add_power): find '    return a / b', replace the same line
    # plus a new `def power` at column 0; shifting added a second indent to
    # the first line and the patch was refused as unparseable.
    if not replace.strip() or target == current:
        return replace
    body = _normalise_block(replace)
    first, rest = body[0], body[1:]
    # The first line lands after whatever precedes `find` on its line, so it
    # takes the target indent only through `find` itself.
    out = [(target + first) if first.strip() else ""]
    # A new top-level definition after the first line stays at column 0.
    # Measured, same run: find 'return a / b' (mid-line), replace it plus
    # 'def power(a, b):' at column 0; shifting nested power inside divide,
    # which parsed, and the model spent up to 11 more turns undoing it.
    top = next((i for i, ln in enumerate(rest) if _TOP_LEVEL.match(ln)), len(rest)) \
        if target else len(rest)
    out += [target + ln if ln.strip() else "" for ln in rest[:top]]
    out += rest[top:]
    return "\n".join(out)


_TOP_LEVEL = re.compile(r"(def |async def |class |@)")


def _syntax_error(path: Path, content: str) -> str | None:
    if path.suffix != ".py":
        return None
    try:
        ast.parse(content)
    except SyntaxError as exc:
        line = (exc.text or "").rstrip()
        return f"line {exc.lineno}: {exc.msg}" + (f"\n    {line}" if line else "")
    return None


def write_file(path: str, content: str, repo_root: Path, dry_run: bool = False, **_) -> str:
    p = Path(path)
    content = _unescape_if_literal(content)
    rel = os.path.relpath(p, repo_root)
    err = _syntax_error(p, content)
    if err:
        return (f"ERROR: refusing to write {rel}: the result would not parse "
                f"({err}). Nothing was written. Fix the code and try again.")
    if dry_run:
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        return PREVIEW_PREFIX + unified_diff(rel, old, content)
    existed = p.exists()
    _journal_write(p, content)
    verb = "updated" if existed else "created"
    return f"{verb} {rel} ({len(content.splitlines())} lines)"


# Typographic quotes and dashes a model may write for plain ones. Unicode
# spaces (no-break, thin, ideographic) are already \s. Taken from opencode
# (packages/core/src/tool/plugin/edit.ts, 2026-11-19), whose edit tool
# retries with the same mapping when there is no exact match.
_TYPOGRAPHIC = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2015": "-", "\u2212": "-"})


def _plain_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.translate(_TYPOGRAPHIC)).strip()


def _normalise(block: str) -> list[str]:
    """Strip per-line indentation, collapse internal runs of spaces, and
    read typographic quotes and dashes as plain ones."""
    return [_plain_line(ln) for ln in block.splitlines() if ln.strip()]


def _find_fuzzy(haystack_lines: list[str], needle: str) -> tuple[int, int, int] | None:
    """Locate `needle` ignoring indentation, internal whitespace and blanks.

    Small models reproduce code text almost right but rarely byte-exact,
    and indentation is the usual casualty. Measured on this project:
    exact-match patching failed 8 times out of 9 on a multi-file rename
    with a 7B model.

    Second pass: comment-only lines in the file are skipped too, when the
    needle has none. Measured: a model sent 16 patches in one run, each
    omitting the `# BUG:` comment inside the function it was editing.

    Uniqueness is still required in both passes, which keeps the edit safe.
    Returns (start, end, comment_lines_skipped).
    """
    want = _normalise(needle)
    if not want:
        return None
    norm = [_plain_line(ln) for ln in haystack_lines]
    passes = [False] + ([True] if not any(w.startswith("#") for w in want) else [])
    for skip_comments in passes:
        entries = [(i, n) for i, n in enumerate(norm)
                   if n and not (skip_comments and n.startswith("#"))]
        hits = []
        for k in range(len(entries) - len(want) + 1):
            if [n for _, n in entries[k:k + len(want)]] == want:
                start, end = entries[k][0], entries[k + len(want) - 1][0] + 1
                skipped = sum(1 for n in norm[start:end] if n.startswith("#")) if skip_comments else 0
                hits.append((start, end, skipped))
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None
    return None


PREVIEW_PREFIX = "PREVIEW\n"


def unified_diff(rel: str, old: str, new: str, context: int = 2, limit: int = 120) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                      f"a/{rel}", f"b/{rel}", n=context, lineterm=""))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... [{len(lines) - limit} more diff lines]"]
    return "\n".join(lines) or "(no change)"


def _already_applied(text: str, lines: list[str], replace: str) -> bool:
    """Is the replacement already in the file? Only for distinctive text.

    A short replacement such as `x` occurs almost anywhere; claiming it was
    "already applied" would tell the model a failed edit had succeeded.
    """
    body = replace.strip()
    if len(body) < 8:
        return False
    if "\n" in body:
        return _find_fuzzy([l.rstrip("\n") for l in lines], replace) is not None
    return body in text


def _commit(p: Path, rel: str, new_text: str, how: str, n: int = 1,
            dry_run: bool = False) -> str:
    err = _syntax_error(p, new_text)
    if err:
        return (f"ERROR: refusing to patch {rel}: the result would not parse "
                f"({err}). Nothing was written. Check indentation and that "
                f"newlines are real newlines, then try again.")
    if dry_run:
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        return PREVIEW_PREFIX + unified_diff(rel, old, new_text)
    _journal_write(p, new_text)
    return f"patched {rel} ({how})" if n > 1 else f"patched {rel} (1 replacement, {how})"


def apply_patch(path: str, find: str, replace: str, repo_root: Path,
                replace_all: bool = False, dry_run: bool = False, **_) -> str:
    """Replace a unique snippet, or every occurrence with replace_all.

    Diff-shaped writes rather than whole-file rewrites: output tokens are
    the expensive ones. A failed match is returned to the model with a
    hint, never silently widened into a full rewrite.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return f"ERROR: cannot read {path}: {exc}"
    rel = os.path.relpath(p, repo_root)
    _commit_ = partial(_commit, dry_run=dry_run)
    # Prefer the arguments exactly as sent; fall back to the decoded pair
    # only when the raw find does not occur (see _unescape_if_literal).
    anchor = _numbered_from(find)
    raw_find, raw_replace = _strip_line_numbers(find), _strip_line_numbers(replace)
    dec_find = _strip_line_numbers(_unescape_if_literal(find))
    dec_replace = _strip_line_numbers(_unescape_if_literal(replace))
    if raw_find.strip() and raw_find in text and raw_find != dec_find:
        find, replace = raw_find, raw_replace
    else:
        find, replace = dec_find, dec_replace
    if not find.strip():
        # Measured: models send an empty `find` to mean "append". Treat it
        # as exactly that rather than refusing a clear intent.
        if not replace.strip():
            return "ERROR: both `find` and `replace` are empty. Nothing was written."
        body = text.rstrip("\n") + "\n\n\n" + replace.strip("\n") + "\n"
        return _commit_(p, rel, body, "appended at end of file")

    count = text.count(find)
    if count > 1 and replace_all:
        return _commit_(p, rel, text.replace(find, replace), f"{count} replacements",
                       n=count)
    # Measured 2026-11-18: Gemma 31B copied `   10      return []` from a
    # read_file result five times running; the stripped text occurs twice
    # in get_factors.py, so every attempt was refused as ambiguous. The
    # line number it copied says which one it means.
    at = _occurrence_at(text, find, anchor) if count > 1 and anchor else None
    if count == 1 or at is not None:
        idx = text.index(find) if at is None else at
        line_start = text.rfind("\n", 0, idx) + 1
        lead = text[line_start:idx]
        if lead and not lead.strip() and "\n" in replace:
            # `find` starts mid-line after indentation. Widen it to the whole
            # line so a multi-line replacement can be indented consistently.
            find, idx = lead + find, line_start
        fixed = _match_indent(find, replace)
        how = "exact match" if fixed == replace else "exact match, indentation aligned"
        if at is not None:
            how += f", line {anchor} as numbered"
        return _commit_(p, rel, text[:idx] + fixed + text[idx + len(find):], how)
    if count > 1:
        return (f"ERROR: the text appears {count} times in {rel}. Nothing was written. "
                "Include more surrounding context to make it unique, or pass "
                "replace_all=true to replace every occurrence.")

    lines = text.splitlines(keepends=True)
    span = _find_fuzzy([l.rstrip("\n") for l in lines], find)
    if span:
        start, end, skipped = span
        indent = re.match(r"\s*", lines[start]).group(0)
        # The replacement usually carries its own indentation. Strip the
        # common prefix, then re-indent to the match site, or the two stack.
        body = [indent + ln if ln.strip() else "" for ln in _normalise_block(replace)]
        want = _normalise(find)
        if skipped and _normalise("\n".join(body))[:len(want)] == want:
            # Pure insertion after the anchor: keep the original lines,
            # including the comments the model left out, and add the rest.
            seen, cut = 0, len(body)
            for j, ln in enumerate(body):
                seen += bool(ln.strip())
                if seen == len(want):
                    cut = j + 1
                    break
            new = lines[:end] + [("\n".join(body[cut:]) + "\n")] + lines[end:]
            how = f"inserted after lines {start + 1}-{end}"
        else:
            new = lines[:start] + [("\n".join(body) + "\n")] + lines[end:]
            how = f"whitespace-insensitive match at lines {start + 1}-{end}"
        if skipped:
            how += f", {skipped} comment line(s) in the file not in `find`"
        return _commit_(p, rel, "".join(new), how)

    if _already_applied(text, lines, replace):
        # Measured: in a rename, the model retried edits it had already made
        # 15 times, each answered "not found". Say what is actually true.
        return (f"no change: {rel} no longer contains that text but already contains "
                f"the replacement, so this edit is already applied. Move on.")

    want = _normalise(find)
    plain = [l.rstrip("\n") for l in lines]
    norm = [re.sub(r"\s+", " ", l).strip() for l in plain]
    close = difflib.get_close_matches(want[0] if want else "", norm, n=1, cutoff=0.5)
    hint = ""
    if close:
        i = norm.index(close[0])
        region = "\n".join(plain[max(0, i - 1): i + max(len(want), 1) + 2])
        hint = ("\nThe closest region in the file, verbatim (copy from here, "
                f"without line numbers):\n{region}")
    elsewhere = _locate_elsewhere(find, p, Path(repo_root))
    return (f"ERROR: the text to replace was not found in {rel}. Nothing was written."
            f"{elsewhere}{hint}")


def _locate_elsewhere(find: str, target: Path, repo_root: Path, limit: int = 3) -> str:
    """Point at other files that contain the text the model meant to edit.

    Measured: the model sent exactly the right `find` text to the wrong
    file. Never edits the other file: the operator approved this one.
    """
    want = _normalise(find)
    if not want:
        return ""
    hits = []
    for p in _rename_candidates(repo_root, None):
        if p.resolve() == target.resolve() or p.suffix not in _CODE_SUFFIXES:
            continue
        try:
            plain = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        span = _find_fuzzy(plain, find)
        if span:
            hits.append(f"  {os.path.relpath(p, repo_root)} line {span[0] + 1}")
        if len(hits) >= limit:
            break
    if hits:
        return ("\nThat text DOES exist in another file. Patch that file instead:\n"
                + "\n".join(hits))
    defs = _definitions_of(find, repo_root)
    msg = ("\nThis text does not exist anywhere in the repository. Do not keep "
           "guessing: read the code where it really is.")
    if defs:
        msg += "\nNames from your text are defined here:\n" + "\n".join(defs)
    return msg


_KEYWORDS = {"self", "return", "import", "from", "class", "def", "None", "True", "False",
             "elif", "else", "while", "with", "pass", "raise", "lambda", "yield", "print"}


def _definitions_of(text: str, repo_root: Path, limit: int = 4) -> list[str]:
    """Where the identifiers mentioned in `text` are defined (def/class/assignment)."""
    names = [n for n in dict.fromkeys(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text))
             if n not in _KEYWORDS][:12]
    if not names:
        return []
    pats = {n: re.compile(rf"^\s*(?:def|class)\s+{re.escape(n)}\b|^\s*{re.escape(n)}\s*=")
            for n in names}
    out = []
    for p in _rename_candidates(repo_root, None):
        if p.suffix not in (".py", ".pyi"):
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines):
            for n, pat in pats.items():
                if pat.match(line):
                    out.append(f"  {n}: {os.path.relpath(p, repo_root)} line {i + 1}")
                    if len(out) >= limit:
                        return out
    return out


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CODE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
                  ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".scala",
                  ".sh", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".md", ".rst", ".txt"}
_RENAME_MAX_FILES = 2000
_RENAME_MAX_BYTES = 1_000_000


def _rename_candidates(repo_root: Path, path: str | None):
    from ..security.policy import is_secret_path
    from .read import SKIP_DIRS

    root = Path(repo_root).resolve()
    if path:
        p = Path(path)
        yield from ([p] if p.is_file() else sorted(p.rglob("*")))
        return
    seen = 0
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if SKIP_DIRS.intersection(rel.parts) or not p.is_file():
            continue
        if p.suffix not in _CODE_SUFFIXES or is_secret_path(str(rel)):
            continue
        seen += 1
        if seen > _RENAME_MAX_FILES:
            return
        yield p


def rename_symbol(old: str, new: str, repo_root: Path, path: str | None = None,
                  dry_run: bool = False, **_) -> str:
    """Whole-word rename of an identifier across the repository (or `path`).

    One approved call instead of one patch per occurrence. Measured before
    this tool existed: a 4-file rename took 12 turns and 30,110 tokens,
    with the model patching code it had guessed rather than read.
    All-or-nothing: if any changed Python file would stop parsing,
    nothing is written.
    """
    if not (_IDENT.match(old or "") and _IDENT.match(new or "")):
        return "ERROR: old and new must be plain identifiers (letters, digits, underscore)."
    if old == new:
        return "ERROR: old and new are the same. Nothing was written."
    root = Path(repo_root).resolve()
    pattern = re.compile(rf"\b{re.escape(old)}\b")
    changes: list[tuple[Path, str, str, int]] = []
    new_present = False
    for p in _rename_candidates(root, path):
        try:
            if p.stat().st_size > _RENAME_MAX_BYTES:
                continue
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated, n = pattern.subn(new, text)
        if n:
            changes.append((p, text, updated, n))
        elif re.search(rf"\b{re.escape(new)}\b", text):
            new_present = True
    if not changes:
        if new_present:
            return f"no change: no occurrence of {old} remains and {new} is present. Already renamed."
        return f"ERROR: {old} does not occur in any source file. Nothing was written."
    for p, _, updated, _ in changes:
        err = _syntax_error(p, updated)
        if err:
            rel = os.path.relpath(p, root)
            return (f"ERROR: refusing to rename: {rel} would not parse afterwards ({err}). "
                    "Nothing was written.")
    if dry_run:
        return PREVIEW_PREFIX + "\n".join(
            unified_diff(os.path.relpath(p, root), text, updated, context=0, limit=40)
            for p, text, updated, _ in changes)
    batch: list = []
    for p, _, updated, _ in changes:
        _journal_write(p, updated, batch)
    listing = ", ".join(f"{os.path.relpath(p, root)} ({n})" for p, _, _, n in changes)
    total = sum(n for *_, n in changes)
    # Show the result and what is left, so checking needs no extra turns.
    # Measured 2026-11-19 (Haiku, rename_symbol, 10 runs): after a bare
    # count the model grepped for the old name, found test_multiply (not a
    # whole-word match), patched it, then re-read every file to check.
    diff = "\n".join(unified_diff(os.path.relpath(p, root), text, updated, context=0, limit=30)
                     for p, text, updated, _ in changes)
    left = []
    for p in _rename_candidates(root, path):
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if old in line and len(left) < 10:
                    left.append(f"  {os.path.relpath(p, root)}:{i}: {line.strip()[:120]}")
        except (OSError, UnicodeDecodeError):
            continue
    rest = ("\nNo occurrence of the text " + repr(old) + " remains." if not left else
            f"\nThe text {old!r} still occurs inside other names or strings (not renamed, "
            "since it is not the whole identifier):\n" + "\n".join(left))
    return (f"renamed {old} -> {new}: {total} occurrences in {len(changes)} files: {listing}"
            f"\n{diff}{rest}")
