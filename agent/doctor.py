"""`himin doctor`: check the setup, and say how to fix what is wrong.

Every check reads the same settings the agent would (install .env, user
config, environment), and every model is probed through the gateway with
the same client, key and privacy rules as a real task. A probe is one
short request with one tool, because a model that answers but cannot
call tools is the most common way a local setup fails: it looks alive
and cannot do any work.

Exit status 1 when any check fails, so it can gate a script.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config as config_mod
from . import llm as llm_mod
from .budget import QUOTA_PATH
from .router import Tier, model_for
from .security.privacy import PrivacyMode
from .tools import shell as shell_mod

PROBE_TIMEOUT = 120.0     # a cold local model can take a minute to load
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY", "vllm": "VLLM_API_KEY",
}
PING_TOOL = {"type": "function", "function": {
    "name": "ping", "description": "Answer a health check.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}


@dataclass
class Check:
    name: str
    status: str            # ok | warn | fail | skip | info
    detail: str
    fix: str = ""


class Doctor:
    def __init__(self, settings, probe_models: bool = True, out=print):
        self.settings = settings
        self.probe_models = probe_models
        self.out = out
        self.checks: list[Check] = []
        self.privacy = PrivacyMode.build(settings.confidential, settings.vk_standard,
                                         settings.vk_confidential)

    # Network access in two methods, so tests can replace them.
    def _get(self, path: str, headers: dict) -> tuple[int, str]:
        """(status, error text); status 0 when nothing answered."""
        req = urllib.request.Request(self.settings.bifrost_base_url + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status, ""
        except urllib.error.HTTPError as exc:
            return exc.code, str(exc)
        except (urllib.error.URLError, OSError) as exc:
            return 0, str(getattr(exc, "reason", exc))

    def _probe(self, model: str) -> llm_mod.CallResult:
        client = llm_mod.LLMClient(self.settings, self.privacy)
        client._client = client._client.with_options(timeout=PROBE_TIMEOUT, max_retries=0)
        return client.call(llm_mod.CallSpec(
            messages=[{"role": "system", "content": "You are a health check."},
                      {"role": "user", "content": "Call the ping tool now."}],
            model=model, tools=[PING_TOOL], max_tokens=256))

    def add(self, check: Check) -> None:
        self.checks.append(check)
        label = {"fail": "FAIL"}.get(check.status, check.status)
        self.out(f"  {label:5} {check.name:17} {check.detail}")
        if check.fix:
            self.out(f"  {'':5} {'':17} fix: {check.fix}")

    # ------------------------------------------------------------ checks

    def python(self) -> None:
        v = sys.version_info
        ok = v >= (3, 11)
        self.add(Check("python", "ok" if ok else "fail", f"{v.major}.{v.minor}.{v.micro}",
                       "" if ok else "Himinbjorg needs Python 3.11 or newer"))

    def settings_files(self) -> None:
        found = [str(p) for p in (Path(config_mod.__file__).resolve().parent.parent / ".env",
                                  config_mod.user_config_path()) if p.exists()]
        if found:
            self.add(Check("settings", "ok", ", ".join(found)))
        else:
            self.add(Check("settings", "warn", "no settings file; using defaults and the environment",
                           f"copy .env.example to {config_mod.user_config_path()}"))

    def gateway(self) -> bool:
        url = self.settings.bifrost_base_url
        status, err = self._get("/health", {})
        if status == 200:
            self.add(Check("gateway", "ok", url))
            return True
        self.add(Check("gateway", "fail",
                       f"{url} {'returned ' + str(status) if status else 'not reachable: ' + err}",
                       "start it with `make up` in the clone, or set BIFROST_BASE_URL"))
        return False

    def key(self, name: str, var: str, value: str, needed: bool) -> bool:
        status, _ = self._get(QUOTA_PATH, {"x-bf-vk": value})
        if status == 200:
            self.add(Check(name, "ok", f"{var} is a key the gateway knows"))
            return True
        why = {401: "not a key the gateway knows", 403: "refused by the gateway"}.get(
            status, f"quota check returned {status}")
        self.add(Check(name, "fail" if needed else "warn", f"{var}: {why}",
                       f"set {var} to a virtual key from infra/data/config.json"
                       + ("" if needed else " (needed only for confidential repositories)")))
        return False

    def cost_reporting(self) -> None:
        token = self.settings.bifrost_setup_token
        if not token:
            self.add(Check("cost", "warn", "BIFROST_SETUP_TOKEN not set; task cost shows as unknown",
                           "set BIFROST_SETUP_TOKEN to the gateway's setup token"))
            return
        status, _ = self._get("/api/logs?limit=1", {"X-Bifrost-Setup-Token": token})
        if status == 200:
            self.add(Check("cost", "ok", "gateway log readable; tasks report their cost"))
        else:
            self.add(Check("cost", "warn", f"log API returned {status}; task cost shows as unknown",
                           "BIFROST_SETUP_TOKEN does not match the gateway's setup token"))

    def tiers(self) -> list[tuple[str, str]]:
        """(tier names, model) for each distinct model the agent may call."""
        tiers = ([Tier.T0_LOCAL] if self.settings.confidential
                 else [Tier.T0_LOCAL, Tier.T1_CHEAP, Tier.T2_CAPABLE])
        names = {Tier.T0_LOCAL: "local", Tier.T1_CHEAP: "cheap", Tier.T2_CAPABLE: "capable"}
        by_model: dict[str, list[str]] = {}
        for t in tiers:
            by_model.setdefault(model_for(t, self.settings), []).append(names[t])
        return [("+".join(n), m) for m, n in by_model.items()]

    def model(self, tiers: str, model: str) -> None:
        name = f"model {tiers}"
        provider = model.split("/", 1)[0]
        started = time.time()
        try:
            result = self._probe(model)
        except llm_mod.ModelNotAllowed:
            # The gateway answers 403 for an unknown name too: the key's
            # allow-list is checked before the provider is.
            self.add(Check(name, "fail", f"{model}: not allowed on this virtual key, "
                                         "or no such model",
                           "check the name; the key's allowed models are in "
                           "infra/data/config.json"))
            return
        except llm_mod.AuthFailed:
            self.add(Check(name, "fail", f"{model}: the gateway rejected the key",
                           "check the virtual key settings above"))
            return
        except llm_mod.BudgetExceeded:
            self.add(Check(name, "fail", f"{model}: this key's budget is used up",
                           "wait for the budget to reset, or raise it in infra/data/config.json"))
            return
        except llm_mod.ProviderPaymentRequired:
            self.add(Check(name, "fail", f"{model}: the {provider} account has no credit",
                           f"add credit at {provider}, or set this tier to another model"))
            return
        except llm_mod.GatewayTimeout:
            self.add(Check(name, "warn", f"{model}: no answer within {PROBE_TIMEOUT:.0f} s",
                           "the provider is slow or overloaded right now; run doctor again, "
                           "or set this tier to a faster model"))
            return
        except llm_mod.RateLimited:
            self.add(Check(name, "warn", f"{model}: rate limited right now",
                           "try again shortly; a free tier may be too small for the agent"))
            return
        except Exception as exc:                            # noqa: BLE001
            self.add(Check(name, "fail", f"{model}: {_first_line(exc)}", _model_fix(provider, model)))
            return
        took = time.time() - started
        if result.tool_calls:
            recovered = any(str(t.get("id", "")).startswith("recovered_") for t in result.tool_calls)
            how = "tool call recovered from text" if recovered else "tool calling works"
            self.add(Check(name, "ok", f"{model}: answered in {took:.1f} s, {how}"))
        else:
            self.add(Check(name, "warn", f"{model}: answered in {took:.1f} s but made no tool call",
                           "this model cannot drive the agent; use an instruct model with tool "
                           "support (the qwen2.5-coder family returns tool calls as plain text)"))

    def sandbox(self) -> None:
        line = shell_mod.sandbox_status(self.settings.sandbox, self.settings.sandbox_network)
        ok = line.startswith("Landlock")
        self.add(Check("sandbox", "ok" if ok else "warn", line,
                       "" if ok else "approved commands are not confined; Landlock needs Linux 5.13+"))

    def tools(self) -> None:
        if shutil.which("git"):
            self.add(Check("git", "ok", shutil.which("git")))
        else:
            self.add(Check("git", "warn", "git not found; the git tool will not work",
                           "install git"))
        if self.privacy.confidential:
            self.add(Check("web search", "info", "off in confidential mode"))
        elif self.settings.tavily_api_key:
            self.add(Check("web search", "ok", "TAVILY_API_KEY set"))
        else:
            self.add(Check("web search", "info", "off; set TAVILY_API_KEY to enable it"))

    # -------------------------------------------------------------- run

    def run(self) -> int:
        s = self.settings
        self.out(f"\n  Himinbjorg doctor (himin {config_mod._version()})\n")
        self.out(f"  {'':5} {'repository':17} {s.repo_root} ({self.privacy.describe()})")
        self.python()
        self.settings_files()
        if self.gateway():
            std = self.key("key", "BIFROST_VK_DEMO", s.vk_standard, needed=not s.confidential)
            conf = self.key("key (local)", "BIFROST_VK_CONFIDENTIAL", s.vk_confidential,
                            needed=s.confidential)
            self.cost_reporting()
            usable = conf if s.confidential else std
            for tiers, model in self.tiers():
                if not self.probe_models:
                    self.add(Check(f"model {tiers}", "skip", f"{model} (not probed)"))
                elif usable:
                    self.model(tiers, model)
                else:
                    self.add(Check(f"model {tiers}", "skip", f"{model}: no usable key"))
        else:
            self.add(Check("models", "skip", "the gateway is not reachable"))
        self.sandbox()
        self.tools()

        fails = sum(c.status == "fail" for c in self.checks)
        warns = sum(c.status == "warn" for c in self.checks)
        if fails:
            self.out(f"\n  {_count(fails, 'problem')}, {_count(warns, 'warning')}. "
                     "The agent will not work until the problems are fixed.\n")
        else:
            self.out(f"\n  Ready{', with ' + _count(warns, 'warning') if warns else ''}.\n")
        return 1 if fails else 0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]


def _model_fix(provider: str, model: str) -> str:
    if provider == "ollama":
        name = model.split("/", 1)[1]
        return f"start Ollama and run `ollama pull {name}`, or set this tier to a cloud model"
    key = PROVIDER_KEYS.get(provider)
    if key:
        return (f"check the model name, and that {key} is set in .env (then restart the "
                "gateway), or set this tier to a model you have")
    return "check the model name and the provider's configuration in infra/data/config.json"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="himin doctor",
                                description="Check the setup and say how to fix what is wrong")
    p.add_argument("--repo", default=".", help="Repository to check the mode for")
    p.add_argument("--confidential", action="store_true", help="Check confidential mode")
    p.add_argument("--no-models", action="store_true",
                   help="Do not send a probe request to each model")
    args = p.parse_args(argv)
    settings = config_mod.load(["--repo", args.repo]
                               + (["--confidential"] if args.confidential else []))
    return Doctor(settings, probe_models=not args.no_models).run()
