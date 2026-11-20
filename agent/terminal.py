"""How the CLI looks on a terminal.

Only presentation: every line still comes from the agent, the gate and the
report as plain text, and this module restyles it when stdout is an
interactive terminal. Piped output, the benchmark, the tests and the
editor server never pass through here, so what they parse is unchanged.
NO_COLOR (https://no-color.org) and TERM=dumb turn it off.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

# The deck's palette, as 256-colour codes: amber, sky, blue.
AMBER, SKY, BLUE = 179, 117, 32
GREEN, RED, CYAN, GREY = 71, 167, 73, 245


def enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    return (hasattr(stream, "isatty") and stream.isatty()
            and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb")


def fg(code: int, text: str) -> str:
    return f"\x1b[38;5;{code}m{text}\x1b[0m"


def bold(text: str) -> str:
    return f"\x1b[1m{text}\x1b[0m"


def dim(text: str) -> str:
    return f"\x1b[2m{text}\x1b[0m"


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def width(text: str) -> int:
    return len(_ANSI.sub("", text))


# ------------------------------------------------------------- welcome

_GLYPHS = {
    "H": ["█  █", "█  █", "████", "█  █", "█  █"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "B": ["███ ", "█  █", "███ ", "█  █", "███ "],
    "J": ["  ███", "    █", "    █", "█   █", " ███ "],
    "O": [" ██ ", "█  █", "█  █", "█  █", " ██ "],
    "R": ["███ ", "█  █", "███ ", "█ █ ", "█  █"],
    "G": [" ███", "█   ", "█ ██", "█  █", " ███"],
}
_BRIDGE = [AMBER, AMBER, 222, 153, SKY, SKY, 75, 68, BLUE, BLUE]


def wordmark(word: str = "HIMINBJORG") -> list[str]:
    """The name in block letters, coloured across like the Bifrost."""
    rows = []
    for r in range(5):
        rows.append("  " + " ".join(fg(_BRIDGE[i % len(_BRIDGE)], _GLYPHS[ch][r])
                                    for i, ch in enumerate(word)))
    return rows


def box(lines: list[str], title: str = "", color: int = AMBER) -> list[str]:
    inner = max([width(l) for l in lines] + [width(title) + 2, 56])
    inner = min(inner, max(40, shutil.get_terminal_size((80, 24)).columns - 6))
    top = fg(color, "╭─" + (f" {title} " if title else "") +
             "─" * (max(0, inner - width(title) - 2) if title else inner) + "─╮")
    out = [top]
    for line in lines:
        pad = max(0, inner - width(line))
        out.append(fg(color, "│ ") + line + " " * pad + fg(color, " │"))
    out.append(fg(color, "╰" + "─" * (inner + 2) + "╯"))
    return ["  " + l for l in out]


def welcome(version: str, repo: str, mode: str, sandbox: str, models: list[str],
            budget: str, resumed: str = "") -> str:
    home = os.path.expanduser("~")
    repo = "~" + repo[len(home):] if repo.startswith(home) else repo
    room = max(20, shutil.get_terminal_size((80, 24)).columns - 18)
    if len(repo) > room:
        repo = "…" + repo[-(room - 1):]        # keep the end: it names the repository
    rows = [
        bold("Himinbjorg") + dim(f"  v{version}") + dim("  ·  a coding agent on the fewest tokens"),
        "",
        f"{dim('repo   ')} {repo}",
        f"{dim('mode   ')} {mode}",
        f"{dim('models ')} {'  ·  '.join(models)}",
        f"{dim('sandbox')} {sandbox}",
        f"{dim('budget ')} {budget}",
    ]
    if resumed:
        # Wrapped to the box, so a long notice cannot push its border out.
        cols = shutil.get_terminal_size((80, 24)).columns
        room = min(max([width(r) for r in rows] + [56]), max(40, cols - 6))
        rows += [""] + [fg(SKY, l) for l in textwrap.wrap(resumed.strip(), room)]
    hint = dim("  /help for commands  ·  /undo reverts an edit  ·  Ctrl-C interrupts  ·  /exit")
    return "\n".join([""] + wordmark() + [""] + box(rows) + [hint, ""])


# ---------------------------------------------------------- markdown

class Markdown:
    """The model's Markdown, styled for a terminal as it streams.

    Fed one whitespace-free token at a time (and each newline), so a code
    span or bold run that crosses words or stream chunks still works:
    `code` is coloured, **bold** is bold, *emphasis* is italic, a heading
    is bold, a list marker becomes a bullet, --- becomes a rule, and a
    fenced block is coloured without its fences. Tables are drawn by
    table(). Anything else is left as written.
    """

    CODE = f"\x1b[38;5;{SKY}m"
    FG_OFF, BOLD_ON, BOLD_OFF = "\x1b[39m", "\x1b[1m", "\x1b[22m"
    ITALIC_ON, ITALIC_OFF = "\x1b[3m", "\x1b[23m"

    def __init__(self) -> None:
        self.code = self.strong = self.em = self.heading = self.fence = self.skip = False
        self.bol = True                 # nothing but spaces yet on this line
        self.eat_space = False          # the space after a dropped "#"

    def token(self, tok: str) -> str:
        if self.bol:
            self.bol = False
            if tok.startswith("```"):
                self.fence = not self.fence
                self.skip = True        # the fence line itself, with its language
                return ""
            if not self.fence:
                if re.fullmatch(r"#{1,6}", tok):
                    self.heading = self.eat_space = True
                    return self.BOLD_ON
                if tok in ("-", "*", "+"):
                    return fg(GREY, "•")
                if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", tok):     # a horizontal rule
                    return dim("─" * 24)
        if self.skip:
            return ""
        if self.fence:
            return self.CODE + tok + self.FG_OFF
        out, i = [], 0
        while i < len(tok):
            if tok[i] == "`":
                self.code = not self.code
                out.append(self.CODE if self.code else self.FG_OFF)
                i += 1
            elif tok.startswith("**", i) and not self.code and len(tok) > 2:
                self.strong = not self.strong
                out.append(self.BOLD_ON if self.strong or self.heading else self.BOLD_OFF)
                i += 2
            elif (tok[i] == "*" and not self.code
                  and ((not self.em and i == 0 and len(tok) > 1)
                       or (self.em and i > 0))):
                # *emphasis*: opened at a word's start, closed inside or at its
                # end, so a lone "*" or a*b is left alone.
                self.em = not self.em
                out.append(self.ITALIC_ON if self.em else self.ITALIC_OFF)
                i += 1
            else:
                out.append(tok[i])
                i += 1
        return "".join(out)

    def space(self, text: str) -> str:
        if self.eat_space:
            self.eat_space = False
            return ""
        return text

    def newline(self) -> str:
        out = self.BOLD_OFF if self.heading and not self.strong else ""
        self.heading = self.skip = False
        self.bol = True
        return out

    def close(self) -> str:
        """Reset whatever an unfinished answer left open."""
        styled = self.code or self.strong or self.em or self.heading or self.fence
        self.__init__()
        return "\x1b[0m" if styled else ""


_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CELL_RUN = re.compile(r"(`[^`]*`|\*\*[^*]+\*\*)")


def _cell_words(cell: str) -> list[list[tuple[str, str]]]:
    """The cell's words, each a list of (text, style) segments, style
    "code", "bold" or "": `None`, is one word of two segments."""
    words: list[list[tuple[str, str]]] = []
    glue = False                        # the next segment continues the last word
    for run in _CELL_RUN.split(cell):
        if not run:
            continue
        if run.startswith("`") and run.endswith("`") and len(run) > 1:
            style, run = "code", run[1:-1]
        elif run.startswith("**") and run.endswith("**") and len(run) > 4:
            style, run = "bold", run[2:-2]
        else:
            style = ""
        for piece in re.split(r"(\s+)", run):
            if not piece:
                continue
            if piece.isspace():
                glue = False
                continue
            if glue and words:
                words[-1].append((piece, style))
            else:
                words.append([(piece, style)])
            glue = True
    return words


def _width(word: list[tuple[str, str]]) -> int:
    return sum(len(t) for t, _ in word)


def _wrap_cell(cell: str, width: int, header: bool) -> list[str]:
    lines, cur, cur_w = [], [], 0
    for word in _cell_words(cell):
        w = _width(word)
        if w > width:                               # wider than the column: cut it
            text, style = "".join(t for t, _ in word), word[0][1]
            if cur:
                lines.append((cur, cur_w))
                cur, cur_w = [], 0
            while len(text) > width:
                lines.append(([[(text[:width], style)]], width))
                text = text[width:]
            word, w = [(text, style)], len(text)
        add = w + (1 if cur else 0)
        if cur and cur_w + add > width:
            lines.append((cur, cur_w))
            cur, cur_w, add = [], 0, w
        cur.append(word)
        cur_w += add
    if cur or not lines:
        lines.append((cur, cur_w))

    def seg(t: str, s: str) -> str:
        return fg(SKY, t) if s == "code" else bold(t) if s == "bold" or header else t
    return [" ".join("".join(seg(t, s) for t, s in word) for word in words) + " " * (width - w)
            for words, w in lines]


def table(rows: list[str], columns: int) -> str:
    """A Markdown table drawn with aligned columns, wrapped to the terminal."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows
             if r.strip() and not _TABLE_RULE.match(r)]
    if not cells:
        return ""
    n = max(len(r) for r in cells)
    cells = [r + [""] * (n - len(r)) for r in cells]
    want = [max(sum(_width(w) for w in _cell_words(r[i])) + max(0, len(_cell_words(r[i])) - 1)
                for r in cells) for i in range(n)]
    room = max(n * 4, columns - 3 * (n - 1) - 1)
    widths = [max(1, w) for w in want]
    while sum(widths) > room:                       # narrow the widest column first
        i = widths.index(max(widths))
        widths[i] -= 1
    sep = dim(" │ ")
    out = []
    for k, row in enumerate(cells):
        wrapped = [_wrap_cell(c, widths[i], header=k == 0) for i, c in enumerate(row)]
        for j in range(max(len(w) for w in wrapped)):
            out.append(sep.join(w[j] if j < len(w) else " " * widths[i]
                                for i, w in enumerate(wrapped)).rstrip())
        if k == 0:
            out.append(dim("─┼─".join("─" * w for w in widths)))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------- live lines

_ACTIVITY = re.compile(r"^  \. (\S+) ?(.*?)  \[(ok|failed)\] ?(.*)$")
_NOTICE = re.compile(r"^\s*(Provider changed|Provider restored|Rate limited|Still rate limited|"
                     r"Budget exceeded|Low budget|Lost the gateway|Gateway error|All providers|"
                     r"Configuration error|Context near|The provider)")


def style_line(text: str) -> str:
    """One line the agent printed: a tool call, a notice, or as it was."""
    m = _ACTIVITY.match(text)
    if m:
        name, target, mark, rest = m.groups()
        dot = fg(GREEN, "●") if mark == "ok" else fg(RED, "●")
        line = f"  {dot} {bold(name)} {target}".rstrip()
        if rest:
            line += "\n    " + dim("⎿ " + rest) if mark == "ok" else "\n    " + fg(RED, "⎿ " + rest)
        return line
    if _NOTICE.match(text):
        return fg(AMBER, text)
    return text


def style_approval(prompt: str) -> str:
    """The gate's prompt, framed, with the diff in colour. Same words."""
    out = []
    for line in prompt.split("\n"):
        body = line[2:] if line.startswith("  ") else line
        if set(body.strip()) == {"-"} and len(body.strip()) > 20:
            out.append("  " + fg(AMBER, "─" * min(64, shutil.get_terminal_size((80, 24)).columns - 4)))
        elif body.startswith("APPROVAL REQUIRED:"):
            out.append("  " + fg(AMBER, bold("● " + body)))
        elif body.startswith("Approve?"):
            out.append("  " + bold(body))
        elif body.startswith(("+++", "---")):
            out.append("  " + bold(body))
        elif body.startswith("@@"):
            out.append("  " + fg(CYAN, body))
        elif body.startswith("+"):
            out.append("  " + fg(GREEN, body))
        elif body.startswith("-"):
            out.append("  " + fg(RED, body))
        elif body.startswith("NOTE:"):
            out.append("  " + fg(RED, bold(body)))
        else:
            out.append(line)
    return "\n".join(out)


def summary(s: dict, cost: float | None, label: str = "") -> str:
    """The closing line of a task, in place of the four-line report."""
    parts = ([label] if label else []) + [f"{s['turns']} turns", f"{s['total_tokens']:,} tokens"]
    if s.get("cached_tokens"):
        parts.append(f"{s['cached_tokens']:,} from the gateway cache")
    if s.get("prompt_cache_read"):
        parts.append(f"{s['prompt_cache_read']:,} provider cache reads")
    if s.get("approvals_requested"):
        parts.append(f"{s['approvals_granted']}/{s['approvals_requested']} approved")
    if cost is not None:
        parts.append(f"${cost:.4f}")
    return "  " + dim("✓ " + "  ·  ".join(parts))


def prompt() -> str:
    """The input prompt; the escapes are wrapped so readline counts its width."""
    return "\n\001\x1b[38;5;179m\x1b[1m\002›\001\x1b[0m\002 "


# ----------------------------------------------------------- input box

INPUT_HINT = "  enter send · alt+enter or \\ newline · ↑ history · ctrl-c clear · ctrl-d exit"


def input_box(history=None, input=None, output=None) -> str:
    """Read one task in a bordered box, as Claude Code does.

    Needs prompt_toolkit; raises ImportError without it, and the caller
    falls back to input(). Ctrl-C on an empty box raises KeyboardInterrupt
    and Ctrl-D raises EOFError, the same as input(), so the REPL treats both
    the same way. The box is erased on submit and the caller echoes the task.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea

    area = TextArea(multiline=True, wrap_lines=True, history=history,
                    height=Dimension(min=1, max=12), dont_extend_height=True)
    keys = KeyBindings()

    @keys.add("enter", eager=True)
    def _send(event):
        buf = area.buffer
        if buf.document.text_before_cursor.endswith("\\"):
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
            return
        buf.append_to_history()
        event.app.exit(result=buf.text)

    @keys.add("escape", "enter", eager=True)
    def _newline(event):
        area.buffer.insert_text("\n")

    @keys.add("c-c", eager=True)
    def _clear(event):
        if area.text:
            area.buffer.reset()
        else:
            event.app.exit(exception=KeyboardInterrupt())

    @keys.add("c-d", eager=True)
    def _eof(event):
        if not area.text:
            event.app.exit(exception=EOFError())
        else:
            area.buffer.delete()

    area.control.key_bindings = keys

    def edge(left: str, right: str):
        return VSplit([Window(width=1, height=1, char=left, style="class:box"),
                       Window(height=1, char="─", style="class:box"),
                       Window(width=1, height=1, char=right, style="class:box")])

    body = VSplit([Window(width=1, char="│", style="class:box"),
                   Window(FormattedTextControl([("class:prompt", " › ")]), width=3,
                          dont_extend_width=True),
                   area,
                   Window(width=1, char="│", style="class:box")])
    box_ = HSplit([edge("╭", "╮"), body, edge("╰", "╯")])
    root = HSplit([VSplit([Window(width=2), box_, Window(width=1)]),
                   Window(FormattedTextControl([("class:hint", INPUT_HINT)]), height=1)])
    style = Style.from_dict({"box": "fg:#e0a43a", "prompt": "fg:#e0a43a bold",
                             "hint": "fg:#808080"})
    app = Application(layout=Layout(root, focused_element=area), style=style,
                      full_screen=False, erase_when_done=True, mouse_support=False,
                      input=input, output=output)
    return app.run()


def echo_task(text: str) -> str:
    """The submitted task as it stays in the scrollback."""
    lines = text.split("\n")
    return "\n".join([fg(AMBER, bold("› ")) + lines[0]] + ["  " + l for l in lines[1:]])
