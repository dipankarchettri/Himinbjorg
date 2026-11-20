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
