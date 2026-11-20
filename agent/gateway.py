"""The Bifrost gateway that himin runs for you.

`himin setup` writes a gateway of its own under the user's data directory
(XDG_DATA_HOME/himinbjorg/gateway): a compose file for Bifrost and Redis
Stack, and a Bifrost config with only the providers the user chose. Its
virtual keys and setup token are generated per install, so no two
installs share a key. `himin` starts it when it is not running, and
`himin gateway down` stops it. A source checkout can still run its own
gateway with `make up`; this one is for an installed himin.

The compose project is named "himinbjorg", so it never touches a gateway
started from a clone (project "infra").
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PROJECT = "himinbjorg"
DEFAULT_PORT = 8099
BIFROST_IMAGE = "maximhq/bifrost"
REDIS_IMAGE = "redis/redis-stack-server:latest"


@dataclass(frozen=True)
class Provider:
    name: str              # Bifrost's provider name
    label: str             # what setup shows
    key_env: str           # the variable holding its API key ("" for Ollama)
    cheap: str             # model ids, without the provider prefix
    capable: str
    models: tuple[str, ...]
    key_url: str = ""


PROVIDERS: dict[str, Provider] = {p.name: p for p in (
    Provider("gemini", "Google Gemini (has a free tier)", "GEMINI_API_KEY",
             "gemini-3.1-flash-lite", "gemini-3.5-flash",
             ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash"),
             "https://aistudio.google.com/apikey"),
    Provider("openrouter", "OpenRouter (many models, one key)", "OPENROUTER_API_KEY",
             "deepseek/deepseek-v4.1-flash", "anthropic/claude-haiku-5.5",
             ("deepseek/deepseek-v4.1-flash", "anthropic/claude-haiku-5.5",
              "anthropic/claude-sonnet-5.5"),
             "https://openrouter.ai/keys"),
    Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY",
             "claude-haiku-5-5", "claude-sonnet-5-5",
             ("claude-haiku-5-5", "claude-sonnet-5-5"),
             "https://console.anthropic.com/settings/keys"),
    Provider("openai", "OpenAI", "OPENAI_API_KEY",
             "gpt-4o-mini", "gpt-4o", ("gpt-4o-mini", "gpt-4o"),
             "https://platform.openai.com/api-keys"),
    Provider("groq", "Groq", "GROQ_API_KEY",
             "openai/gpt-oss-20b", "openai/gpt-oss-120b",
             ("openai/gpt-oss-20b", "openai/gpt-oss-120b"),
             "https://console.groq.com/keys"),
)}


@dataclass
class Choice:
    """What setup decided: one cloud provider or none, Ollama or not."""
    provider: str = ""                     # a PROVIDERS key, or "" for local only
    ollama_models: list[str] = field(default_factory=list)
    ollama_url: str = ""                   # as the host reaches it
    port: int = DEFAULT_PORT
    budget_usd: float = 5.0


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "himinbjorg" / "gateway"


def new_key(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(16)}"


def pick_local_model(models: list[str]) -> str:
    """The Ollama model for code: a known good instruct model if pulled."""
    for want in ("qwen2.5:32b-instruct", "qwen2.5-coder:32b", "qwen2.5-coder:14b",
                 "qwen2.5:14b-instruct", "qwen2.5-coder:7b", "qwen2.5:7b-instruct"):
        if want in models:
            return want
    return models[0] if models else ""


def tier_models(choice: Choice) -> dict[str, str]:
    """AGENT_MODEL_* for the choice. Without Ollama every tier is cloud."""
    local = pick_local_model(choice.ollama_models)
    small = min(choice.ollama_models, key=len) if choice.ollama_models else ""
    out: dict[str, str] = {}
    if choice.provider:
        p = PROVIDERS[choice.provider]
        out["AGENT_MODEL_CHEAP"] = f"{p.name}/{p.cheap}"
        out["AGENT_MODEL_CAPABLE"] = f"{p.name}/{p.capable}"
    if local:
        out["AGENT_MODEL_LOCAL_CODE"] = f"ollama/{local}"
        out["AGENT_MODEL_LOCAL_SMALL"] = f"ollama/{small}"
        out.setdefault("AGENT_MODEL_CHEAP", f"ollama/{local}")
        out.setdefault("AGENT_MODEL_CAPABLE", f"ollama/{local}")
    else:
        out["AGENT_MODEL_LOCAL_CODE"] = out["AGENT_MODEL_CHEAP"]
        out["AGENT_MODEL_LOCAL_SMALL"] = out["AGENT_MODEL_CHEAP"]
    return out


def container_ollama_url(host_url: str) -> str:
    """The Ollama URL as the gateway container reaches it."""
    for local in ("localhost", "127.0.0.1", "172.17.0.1", "0.0.0.0"):
        host_url = host_url.replace(f"//{local}:", "//host.docker.internal:")
    return host_url.rstrip("/")


def render_config(choice: Choice, vk_standard: str, vk_confidential: str,
                  setup_token: str) -> dict:
    providers: dict = {}
    standard: list = []
    confidential: list = []
    if choice.provider:
        p = PROVIDERS[choice.provider]
        providers[p.name] = {"keys": [{"name": f"{p.name}-primary", "value": f"env.{p.key_env}",
                                       "models": list(p.models), "weight": 1.0}]}
        standard.append({"provider": p.name, "weight": 1.0,
                         "allowed_models": list(p.models), "key_ids": ["*"]})
    if choice.ollama_models:
        url = container_ollama_url(choice.ollama_url)
        providers["ollama"] = {
            "keys": [{"name": "ollama-local", "value": "", "models": choice.ollama_models,
                      "weight": 1.0, "ollama_key_config": {"url": url}}],
            "network_config": {"base_url": url, "allow_private_network": True}}
        local = {"provider": "ollama", "weight": 1.0,
                 "allowed_models": choice.ollama_models, "key_ids": ["*"]}
        standard.append(local)
        confidential.append(dict(local))
    vks = [{"id": "vk-himin-user", "name": "himin", "value": vk_standard,
            "is_active": True, "provider_configs": standard}]
    if confidential:
        # No cloud provider at all: the gateway, not the agent, keeps a
        # confidential repository's code on this machine.
        vks.append({"id": "vk-himin-confidential", "name": "himin confidential (local only)",
                    "value": vk_confidential, "is_active": True,
                    "provider_configs": confidential})
    return {
        "$schema": "https://www.getbifrost.ai/schema",
        "source_of_truth": "config.json",
        "client": {"drop_excess_requests": False, "enforce_auth_on_inference": True},
        "providers": providers,
        "vector_store": {"enabled": True, "type": "redis", "config": {"addr": "redis:6379"}},
        "plugins": [{"name": "semantic_cache", "enabled": True, "config": {
            "dimension": 1, "ttl": "24h", "conversation_history_threshold": 50,
            "cache_by_model": True, "cache_by_provider": True,
            "cache_tool_call_responses": True, "default_cache_key": ""}}],
        "governance": {
            "virtual_keys": vks,
            "budgets": [{"id": "bud-himin-user", "virtual_key_id": "vk-himin-user",
                         "max_limit": choice.budget_usd, "reset_duration": "1M"}],
        },
        "setup_token": setup_token,
    }


def render_compose(choice: Choice) -> str:
    keys = "".join(f"      - {p.key_env}=${{{p.key_env}:-}}\n" for p in PROVIDERS.values())
    return f"""# Written by `himin setup`. Bifrost and Redis Stack for himin.
# Keys come from the env file next to this one; rerun `himin setup` to change it.
name: {PROJECT}
services:
  bifrost:
    image: {BIFROST_IMAGE}
    ports:
      - "127.0.0.1:{choice.port}:8080"
    volumes:
      - ./data:/app/data
    environment:
      - LOG_LEVEL=info
      - BIFROST_SETUP_TOKEN=${{BIFROST_SETUP_TOKEN}}
{keys}    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped
  redis:
    image: {REDIS_IMAGE}
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped
"""


def write(choice: Choice, env_values: dict[str, str], root: Path | None = None) -> Path:
    """Write the gateway's files. env_values holds the keys and token."""
    root = root or data_dir()
    (root / "data").mkdir(parents=True, exist_ok=True)
    config = render_config(choice, env_values["BIFROST_VK_DEMO"],
                           env_values["BIFROST_VK_CONFIDENTIAL"],
                           env_values["BIFROST_SETUP_TOKEN"])
    _private(root / "data" / "config.json", json.dumps(config, indent=2) + "\n")
    _private(root / "docker-compose.yml", render_compose(choice))
    return root


def _private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def installed(root: Path | None = None) -> bool:
    return ((root or data_dir()) / "docker-compose.yml").exists()


def owns(base_url: str) -> bool:
    """Is base_url the gateway `himin setup` wrote, so himin may start it?"""
    from . import config as config_mod
    configured = config_mod.load_dotenv(config_mod.user_config_path()).get("BIFROST_BASE_URL", "")
    return installed() and bool(configured) and configured.rstrip("/") == base_url.rstrip("/")


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_port(start: int = DEFAULT_PORT) -> int:
    for port in range(start, start + 50):
        if port_free(port):
            return port
    raise RuntimeError(f"no free port in {start}-{start + 49}")


def docker_problem() -> str:
    """"" when docker compose can run here, else what is wrong and the fix."""
    if not shutil.which("docker"):
        return "Docker is not installed. Install Docker Desktop or Docker Engine, then rerun."
    try:
        out = subprocess.run(["docker", "compose", "version"], capture_output=True,
                             text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"docker compose did not run ({exc.__class__.__name__})."
    if out.returncode != 0:
        return "The docker compose plugin is missing. Install it, then rerun."
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)
    if info.returncode != 0:
        err = (info.stderr or "").lower()
        if "permission denied" in err:
            return ("This user cannot reach the Docker daemon. Add it to the docker group "
                    "(sudo usermod -aG docker $USER, then log in again).")
        return "The Docker daemon is not running. Start Docker, then rerun."
    return ""


def compose(args: list[str], env_file: Path, root: Path | None = None,
            capture: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
    root = root or data_dir()
    return subprocess.run(
        ["docker", "compose", "--project-name", PROJECT, "--env-file", str(env_file),
         "-f", str(root / "docker-compose.yml"), *args],
        cwd=root, capture_output=capture, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL)


def healthy(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:                               # noqa: BLE001
        return False


def up(base_url: str, env_file: Path, root: Path | None = None, wait: float = 120,
       out=print) -> str:
    """Start the gateway and wait for it. "" on success, else the problem."""
    problem = docker_problem()
    if problem:
        return problem
    out("  Starting the gateway (Bifrost and Redis in Docker)...")
    res = compose(["up", "-d", "--quiet-pull"], env_file, root)
    if res.returncode != 0:
        tail = "\n".join((res.stderr or res.stdout or "").strip().splitlines()[-4:])
        return f"docker compose up failed:\n{tail}"
    deadline = time.time() + wait
    while time.time() < deadline:
        if healthy(base_url):
            return ""
        time.sleep(1)
    return f"the gateway did not answer at {base_url}/health within {int(wait)} s"


def down(env_file: Path, root: Path | None = None) -> str:
    problem = docker_problem()
    if problem:
        return problem
    res = compose(["down"], env_file, root)
    return "" if res.returncode == 0 else (res.stderr or res.stdout or "").strip()


def ollama_models(url: str, timeout: float = 3.0) -> list[str]:
    """Installed Ollama models, or [] when Ollama does not answer."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as r:
            return sorted(m["name"] for m in json.loads(r.read()).get("models", []))
    except Exception:                               # noqa: BLE001
        return []


def find_ollama() -> tuple[str, list[str]]:
    """Where Ollama answers from this host, and its models."""
    for url in (os.environ.get("OLLAMA_URL", ""), "http://localhost:11434",
                "http://172.17.0.1:11434"):
        if url:
            models = ollama_models(url)
            if models:
                return url, models
    return "", []
