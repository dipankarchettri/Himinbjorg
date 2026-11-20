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


def test_absolute_path_inside_the_repo_is_allowed():
    """Models often pass absolute paths; one inside the root is legitimate."""
    spec = policy.ToolSpec("write_file", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    out = policy.validate_args(spec, {"path": str(REPO / "calc.py")}, REPO)
    assert out["_rel"] == "calc.py"
    assert Path(out["path"]) == (REPO / "calc.py").resolve()


@pytest.mark.parametrize("secret", [
    ".env", ".env.local", "id_rsa", "deploy.pem", "server.key",
    ".git/config", ".npmrc", "credentials.json",
    "id_dsa", "release.jks", "app.keystore", "terraform.tfstate",
    "terraform.tfstate.backup", ".gnupg/secring.gpg", ".kube/config",
    "kubeconfig", ".pgpass", ".htpasswd",
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


def test_grep_withholds_credentials_files(tmp_path):
    from agent.tools import read as read_mod
    (tmp_path / "app.py").write_text("TOKEN_NAME = 'x'\n")
    (tmp_path / ".env").write_text("TOKEN_NAME=secret-value\n")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("TOKEN_NAME private\n")
    out = read_mod.grep("TOKEN_NAME", tmp_path)
    assert "app.py" in out
    assert "secret-value" not in out and "private" not in out
    assert "2 matches in credentials files withheld" in out


def test_grep_pattern_is_never_an_option(tmp_path):
    from agent.tools import read as read_mod
    (tmp_path / "a.txt").write_text("-V marks the version flag\n")
    out = read_mod.grep("-V", tmp_path)
    assert "GNU grep" not in out
    assert "a.txt:1:" in out


def test_grep_takes_the_regex_syntax_models_write(tmp_path):
    # In grep's basic syntax "a|b" is a literal "|" and "\\{" is an error that
    # was reported as a timeout. About a fifth of recorded grep calls used "|".
    from agent.tools import read as read_mod
    (tmp_path / "m.py").write_text("def get_metavar():\n    pass\ndef make_metavar():\n    x = '[{'\n")
    out = read_mod.grep("def get_metavar|def make_metavar", tmp_path)
    assert "m.py:1:" in out and "m.py:3:" in out
    assert "m.py:4:" in read_mod.grep(r"\[\{", tmp_path)


def test_grep_searches_an_invalid_regex_as_plain_text(tmp_path):
    from agent.tools import read as read_mod
    (tmp_path / "m.py").write_text("def make_metavar(self, ctx):\n")
    out = read_mod.grep("make_metavar(self", tmp_path)
    assert "m.py:1:" in out and "searched for the plain text" in out
    assert "within" not in out


def test_git_read_shows_changes_and_cannot_commit(tmp_path):
    import subprocess
    from agent.tools import git as git_mod
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    run("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    run("add", "a.py")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "b.py").write_text("y = 1\n")
    assert "M a.py" in git_mod.git_read("status", tmp_path)
    assert "+x = 2" in git_mod.git_read("diff", tmp_path)
    run("add", "b.py")
    assert "+y = 1" in git_mod.git_read("diff_staged", tmp_path)
    assert git_mod.git_read("commit", tmp_path).startswith("ERROR")
    assert git_mod.git_read("add", tmp_path).startswith("ERROR")


# Fake credentials are assembled at runtime so no key-shaped string is
# committed (secret scanners would flag it, and rightly).
_FAKE_KEYS = {
    "aws_access_key": "AKIA" + "Q" * 16,
    "github_token": "ghp_" + "a1" * 18,
    "anthropic_key": "sk-ant-" + "api03-" + "x" * 30,
    "openrouter_key": "sk-or-v1-" + "f0" * 16,
    "openai_key": "sk-proj-" + "Ab1" * 10,
    "google_api_key": "AIza" + "S" * 35,
    "groq_key": "gsk_" + "z9" * 15,
    "tavily_key": "tvly-" + "dev-" + "k" * 24,
    "slack_token": "xoxb-" + "1234567890-abc",
    "stripe_key": "sk_" + "live_" + "m" * 24,
}


@pytest.mark.parametrize("kind", sorted(_FAKE_KEYS))
def test_redact_replaces_each_credential_shape(kind):
    from agent.security.redact import redact
    text, n = redact(f"API = '{_FAKE_KEYS[kind]}'  # set in prod")
    assert n == 1
    assert _FAKE_KEYS[kind] not in text
    assert f"[REDACTED:{kind}]" in text


def test_redact_removes_a_whole_private_key_block():
    from agent.security.redact import redact
    block = ("-----BEGIN OPENSSH " + "PRIVATE KEY-----\n" + "b3Blbn" * 20 + "\n"
             "-----END OPENSSH " + "PRIVATE KEY-----")
    text, n = redact(f"key:\n{block}\nafter")
    assert n == 1 and "b3Blbn" not in text and text.endswith("after")


def test_redact_leaves_ordinary_code_alone():
    from agent.security.redact import redact
    code = ("import sklearn  # sk-learn\ntask-runner --skip-ask\n"
            "AKIA_PREFIX = 'AKIA'\nheader = 'Bearer ' + token\n")
    assert redact(code) == (code, 0)


def test_tool_output_reaches_the_model_redacted(tmp_path):
    from bench.test_loop import call, make_agent
    from bench.tasks import TASKS_BY_ID, materialise
    repo = materialise(TASKS_BY_ID["zero_check"], tmp_path / "repo")
    key = _FAKE_KEYS["openai_key"]
    (repo / "settings.py").write_text(f"OPENAI_KEY = '{key}'\nDEBUG = True\n")
    agent, client = make_agent(repo, [([call("read_file", path="settings.py")], ""),
                                      ([call("grep", pattern="OPENAI_KEY")], "")])
    agent.run("what is in settings.py?")
    sent = " ".join(str(m) for spec in client.specs for m in spec.messages)
    assert key not in sent
    assert "[REDACTED:openai_key]" in sent
    assert "DEBUG = True" in sent


def _shell(command, repo=None):
    spec = policy.ToolSpec("run_command", Permission.GATED, "", {}, lambda **k: "", mutating=True)
    return policy.validate_args(spec, {"command": command}, repo or REPO)


@pytest.mark.parametrize("command", [
    ["sh", "-c", "rm -rf ."],
    ["bash", "-c", "echo hi"],
    "env rm -rf build",
    "timeout 5 rm notes.txt",
    "xargs kill",
    "nice -n 10 bash -c true",
    "find . -delete",
    "find . -name '*.pyc' -exec rm {} +",
    "cat .env",
    "cat config/.env.local",
    "cat ~/.ssh/id_rsa",
    "cat /etc/passwd",
    "cat ../other-project/secrets.py",
    "python -m tool --output=/tmp/x",
])
def test_shell_refuses_routes_around_the_gate(command):
    with pytest.raises(InvalidArgs):
        _shell(command)


@pytest.mark.parametrize("command", [
    "python -m pytest -q",
    "pytest tests/test_orders.py -k 'not slow'",
    "npm test",
    "ruff check .",
    "grep -rn kill src",
    "find . -name '*.py'",
    "python -c 'print(1)'",
    "pytest -p no:cacheprovider --basetemp=build/tmp",
    "git --no-pager log -n 1",
    "ls -la",
    "node scripts/build.js --out /dev/null",
])
def test_shell_allows_ordinary_dev_commands(command):
    assert _shell(command)["command"]


def test_path_with_nul_byte_is_refused_not_a_crash(tmp_path):
    spec = policy.ToolSpec("read_file", Permission.FREE, "", {}, lambda **k: "")
    with pytest.raises(InvalidArgs):
        policy.validate_args(spec, {"path": "calc\x00.py"}, tmp_path)


def test_symlink_loop_is_refused_or_contained(tmp_path):
    import os
    os.symlink("loop", tmp_path / "loop")
    spec = policy.ToolSpec("read_file", Permission.FREE, "", {}, lambda **k: "")
    try:
        policy.validate_args(spec, {"path": "loop"}, tmp_path)
    except InvalidArgs:
        pass                       # any other exception type fails the test


@pytest.mark.parametrize("name", [".ENV", ".Env.Local", "ID_RSA", "Credentials.JSON"])
def test_secret_shield_ignores_case(name):
    assert policy.is_secret_path(name)


@pytest.mark.parametrize("command", [
    ["python3", "-c", "from bitcount import bitcount; print(bitcount(127))"],
    "python -c 'import sys; print(sys.version)'",
    ["node", "-e", "console.log(1 > 0)"],
])
def test_inline_code_may_use_its_own_semicolons(command):
    # Measured 2026-11-18: refusing these blocked Gemma on three QuixBugs
    # tasks. The code argument is Python or JavaScript, not shell.
    assert _shell(command)["command"][0] in ("python", "python3", "node")


@pytest.mark.parametrize("command", [
    ["python3", "-c", "print(1)", ";", "rm", "x"],
    ["python3", "-c", "print(1)", "|", "sh"],
    ["python3", "run.py", "a;b"],
    ["python3", "-B", "-c", "import os; os.remove('x')"],
    "python -c 'print(1)' > /tmp/out",
])
def test_only_the_inline_code_argument_is_exempt(command):
    with pytest.raises(InvalidArgs):
        _shell(command)
