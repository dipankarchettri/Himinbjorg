"""`himin doctor`, without a gateway or a model: network calls are faked."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest                                              # noqa: E402

from agent import doctor as doctor_mod                     # noqa: E402
from agent import llm as llm_mod                           # noqa: E402
from agent import main as main_mod                         # noqa: E402
from agent.config import LeverFlags, Settings              # noqa: E402
from agent.telemetry import CacheInfo, RoutingInfo, Usage  # noqa: E402


def settings(tmp_path, confidential=False, **models) -> Settings:
    return Settings(
        bifrost_base_url="http://gateway", vk_standard="sk-std", vk_confidential="sk-conf",
        tavily_api_key=None, bifrost_setup_token="token", repo_root=tmp_path,
        confidential=confidential, max_turns=5, levers=LeverFlags(),
        model_local_small="ollama/small", trace_dir=tmp_path / "traces",
        model_local_code=models.get("local", "ollama/local"),
        model_cheap=models.get("cheap", "gemini/cheap"),
        model_capable=models.get("capable", "gemini/capable"))


def result(tool_calls) -> llm_mod.CallResult:
    return llm_mod.CallResult(text="", tool_calls=tool_calls, usage=Usage(1, 1, 2),
                              routing=RoutingInfo(), cache=CacheInfo(), request_id="r",
                              latency_s=0.1)


PING = [{"id": "c1", "type": "function", "function": {"name": "ping", "arguments": "{}"}}]


def run(s, get=None, probe=None, probe_models=True):
    """Run the doctor with faked network; returns (exit code, checks, probed models)."""
    probed = []
    lines = []
    d = doctor_mod.Doctor(s, probe_models=probe_models, out=lines.append)
    d._get = get or (lambda path, headers: (200, ""))

    def fake_probe(model):
        probed.append(model)
        return (probe or (lambda m: result(PING)))(model)
    d._probe = fake_probe
    code = d.run()
    return code, {c.name: c for c in d.checks}, probed


def test_a_working_setup_is_ready(tmp_path):
    code, checks, probed = run(settings(tmp_path))
    assert code == 0
    assert probed == ["ollama/local", "gemini/cheap", "gemini/capable"]
    assert all(checks[f"model {t}"].status == "ok" for t in ("local", "cheap", "capable"))


def test_tiers_sharing_a_model_are_probed_once(tmp_path):
    _, checks, probed = run(settings(tmp_path, cheap="gemini/same", capable="gemini/same"))
    assert probed == ["ollama/local", "gemini/same"]
    assert "model cheap+capable" in checks


def test_gateway_down_fails_and_skips_what_needs_it(tmp_path):
    code, checks, probed = run(settings(tmp_path), get=lambda p, h: (0, "Connection refused"))
    assert code == 1
    assert checks["gateway"].status == "fail" and "make up" in checks["gateway"].fix
    assert checks["models"].status == "skip"
    assert probed == []


def test_unknown_key_fails_with_the_variable_to_set(tmp_path):
    def get(path, headers):
        return (401, "") if headers.get("x-bf-vk") == "sk-std" else (200, "")
    code, checks, probed = run(settings(tmp_path), get=get)
    assert code == 1
    assert checks["key"].status == "fail" and "BIFROST_VK_DEMO" in checks["key"].fix
    assert probed == []                      # no usable key, so no model is probed


def test_missing_confidential_key_only_warns_in_standard_mode(tmp_path):
    def get(path, headers):
        return (401, "") if headers.get("x-bf-vk") == "sk-conf" else (200, "")
    code, checks, _ = run(settings(tmp_path), get=get)
    assert code == 0
    assert checks["key (local)"].status == "warn"


def test_confidential_mode_probes_only_the_local_model(tmp_path):
    code, checks, probed = run(settings(tmp_path, confidential=True))
    assert code == 0
    assert probed == ["ollama/local"]
    assert checks["web search"].status == "info"


def test_a_model_that_answers_without_a_tool_call_is_flagged(tmp_path):
    code, checks, _ = run(settings(tmp_path),
                          probe=lambda m: result([]) if m == "ollama/local" else result(PING))
    assert code == 0                         # a warning, not a failure
    assert checks["model local"].status == "warn"
    assert "tool" in checks["model local"].fix


@pytest.mark.parametrize("exc, status, hint", [
    (llm_mod.ModelNotAllowed("403"), "fail", "allowed models"),
    (llm_mod.ProviderPaymentRequired("402"), "fail", "credit"),
    (llm_mod.BudgetExceeded("402"), "fail", "budget"),
    (llm_mod.RateLimited("429"), "warn", "again"),
    (llm_mod.GatewayTimeout("slow"), "warn", "slow"),
    (llm_mod.GatewayError("connection refused"), "fail", "ollama pull local"),
])
def test_probe_errors_say_what_to_do(tmp_path, exc, status, hint):
    def probe(model):
        if model == "ollama/local":
            raise exc
        return result(PING)
    _, checks, _ = run(settings(tmp_path), probe=probe)
    assert checks["model local"].status == status
    assert hint in checks["model local"].fix


def test_a_cloud_model_error_names_the_provider_key(tmp_path):
    def probe(model):
        if model == "gemini/cheap":
            raise llm_mod.GatewayError("unknown model")
        return result(PING)
    _, checks, _ = run(settings(tmp_path), probe=probe)
    assert "GEMINI_API_KEY" in checks["model cheap"].fix


def test_no_models_skips_the_probes(tmp_path):
    code, checks, probed = run(settings(tmp_path), probe_models=False)
    assert code == 0 and probed == []
    assert checks["model local"].status == "skip"


def test_himin_doctor_is_dispatched(monkeypatch):
    seen = {}
    monkeypatch.setattr(doctor_mod, "main", lambda argv: seen.setdefault("argv", argv) and 7)
    assert main_mod.main(["doctor", "--no-models"]) == 7
    assert seen["argv"] == ["--no-models"]


def test_the_cli_stops_with_one_message_when_the_gateway_is_down(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod.budget_mod, "gateway_error",
                        lambda s: "not reachable (Connection refused)")
    monkeypatch.setenv("BIFROST_BASE_URL", "http://localhost:1")
    assert main_mod.main(["explain this", "--repo", str(tmp_path),
                          "--trace-dir", str(tmp_path / "t")]) == 1
    out = capsys.readouterr().out
    assert "make up" in out and "Traceback" not in out

