"""`himin setup`: from a fresh install to a working agent in one step.

It asks which provider to use and for its key (typed hidden), finds
Ollama if it is running, writes the user's env file (mode 0600) with
keys generated for this install, writes and starts the gateway
(agent/gateway.py), then runs `himin doctor`, which sends each model
tier a real request with a tool. `himin` runs it by itself the first
time, when nothing is configured.

Everything can also be given as flags, for a script (or the VS Code
extension, which passes the key in the environment):
    GEMINI_API_KEY=... himin setup --provider gemini --no-ollama --yes
    himin setup --provider openrouter --cheap-model deepseek/deepseek-v4.1-flash \
        --capable-model anthropic/claude-sonnet-5.5 --yes
`himin setup --describe` prints the providers, their models, which keys
are saved (never the keys) and the current models, as JSON.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import sys
from pathlib import Path

from . import config as config_mod
from . import gateway as gw

MANAGED = ("BIFROST_BASE_URL", "BIFROST_VK_DEMO", "BIFROST_VK_CONFIDENTIAL",
           "BIFROST_SETUP_TOKEN", "OLLAMA_URL", "AGENT_MODEL_LOCAL_SMALL",
           "AGENT_MODEL_LOCAL_CODE", "AGENT_MODEL_CHEAP", "AGENT_MODEL_CAPABLE")


def needs_setup(environ=None) -> bool:
    """True when nothing tells himin where its gateway and key are."""
    environ = os.environ if environ is None else environ
    if config_mod.user_config_path().exists() or environ.get("BIFROST_VK_DEMO"):
        return False
    install_env = Path(config_mod.__file__).resolve().parent.parent / ".env"
    return "BIFROST_VK_DEMO" not in config_mod.load_dotenv(install_env)


class Asker:
    """Questions on the terminal; `yes` takes every default without asking."""

    def __init__(self, yes: bool = False, out=print, ask=input, secret=getpass.getpass):
        self.yes, self.out, self._ask, self._secret = yes, out, ask, secret

    def choose(self, question: str, options: list[str], default: int = 1) -> int:
        if self.yes:
            return default
        for i, label in enumerate(options, 1):
            self.out(f"    {i}. {label}")
        while True:
            raw = self._ask(f"  {question} [{default}]: ").strip()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw)
            self.out(f"  Type a number from 1 to {len(options)}.")

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.yes:
            return default
        raw = self._ask(f"  {question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not raw else raw in ("y", "yes")

    def number(self, question: str, default: float) -> float:
        if self.yes:
            return default
        while True:
            raw = self._ask(f"  {question} [{default:g}]: ").strip().lstrip("$")
            if not raw:
                return default
            try:
                value = float(raw)
                if value > 0:
                    return value
            except ValueError:
                pass
            self.out("  Type an amount in US dollars, such as 5.")

    def model(self, question: str, models: list[str], default: str) -> str:
        """One of `models`, or an id typed in."""
        if self.yes:
            return default
        options = list(dict.fromkeys([default, *models]))
        i = self.choose(question, [*options, "Another model (type its id)"], 1)
        if i <= len(options):
            return options[i - 1]
        while True:
            raw = self._ask("  Model id, as the provider names it: ").strip()
            if raw and not any(c.isspace() for c in raw):
                return raw
            self.out("  Type the id with no spaces, such as deepseek/deepseek-v4.1-flash.")

    def secret(self, question: str, optional: bool = False) -> str:
        while True:
            value = self._secret(f"  {question}: ").strip()
            if value or optional:
                return value
            self.out("  Nothing was entered. Paste the key and press Enter.")


def _write_env(path: Path, values: dict[str, str]) -> None:
    lines = ["# Written by `himin setup`. Rerun it to change providers or keys.",
             "# Keep this file private: it holds your API keys."]
    lines += [f"{k}={v}" for k, v in values.items() if v != ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def _port_from(url: str) -> int:
    try:
        return int(url.rsplit(":", 1)[1].split("/")[0])
    except (IndexError, ValueError):
        return 0


def _model_id(raw: str, provider: str) -> str:
    """A tier model as given on the command line, without its provider."""
    raw = raw.strip()
    return raw[len(provider) + 1:] if raw.startswith(provider + "/") else raw


def describe(environ=None) -> dict:
    """What the extension needs to offer a choice: providers, their models,
    which keys are saved (as true or false, never the value) and the
    current tier models and budget."""
    environ = os.environ if environ is None else environ
    # The settings the agent itself runs on, layered as config.load does.
    install_env = Path(config_mod.__file__).resolve().parent.parent / ".env"
    saved = {**config_mod.load_dotenv(install_env),
             **config_mod.load_dotenv(config_mod.user_config_path()), **environ}
    url, models = gw.find_ollama()
    cheap = saved.get("AGENT_MODEL_CHEAP", "")
    provider = cheap.split("/", 1)[0] if cheap.split("/", 1)[0] in gw.PROVIDERS else ""
    budget = 0.0
    try:
        config = json.loads((gw.data_dir() / "data" / "config.json").read_text())
        budget = float(config["governance"]["budgets"][0]["max_limit"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    has = lambda k: bool(saved.get(k))
    return {
        "providers": [{"name": p.name, "label": p.label, "key_env": p.key_env,
                       "key_url": p.key_url, "key_saved": has(p.key_env),
                       "cheap": p.cheap, "capable": p.capable, "models": list(p.models)}
                      for p in gw.PROVIDERS.values()],
        "ollama": {"url": url, "models": models},
        "tavily_saved": has("TAVILY_API_KEY"),
        "configured": not needs_setup(environ),
        "current": {"provider": provider, "budget_usd": budget,
                    **{k: saved.get(k, "") for k in ("AGENT_MODEL_CHEAP", "AGENT_MODEL_CAPABLE",
                                                     "AGENT_MODEL_LOCAL_CODE")}},
    }


def run(args, asker: Asker | None = None, out=print) -> int:
    asker = asker or Asker(args.yes, out)
    env_path = config_mod.user_config_path()
    old = config_mod.load_dotenv(env_path)
    out("\n  Himinbjorg setup\n")
    out("  himin sends every model call through a Bifrost gateway on this machine,")
    out("  which keeps your spending limit and keeps confidential code local.\n")

    problem = gw.docker_problem()
    if problem:
        out(f"  ✗ {problem}\n")
        return 1
    out("  ✓ Docker is ready")

    # Local models
    choice = gw.Choice()
    url, models = ("", []) if args.no_ollama else gw.find_ollama()
    if models:
        local = gw.pick_local_model(models)
        out(f"  ✓ Ollama at {url}: {len(models)} models")
        if platform.system() == "Linux" and "//localhost:" in url:
            out("    Note: Ollama listens on localhost only, which Docker cannot reach on Linux.")
            out("    If the local tier fails below, set OLLAMA_HOST=0.0.0.0 for the Ollama service.")
        if asker.confirm("Use these local models? Confidential mode needs them", True):
            choice.ollama_url, choice.ollama_models = url, models
            if args.local_model:
                if args.local_model.removeprefix("ollama/") not in models:
                    out(f"  ✗ {args.local_model} is not pulled in Ollama. One of: {', '.join(models)}.")
                    return 2
                choice.local = args.local_model.removeprefix("ollama/")
            elif len(models) > 1:
                choice.local = asker.model("Local model for code", models, local)
            out(f"  ✓ {choice.local or local} for code")
    elif not args.no_ollama:
        out("  · Ollama is not running, so every model will be a cloud model.")
        out("    Confidential mode (code that never leaves this machine) needs Ollama.")

    # Cloud provider
    names = list(gw.PROVIDERS)
    if args.provider:
        if args.provider not in names + ["none"]:
            out(f"  ✗ Unknown provider {args.provider!r}. One of: {', '.join(names)}, none.")
            return 2
        picked = "" if args.provider == "none" else args.provider
    else:
        out("\n  Which cloud provider should himin use?")
        labels = [gw.PROVIDERS[n].label for n in names]
        labels.append("None, local models only" if choice.ollama_models else
                      "None (needs local models)")
        picked_i = asker.choose("Provider", labels, 1)
        picked = names[picked_i - 1] if picked_i <= len(names) else ""
    if not picked and not choice.ollama_models:
        out("  ✗ No models: choose a cloud provider, or start Ollama and rerun.")
        return 1
    choice.provider = picked

    values = {k: v for k, v in old.items()}
    if picked:
        p = gw.PROVIDERS[picked]
        have = os.environ.get(p.key_env) or old.get(p.key_env, "")
        if have and (asker.yes or asker.confirm(
                f"Use the {p.key_env} already in your {'environment' if os.environ.get(p.key_env) else 'settings'}?", True)):
            values[p.key_env] = have
        elif asker.yes:
            out(f"  ✗ --yes needs {p.key_env} in the environment.")
            return 2
        else:
            out(f"\n  Get a key at {p.key_url}")
            values[p.key_env] = asker.secret(f"Paste your {p.key_env} (hidden)")
    if picked:
        # The tier models: flags, else a question, else the provider's defaults.
        choice.cheap = _model_id(args.cheap_model or asker.model(
            "Model for everyday work", list(p.models), p.cheap), picked)
        choice.capable = _model_id(args.capable_model or asker.model(
            "Model for harder tasks", list(p.models), p.capable), picked)
    if os.environ.get("TAVILY_API_KEY"):
        values["TAVILY_API_KEY"] = os.environ["TAVILY_API_KEY"]
    elif not asker.yes and not old.get("TAVILY_API_KEY"):
        tavily = asker.secret("Tavily key for web search (optional, Enter to skip)", optional=True)
        if tavily:
            values["TAVILY_API_KEY"] = tavily
    choice.budget_usd = args.budget if args.budget else asker.number(
        "Monthly spending limit in US dollars", 5.0)

    # The gateway: keep a port it already had, else the first free one.
    port = _port_from(old.get("BIFROST_BASE_URL", "")) if gw.installed() else 0
    if gw.installed():
        gw.down(env_path)                  # rewritten below; restart it on the new config
    choice.port = port if port and gw.port_free(port) else gw.free_port(args.port)
    base = f"http://localhost:{choice.port}"
    values.update({
        "BIFROST_BASE_URL": base,
        "BIFROST_VK_DEMO": gw.new_key("sk-bf-"),
        "BIFROST_VK_CONFIDENTIAL": gw.new_key("sk-bf-local-"),
        "BIFROST_SETUP_TOKEN": gw.new_key(""),
        "OLLAMA_URL": choice.ollama_url,
        **gw.tier_models(choice),
    })
    if not choice.ollama_url:
        values.pop("OLLAMA_URL", None)
    _write_env(env_path, values)
    root = gw.write(choice, values)
    out(f"\n  ✓ Settings saved to {env_path} (only you can read it)")
    out(f"  ✓ Gateway files in {root}")

    problem = gw.up(base, env_path, out=out)
    if problem:
        out(f"  ✗ {problem}\n  Fix it, then run `himin gateway up`.\n")
        return 1
    out(f"  ✓ Gateway running at {base}")

    if args.no_check:
        out("\n  Setup done. Run `himin doctor` to check each model.\n")
        return 0
    out("\n  Checking each model with a real request...")
    from . import doctor
    code = doctor.main(["--repo", "."])
    if code == 0:
        out("  Setup done. Run `himin` in any repository to start.\n")
    else:
        out("  Setup finished with problems; the fixes are listed above.\n")
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="himin setup",
                                description="Choose providers, save keys, start the gateway")
    p.add_argument("--provider", help=f"one of: {', '.join(gw.PROVIDERS)}, none")
    p.add_argument("--no-ollama", action="store_true", help="do not use local models")
    p.add_argument("--budget", type=float, default=0.0,
                   help="monthly spending limit in US dollars (default 5)")
    p.add_argument("--port", type=int, default=gw.DEFAULT_PORT,
                   help="first port to try for the gateway (default 8099)")
    p.add_argument("--yes", action="store_true",
                   help="take every default; keys must be in the environment")
    p.add_argument("--no-check", action="store_true", help="skip the model check at the end")
    p.add_argument("--cheap-model", default="",
                   help="the provider's model for everyday work, any id it serves")
    p.add_argument("--capable-model", default="",
                   help="the provider's model for harder tasks, any id it serves")
    p.add_argument("--local-model", default="", help="the Ollama model for code (must be pulled)")
    p.add_argument("--describe", action="store_true",
                   help="print the providers, models and saved keys as JSON, and exit")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.describe:
        print(json.dumps(describe()))
        return 0
    try:
        return run(args)
    except (KeyboardInterrupt, EOFError):
        print("\n  Setup cancelled. Nothing was started.")
        return 130


def gateway_main(argv: list[str]) -> int:
    """`himin gateway up|down|status`."""
    p = argparse.ArgumentParser(prog="himin gateway",
                                description="Start, stop or check the gateway himin set up")
    p.add_argument("action", choices=["up", "down", "status"])
    args = p.parse_args(argv)
    env_path = config_mod.user_config_path()
    base = config_mod.load_dotenv(env_path).get("BIFROST_BASE_URL", "http://localhost:8099")
    if not gw.installed():
        print("  No gateway set up yet. Run `himin setup` (or `make up` in a clone).")
        return 1
    if args.action == "status":
        ok = gw.healthy(base)
        print(f"  The gateway at {base} is {'running' if ok else 'not running'}.")
        return 0 if ok else 1
    if args.action == "down":
        problem = gw.down(env_path)
        print(f"  ✗ {problem}" if problem else "  Gateway stopped.")
        return 1 if problem else 0
    if gw.healthy(base):
        print(f"  The gateway at {base} is already running.")
        return 0
    problem = gw.up(base, env_path)
    print(f"  ✗ {problem}" if problem else f"  ✓ Gateway running at {base}")
    return 1 if problem else 0
