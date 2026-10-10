"""compound_run.py - the chain that turns a project's chat into memory.

Five steps, in this order, under one machine-wide lock:

  1. filter   the project's transcripts -> one markdown file per exchange, with
              a stable id, plus `_index.jsonl`
  2. mine     those exchange files      -> MemPalace drawers, the ground-truth
              store for chat
  3. Graph A  the repository            -> `graphify-out/graph.json`, the code
              navigation layer
  4. Graph B  the exchanges directory   -> `graphify-out/graph.json` there, the
              chat navigation layer
  5. sweep    `claude.exe` workers orphaned by a timed-out CLI call (Windows)

The order is not a preference. Graph B reads the filtered exchanges, so it has
to follow the filter, and the mine and a Graph B extraction must never run at
the same time - together they exhausted a 16 GB machine. The lock guarantees
that within the plugin, and every step touches the lock before and after it runs
so that a chain which outlives `compound.lock_stale_hours` is never mistaken for
an abandoned one.

Graph A lands inside the repository, so before building it the chain adds
`graphify-out/` to the repository's own `.git/info/exclude` - never to a tracked
file - and the user's `git status` stays clean.

This is Python rather than a shell `&&` string so that it behaves identically
on every OS, logs each step with its duration and return code, survives a step
failing, and always releases the lock.

Code is never mined into MemPalace. The repository is already the ground-truth
store for code: always current, line-addressable. Drawers mined from it
duplicate the filesystem badly and go stale on the next edit while still
looking authoritative.

The mine writes into THIS project's own palace, the directory
`gm_common.palace_path` names, and every mempalace command is built by
`gm_common.mempalace_argv`, so no call here can forget `--palace` and land in
the shared default one. A MemPalace search, its knowledge graph and the
cross-wing tunnels it digs after every mine are all palace-wide: two projects
sharing a palace would read each other's memory. One palace per project is what
keeps them apart. MemPalace creates the directory on the first mine; nothing
here creates it.

Usage:
    compound_run.py [--project DIR] [--transcripts DIR] [--session ID]
                    [--wait] [--no-graph-a] [--no-graph-b] [--only STEPS]
                    [--json]

`--wait` prints a human summary to stdout; `--json` prints a machine summary.
Without either, the only record is `~/.graph-mempalace/compound.log`, which is
what the detached runs the Stop gate spawns rely on.

`--only filter,mine` runs just the steps named, out of `filter`, `mine`,
`graph_a` and `graph_b`. The rest do not run, are not failures, and come back
under `skipped`.

Exit code 0 on success and on every handled error, 1 only for a bad argument.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gm_common as gm  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)

# Graphify reports the files whose chunk came back empty like this:
#   "18/80 dispatched file(s) produced no nodes"
# A non-zero first group means another pass is worth running; extraction is
# cached per file, so the pass retries only those files.
NO_NODES = re.compile(r"(\d+)/(\d+) dispatched file\(s\) produced no nodes")

# MemPalace's mine prints how many drawers it filed ("Drawers filed: 60").
DRAWERS_FILED = re.compile(r"Drawers filed:\s*([\d,]+)", re.IGNORECASE)

# This runs in the background, so a big graph may be parsed just to count it;
# session_start.py keeps its own, much smaller limit because it is on the hook path.
GRAPH_SIZE_LIMIT = 200 * 1024 * 1024

# Exit codes that mean "interrupted from outside", not "the tool failed":
# STATUS_CONTROL_C_EXIT on Windows (a console closing or Ctrl+C reaching the
# child), SIGINT/SIGTERM on POSIX. One retry is worth it for those.
INTERRUPTED_RCS = {3221225786, -1073741510, -2, -15, 130, 143}
MAX_LOG_LINES = 2000                 # per step, so one chatty run cannot bloat the log

FILTER_TIMEOUT = 10 * 60
MINE_TIMEOUT = 3 * 60 * 60
GRAPHIFY_TIMEOUT = 8 * 60 * 60
SWEEP_TIMEOUT = 120

# A palace takes one writer at a time. A second MemPalace client - another
# project's compound from a different install, or the user's own `mempalace
# mine` - makes this one exit 1 with
#   mempalace: palace <path> is held by PID <n> (<the other command>)
# Seen in a live run, where the chain then skipped the mine and went on to the
# graphs: the graphs were rebuilt over exchanges that had never been filed, so
# the navigation layer pointed at drawers that did not exist. The other client
# is finishing, not stuck, so the mine waits it out instead.
PALACE_HELD = "is held by PID"
PALACE_HELD_RETRIES = 10        # retries after the first attempt
PALACE_HELD_WAIT_SECONDS = 60   # tests set this to 0; 10 x 60 s is ~10 minutes
LOCK_TOUCH_EVERY = 10           # refresh our own lock this often while waiting
HEARTBEAT_SECONDS = 60          # and this often while a step's child runs

SETUP_FIX = "run /graph-mempalace:kb-setup"

# The steps `--only` can name, in the order the chain runs them. Clustering is
# not in the vocabulary: it belongs to the graph step it names, and runs with it.
# The sweep is not either - it is housekeeping and always runs.
ONLY_STEPS = ("filter", "mine", "graph_a", "graph_b")


class Step(NamedTuple):
    """What one subprocess did: its name, result, output and wall time."""

    name: str
    rc: int
    out: str
    seconds: float
    timed_out: bool


# ---------------------------------------------------------------------------
# running one step
# ---------------------------------------------------------------------------
def _as_text(blob) -> str:
    if blob is None:
        return ""
    if isinstance(blob, bytes):
        return blob.decode("utf-8", "replace")
    return str(blob)


def _log_output(name: str, out: str) -> None:
    """Copy a step's output into the log as one block, with a line cap."""
    text = (out or "").strip()
    if not text:
        return
    lines = text.splitlines()
    if len(lines) > MAX_LOG_LINES:
        dropped = len(lines) - MAX_LOG_LINES
        lines = lines[:MAX_LOG_LINES] + [f"... [{dropped} more lines of {name} output]"]
    gm.append_log(f"output of {name}:\n" + "\n".join(lines))


def _wait_for(proc, started: float, timeout: int) -> tuple[str, int, bool]:
    """Wait for a child, refreshing the lock while it runs.

    Returns (output, rc, timed_out). `communicate` is called in
    `HEARTBEAT_SECONDS` ticks instead of one blocking call so that the lock's
    mtime moves DURING a step, not only between steps: a single Graph B pass can
    run for longer than `compound.lock_stale_hours`, and a chain whose lock is
    declared stale mid-step gets a second chain started beside it. Retrying
    `communicate` after a `TimeoutExpired` is documented as losing no output,
    which is what makes the tick loop safe.

    Once the TOTAL elapsed time passes the step's own timeout the child is
    killed and rc is 124, exactly as a single blocking timeout did.
    """
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=HEARTBEAT_SECONDS)
            return _as_text(stdout) + _as_text(stderr), proc.returncode, False
        except subprocess.TimeoutExpired:
            gm.lock_touch()
            if time.monotonic() - started <= timeout:
                continue
            proc.kill()
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except Exception:
                stdout = stderr = ""
            return _as_text(stdout) + _as_text(stderr), 124, True


def run_step(name: str, argv: list[str], cwd: str | None = None,
             env: dict | None = None, timeout: int = 600) -> Step:
    """Run one step to completion. Logs the command, the output, rc and time.

    Never raises. A timeout kills the child and returns rc 124 so the chain can
    carry on to the next step; a missing executable returns 127. The wait is a
    heartbeat loop, so the lock stays fresh for as long as the child runs.
    """
    where = f" (in {cwd})" if cwd else ""
    gm.append_log(f"--- step {name}{where}: {' '.join(argv)}")
    started = time.monotonic()
    timed_out = False
    # Windows: give every child its own process group and no console window.
    # The chain is started detached by a hook, and the session that started it
    # may close at any moment; a child sharing the old console group died with
    # STATUS_CONTROL_C_EXIT when that happened in a live run.
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
    except FileNotFoundError as exc:
        out = f"{exc}"
        rc = 127
    except Exception as exc:
        out = f"{type(exc).__name__}: {exc}"
        rc = 1
    else:
        try:
            out, rc, timed_out = _wait_for(proc, started, timeout)
        except Exception as exc:
            out = f"{type(exc).__name__}: {exc}"
            rc = 1
    seconds = time.monotonic() - started
    _log_output(name, out)
    suffix = f" TIMED OUT after {timeout}s" if timed_out else ""
    gm.append_log(f"--- step {name}: rc={rc} seconds={seconds:.0f}{suffix}")
    return Step(name, rc, out, seconds, timed_out)


def step(name: str, argv: list[str], **kwargs) -> Step:
    """`run_step` with a lock heartbeat on either side of it.

    `lock_held` calls a lock older than `compound.lock_stale_hours` abandoned
    and removes it. A full chain - filter, mine, two Graph B passes, two
    clustering runs - easily sums past the default three hours, and without a
    heartbeat a live chain would have its own lock deleted under it and a second
    chain would start beside it, which is the one thing the lock exists to
    prevent. Touching the lock on both sides of each step resets its age to
    zero between steps, so the lock can never look older than the step that is
    running right now.

    A single long step is covered too: `run_step` waits on the child in
    `HEARTBEAT_SECONDS` slices and touches the lock on every slice, so a Graph B
    pass that runs for hours keeps its lock as long as the process is alive.
    Only a chain that actually died goes stale.
    """
    gm.lock_touch()
    try:
        return run_step(name, argv, **kwargs)
    finally:
        gm.lock_touch()


# ---------------------------------------------------------------------------
# counting what happened
# ---------------------------------------------------------------------------
def count_exchanges(directory: Path) -> int:
    """How many exchange files the filter has written. 0 when the dir is absent."""
    try:
        return sum(1 for _ in directory.glob("*.md"))
    except OSError:
        return 0


def node_count(path: Path) -> int | None:
    """len(graph["nodes"]) for a graph.json, or None.

    None also means "not counted": a graph over 20 MB is left unread rather
    than parsed into memory just to print one number.
    """
    try:
        if not path.exists() or path.stat().st_size > GRAPH_SIZE_LIMIT:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception:
        return None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    return len(nodes) if isinstance(nodes, list) else None


def drawers_from_mine(out: str) -> int | None:
    """The drawer count MemPalace's mine reports ("Drawers filed: N"), if any."""
    match = DRAWERS_FILED.search(out or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def omitted_from(out: str) -> int | None:
    """Files that produced no nodes, from Graphify's own summary line."""
    match = NO_NODES.search(out or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _n(value) -> str:
    """A count for the log markers: the number, or '?' when it is unknown."""
    return "?" if value is None else str(value)


# ---------------------------------------------------------------------------
# the orphan sweep
# ---------------------------------------------------------------------------
SWEEP_PS = r"""
$cut = (Get-Date).AddMinutes(-10)
Get-CimInstance Win32_Process -Filter "Name='claude.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -like '*--json-schema*' -and
    $_.CommandLine -like '*--no-session-persistence*' -and
    $_.CreationDate -lt $cut -and
    -not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)
  } | ForEach-Object {
    Write-Output ("orphan pid=" + $_.ProcessId + " started=" + $_.CreationDate)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
"""


def sweep_orphans() -> Step | None:
    """Kill `claude.exe` workers a timed-out Graphify call left behind (Windows).

    Graphify runs `claude.cmd -p ...` with a timeout. The timeout kills the
    `cmd.exe` wrapper, not the `claude.exe` child, which then holds a slot's
    worth of memory while Graphify has already retried the chunk. The signature
    is a `claude.exe` with BOTH `--json-schema` and `--no-session-persistence`
    on its command line, older than ten minutes, whose parent process id no
    longer exists. Both flags are needed: Graphify passes the pair, while a
    person's own `claude -p --json-schema ...` job does not pass the second, and
    killing that would be this plugin reaching outside its own work.

    Does nothing off Windows. Every error is ignored: this is housekeeping.
    """
    if os.name != "nt":
        return None
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", SWEEP_PS]
    return step("sweep", argv, timeout=SWEEP_TIMEOUT)


# ---------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------
def wait_holding_lock(seconds: float) -> None:
    """Sleep for `seconds`, refreshing this chain's lock as it goes.

    The waiting is idle time, and an idle chain still owns the compound. Without
    the refresh a long enough wait would let `lock_held` call this chain
    abandoned and a second one would start beside it. `seconds <= 0` returns
    immediately, which is what the tests set.
    """
    gm.lock_touch()
    remaining = float(seconds)
    while remaining > 0:
        time.sleep(min(float(LOCK_TOUCH_EVERY), remaining))
        remaining -= LOCK_TOUCH_EVERY
        gm.lock_touch()


def run_mine(project: Path, cfg: dict, exchanges: Path, wing: str, env: dict,
             note) -> Step:
    """`mempalace --palace <this project's palace> mine`, waiting out another client.

    The argv comes from `gm.mempalace_argv`, so the mine files the exchanges in
    this project's own palace and never in the shared default one. `--palace` is
    a global option of the mempalace CLI and goes before the subcommand, which
    is the one reason this command is not assembled here by hand.

    A palace takes one writer at a time, so a mine can fail for a reason that
    has nothing to do with this project: another compound, or the user's own
    `mempalace mine`, is writing. That is not a failure to report and give up
    on, it is a queue to join - so each `is held by PID` exit is followed by a
    wait and another attempt, up to `PALACE_HELD_RETRIES` of them.

    Returns the LAST attempt's Step. Every attempt is in the log with its own
    `--- step mine: rc=` line; only the outcome goes into the summary, and each
    wait leaves a note. Any other non-zero exit is returned at once: a real
    mine error is not fixed by waiting.
    """
    argv = gm.mempalace_argv(project, cfg, "mine", str(exchanges),
                             "--mode", "projects", "--wing", wing)
    if argv is None:
        # Unreachable: run_chain stops on a missing mempalace before this runs.
        return Step("mine", 127, f"cannot find mempalace; {SETUP_FIX}", 0.0, False)
    attempt = 0
    while True:
        mined = step("mine", argv, env=env, timeout=MINE_TIMEOUT)
        if mined.rc == 0 or PALACE_HELD not in (mined.out or ""):
            return mined
        attempt += 1
        if attempt > PALACE_HELD_RETRIES:
            return mined
        note(f"palace held by another process; waiting {PALACE_HELD_WAIT_SECONDS} s "
             f"(attempt {attempt}/{PALACE_HELD_RETRIES})")
        wait_holding_lock(PALACE_HELD_WAIT_SECONDS)


def graph_a_argv(graphify: str, cfg: dict, project: Path) -> list[str]:
    """The Graphify command for the code graph, per `graph_a.mode`.

    code-only: a first build is `graphify . --code-only` (tree-sitter, free);
    afterwards `graphify update .` re-extracts only what changed.
    full: the LLM backend, so prose files in the repo get nodes too.
    """
    if str(cfg["graph_a"]["mode"]) == "full":
        return [graphify, ".", "--backend", "claude-cli",
                "--max-concurrency", str(int(cfg["graph_b"]["concurrency"])),
                "--token-budget", str(int(cfg["graph_b"]["token_budget"]))]
    if not gm.graph_a_path(project).exists():
        return [graphify, ".", "--code-only"]
    return [graphify, "update", "."]


GIT_EXCLUDE_LINE = "graphify-out/"


def hide_graph_a_from_git(project: Path) -> str | None:
    """Add `graphify-out/` to `<project>/.git/info/exclude`. Returns a log line.

    Graph A is written INTO the repository, which the user did not ask for and
    should not have to clean out of `git status` by hand. `info/exclude` is the
    repository's own private ignore list: it is not tracked, so nothing the
    plugin writes there can show up in the user's diff, in a commit or in a
    colleague's checkout. A tracked `.gitignore` would do all three, so it is
    never touched.

    Idempotent, and silent about every failure - a read-only `.git`, a
    repository on a network share, a `.git` FILE (a worktree or submodule,
    where this is skipped) are all fine; the graph still builds.
    """
    git_dir = project / ".git"
    if not git_dir.is_dir():
        return None
    path = git_dir / "info" / "exclude"
    try:
        existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if any(line.strip() == GIT_EXCLUDE_LINE for line in existing.splitlines()):
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        lead = "" if (not existing or existing.endswith("\n")) else "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{lead}{GIT_EXCLUDE_LINE}\n")
    except OSError as exc:
        return f"could not add {GIT_EXCLUDE_LINE} to {path}: {exc}"
    return f"added {GIT_EXCLUDE_LINE} to {path} (keeps git status clean)"


def done_marker(token: str, result: dict) -> str:
    """The one log line SessionStart parses, built the same way on every path.

    EVERY exit from the chain writes one, including the two early ones (no
    tools, an exception): a compound that never started is exactly what the next
    session needs to be told about, and a marker only written on the happy path
    would leave it reporting the previous, successful run instead. Unknown
    counts print as `?`. `status` is last so a parser that stops at `seconds=`
    still reads the rest.
    """
    return (
        f"=== compound done project={token} exchanges={_n(result.get('exchanges'))} "
        f"drawers_added={_n(result.get('drawers_added'))} "
        f"graph_a_nodes={_n(result.get('graph_a_nodes'))} "
        f"graph_b_nodes={_n(result.get('graph_b_nodes'))} "
        f"omitted={_n(result.get('omitted'))} seconds={int(result.get('seconds') or 0)} "
        f"status={result.get('status') or 'unknown'} ==="
    )


def _status(failed: list) -> str:
    """`ok`, or `failed:` plus every failed step, joined by `+`.

    One function so the log marker, `--json` and the human summary can never
    disagree. SessionStart reads the value back out of the `=== compound done`
    marker to tell the next session that the last compound did not finish -
    which only covers a chain that reached the end of `run_chain`, since the
    two early exits (no tools, an exception) write no marker at all and surface
    their status through `--json` and `--wait` only.
    """
    if not failed:
        return "ok"
    return "failed:" + "+".join(s.replace(" ", "_") for s in failed)


def cluster_argv(graphify: str, concurrency: int) -> list[str]:
    """`cluster-only` names the communities and rewrites GRAPH_REPORT.md.

    Labelling batches 100 nodes per call, so it is cheap next to extraction.
    """
    return [graphify, "cluster-only", ".", "--backend", "claude-cli",
            "--batch-size", "100", "--max-concurrency", str(concurrency)]


# ---------------------------------------------------------------------------
# which steps this run is allowed to execute
# ---------------------------------------------------------------------------
def parse_only(value: str) -> list[str]:
    """`--only graph_b,mine` -> ["mine", "graph_b"], in chain order.

    A name that is not one of `ONLY_STEPS`, and an empty list, exit 2 before
    anything runs: the one caller that passes this flag is the SessionStart
    hook, and a typo there has to be loud rather than silently compound less
    than it meant to. 2 is argparse's own code for an unusable argument value;
    1 stays what a bad `--project` returns.
    """
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [n for n in names if n not in ONLY_STEPS]
    if unknown or not names:
        bad = ", ".join(repr(n) for n in unknown) or "an empty list"
        print(f"compound_run.py: argument --only: invalid step {bad} "
              f"(choose from {', '.join(ONLY_STEPS)})", file=sys.stderr)
        raise SystemExit(2)
    return [s for s in ONLY_STEPS if s in names]


def selected_steps(args: argparse.Namespace) -> list[str]:
    """The steps that will run: `--only` if it was given, minus `--no-graph-*`.

    Both switches mean the same thing to the chain - do not run this step, and
    do not call it a failure - so they are resolved into one list here instead
    of being tested separately at each step.
    """
    chosen = list(getattr(args, "only", None) or ONLY_STEPS)
    off = set()
    if getattr(args, "no_graph_a", False):
        off.add("graph_a")
    if getattr(args, "no_graph_b", False):
        off.add("graph_b")
    return [s for s in ONLY_STEPS if s in chosen and s not in off]


def skipped_note(label: str, flag: str | None) -> str:
    """Why a step did not run: the flag that switched it off, or `--only`."""
    return f"{label} skipped ({flag})" if flag else f"{label} skipped (not in --only)"


def run_chain(args: argparse.Namespace, cfg: dict, project: Path, transcripts: Path,
              exchanges: Path, wing: str, token: str, session: str) -> dict:
    """Run every enabled step and return the summary. Never raises."""
    started = time.monotonic()
    gm.append_log(f"=== compound start project={token} session={session} ===")
    palace = gm.palace_path(project, cfg)

    result: dict = {
        "project": token,
        "session": session,
        "transcripts": str(transcripts),
        "exchanges_dir": str(exchanges),
        "wing": wing,
        "palace": str(palace),
        "skipped": [],
        "exchanges": None,
        "drawers_added": None,
        "graph_a_nodes": None,
        "graph_b_nodes": None,
        "omitted": None,
        "seconds": 0,
        "steps": [],
        "notes": [],
        "failed": [],
        "status": "unknown",
        "log": str(gm.LOG_PATH),
    }

    def note(text: str) -> None:
        result["notes"].append(text)
        gm.append_log(text)

    def record(step: Step | None) -> None:
        if step is None:
            return
        result["steps"].append({"name": step.name, "rc": step.rc,
                                "seconds": round(step.seconds, 1),
                                "timed_out": step.timed_out})

    # Which palace this run writes to is the first thing the log should say: a
    # mine that filed drawers somewhere unexpected is otherwise invisible.
    note(f"palace: {palace}")

    # MEMPALACE_PALACE_PATH is in the environment as well as on the command
    # line, so a mempalace child process (its hub) lands in the same palace.
    env = gm.tool_env(cfg, project)
    selected = selected_steps(args)
    result["skipped"] = [s for s in ONLY_STEPS if s not in selected]
    want_a = "graph_a" in selected
    want_b = "graph_b" in selected
    concurrency_b = int(cfg["graph_b"]["concurrency"])

    mempalace = gm.find_tool("mempalace")
    graphify = gm.find_tool("graphify")
    missing = []
    if mempalace is None:
        missing.append("mempalace")
    if graphify is None and (want_a or want_b):
        missing.append("graphify")
    if missing:
        note(f"stopped: cannot find {', '.join(missing)}; {SETUP_FIX}")
        result["failed"].append("prerequisites")
        result["status"] = _status(result["failed"])
        result["seconds"] = int(time.monotonic() - started)
        gm.append_log(done_marker(token, result))
        return result

    # 1. transcripts -> one markdown file per exchange
    if "filter" not in selected:
        note(skipped_note("filter", None))
    elif transcripts.is_dir():
        record(step(
            "filter",
            [gm.python_exe(), str(gm.SCRIPTS_DIR / "filter_transcripts.py"),
             str(transcripts), str(exchanges)],
            env=env, timeout=FILTER_TIMEOUT,
        ))
    else:
        note(f"no transcripts at {transcripts}; nothing to filter or mine")

    result["exchanges"] = count_exchanges(exchanges)

    # 2. exchanges -> MemPalace, in this project's own palace
    if "mine" not in selected:
        note(skipped_note("mine", None))
    elif exchanges.is_dir() and result["exchanges"]:
        mined = run_mine(project, cfg, exchanges, wing, env, note)
        record(mined)
        result["drawers_added"] = drawers_from_mine(mined.out)
        if mined.rc != 0 and PALACE_HELD in (mined.out or ""):
            # The graphs are about to be built over exchanges that were never
            # filed, so this has to be visible rather than a skipped step.
            result["failed"].append("mine")
            note("palace held by another process for 10 minutes; "
                 "run /graph-mempalace:kb-compound later")
    elif not result["exchanges"]:
        note(f"no exchange files in {exchanges}; skipped the mine")

    # 3. Graph A - the code graph
    if want_a:
        hidden = hide_graph_a_from_git(project)
        if hidden:
            gm.append_log(hidden)
        built = step("graph A", graph_a_argv(graphify, cfg, project),
                     cwd=str(project), env=env, timeout=GRAPHIFY_TIMEOUT)
        record(built)
        if built.rc != 0:
            result["failed"].append("graph A")
            note(f"graph A failed (rc={built.rc}); the code graph may be stale")
        if cfg["graph_a"]["cluster"] and gm.graph_a_path(project).exists():
            record(step("graph A cluster", cluster_argv(graphify, concurrency_b),
                        cwd=str(project), env=env, timeout=GRAPHIFY_TIMEOUT))
    else:
        note(skipped_note("graph A", "--no-graph-a" if args.no_graph_a else None))

    # 4. Graph B - the chat graph, in the exchanges directory
    if want_b and exchanges.is_dir():
        passes = max(1, int(cfg["graph_b"]["passes"]))
        budget = int(cfg["graph_b"]["token_budget"])
        extract = [graphify, ".", "--backend", "claude-cli",
                   "--max-concurrency", str(concurrency_b),
                   "--token-budget", str(budget)]
        attempt = 0
        interrupted_retry = False
        last_rc = 0
        while attempt < passes:
            attempt += 1
            extracted = step(f"graph B pass {attempt}", extract,
                             cwd=str(exchanges), env=env, timeout=GRAPHIFY_TIMEOUT)
            record(extracted)
            last_rc = extracted.rc
            if extracted.rc in INTERRUPTED_RCS and not interrupted_retry:
                # Interrupted from outside, not a tool error: one more try.
                interrupted_retry = True
                attempt -= 1
                note(f"graph B pass was interrupted (rc={extracted.rc}); retrying once")
                continue
            if extracted.rc != 0:
                note(f"graph B pass {attempt} failed (rc={extracted.rc})")
                break
            left = omitted_from(extracted.out)
            result["omitted"] = 0 if left is None else left
            if not left:
                break
            if attempt < passes:
                note(f"{left} file(s) produced no nodes; running pass {attempt + 1}")
        graph_b_file = gm.graph_b_path(project, cfg)
        if last_rc != 0 and not graph_b_file.exists():
            result["failed"].append("graph B")
            note("graph B did not produce a graph; run /graph-mempalace:kb-compound to retry")
        if cfg["graph_b"]["cluster"] and graph_b_file.exists():
            record(step("graph B cluster", cluster_argv(graphify, concurrency_b),
                        cwd=str(exchanges), env=env, timeout=GRAPHIFY_TIMEOUT))
    elif not want_b:
        note(skipped_note("graph B", "--no-graph-b" if args.no_graph_b else None))
    else:
        note(f"no exchanges directory at {exchanges}; skipped graph B")

    # 5. housekeeping
    record(sweep_orphans())

    result["graph_a_nodes"] = node_count(gm.graph_a_path(project))
    result["graph_b_nodes"] = node_count(gm.graph_b_path(project, cfg))
    result["seconds"] = int(time.monotonic() - started)

    result["status"] = _status(result["failed"])
    gm.append_log(done_marker(token, result))
    return result


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def human_summary(result: dict) -> str:
    """The report `/graph-mempalace:kb-compound` shows a person."""
    seconds = int(result.get("seconds") or 0)
    clock = f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"
    rows = [
        ("project", result.get("project")),
        ("exchanges", _n(result.get("exchanges"))),
        ("drawers added", _n(result.get("drawers_added"))),
        ("Graph A nodes", _n(result.get("graph_a_nodes"))),
        ("Graph B nodes", _n(result.get("graph_b_nodes"))),
        ("files omitted", _n(result.get("omitted"))),
        ("wing", result.get("wing")),
        ("palace", result.get("palace")),
        ("exchanges dir", result.get("exchanges_dir")),
        ("log", result.get("log")),
    ]
    lines = [f"compound finished in {clock}"]
    lines += [f"  {label:<14}{value}" for label, value in rows]
    if result.get("skipped"):
        # Named steps that did not run, so a short summary cannot be mistaken
        # for a full compound.
        lines.append(f"  {'skipped':<14}" + ", ".join(result["skipped"]))
    if result.get("failed"):
        lines.append("  FAILED: " + ", ".join(result["failed"]) + " (see the log, then re-run)")
    failed = [s for s in result.get("steps", []) if s.get("rc")]
    if failed:
        lines.append("  steps that did not return 0:")
        for step in failed:
            flag = " (timed out)" if step.get("timed_out") else ""
            lines.append(f"    {step['name']}: rc={step['rc']}{flag}")
    for text in result.get("notes", []):
        lines.append(f"  note: {text}")
    if any("cannot find" in n for n in result.get("notes", [])):
        lines.append(f"  fix: {SETUP_FIX}")
    return "\n".join(lines)


class _Parser(argparse.ArgumentParser):
    """Argument errors exit 1; everything else in this script exits 0."""

    def error(self, message: str):  # noqa: D102
        self.print_usage(sys.stderr)
        print(f"compound_run.py: {message}", file=sys.stderr)
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="compound_run.py",
        description="Filter this project's chat, mine it into MemPalace, and "
                    "refresh the code and chat graphs.",
    )
    parser.add_argument("--project", default=os.getcwd(),
                        help="the repository to compound (default: the current directory)")
    parser.add_argument("--transcripts", default=None,
                        help="Claude Code transcript directory "
                             "(default: ~/.claude/projects/<slug> for the project)")
    parser.add_argument("--session", default=None,
                        help="session id, recorded in the log so a run can be traced back")
    parser.add_argument("--wait", action="store_true",
                        help="print a human summary to stdout when the chain finishes")
    parser.add_argument("--no-graph-a", action="store_true",
                        help="skip the code graph")
    parser.add_argument("--no-graph-b", action="store_true",
                        help="skip the chat graph (the slow, LLM-backed step)")
    parser.add_argument("--only", type=parse_only, default=None, metavar="STEPS",
                        help="run only these steps, comma separated, out of "
                             + ",".join(ONLY_STEPS)
                             + " (clustering runs with its own graph step)")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="print a machine-readable summary to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    project = Path(args.project).expanduser()
    if not project.is_dir():
        print(f"compound_run.py: --project is not a directory: {project}", file=sys.stderr)
        return 1
    project = project.resolve()

    cfg = gm.load_config(project)
    if args.transcripts:
        transcripts = Path(args.transcripts).expanduser()
    else:
        transcripts = gm.transcripts_dir(project)
    exchanges = gm.exchanges_dir(project, cfg)
    wing = gm.wing_for(project, cfg)
    token = gm.project_token(project)
    session = args.session or "-"

    if not gm.lock_acquire(float(cfg["compound"]["lock_stale_hours"])):
        message = f"a compound is already running (lock: {gm.LOCK_PATH})"
        gm.append_log("skipped: " + message)
        # A --json caller gets a document on every path, or it has to parse
        # prose to find out what happened. Nothing failed here: another chain
        # holds the lock, which is a correct no-op, so `failed` stays empty and
        # `status` says why there is no result.
        if args.as_json:
            print(json.dumps({"project": token, "session": session, "seconds": 0,
                              "palace": str(gm.palace_path(project, cfg)),
                              "notes": [message], "failed": [],
                              "status": "skipped:locked",
                              "log": str(gm.LOG_PATH), "steps": []}, indent=2))
        else:
            print(message)
        return 0

    try:
        result = run_chain(args, cfg, project, transcripts, exchanges, wing, token, session)
    except Exception as exc:  # a broken chain still releases the lock and exits 0
        gm.append_log(f"compound error: {type(exc).__name__}: {exc}")
        result = {"project": token, "session": session, "seconds": 0,
                  "palace": str(gm.palace_path(project, cfg)),
                  "notes": [f"failed: {type(exc).__name__}: {exc}"],
                  "failed": ["chain"], "status": "failed:chain",
                  "log": str(gm.LOG_PATH), "steps": []}
        gm.append_log(done_marker(token, result))
    finally:
        gm.lock_release()

    if args.wait:
        print(human_summary(result))
    if args.as_json:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        gm.append_log(f"compound fatal: {type(exc).__name__}: {exc}")
        sys.exit(0)
