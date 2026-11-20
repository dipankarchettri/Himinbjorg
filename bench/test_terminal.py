"""How the CLI looks: styling only, never on a pipe, same words."""
import re

from agent import terminal

ANSI = re.compile(r"\x1b\[[0-9;]*m|\001|\002")


def plain(text: str) -> str:
    return ANSI.sub("", text)


def test_off_on_a_pipe_and_with_no_color(monkeypatch, tmp_path):
    with open(tmp_path / "out", "w") as f:
        assert not terminal.enabled(f)

    class Tty:
        def isatty(self):
            return True
    assert terminal.enabled(Tty())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not terminal.enabled(Tty())


def test_the_approval_prompt_keeps_every_word():
    prompt = "\n".join([
        "", "  " + "-" * 63, "  APPROVAL REQUIRED: apply_patch", "  " + "-" * 63,
        "  path: /r/calc.py", "  --- a/calc.py", "  +++ b/calc.py", "  @@ -1 +1 @@",
        "  -    return a / b", "  +    return None", "  " + "-" * 63,
        "  Approve? [y/N/a = all edits this session] "])
    styled = plain(terminal.style_approval(prompt))
    for word in ("APPROVAL REQUIRED: apply_patch", "path: /r/calc.py", "-    return a / b",
                 "+    return None", "Approve? [y/N/a = all edits this session]"):
        assert word in styled


def test_tool_lines_and_other_lines():
    assert plain(terminal.style_line("  . read_file calc.py  [ok]")) == "  ● read_file calc.py"
    failed = plain(terminal.style_line("  . run_command pytest  [failed] exit 1"))
    assert failed.startswith("  ● run_command pytest") and "exit 1" in failed
    assert terminal.style_line("just text") == "just text"


def test_welcome_and_summary():
    w = plain(terminal.welcome("0.5.0", "/r", "standard", "Landlock ABI 4", ["a", "b"], "$0 of $5"))
    assert "Himinbjorg" in w and "v0.5.0" in w and "a  ·  b" in w
    rows = [l for l in w.splitlines() if l.strip().startswith("│")]
    assert len({len(l) for l in rows}) == 1        # the box's right edge lines up
    s = {"turns": 6, "total_tokens": 9551, "cached_tokens": 0, "prompt_cache_read": 0,
         "approvals_requested": 3, "approvals_granted": 3}
    assert plain(terminal.summary(s, 0.002)) == "  ✓ 6 turns  ·  9,551 tokens  ·  3/3 approved  ·  $0.0020"


def test_the_box_edges_line_up():
    lines = [plain(l) for l in terminal.box(["one", "a longer row"])]
    assert len({len(l) for l in lines}) == 1


def test_a_long_repository_path_still_fits_the_box(monkeypatch):
    import os
    monkeypatch.setattr(terminal.shutil, "get_terminal_size", lambda *a: os.terminal_size((80, 24)))
    w = plain(terminal.welcome("0.5.0", "/x" * 80 + "/repo", "standard", "on", ["m"], "$0"))
    rows = [l for l in w.splitlines() if l.strip() and l.strip()[0] in "│╭╰"]
    assert len({len(l) for l in rows}) == 1 and max(len(l) for l in rows) <= 80
    assert "/repo" in w


def test_a_long_resume_notice_wraps_inside_the_box(monkeypatch):
    import os
    monkeypatch.setattr(terminal.shutil, "get_terminal_size", lambda *a: os.terminal_size((96, 24)))
    notice = ("  A session from 0 min ago is saved for this repository; `himin --resume` "
              "continues it. Starting a new one replaces it.\n")
    w = plain(terminal.welcome("0.6.0", "/r", "standard", "on", ["a", "b"], "$0", notice))
    rows = [l for l in w.splitlines() if l.strip() and l.strip()[0] in "│╭╰"]
    assert len({len(l) for l in rows}) == 1 and max(len(l) for l in rows) <= 96
    assert "--resume" in w and "replaces" in w


def _box(keys: str):
    import pytest
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    with create_pipe_input() as inp:
        inp.send_text(keys)
        return terminal.input_box(input=inp, output=DummyOutput())


def test_the_input_box_sends_on_enter():
    assert _box("fix divide()\r") == "fix divide()"


def test_the_input_box_takes_a_new_line_two_ways():
    assert _box("one\\\rtwo\x1b\rthree\r") == "one\ntwo\nthree"


def test_the_input_box_ctrl_c_clears_then_interrupts():
    import pytest
    assert _box("draft\x03kept\r") == "kept"
    with pytest.raises(KeyboardInterrupt):
        _box("\x03")


def test_the_input_box_ctrl_d_on_empty_exits():
    import pytest
    with pytest.raises(EOFError):
        _box("\x04")


def test_a_streamed_answer_wraps_between_words(monkeypatch):
    import io
    import os
    from agent import main as main_mod
    monkeypatch.setattr(main_mod.shutil, "get_terminal_size", lambda *a: os.terminal_size((30, 24)))
    out = io.StringIO()
    live = main_mod.LiveText(out=out, styled=True)
    text = "The divide function does not handle division by zero and raises ZeroDivisionError.\nDone"
    for i in range(0, len(text), 7):                # arrives in pieces that split words
        live.text(text[i:i + 7])
    live.finish()
    lines = plain(out.getvalue()).split("\n")
    assert all(len(l) <= 30 for l in lines)
    assert "ZeroDivisionError." in plain(out.getvalue()).split()
    assert lines[-1] == "Done"
    assert " ".join(plain(out.getvalue()).replace("●", "").split()) == " ".join(text.split())


def test_streamed_text_ends_with_its_last_word_before_an_approval():
    import io
    from agent import main as main_mod
    out = io.StringIO()
    live = main_mod.LiveText(out=out, styled=True)
    live.text("I'll reproduce it before editing.")
    live.end_text()                             # what the approval prompt calls first
    out.write("APPROVAL")
    assert plain(out.getvalue()).endswith("before editing.\nAPPROVAL")


_MD = """## Summary

The cause is in `src/click/utils.py`: **`out = out or ""`** turns `b''` into a str.

- `echo()` now checks the type first

```python
out += b"\\n"
```

Done."""


def _streamed(text: str, styled: bool, step: int = 5) -> str:
    import io
    from agent import main as main_mod
    out = io.StringIO()
    live = main_mod.LiveText(out=out, styled=True) if styled else main_mod.LiveText(out=out)
    for i in range(0, len(text), step):                 # chunks that split the markers
        live.text(text[i:i + step])
    live.end_text()
    return out.getvalue()


def test_a_streamed_answer_is_drawn_as_markdown_not_shown_raw():
    raw = _streamed(_MD, styled=True)
    text = plain(raw)
    assert "`" not in text and "**" not in text and "#" not in text
    assert text.splitlines()[1] == "● Summary"
    assert "• echo() now checks the type first" in text
    assert 'out += b"\\n"' in text and "python" not in text      # fences and language dropped
    assert terminal.Markdown.CODE + "src/click/utils.py" in raw
    assert raw.count("\x1b[1m") >= 3                              # the dot, the heading, the bold run
    assert not raw.rstrip("\n").endswith(terminal.Markdown.CODE)


def test_piped_output_keeps_the_markdown():
    assert _streamed(_MD, styled=False).strip() == _MD
