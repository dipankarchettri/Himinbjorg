"""Append-only JSONL session trace.

Three jobs, one file: session durability (resume after an outage), the
evidence harness's data source, and the security audit log. Written
unbuffered because a trace that loses its last line on a crash is useless
for the first and third of those.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_trace_path: Path | None = None


def init(path: Path) -> Path:
    global _trace_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _trace_path = path
    return path


def path() -> Path | None:
    return _trace_path


def emit(event_type: str, payload: dict[str, Any]) -> None:
    """One JSON object per line. Never buffered across a crash."""
    if _trace_path is None:
        return
    record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event_type}
    record.update(payload)
    line = json.dumps(record, default=str, ensure_ascii=False)
    with open(_trace_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
