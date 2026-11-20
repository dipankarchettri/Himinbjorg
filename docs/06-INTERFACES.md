# 06 - Module Interfaces

Contracts only. No implementation. Build against these so that the five
unverified gateway questions in [01-RESEARCH.md](01-RESEARCH.md) can be answered
in parallel with writing everything that does not depend on them.

Signatures are Python-flavoured pseudocode. Types matter, bodies do not.

## Layout

```
agent/
  main.py          CLI entry, arg parsing, session construction
  config.py        env load, virtual key selection, confidential resolution
  llm.py           the ONLY module that makes HTTP calls to Bifrost
  telemetry.py     three-tier extraction of routing/cache/usage data
  router.py        step -> tier
  loop.py          plan / act / observe
  budget.py        quota polling, degradation ladder
  tools/
    registry.py    registration, schema emission, dispatch
    read.py write.py shell.py git.py search.py
  security/
    policy.py      permission enum, gate, argument validation
    privacy.py     confidential mode object, egress assertion
  memory/
    files.py       path -> hash -> summary
    ledger.py      context ledger and pruning
  trace.py         append-only JSONL session trace
```

---

## config.py

```
@frozen
class Settings:
    bifrost_base_url: str
    vk_standard: str
    vk_confidential: str
    tavily_api_key: str | None
    repo_root: Path
    confidential: bool          # resolved once, never mutated
    max_turns: int
    levers: LeverFlags          # every token lever individually switchable

@frozen
class LeverFlags:
    cache: bool; routing: bool; file_memory: bool; retrieval: bool
    pruning: bool; tool_disclosure: bool; diff_writes: bool; loop_limits: bool

def load(argv) -> Settings
def is_confidential(repo_root: Path, flag: bool) -> bool
```

`LeverFlags` exists so the benchmark can disable one lever at a time. Design it
in from the start; retrofitting it is painful.

---

## security/privacy.py

```
@frozen
class PrivacyMode:
    confidential: bool
    virtual_key: str
    allowed_model_prefixes: tuple[str, ...]   # ("ollama/",) when confidential
    external_tools_enabled: bool

    def assert_egress(self, model: str, fallbacks: list[str], vk: str) -> None
        # Raises and aborts the process on violation. Never returns False.
```

`assert_egress` is called by `llm.py` immediately before every HTTP call. It is
the last chokepoint. It raises; it does not return a boolean, because a boolean
can be ignored.

---

## security/policy.py

```
class Permission(Enum): FREE, GATED

@frozen
class ToolSpec:
    name: str
    permission: Permission        # hardcoded at registration
    schema: dict
    handler: Callable

@frozen
class GateDecision:
    tool: str
    permission: Permission
    decision: Literal["ALLOW", "APPROVED", "DENIED", "BLOCKED_INVALID_ARGS"]
    injection_markers_seen: bool
    resolved_args: dict
    timestamp: str

def validate_args(spec: ToolSpec, args: dict, repo_root: Path) -> dict
    # Path jail, secret shield, .git protection, argv sanitation.
    # Raises InvalidArgs before any approval prompt is shown.

def gate(spec: ToolSpec, resolved_args: dict) -> GateDecision
    # FREE -> ALLOW without prompting.
    # GATED -> prompt on /dev/tty, display resolved_args verbatim.
    # Consults no model output. Takes no context parameter by design.
```

`gate` deliberately has no access to the conversation. It cannot be influenced
by prompt content because it cannot see prompt content. Every `GateDecision` is
appended to the trace.

---

## tools/registry.py

```
class Registry:
    def __init__(self, privacy: PrivacyMode): ...
        # Does not construct search.py when privacy.external_tools_enabled is False.

    def schemas(self, mutating_allowed: bool) -> list[dict]
        # Progressive disclosure: read tools only unless mutating_allowed.

    def dispatch(self, name: str, args: dict) -> ToolResult
        # Unknown name -> error result, never an exception that ends the session.

@frozen
class ToolResult:
    ok: bool
    content: str
    tokens_estimate: int
    untrusted: bool = True        # everything from a tool is untrusted
```

---

## llm.py

```
@frozen
class CallSpec:
    messages: list[dict]
    tier: Tier
    model: str                    # always "provider/model"
    fallbacks: list[str]
    cache_key: str | None         # None for Shape A conversation calls
    tools: list[dict] | None

@frozen
class CallResult:
    text: str
    tool_calls: list[dict]
    usage: Usage                  # prompt, completion, total
    routing: RoutingInfo          # from telemetry.py
    cache: CacheInfo
    request_id: str               # x-bifrost-request-id, always present

def call(spec: CallSpec, settings: Settings, privacy: PrivacyMode) -> CallResult
    # 1. privacy.assert_egress(...)
    # 2. build request, extra_body for fallbacks, headers for vk and cache key
    # 3. HTTP
    # 4. map 402/429/403/401 to typed exceptions
    # 5. telemetry.extract(...)
```

The only module that performs HTTP to Bifrost. If `extra_body` turns out not to
carry `fallbacks` (open question 1), this file changes and nothing else does.

Typed exceptions: `BudgetExceeded` (402), `RateLimited` (429),
`ModelNotAllowed` (403), `AuthFailed` (401), `AllProvidersFailed`.

---

## telemetry.py

```
@frozen
class RoutingInfo:
    provider: str; model: str
    is_fallback: bool
    primary_provider: str | None; primary_model: str | None
    source: Literal["body", "header", "log"]   # which tier produced this

@frozen
class CacheInfo:
    hit: bool
    hit_type: Literal["direct", "semantic"] | None
    similarity: float | None
    source: Literal["body", "header", "log"]

def probe(settings) -> TelemetryCapability
    # One startup call. Records which tiers this gateway build supports.

def extract(response, capability) -> tuple[RoutingInfo, CacheInfo]
    # body extra_fields -> response headers -> GET /api/logs/{request_id}

def fetch_log(request_id: str, settings) -> dict
    # Always tier 3. Used unconditionally by the evidence harness.
```

Carrying `source` on both structs is what lets the evidence harness state where
a number came from, which is the difference between evidence and assertion.

---

## router.py

```
class Tier(Enum): T0_LOCAL, T1_CHEAP, T2_CAPABLE

@frozen
class RoutingDecision:
    tier: Tier
    reason: str
    method: Literal["heuristic", "classified", "forced_confidential", "escalated"]

def route(step: StepContext, privacy: PrivacyMode, levers: LeverFlags) -> RoutingDecision
    # Confidential -> T0_LOCAL unconditionally, method="forced_confidential".
    # levers.routing False -> T2_CAPABLE always (the naive baseline).
    # Heuristics first; T0 classification call only when inconclusive.

def escalate(d: RoutingDecision) -> RoutingDecision
```

`levers.routing == False` yielding "everything to T2" is how the benchmark gets
its baseline arm without a second codebase.

---

## memory/files.py and memory/ledger.py

```
@frozen
class FileRecord:
    path: Path; sha256: str; first_sent_turn: int
    summary: str | None; full_content_in_context: bool

class FileMemory:
    def observe(self, path, content) -> FileRecord
    def should_resend(self, path, content) -> Literal["full", "diff", "summary", "skip"]
    def invalidate(self, path)      # called synchronously by write tools

class Ledger:
    def append(self, entry: LedgerEntry)
    def render(self, max_tokens: int) -> list[dict]
        # Applies pruning. Writes an explicit elision marker where content was dropped.
    def stats(self) -> LedgerStats
```

---

## budget.py

```
@frozen
class Quota:
    max_limit: float; current_usage: float; headroom: float
    reset_duration: str; last_reset: str

def poll(settings, cache_ttl_seconds: int) -> Quota
def should_preempt(q: Quota, tier: Tier) -> bool
def degrade(current: Tier) -> Tier | None       # T2 -> T1 -> T0 -> None
```

---

## trace.py

```
def emit(event_type: str, payload: dict) -> None
    # Append one JSON object per line. Never buffered across a crash.
```

Event types: `session_start`, `llm_call`, `gate_decision`, `tool_result`,
`routing_decision`, `provider_change`, `budget_event`, `error`, `session_end`.

The trace is the benchmark's data source and the security audit log, written
once. Resuming a session uses `agent/session.py`, which saves the conversation
after every turn.

---

## Build order

`config.py` and `privacy.py` first, because every other module takes them as
parameters. Then `trace.py`, since everything emits to it. Then `llm.py` and
`telemetry.py` together against the live gateway. `policy.py` and `registry.py`
can be built in parallel by a second person with no gateway dependency at all,
which matters if the team is more than one.
