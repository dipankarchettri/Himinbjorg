"""Standalone approval-prompt check.

Exercises the gate directly, with no model and no gateway, so a broken
prompt can be told apart from a model that never attempted a write.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.security import policy                       # noqa: E402
from agent.security.policy import Permission, ToolSpec  # noqa: E402

spec = ToolSpec("apply_patch", Permission.GATED, "demo", {}, lambda **k: "", mutating=True)
print("stdin.isatty():", sys.stdin.isatty())
print("Asking for approval of a fake patch. Answer y or n.\n")
d = policy.gate(spec, {"path": "/tmp/example/calc.py",
                       "find": "return a / b",
                       "replace": "if b == 0:\n    return None\nreturn a / b"},
                injection_markers_seen=True)
print(f"\ndecision = {d.decision}")
print(f"reason   = {d.reason!r}")
print("\nPASS: the prompt worked." if d.decision in ("APPROVED", "DENIED") and not d.reason.startswith("/dev/tty")
      else "\nFAIL: the prompt could not be shown. Reason above.")
