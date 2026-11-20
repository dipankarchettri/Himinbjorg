"""Real bugs from a real, medium-sized project: pallets/click.

Each task is a bug the click maintainers fixed, with the report as the
prompt (an issue, or the fix's pull request description written as a
report). The repository is checked out at the commit before the fix, the
agent works with no hint of the fix, and then the maintainers' own tests
from the fix commit are added and run, SWE-bench style: the task counts
only if those tests pass and the rest of click's suite still passes.

Where a report included a suggested patch, the patch is cut from the
prompt. Where a hidden test checks a detail the report does not state,
the prompt states it in one sentence (marked in the task's `note`).

    python bench/realrepo_run.py check                 # the gold fix passes, the bug fails
    python bench/realrepo_run.py run --arms naive,optimized --repeats 3

Click is cloned into bench/.realrepo (ignored by git). Results go to
evidence/click-<stamp>/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import load_dotenv                 # noqa: E402
from bench.benchmark import ARMS, history_to, run_one, summarise  # noqa: E402

CLICK_URL = "https://github.com/pallets/click.git"
CLICK_HEAD = "08b803c"          # the clone's HEAD when the tasks were chosen
CACHE = ROOT / "bench" / ".realrepo"
BASE = CACHE / "click"
VENV = CACHE / "venv-click"
WORK = CACHE / "work"
EVIDENCE = ROOT / "evidence"


@dataclass
class ClickTask:
    id: str
    fix: str                    # the maintainers' fix commit; the agent starts at fix^
    source: str                 # where the report comes from
    prompt: str
    note: str = ""              # what the prompt adds to or cuts from the report

    def verify(self, repo: Path) -> bool:
        ok, self.detail = grade(repo, self)     # run_one calls this once, after the run
        return ok


TASKS = [
    ClickTask(
        id="usage-no-args",
        fix="0551bf5",
        source="issue #3360",
        prompt="""Empty output from `HelpFormatter.write_usage` for a program without arguments

If no `args` are passed to `HelpFormatter.write_usage`, the Usage line is not printed.

### Reproduction

```python
import click

f = click.HelpFormatter()
f.write_usage("program")
print(f.getvalue())
```

### Expected output
```
Usage: program
```

### Actual output
(an empty line)

### Environment

- Python version: 3.12.3
- Click version: 8.3.1

### Additional information

We use `click` for a CLI program with interactive mode. We are about to use `click.HelpFormatter` to print help for internal commands, some of which do not accept any arguments (`exit`, for example).""",
    ),
    ClickTask(
        id="echo-empty-bytes",
        fix="4d3db84",
        source="issue #3487",
        note="The report's suggested patch is cut.",
        prompt="""Echoing empty bytes or bytearray raises TypeError

Echoing an empty byte string will cause a crash if `nl=True`.

To replicate:

```shell
$ python -c "import io; import click; click.echo(b'', io.BytesIO(), nl=True)"

Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/myproject/click/src/click/utils.py", line 337, in echo
    file.write(out)  # type: ignore
    ^^^^^^^^^^^^^^^
TypeError: a bytes-like object is required, not 'str'
```

Expected behaviour:

We expect `b"\\n"` to be written to the stream and for `TypeError` not to be thrown.

Environment:

- Python version: 3.11.2
- Click version: 8.5.0.dev0""",
    ),
    ClickTask(
        id="funcparam-message",
        fix="fc6c7c4",
        source="issue #3105",
        note="The report's suggested code is cut; the last paragraph states the "
             "empty-message case the hidden test checks.",
        prompt="""`FuncParamType` should use the `ValueError` message for `self.fail(message)`

`FuncParamType` currently just calls `self.fail(value, ...)` if `self.func(value)` raises
a `ValueError`. This is missing the point of `self.fail`, which is to give better
feedback. With a parser function like this one:

```python
def from_str(value):
    if value != "works":
        raise ValueError(f"input was {value!r}, should be works")
    return value
```

the error shown to the user should give `str(error)`, not `str(input)`.

If the `ValueError` has no message, keep showing the value as before.""",
    ),
    ClickTask(
        id="choice-brackets",
        fix="762c97e",
        source="pull request #3578, written as a report",
        prompt="""Optional Choice argument shows double square brackets in the usage line

```python
import click

@click.command()
@click.argument("method", type=click.Choice(["foo", "bar", "baz"]), nargs=-1)
def cli(method):
    pass
```

`cli --help` prints `Usage: cli [OPTIONS] [[foo|bar|baz]]...`. The same happens with
`required=False`, and with other types whose metavar already has brackets, such as
`click.DateTime(formats=["%Y-%m-%d"])`, which shows `[[%Y-%m-%d]]`.

Expected: `Usage: cli [OPTIONS] [foo|bar|baz]...` (and `[foo|bar|baz]` for
`required=False`): one pair of brackets, not two.""",
    ),
    ClickTask(
        id="envvar-display",
        fix="c53d373",
        source="pull request #3884, written as a report",
        prompt="""`show_envvar` output is wrong for a sequence or an empty `envvar`

With `show_envvar=True`, the hint in an error such as
`Error: Missing option '--units' (env var: ...)` renders badly in special cases:

| Parameter value | Hint now | Expected hint |
| --- | --- | --- |
| `envvar=None` | Nothing | Nothing |
| `envvar=""` | `(env var: '')` | Nothing |
| `envvar=[]` | `(env var: '[]')` | Nothing |
| `envvar=["WEATHER_UNITS", "UNITS"]` | `(env var: '['WEATHER_UNITS', 'UNITS']')` | `(env var: 'WEATHER_UNITS', 'UNITS')` |

The help screen has the same problem:

| Parameter value | Help screen now | Expected |
| --- | --- | --- |
| `envvar=None` | Nothing | Nothing |
| `envvar=""` | `[env var: ]` | Nothing |
| `envvar=[]` | `[env var: ]` | Nothing |

An empty `envvar` should behave exactly like no `envvar`, including when
`auto_envvar_prefix` is set.""",
    ),
    ClickTask(
        id="argument-help-record",
        fix="05f6fd0",
        source="pull request #3860, written as a report",
        prompt="""`Argument.get_help_record` returns `None` for an argument without help

```python
import click

arg = click.Argument(["src"])
ctx = click.Context(click.Command("cli"))
print(arg.get_help_record(ctx))     # None
```

Options always get a help row, but an argument with no `help` (or `help=""`) gets
`None`, so it silently disappears from any listing built from help records. It should
always return a `(name, help)` tuple: `("SRC", "")` here, and
`("SRC", "Source path")` for `help="Source path"`.""",
    ),
    # Features across several files, for work bigger than a one-file bug.
    ClickTask(
        id="no-such-command",
        fix="831c8f0",
        source="pull request #3228, written as a feature request",
        note="states the exact messages, which the maintainers' tests check",
        prompt="""Suggest commands for a misspelled subcommand

A misspelled option gets a suggestion, but a misspelled subcommand only gets
`Error: No such command 'pause'.`. Add the same help for commands, and make the
two read the same way.

- Add a `NoSuchCommand` exception, a `UsageError` like `NoSuchOption`, with the
  close matches as `possibilities`, exported as `click.NoSuchCommand`. A group
  raises it for an unknown subcommand and suggests the group's commands that are
  close to the name.
- Messages, for commands:
  - `No such command 'unknown'.`
  - one close match: `Did you mean 'push'?`
  - several: `(Did you mean one of: 'declare', 'refine'?)`
- and for options, which change to match:
  - `No such option '--cat'.` (was `No such option: --cat`)
  - `Did you mean '--count'?` (was `Did you mean --count?`)
  - `(Did you mean one of: '--bound', '--count'?)` (was `(Possible options: --bound, --count)`)""",
    ),
    ClickTask(
        id="help-option-cls",
        fix="0b7a9ac",
        source="issue #2832, written as a report",
        prompt="""`@click.help_option` breaks when given a custom option class

```python
import click

class MyOption(click.Option):
    pass

@click.command()
@click.help_option("-h", cls=MyOption)
def cmd():
    pass
```

`cmd -h` fails with `Error: Option '-h' requires an argument.`: with `cls`, the help
option is no longer a flag that prints the help and exits. `help_option` should take
the same arguments as `click.option` and still make a help option of them, with any
`cls` that subclasses `click.Option`. Without `cls` it works as before: `-h` and
`--help` together give one row `-h, --help  Show this message and exit.`, and `-h`
alone adds a row beside the command's own `--help`.""",
    ),
]
TASKS_BY_ID = {t.id: t for t in TASKS}


def sh(args: list[str], cwd: Path, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True, **kw)


def ensure_base() -> None:
    if not BASE.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "-q", CLICK_URL, str(BASE)], CACHE)
    if not (VENV / "bin" / "python").exists():
        sh([sys.executable, "-m", "venv", str(VENV)], CACHE)
        # The version in click's uv.lock at every task's base commit.
        sh([str(VENV / "bin" / "pip"), "install", "-q", "pytest==9.0.2"], CACHE)


def fix_test_files(task: ClickTask) -> list[str]:
    out = sh(["git", "show", "--name-only", "--format=", task.fix, "--", "tests"], BASE).stdout
    return [line for line in out.splitlines() if line.strip()]


def prepare(task: ClickTask, dest: Path, at: str | None = None) -> Path:
    """A clean checkout at fix^ (or `at`), with a .venv whose path finds src/."""
    history_to(BASE, at or f"{task.fix}^", dest)
    sh(["git", "config", "user.email", "bench@localhost"], dest)
    sh(["git", "config", "user.name", "bench"], dest)
    venv = dest / ".venv"
    shutil.copytree(VENV, venv, symlinks=True)
    site = next(venv.glob("lib/python3*/site-packages"))
    (site / "click-src.pth").write_text(str(dest / "src") + "\n")
    # What an editable install would add: click's tests read its metadata.
    import tomllib
    version = tomllib.loads((dest / "pyproject.toml").read_text())["project"]["version"]
    info = site / f"click-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: click\nVersion: {version}\n")
    return dest


def grade(repo: Path, task: ClickTask) -> tuple[bool, str]:
    """Add the fix's tests (replacing any edits to those files) and run them,
    then the whole suite."""
    files = fix_test_files(task)
    # Named by hash: the checkout holds no commit after the bug's, the fix
    # included, so "<fix>^" does not resolve there.
    base = sh(["git", "rev-parse", f"{task.fix}^"], BASE).stdout.strip()
    sh(["git", "checkout", "-q", base, "--", *files], repo)
    patch = sh(["git", "show", "--format=", task.fix, "--", *files], BASE).stdout
    applied = subprocess.run(["git", "apply", "-"], cwd=repo, input=patch, text=True,
                             capture_output=True)
    if applied.returncode:
        return False, "hidden tests did not apply: " + applied.stderr[:200]
    py = str(repo / ".venv" / "bin" / "python")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    target = subprocess.run([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *files],
                            cwd=repo, text=True, capture_output=True, env=env, timeout=600)
    if target.returncode:
        return False, "fix tests failed: " + target.stdout[-300:]
    suite = subprocess.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                           cwd=repo, text=True, capture_output=True, env=env, timeout=900)
    if suite.returncode:
        return False, "suite failed: " + suite.stdout[-300:]
    return True, suite.stdout.strip().splitlines()[-1]


def check(tasks: list[ClickTask]) -> int:
    """Validate each task: the bug fails the fix's tests, the fix passes everything."""
    ensure_base()
    bad = 0
    for t in tasks:
        before = grade(prepare(t, WORK / f"check-{t.id}"), t)
        gold = grade(prepare(t, WORK / f"check-{t.id}", at=t.fix), t)
        ok = not before[0] and gold[0]
        bad += not ok
        print(f"{t.id:22} bug: {'fails' if not before[0] else 'PASSES (bad task)'}"
              f" | fix: {gold[1] if gold[0] else 'FAILS: ' + gold[1]}")
    shutil.rmtree(WORK, ignore_errors=True)
    return 1 if bad else 0


RATE_LIMITED = "stopped: rate limited"


def run(tasks: list[ClickTask], arms: list[str], repeats: int, resume: Path | None = None) -> int:
    """resume: an earlier run's directory. Its runs are kept, except those
    stopped by a rate limit, which never reached the model and say nothing
    about the agent; those cells and any missing ones are run again."""
    ensure_base()
    env = {**load_dotenv(ROOT / ".env"),
           **{k: v for k, v in os.environ.items()
              if k.startswith(("BENCH_", "AGENT_", "BIFROST_"))}}
    env.setdefault("BENCH_MAX_TURNS", "30")
    # No web search: every fix is public. Measured: with search on, the
    # feature runs searched for the upstream change, up to 13 times a run.
    env["TAVILY_API_KEY"] = ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows, rerun = [], []
    if resume:
        out = resume
        for row in json.loads((out / "results.json").read_text())["runs"]:
            (rerun if row.get("stop") == RATE_LIMITED else rows).append(row)
    else:
        out = EVIDENCE / f"click-{stamp}"
        out.mkdir(parents=True)
    done = {(r["task"], r["arm"], r["repeat"]) for r in rows}
    # A spending cap: no new run starts once the runs so far cost this much.
    max_cost = float(env.get("BENCH_MAX_COST") or "inf")
    for rep in range(repeats):
        for t in tasks:
            for arm in arms:
                if (t.id, arm, rep + 1) in done:
                    continue
                spent = sum(r.get("cost_usd") or 0 for r in rows)
                if spent >= max_cost:
                    print(f"stopping: ${spent:.4f} spent, cap ${max_cost:.2f}", flush=True)
                    break
                make_levers, prune_mode = ARMS[arm]
                for attempt in range(6):
                    repo = prepare(t, WORK / f"{t.id}-{arm}")
                    r = run_one(t, arm, make_levers(), env, stamp, prune_mode=prune_mode, repo=repo)
                    if r.stop != RATE_LIMITED:
                        break
                    print(f"{t.id} {arm}: rate limited by the gateway, waiting 10 min", flush=True)
                    time.sleep(600)
                ok, detail = r.success, (r.error or getattr(t, "detail", ""))
                row = {**asdict(r), "repeat": rep + 1, "grade": detail}
                rows.append(row)
                print(f"{t.id:22} {arm:10} rep{rep + 1} {'PASS' if ok else 'fail'} "
                      f"tokens={r.total_tokens} turns={r.turns} cost={r.cost_usd} "
                      f"{r.wall_seconds}s  {detail[:80]}", flush=True)
                (out / "results.json").write_text(json.dumps({
                    "repo": CLICK_URL, "head": CLICK_HEAD,
                    "model_cheap": env.get("BENCH_MODEL_CHEAP"),
                    "model_capable": env.get("BENCH_MODEL_CAPABLE"),
                    "max_turns": env["BENCH_MAX_TURNS"],
                    "rerun_after_rate_limit": len(rerun),
                    "tasks": [asdict(x) for x in tasks],
                    "runs": sorted(rows, key=lambda x: (x["repeat"], x["task"], x["arm"]))},
                    indent=2))
                shutil.rmtree(repo, ignore_errors=True)
    from bench.benchmark import RunResult
    fields = RunResult.__dataclass_fields__
    summary = summarise([RunResult(**{k: v for k, v in r.items() if k in fields}) for r in rows])
    (out / "summary.txt").write_text(summary)
    print(summary)
    print(f"evidence: {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Real click bugs, graded by click's own tests")
    ap.add_argument("command", choices=["check", "run"])
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--arms", default="naive,optimized")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--resume", type=Path, help="an earlier run's evidence directory")
    args = ap.parse_args()
    tasks = TASKS if args.tasks == "all" else [TASKS_BY_ID[t] for t in args.tasks.split(",")]
    if args.command == "check":
        return check(tasks)
    return run(tasks, args.arms.split(","), args.repeats, args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
