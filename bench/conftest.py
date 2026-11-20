"""Shared test setup: nothing a test does reaches the user's state directory."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _private_state(tmp_path_factory, monkeypatch):
    from agent.tools import write as write_mod
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))
    # Never read or write the user's own settings or gateway from a test.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("data")))
    yield
    write_mod.use_journal(None)
