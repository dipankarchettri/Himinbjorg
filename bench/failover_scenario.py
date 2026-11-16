"""Scenario 2: provider failover with attribution.

The primary is a deliberately broken provider entry, so the outage is
deterministic and re-runnable. No keys are revoked by hand at demo time.

On this machine the OpenAI key in the environment is rejected by OpenAI,
which is a genuine provider failure and serves the scenario exactly.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://localhost:8099"
VK = "sk-bf-demo-user-key"
SETUP_TOKEN = "bifrost-local-setup-token"
LOCAL = os.environ.get("FAILOVER_LOCAL", "ollama/qwen2.5:7b-instruct")
# Default chain: broken OpenAI -> Anthropic -> local. With a working
# Anthropic key the outage is absorbed cloud-to-cloud at fallback index 1;
# without one, Anthropic fails too and local takes over at index 2. Both
# are genuine provider failures and both are reported to the user.
# Override both to test another chain, e.g. a real Gemini rate limit:
#   FAILOVER_PRIMARY=gemini/gemini-pro-latest \
#   FAILOVER_CLOUD=gemini/gemini-3.1-flash-lite python bench/failover_scenario.py
# FAILOVER_LOCAL overrides the local fallback (e.g. ollama/qwen2.5:3b).
BROKEN_PRIMARY = os.environ.get("FAILOVER_PRIMARY", "openai/gpt-4o-mini")
# FAILOVER_CLOUD takes a comma-separated list for a longer chain.
CLOUD_FALLBACKS = [m.strip() for m in
                   os.environ.get("FAILOVER_CLOUD", "anthropic/claude-haiku-5-5").split(",") if m.strip()]
FALLBACKS = [*CLOUD_FALLBACKS, LOCAL]


def _provider(model: str) -> str:
    return model.split("/", 1)[0]


def call(model: str, fallbacks: list[str]):
    payload = {"model": model, "max_tokens": 200,   # reasoning models spend some first
               "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}
    if fallbacks:
        payload["fallbacks"] = fallbacks
    req = urllib.request.Request(
        BASE + "/openai/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-bf-vk": VK})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        return e.code, body, dict(e.headers)


def gateway_log(rid: str) -> dict:
    if not rid:
        return {}
    time.sleep(4)   # logs are flushed asynchronously
    req = urllib.request.Request(f"{BASE}/api/logs/{rid}",
                                 headers={"X-Bifrost-Setup-Token": SETUP_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:                                   # noqa: BLE001
        return {}


def main() -> int:
    print("=" * 68)
    print("SCENARIO 2: failover from a broken primary, with attribution")
    print("=" * 68)
    print(f"  primary  : {BROKEN_PRIMARY}  (expected to fail: broken or rate-limited)")
    print(f"  fallbacks: {' -> '.join(FALLBACKS)}")
    print()

    status, body, headers = call(BROKEN_PRIMARY, FALLBACKS)
    h = {k.lower(): v for k, v in headers.items()}
    rid = h.get("x-bifrost-request-id", "")
    ri = (body.get("extra_fields", {}) or {}).get("routing_info", {}) if isinstance(body, dict) else {}

    served = f"{ri.get('provider')}/{ri.get('model')}"
    content = ""
    if isinstance(body, dict) and body.get("choices"):
        content = (body["choices"][0].get("message") or {}).get("content", "")

    print(f"  HTTP            : {status}")
    print(f"  served by       : {served}")
    print(f"  is_fallback     : {ri.get('is_fallback')}")
    print(f"  primary tried   : {ri.get('primary_provider')}/{ri.get('primary_model')}")
    print(f"  fallback index  : {h.get('x-bifrost-fallback-index')}")
    print(f"  content         : {content!r}")
    print()
    print("  header attribution (independent of the body):")
    for k in ("x-bifrost-provider", "x-bifrost-resolved-model",
              "x-bifrost-routing-info-is-fallback",
              "x-bifrost-routing-info-primary-provider"):
        print(f"    {k:42} = {h.get(k)}")

    log = gateway_log(rid)
    if log:
        print()
        print("  gateway log record (independent witness):")
        for k in ("provider", "model", "fallback_index", "status", "virtual_key_name"):
            if k in log:
                print(f"    {k:18}: {log[k]}")

    served_model = f"{ri.get('provider')}/{ri.get('model')}"
    ok = (status == 200 and ri.get("is_fallback") is True
          and served_model in FALLBACKS
          and ri.get("primary_provider") == _provider(BROKEN_PRIMARY))
    kind = "cloud-to-local" if ri.get("provider") == "ollama" else "cloud-to-cloud"
    print()
    print(f"  failover kind   : {kind}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'} - "
          f"session continued across a provider outage, user can be told who took over")

    out = ROOT / "evidence"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"scenario2-failover-{stamp}.json"
    path.write_text(json.dumps({
        "scenario": "provider_failover", "passed": ok, "http_status": status,
        "primary": BROKEN_PRIMARY, "fallbacks": FALLBACKS,
        "routing_info": ri, "request_id": rid,
        "headers": {k: v for k, v in h.items() if k.startswith("x-bifrost")},
        "gateway_log": log,
    }, indent=2))
    print(f"  evidence: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
