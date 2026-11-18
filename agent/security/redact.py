"""Redact credentials from tool output before it enters a model context.

The path shield keeps credentials FILES out of reach; this catches the
credentials that live inside ordinary files (a key pasted into config.py)
or show up in shell and git output. Patterns are chosen for a low false
positive rate: each matches a provider's documented key shape, not "any
long random string", so normal code is left alone.

A redacted line cannot be patched by exact match, since the model never
saw the original text. That is the safe failure: the edit errors and
nothing is written.
"""
from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(rx)) for kind, rx in (
        ("private_key", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?"
                        r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)"),
        ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        ("github_token", r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
        ("anthropic_key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
        ("openrouter_key", r"\bsk-or-v1-[A-Za-z0-9]{20,}"),
        ("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
        ("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}"),
        ("groq_key", r"\bgsk_[A-Za-z0-9]{20,}"),
        ("tavily_key", r"\btvly-[A-Za-z0-9_\-]{20,}"),
        ("slack_token", r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
        ("stripe_key", r"\b[rs]k_live_[A-Za-z0-9]{20,}"),
    )
)


def redact(text: str) -> tuple[str, int]:
    """Return (text with credentials replaced, number replaced)."""
    if not text:
        return text, 0
    total = 0
    for kind, rx in _PATTERNS:
        text, n = rx.subn(f"[REDACTED:{kind}]", text)
        total += n
    return text, total
