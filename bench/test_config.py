"""Settings loading: the .env parser, without a gateway or a model."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import load_dotenv     # noqa: E402


def write(tmp_path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_inline_comments_are_not_values(tmp_path):
    env = load_dotenv(write(tmp_path, "\n".join([
        "GEMINI_API_KEY=            # Google AI Studio key",
        "OPENROUTER_API_KEY=# one key for Claude, GPT, DeepSeek, Qwen...",
        "GROQ_API_KEY=   # free tier: fast, but about 8K tokens/minute",
        "PRESENT=value   # trailing comment",
    ])))
    assert env == {"GEMINI_API_KEY": "", "OPENROUTER_API_KEY": "",
                   "GROQ_API_KEY": "", "PRESENT": "value"}


def test_quoted_values_keep_hashes_and_spaces(tmp_path):
    env = load_dotenv(write(tmp_path, "\n".join([
        'DOUBLE="a # b"',
        "SINGLE='c # d'",
        "NOT_A_COMMENT=pa#ss",
        "EQUALS=a=b",
    ])))
    assert env["DOUBLE"] == "a # b"
    assert env["SINGLE"] == "c # d"
    assert env["NOT_A_COMMENT"] == "pa#ss"
    assert env["EQUALS"] == "a=b"


def test_quotes_are_only_special_at_the_start_of_a_value(tmp_path):
    env = load_dotenv(write(tmp_path, "\n".join([
        "APOSTROPHE=don't # trailing note",
        'QUOTED_THEN_COMMENT="x # y" # note',
        'UNTERMINATED="a # b',
    ])))
    assert env["APOSTROPHE"] == "don't"
    assert env["QUOTED_THEN_COMMENT"] == "x # y"
    assert env["UNTERMINATED"] == "a # b"


def test_comments_blank_lines_and_missing_files_are_ignored(tmp_path):
    assert load_dotenv(tmp_path / "nope") == {}
    env = load_dotenv(write(tmp_path, "# a comment\n\nKEY=value\nNO_EQUALS\n"))
    assert env == {"KEY": "value"}


def test_the_shipped_example_has_no_comment_as_a_value():
    env = load_dotenv(ROOT / ".env.example")
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                "OPENROUTER_API_KEY", "GROQ_API_KEY", "TAVILY_API_KEY"):
        assert env[key] == "", f"{key} was read as {env[key]!r}"
