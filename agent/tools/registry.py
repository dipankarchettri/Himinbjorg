"""Tool registry: registration, schema emission, dispatch.

Two security properties live here:

1. Permission is a property of registration, not of the call. The model
   cannot propose a tool or describe its own permission level.
2. In confidential mode the search tool is never constructed, so it is
   absent from the schema the model sees.

And one token lever: progressive disclosure. Tool schemas are resent on
every request, so they are pure recurring overhead. Write, shell, and git
schemas are withheld until the task is known to be mutating.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from ..security.policy import Permission, ToolSpec
from . import git as git_mod
from . import read as read_mod
from . import search as search_mod
from . import shell as shell_mod
from . import write as write_mod


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    untrusted: bool = True       # everything a tool returns is untrusted


def _schema(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": required, "additionalProperties": False},
        },
    }


# From opencode's read tool (2026-11-19): one larger read beats many
# small slices, each of which is a turn.
READ_FILE_DESC = ("Read a file. Optional start/end line range; prefer one larger range "
                  "over several small ones.")


class Registry:
    def __init__(self, privacy, settings):
        self.privacy = privacy
        self.settings = settings
        self.repo_root = settings.repo_root
        self.specs: dict[str, ToolSpec] = {}
        self._register_read()
        self._register_mutating()
        # Only where pruning elides output; the agent sets the source.
        self.recall_source = None
        if settings.levers.pruning:
            self._register_recall()
        # No key, no tool: a schema the model can only get an error from is
        # wasted prompt tokens on every turn.
        if privacy.external_tools_enabled and settings.tavily_api_key:
            self._register_search()

    # Descriptions are deliberately terse: a tool description is prompt
    # text billed on every single turn.
    def _register_read(self) -> None:
        r = self.repo_root
        self._add(ToolSpec(
            "read_file", Permission.FREE, READ_FILE_DESC,
            _schema("read_file", READ_FILE_DESC,
                    {"path": {"type": "string"},
                     "start": {"type": "integer"}, "end": {"type": "integer"}},
                    ["path"]),
            partial(read_mod.read_file, repo_root=r,
                    retrieval=self.settings.levers.retrieval,
                    max_chars=read_mod.whole_file_chars(self.settings.context_budget))))
        self._add(ToolSpec(
            "list_dir", Permission.FREE, "List a directory.",
            _schema("list_dir", "List a directory.", {"path": {"type": "string"}}, ["path"]),
            partial(read_mod.list_dir, repo_root=r)))
        self._add(ToolSpec(
            "grep", Permission.FREE, "Search file contents by regex.",
            _schema("grep", "Search file contents by extended regex (a|b works).",
                    {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
            partial(read_mod.grep, repo_root=r)))
        self._add(ToolSpec(
            "symbols", Permission.FREE, "List top-level definitions in a file.",
            _schema("symbols", "List top-level definitions in a file.",
                    {"path": {"type": "string"}}, ["path"]),
            partial(read_mod.symbols, repo_root=r)))

    def _register_recall(self) -> None:
        desc = "Bring back, whole, an earlier tool output that was elided, by its id (r12)."
        self._add(ToolSpec(
            "recall", Permission.FREE, desc,
            _schema("recall", desc, {"id": {"type": "string"}}, ["id"]),
            self._recall))

    def _recall(self, id: str, **_) -> str:
        if self.recall_source is None:
            return "ERROR: nothing to recall in this session"
        return self.recall_source(id)

    def _register_mutating(self) -> None:
        r = self.repo_root
        self._add(ToolSpec(
            "apply_patch", Permission.GATED,
            "Replace an exact unique snippet in a file. Preferred over write_file.",
            _schema("apply_patch",
                    "Replace an exact unique snippet in a file. Preferred over write_file. "
                    "Set replace_all=true to replace every occurrence (renames).",
                    {"path": {"type": "string"}, "find": {"type": "string"},
                     "replace": {"type": "string"},
                     "replace_all": {"type": "boolean"}}, ["path", "find", "replace"]),
            partial(write_mod.apply_patch, repo_root=r), mutating=True))
        self._add(ToolSpec(
            "rename_symbol", Permission.GATED,
            "Rename an identifier everywhere (whole word) in one step.",
            _schema("rename_symbol",
                    "Rename an identifier everywhere (whole word) in one step. "
                    "Optional path limits it to one file or directory.",
                    {"old": {"type": "string"}, "new": {"type": "string"},
                     "path": {"type": "string"}}, ["old", "new"]),
            partial(write_mod.rename_symbol, repo_root=r), mutating=True))
        self._add(ToolSpec(
            "write_file", Permission.GATED, "Write a whole file. Use apply_patch for edits.",
            _schema("write_file", "Write a whole file. Use apply_patch for edits.",
                    {"path": {"type": "string"}, "content": {"type": "string"}},
                    ["path", "content"]),
            partial(write_mod.write_file, repo_root=r), mutating=True))
        self._add(ToolSpec(
            "run_command", Permission.GATED, "Run a command (argv, no shell).",
            _schema("run_command", "Run a command (argv, no shell).",
                    {"command": {"type": "string"}}, ["command"]),
            partial(shell_mod.run_command, repo_root=r, sandbox=self.settings.sandbox,
                    network=self.settings.sandbox_network), mutating=True))
        self._add(ToolSpec(
            "git_op", Permission.GATED, "Git: status, diff, add, log, commit.",
            _schema("git_op", "Git: status, diff, add, log, commit.",
                    {"operation": {"type": "string"}, "message": {"type": "string"}},
                    ["operation"]),
            partial(git_mod.git_op, repo_root=r, sandbox=self.settings.sandbox), mutating=True))
        # Gated although it only looks: a repository's own config can make
        # `git diff` run a filter program, so the operator sees it first.
        self._add(ToolSpec(
            "git_read", Permission.GATED, "Git, read-only: status, diff, diff_staged, log.",
            _schema("git_read", "Git, read-only: status, diff, diff_staged, log.",
                    {"operation": {"type": "string"}}, ["operation"]),
            partial(git_mod.git_read, repo_root=r, sandbox=self.settings.sandbox)))

    def _register_search(self) -> None:
        self._add(ToolSpec(
            "web_search", Permission.FREE, "Search the web.",
            _schema("web_search", "Search the web for library docs or error messages. "
                    "Never put code or secrets in the query.",
                    {"query": {"type": "string"}}, ["query"]),
            partial(search_mod.web_search, api_key=self.settings.tavily_api_key)))

    def _add(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    # Lever 7. Off, the agent can only rewrite whole files, which is how a
    # simple agent edits. Until 2026-11-15 this flag existed but changed
    # nothing, so earlier benchmark runs did not exercise it.
    DIFF_TOOLS = ("apply_patch", "rename_symbol")

    def schemas(self, mutating_allowed: bool, git_allowed: bool = True,
                rename_allowed: bool = True, git_read_allowed: bool = True) -> list[dict]:
        """Progressive disclosure (lever 6).

        git_op is offered only when the task mentions git. Measured: offered
        on every task, the model committed unprompted and looped on
        status/diff/commit inside one completion. git_read is offered when
        the task is about changes or a review, and not beside git_op.
        """
        specs = [s for s in self.specs.values()
                 if self.settings.levers.diff_writes or s.name not in self.DIFF_TOOLS]
        if not self.settings.levers.tool_disclosure:
            return [s.schema for s in specs]
        return [s.schema for s in specs
                if (mutating_allowed or not s.mutating)
                and (git_allowed or s.name != "git_op")
                and (git_read_allowed or s.name != "git_read")
                and (rename_allowed or s.name != "rename_symbol")]

    def get(self, name: str) -> ToolSpec | None:
        return self.specs.get(name)

    def dispatch(self, name: str, args: dict) -> ToolResult:
        if name in self.DIFF_TOOLS and not self.settings.levers.diff_writes:
            return ToolResult(False, f"ERROR: {name} is not available; use write_file "
                                     "with the complete file contents.")
        spec = self.specs.get(name)
        if spec is None:
            # An unknown name is an error result, never an exception that
            # ends the session.
            return ToolResult(False, f"ERROR: unknown tool {name!r}")
        # Only the arguments the tool declares (and dry_run, which the loop
        # adds). The handlers are partials with repo_root, sandbox and
        # network bound, and a call's keywords override a partial's: a
        # model sending repo_root="/" to grep searched outside the
        # repository with no approval (found in review).
        declared = spec.schema["function"]["parameters"].get("properties", {})
        clean = {k: v for k, v in (args or {}).items() if k in declared or k == "dry_run"}
        try:
            out = spec.handler(**clean)
        except TypeError as exc:
            # Name what is wrong; Python's "missing 1 required positional
            # argument" did not say which (seen: grep with only a path).
            params = spec.schema["function"]["parameters"]
            missing = [k for k in params.get("required", []) if k not in clean]
            unknown = [k for k in clean if k not in params.get("properties", {})]
            if missing or unknown:
                said = (f"{name} needs {', '.join(missing)}" if missing else
                        f"{name} takes {', '.join(params.get('properties', {}))}")
                got = ", ".join(clean) or "no arguments"
                return ToolResult(False, f"ERROR: {said}; you sent {got}.")
            return ToolResult(False, f"ERROR: bad arguments for {name}: {exc}")
        except Exception as exc:                  # noqa: BLE001 - returned to the model
            return ToolResult(False, f"ERROR: {name} failed: {exc}")
        return ToolResult(not str(out).startswith("ERROR:"), str(out))
