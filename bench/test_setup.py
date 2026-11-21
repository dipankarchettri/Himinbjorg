"""himin setup and the gateway it runs: config, keys, first run."""
import json
import os
import stat
from types import SimpleNamespace

import pytest

from agent import config as config_mod
from agent import gateway as gw
from agent import main as main_mod
from agent import setup as setup_mod


def test_a_cloud_only_gateway_has_one_provider_one_key_and_a_budget():
    cfg = gw.render_config(gw.Choice(provider="gemini", budget_usd=3), "vk1", "vk2", "tok")
    assert list(cfg["providers"]) == ["gemini"]
    assert cfg["providers"]["gemini"]["keys"][0]["value"] == "env.GEMINI_API_KEY"
    assert [v["value"] for v in cfg["governance"]["virtual_keys"]] == ["vk1"]
    assert cfg["governance"]["budgets"][0]["max_limit"] == 3
    assert cfg["setup_token"] == "tok"


def test_the_confidential_key_reaches_only_ollama():
    choice = gw.Choice(provider="openrouter", ollama_models=["qwen2.5:32b-instruct"],
                       ollama_url="http://localhost:11434")
    cfg = gw.render_config(choice, "vk1", "vk2", "tok")
    keys = {v["value"]: v for v in cfg["governance"]["virtual_keys"]}
    assert [c["provider"] for c in keys["vk2"]["provider_configs"]] == ["ollama"]
    assert {c["provider"] for c in keys["vk1"]["provider_configs"]} == {"openrouter", "ollama"}
    # The container reaches the host's Ollama by its Docker name.
    assert cfg["providers"]["ollama"]["network_config"]["base_url"] == \
        "http://host.docker.internal:11434"


def test_tiers_for_cloud_only_local_only_and_both():
    cloud = gw.tier_models(gw.Choice(provider="gemini"))
    assert set(cloud.values()) <= {"gemini/gemini-3.1-flash-lite", "gemini/gemini-3.5-flash"}
    assert cloud["AGENT_MODEL_LOCAL_CODE"] == "gemini/gemini-3.1-flash-lite"
    local = gw.tier_models(gw.Choice(ollama_models=["qwen3:1.7b", "qwen2.5:32b-instruct"]))
    assert local["AGENT_MODEL_LOCAL_CODE"] == local["AGENT_MODEL_CAPABLE"] == \
        "ollama/qwen2.5:32b-instruct"
    assert local["AGENT_MODEL_LOCAL_SMALL"] == "ollama/qwen3:1.7b"
    both = gw.tier_models(gw.Choice(provider="anthropic", ollama_models=["qwen2.5:32b-instruct"]))
    assert both["AGENT_MODEL_CAPABLE"] == "anthropic/claude-sonnet-5-5"
    assert both["AGENT_MODEL_LOCAL_CODE"] == "ollama/qwen2.5:32b-instruct"


def test_the_compose_file_names_keys_but_holds_none(tmp_path):
    values = {"BIFROST_VK_DEMO": "vk1", "BIFROST_VK_CONFIDENTIAL": "vk2",
              "BIFROST_SETUP_TOKEN": "tok", "GEMINI_API_KEY": "AIza-secret"}
    root = gw.write(gw.Choice(provider="gemini", port=8123), values, tmp_path)
    compose = (root / "docker-compose.yml").read_text()
    assert "AIza-secret" not in compose and "${GEMINI_API_KEY:-}" in compose
    assert '"127.0.0.1:8123:8080"' in compose and "name: himinbjorg" in compose
    for f in (root / "docker-compose.yml", root / "data" / "config.json"):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert json.loads((root / "data" / "config.json").read_text())["setup_token"] == "tok"


def test_new_keys_are_unique():
    assert len({gw.new_key("sk-bf-") for _ in range(50)}) == 50


class Script:
    """Answers for Asker, in order."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt=""):
        self.asked.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def no_docker_work(monkeypatch):
    calls = []
    monkeypatch.setattr(gw, "docker_problem", lambda: "")
    monkeypatch.setattr(gw, "find_ollama", lambda: ("", []))
    monkeypatch.setattr(gw, "up", lambda base, env, **kw: calls.append(("up", base)) or "")
    monkeypatch.setattr(gw, "down", lambda env, **kw: calls.append(("down",)) or "")
    monkeypatch.setattr(gw, "free_port", lambda start=8099: 8105)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    return calls


def _args(**kw):
    base = dict(provider=None, no_ollama=False, budget=0.0, port=8099, yes=False, no_check=True,
                cheap_model="", capable_model="", local_model="")
    return SimpleNamespace(**{**base, **kw})


def test_setup_asks_saves_privately_and_starts_the_gateway(no_docker_work):
    ask, secret = Script("1", "", "", "7"), Script("AIza-typed", "")
    code = setup_mod.run(_args(), setup_mod.Asker(ask=ask, secret=secret, out=lambda *a: None),
                         out=lambda *a: None)
    assert code == 0
    path = config_mod.user_config_path()
    env = config_mod.load_dotenv(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert env["GEMINI_API_KEY"] == "AIza-typed"
    assert env["BIFROST_BASE_URL"] == "http://localhost:8105"
    assert env["BIFROST_VK_DEMO"].startswith("sk-bf-") and env["BIFROST_VK_DEMO"] != "sk-bf-demo-user-key"
    assert env["AGENT_MODEL_CAPABLE"] == "gemini/gemini-3.5-flash"
    assert "TAVILY_API_KEY" not in env
    assert no_docker_work == [("up", "http://localhost:8105")]
    cfg = json.loads((gw.data_dir() / "data" / "config.json").read_text())
    assert cfg["governance"]["budgets"][0]["max_limit"] == 7
    assert cfg["governance"]["virtual_keys"][0]["value"] == env["BIFROST_VK_DEMO"]


def test_setup_with_flags_takes_the_key_from_the_environment(no_docker_work, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    code = setup_mod.run(_args(provider="openrouter", yes=True, no_ollama=True),
                         out=lambda *a: None)
    assert code == 0
    env = config_mod.load_dotenv(config_mod.user_config_path())
    assert env["OPENROUTER_API_KEY"] == "sk-or-env"
    assert env["AGENT_MODEL_CHEAP"] == "openrouter/deepseek/deepseek-v4.1-flash"


def test_a_model_outside_the_list_is_chosen_and_allowed(no_docker_work, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    code = setup_mod.run(_args(provider="openrouter", yes=True, no_ollama=True,
                               cheap_model="openrouter/qwen/qwen3-coder",
                               capable_model="anthropic/claude-sonnet-5.5"), out=lambda *a: None)
    assert code == 0
    env = config_mod.load_dotenv(config_mod.user_config_path())
    assert env["AGENT_MODEL_CHEAP"] == "openrouter/qwen/qwen3-coder"
    assert env["AGENT_MODEL_CAPABLE"] == "openrouter/anthropic/claude-sonnet-5.5"
    cfg = json.loads((gw.data_dir() / "data" / "config.json").read_text())
    allowed = cfg["governance"]["virtual_keys"][0]["provider_configs"][0]["allowed_models"]
    assert "qwen/qwen3-coder" in allowed and "deepseek/deepseek-v4.1-flash" in allowed
    assert cfg["providers"]["openrouter"]["keys"][0]["models"] == allowed


def test_a_typed_model_id_is_asked_for_interactively(no_docker_work):
    # Provider 1 (Gemini), "Another model" for everyday work, the default for harder tasks.
    ask = Script("1", "4", "gemini-3.6-flash", "", "5")
    code = setup_mod.run(_args(), setup_mod.Asker(ask=ask, secret=Script("k", ""), out=lambda *a: None),
                         out=lambda *a: None)
    assert code == 0
    env = config_mod.load_dotenv(config_mod.user_config_path())
    assert env["AGENT_MODEL_CHEAP"] == "gemini/gemini-3.6-flash"
    assert env["AGENT_MODEL_CAPABLE"] == "gemini/gemini-3.5-flash"


def test_a_local_model_must_be_pulled(no_docker_work, monkeypatch):
    monkeypatch.setattr(gw, "find_ollama", lambda: ("http://localhost:11434", ["a:7b", "b:14b"]))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert setup_mod.run(_args(provider="gemini", yes=True, local_model="c:1b"),
                         out=lambda *a: None) == 2
    assert setup_mod.run(_args(provider="gemini", yes=True, local_model="ollama/b:14b"),
                         out=lambda *a: None) == 0
    env = config_mod.load_dotenv(config_mod.user_config_path())
    assert env["AGENT_MODEL_LOCAL_CODE"] == "ollama/b:14b"


def test_a_web_search_key_in_the_environment_is_saved(no_docker_work, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    setup_mod.run(_args(provider="gemini", yes=True, no_ollama=True), out=lambda *a: None)
    assert config_mod.load_dotenv(config_mod.user_config_path())["TAVILY_API_KEY"] == "tvly-env"


def test_describe_says_which_keys_are_saved_and_never_their_values(no_docker_work, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value")
    setup_mod.run(_args(provider="openrouter", yes=True, no_ollama=True, budget=4),
                  out=lambda *a: None)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    d = setup_mod.describe({})
    assert "sk-or-secret-value" not in json.dumps(d)
    saved = {p["name"]: p["key_saved"] for p in d["providers"]}
    assert saved["openrouter"] and not saved["gemini"]
    assert d["current"]["provider"] == "openrouter" and d["current"]["budget_usd"] == 4
    assert d["current"]["AGENT_MODEL_CHEAP"] == "openrouter/deepseek/deepseek-v4.1-flash"
    assert d["configured"]


def test_setup_with_yes_and_no_key_stops_before_writing(no_docker_work):
    code = setup_mod.run(_args(provider="gemini", yes=True, no_ollama=True), out=lambda *a: None)
    assert code == 2 and not config_mod.user_config_path().exists()


def test_setup_stops_without_docker(no_docker_work, monkeypatch):
    monkeypatch.setattr(gw, "docker_problem", lambda: "Docker is not installed.")
    seen = []
    assert setup_mod.run(_args(provider="gemini"), out=seen.append) == 1
    assert any("Docker is not installed" in s for s in seen)
    assert not config_mod.user_config_path().exists()


def test_local_only_needs_ollama(no_docker_work):
    assert setup_mod.run(_args(provider="none"), out=lambda *a: None) == 1


def test_a_rerun_restarts_the_gateway_on_its_old_port(no_docker_work, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    setup_mod.run(_args(provider="gemini", yes=True, no_ollama=True), out=lambda *a: None)
    first = config_mod.load_dotenv(config_mod.user_config_path())
    monkeypatch.setattr(gw, "port_free", lambda port: True)
    setup_mod.run(_args(provider="gemini", yes=True, no_ollama=True), out=lambda *a: None)
    second = config_mod.load_dotenv(config_mod.user_config_path())
    assert second["BIFROST_BASE_URL"] == first["BIFROST_BASE_URL"]
    assert ("down",) in no_docker_work
    assert second["BIFROST_VK_DEMO"] != first["BIFROST_VK_DEMO"]


def test_the_asker_asks_again_on_a_bad_answer():
    ask = Script("9", "x", "2")
    assert setup_mod.Asker(ask=ask, out=lambda *a: None).choose("Pick", ["a", "b"]) == 2
    assert len(ask.asked) == 3


def test_first_run_off_a_terminal_says_to_run_setup(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(setup_mod, "needs_setup", lambda environ=None: True)
    assert main_mod.main(["explain this", "--repo", str(tmp_path)]) == 1
    assert "himin setup" in capsys.readouterr().out


def test_setup_and_gateway_are_dispatched(monkeypatch):
    monkeypatch.setattr(setup_mod, "main", lambda argv: 5)
    monkeypatch.setattr(setup_mod, "gateway_main", lambda argv: 6)
    assert main_mod.main(["setup"]) == 5
    assert main_mod.main(["gateway", "status"]) == 6


def test_needs_setup_is_false_once_a_key_or_settings_exist(monkeypatch):
    monkeypatch.setattr(setup_mod.config_mod, "__file__", "/nonexistent/agent/config.py")
    monkeypatch.delenv("BIFROST_VK_DEMO", raising=False)
    assert setup_mod.needs_setup()
    monkeypatch.setenv("BIFROST_VK_DEMO", "sk-bf-x")
    assert not setup_mod.needs_setup()


def test_gateway_status_without_setup(capsys):
    assert setup_mod.gateway_main(["status"]) == 1
    assert "himin setup" in capsys.readouterr().out
