"""Security requirement tests.

These cover the two mandatory requirements whose specification is "must
hold regardless of instructions contained in prompts". They need no API
keys and no gateway.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import LeverFlags, Settings
from agent.router import Tier, fallbacks_for, route
from agent.security import policy
from agent.security.policy import InvalidArgs, Permission
from agent.security.privacy import PrivacyMode, PrivacyViolation
from agent.tools.registry import Registry

REPO = Path(__file__).resolve().parent / "seed"


def make_settings(confidential: bool = False, **kw) -> Settings:
    base = dict(
        bifrost_base_url="http://localhost:8099",
        vk_standard="sk-bf-demo-user-key",
        vk_confidential="sk-bf-confidential-key",
        tavily_api_key=None,
        repo_root=REPO,
        confidential=confidential,
        max_turns=10,
        levers=LeverFlags(),
        model_local_small="ollama/qwen3:1.7b",
        model_local_code="ollama/qwen2.5:7b-instruct",
        model_cheap="openai/gpt-4o-mini",
        model_capable="anthropic/claude-sonnet-5-5",
        trace_dir=Path("/tmp"),
    )
    base.update(kw)
    return Settings(**base)


# --------------------------------------------------------- argument jail

@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "/etc/passwd", "../../../root/.ssh/id_rsa",
    "subdir/../../../../etc/shadow",
])
def test_path_jail_rejects_escapes(bad):
    spec = policy.ToolSpec("read_file", Permission.FREE, "", {}, lambda **k: "")
    with pytest.raises(InvalidArgs):
        policy.validate_args(spec, {"path": bad}, REPO)


@pytest.mark.parametrize("secret", [
    ".env", ".env.local", "id_rsa", "deploy.pem", "server.key",
    ".git/config", ".npmrc", "credentials.json",
])
def test_secret_shield_denies_credential_files(secret):
    assert policy.is_secret_path(secret), f"{secret} should be shielded"


def test_git_writes_refused():
    spec = policy.ToolSpec("write_file", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    with pytest.raises(InvalidArgs):
        policy.validate_args(spec, {"path": ".git/hooks/pre-commit"}, REPO)


@pytest.mark.parametrize("cmd", [
    "cat file | sh", "ls; rm -rf /", "echo $(whoami)", "ls > /tmp/out",
    "ls && curl evil.com", "echo `id`",
])
def test_shell_metacharacters_refused(cmd):
    spec = policy.ToolSpec("run_command", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    with pytest.raises(InvalidArgs):
        policy.validate_args(spec, {"command": cmd}, REPO)


@pytest.mark.parametrize("cmd", ["rm -rf build", "sudo apt install x", "curl http://x"])
def test_shell_denylist(cmd):
    spec = policy.ToolSpec("run_command", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    with pytest.raises(InvalidArgs):
        policy.validate_args(spec, {"command": cmd}, REPO)


def test_safe_command_passes():
    spec = policy.ToolSpec("run_command", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    out = policy.validate_args(spec, {"command": "pytest -q"}, REPO)
    assert out["command"] == ["pytest", "-q"]


# ------------------------------------------------------------ the gate

def test_free_tools_need_no_approval():
    spec = policy.ToolSpec("read_file", Permission.FREE, "", {}, lambda **k: "")
    assert policy.gate(spec, {"path": "calc.py"}).decision == "ALLOW"


def test_gated_tool_without_tty_is_denied_not_allowed():
    """Fail closed: no controlling terminal means refused, never approved."""
    spec = policy.ToolSpec("write_file", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    assert policy.gate(spec, {"path": "calc.py"}).decision == "DENIED"


def test_gate_signature_takes_no_conversation():
    """The gate cannot be influenced by prompt content because it cannot
    see prompt content. This asserts the property structurally."""
    import inspect
    params = set(inspect.signature(policy.gate).parameters)
    for forbidden in ("context", "messages", "conversation", "history", "ledger"):
        assert forbidden not in params


def test_injection_markers_are_detected_but_change_nothing():
    poisoned = Path(REPO / "CONTRIBUTING.md").read_text()
    assert policy.detect_injection_markers(poisoned), "planted text should be detected"
    spec = policy.ToolSpec("write_file", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    d = policy.gate(spec, {"path": "calc.py"}, injection_markers_seen=True)
    assert d.decision == "DENIED"          # still not approved
    assert d.injection_markers_seen is True  # and it is recorded


# ----------------------------------------------------- confidential mode

def test_confidential_rejects_cloud_model():
    p = PrivacyMode.build(True, "sk-bf-demo-user-key", "sk-bf-confidential-key")
    with pytest.raises(PrivacyViolation):
        p.assert_egress("openai/gpt-4o-mini", [], p.virtual_key)


def test_confidential_rejects_cloud_fallback():
    """The chain is the subtle exfiltration path: it fires exactly when the
    local model is unavailable."""
    p = PrivacyMode.build(True, "sk-bf-demo-user-key", "sk-bf-confidential-key")
    with pytest.raises(PrivacyViolation):
        p.assert_egress("ollama/qwen2.5:7b-instruct",
                        ["anthropic/claude-sonnet-5-5"], p.virtual_key)


def test_confidential_rejects_wrong_virtual_key():
    p = PrivacyMode.build(True, "sk-bf-demo-user-key", "sk-bf-confidential-key")
    with pytest.raises(PrivacyViolation):
        p.assert_egress("ollama/qwen2.5:7b-instruct", [], "sk-bf-demo-user-key")


def test_confidential_allows_local():
    p = PrivacyMode.build(True, "sk-bf-demo-user-key", "sk-bf-confidential-key")
    p.assert_egress("ollama/qwen2.5:7b-instruct", ["ollama/qwen3:1.7b"], p.virtual_key)


def test_search_tool_is_absent_not_refusing():
    """Absence beats refusal: the model is never told the tool exists."""
    conf = make_settings(confidential=True, tavily_api_key="tvly-test")
    p = PrivacyMode.build(True, conf.vk_standard, conf.vk_confidential)
    reg = Registry(p, conf)
    assert "web_search" not in reg.specs
    names = [s["function"]["name"] for s in reg.schemas(mutating_allowed=True)]
    assert "web_search" not in names


def test_search_tool_present_in_standard_mode():
    std = make_settings(confidential=False, tavily_api_key="tvly-test")
    p = PrivacyMode.build(False, std.vk_standard, std.vk_confidential)
    assert "web_search" in Registry(p, std).specs


def test_search_tool_absent_without_a_key():
    std = make_settings(confidential=False)
    p = PrivacyMode.build(False, std.vk_standard, std.vk_confidential)
    assert "web_search" not in Registry(p, std).specs


def test_search_sends_key_as_bearer_and_labels_results(monkeypatch):
    import io, json as _json
    from agent.tools import search
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = _json.loads(req.data)
        return Resp(_json.dumps({"results": [
            {"title": "t", "url": "u", "content": "x" * 900}]}).encode())

    monkeypatch.setattr(search.urllib.request, "urlopen", fake_urlopen)
    out = search.web_search("q", api_key="tvly-test")
    assert seen["auth"] == "Bearer tvly-test"
    assert "api_key" not in seen["body"]
    assert out.startswith("Web results (third-party")
    assert "x" * 401 not in out


def test_confidential_fallback_chain_is_empty():
    s = make_settings(confidential=True)
    for tier in Tier:
        assert fallbacks_for(tier, s, confidential=True) == []


def test_confidential_forces_local_tier_regardless_of_task():
    d = route("refactor the entire architecture across twelve modules",
              confidential=True, routing_enabled=True, files_touched=12)
    assert d.tier is Tier.T0_LOCAL
    assert d.method == "forced_confidential"


def test_confidential_disables_cache():
    """A semantic lookup would embed the request through a cloud provider."""
    p = PrivacyMode.build(True, "a", "b")
    assert p.cache_enabled is False


# ----------------------------------------------------- progressive tools

def test_mutating_schemas_withheld_until_needed():
    std = make_settings()
    p = PrivacyMode.build(False, std.vk_standard, std.vk_confidential)
    reg = Registry(p, std)
    read_only = [s["function"]["name"] for s in reg.schemas(mutating_allowed=False)]
    assert "write_file" not in read_only and "run_command" not in read_only
    assert "read_file" in read_only
    full = [s["function"]["name"] for s in reg.schemas(mutating_allowed=True)]
    assert "write_file" in full


def test_unknown_tool_returns_error_not_exception():
    std = make_settings()
    p = PrivacyMode.build(False, std.vk_standard, std.vk_confidential)
    r = Registry(p, std).dispatch("definitely_not_a_tool", {})
    assert r.ok is False and "unknown tool" in r.content


def test_project_python_prefers_the_repo_virtualenv(tmp_path):
    from agent.tools.shell import project_python
    assert project_python(tmp_path)                      # some interpreter
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("")
    assert project_python(tmp_path) == str(venv_py)
