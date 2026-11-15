"""Tavily web search.

This module is never imported into the registry in confidential mode. The
tool is absent rather than refusing: a model cannot invoke a tool it was
never told exists, so there is no instruction that could reach it.
"""
from __future__ import annotations

import json
import urllib.request

TAVILY_URL = "https://api.tavily.com/search"


def web_search(query: str, api_key: str | None = None, max_results: int = 4, **_) -> str:
    if not api_key:
        return "ERROR: web search is unavailable (no TAVILY_API_KEY configured)"
    payload = json.dumps({
        "api_key": api_key, "query": query,
        "max_results": max_results, "search_depth": "basic",
    }).encode()
    req = urllib.request.Request(
        TAVILY_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:                      # noqa: BLE001 - surfaced to the model
        return f"ERROR: web search failed: {exc}"
    results = data.get("results", [])
    if not results:
        return f"no results for {query!r}"
    lines = []
    for r in results[:max_results]:
        lines.append(f"- {r.get('title', '')}\n  {r.get('url', '')}\n  "
                     f"{(r.get('content') or '')[:400]}")
    return "\n".join(lines)
