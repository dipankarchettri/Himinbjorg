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
        rows += ["", fg(SKY, resumed)]
    hint = dim("  /help for commands  ·  /undo reverts an edit  ·  Ctrl-C interrupts  ·  /exit")
    return "\n".join([""] + wordmark() + [""] + box(rows) + [hint, ""])


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
