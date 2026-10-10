"""SessionStart hook: tell the model the knowledge base exists, in a few lines.

This runs on every session start, resume, clear, compact and fork, and whatever
it prints on stdout is injected into the session, so the session pays for it
every single time. The budget is therefore hard: under 2,500 characters of
stdout and under two seconds of wall time. Nothing here starts a subprocess:
not the status line, not the log read, not the graph count. The two exceptions
are `gm.find_tool`, which may run `uv tool dir` once on a machine whose
`~/.graph-mempalace/tools.json` is still empty, and the one detached compound
on a project seen for the first time.

Four things can be printed:

  * the prerequisites line, when `mempalace` or `graphify` cannot be found.
    Nothing else is worth saying until they are installed.
  * the instruction block: how to consult the knowledge base and how to answer
    from it. Every command goes through `py.sh` so that Python discovery is the
    same here as it is in the hooks.
  * one status line: exchanges on disk, Graph B's node count, and when the last
    compound finished.
  * one line when a background pass was just started: either first sight or the
    palace seed, never both.

First sight of a project gets a detached compound of its whole history, because
a knowledge base nobody had to set up is the entire point of the plugin.

The palace seed is the same idea one release later. Up to 0.1.0 every project on
a machine mined into one shared MemPalace palace; from 0.2.0 each project gets
its own, because MemPalace digs tunnels across wings after every mine and its
search and knowledge graph are palace-wide, so a shared palace leaks one project
into another. An upgraded machine is therefore in a state first sight cannot see:
the exchanges are on disk and ingested, so first sight stays quiet, but this
project has no palace of its own and every query against it comes back empty.
One mine-only compound moves them. Mine-only, because the exchanges and both
graphs are already current - the palace is the only thing missing - and because
a full chain would spend an hour of LLM calls rebuilding Graph B for nothing.
It runs at most once per project: a marker file records the spawn.

Exits 0 always. A hook that fails must never block a session.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gm_common as gm  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)

STDOUT_BUDGET = 2500
GRAPH_SIZE_LIMIT = 20 * 1024 * 1024
LOG_TAIL_BYTES = 2_000_000
# The filter writes `_index.jsonl` as its LAST act, so its presence means a
# filter pass finished, whatever it found. That is what separates "nothing has
# ever ingested this project" from "ingesting it found nothing worth keeping".
INDEX_NAME = "_index.jsonl"

PREREQ_LINE = ("graph-mempalace: prerequisites missing (mempalace, graphify). "
               "Run /graph-mempalace:kb-setup once.")

FIRST_SIGHT_LINE = ("graph-mempalace: first session here - ingesting this project's "
                    "history in the background (a few minutes to an hour; check "
                    "~/.graph-mempalace/compound.log)")

PALACE_SEED_LINE = ("graph-mempalace: moving this project's memory into its own "
                    "palace in the background (one-time, a few minutes)")

# One file per project, written the moment the seed is spawned, so an upgrade
# costs one mine and not one per session start.
SEED_MARKER = "palace_seed.json"

INSTRUCTIONS = """graph-mempalace: this project has a knowledge base of its past conversations and code.
Before answering about past work or decisions, run:
  bash "{s}/py.sh" "{s}/kb_resolve.py" ask "<question>"            # chat history -> verbatim exchanges
  bash "{s}/py.sh" "{s}/kb_resolve.py" ask "<question>" --code     # code graph -> file:line
  bash "{s}/py.sh" "{s}/kb_resolve.py" get <exchange-id>           # one exchange in full
Answer from the quoted exchange or the file, never from a node's description.
Nodes marked verification=unverified are claims. If nothing comes back, say so.
Compounding is automatic; /graph-mempalace:kb-compound runs it now."""

DONE_MARKER = "=== compound done"
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})")
# The chain's marker ends `... omitted=N seconds=N status=S ===`, where S is
# `ok` or `failed:` plus the steps that failed, joined by `+`.
STATUS = re.compile(r"\bstatus=(\S+)")


def read_payload() -> dict:
    """The hook JSON on stdin (`source`, `cwd`, `session_id`), or an empty dict."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def scripts_path() -> str:
    """This directory, absolute, with forward slashes so it quotes cleanly in sh."""
    return str(gm.SCRIPTS_DIR).replace("\\", "/")


def count_exchanges(directory: Path) -> int:
    try:
        return sum(1 for _ in directory.glob("*.md"))
    except OSError:
        return 0


def node_count(path: Path) -> int | None:
    """len(graph["nodes"]), or None when the file is absent, big or unparsable."""
    try:
        if not path.exists() or path.stat().st_size > GRAPH_SIZE_LIMIT:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception:
        return None
    nodes = data.get("nodes") if isinstance(data, dict) else None
    return len(nodes) if isinstance(nodes, list) else None


def last_done_line(token: str | None = None) -> str | None:
    """The newest `=== compound done` line in the log, or None.

    With `token` given, only markers for that project count (`project=<token> `
    is in every marker); the log is shared by every project on the machine, and
    one project's status line must not report another project's run. Reads only
    the tail of the log and scans it backwards, so the size of a log that has
    been accumulating for months does not matter.
    """
    try:
        size = gm.LOG_PATH.stat().st_size
        with open(gm.LOG_PATH, "rb") as fh:
            fh.seek(max(0, size - LOG_TAIL_BYTES))
            chunk = fh.read()
    except OSError:
        return None
    needle = f"project={token} ".encode() if token else None
    for raw in reversed(chunk.split(b"\n")):
        if DONE_MARKER.encode() not in raw:
            continue
        if needle and needle not in raw:
            continue
        line = raw.decode("utf-8", "replace").lstrip()
        if STAMP.match(line):
            return line
    return None


def last_compound(line: str | None = None) -> str | None:
    """"YYYY-MM-DD HH:MM" of the last compound, or None.

    Pass a marker line to read that one; with no argument it finds the newest
    marker itself, so the two accessors can share a single pass over the log.
    """
    line = last_done_line() if line is None else line
    match = STAMP.match(line) if line else None
    return f"{match.group(1)} {match.group(2)}" if match else None


def last_status(line: str | None = None) -> str | None:
    """The `status=` field of the last compound: `ok`, `failed:<steps>`, or None.

    None also covers a marker written before the field existed.
    """
    line = last_done_line() if line is None else line
    match = STATUS.search(line) if line else None
    return match.group(1) if match else None


def failure_note(status: str | None) -> str:
    """What to add to the status line when the last compound did not finish.

    A silent failure is the worst case here: the chain runs detached, so the
    only place its outcome surfaces is this line. `failed:graph_B` becomes
    "graph B"; `failed:graph_A+graph_B` becomes "graph A, graph B".
    """
    if not status or not status.startswith("failed"):
        return ""
    if "prerequisites" in status:
        # Nothing ran at all, and kb-compound would fail the same way.
        return (" - last compound could not start (prerequisites missing); "
                "run /graph-mempalace:kb-setup")
    which = status.split(":", 1)[1] if ":" in status else "unknown"
    which = which.replace("_", " ").replace("+", ", ")
    return f" - last compound FAILED ({which}); run /graph-mempalace:kb-compound"


def status_line(project: str, cfg: dict) -> str:
    """One line of state: exchanges, Graph B nodes, when THIS project last compounded."""
    done = last_done_line(gm.project_token(project))
    # No marker for THIS project means no compound to report - do not let the
    # accessors fall back to the newest marker of some other project.
    tail = failure_note(last_status(done)) if done else ""
    exchanges = count_exchanges(gm.exchanges_dir(project, cfg))
    if not exchanges:
        return "graph-mempalace: not ingested yet" + tail
    bits = [f"{exchanges:,} exchanges"]
    nodes = node_count(gm.graph_b_path(project, cfg))
    if nodes is not None:
        bits.append(f"Graph B {nodes:,} nodes")
    when = last_compound(done) if done else None
    if when:
        bits.append(f"last compound {when}")
    return "graph-mempalace: " + " · ".join(bits) + tail


def maybe_first_sight(project: str, cfg: dict, session_id: str) -> str | None:
    """Compound a project's whole history the first time it is opened.

    Only when no exchange file exists yet AND no filter pass has ever finished
    here, there are transcripts to read, and no other compound is running.
    Returns the line to print, or None.

    Two conditions, because either one alone is wrong. "No exchanges directory"
    was wrong: the filter creates the directory before it parses anything, so a
    filter that died half way disabled first sight for ever. "No exchange files"
    alone is wrong the other way: a project whose transcripts legitimately yield
    nothing worth keeping would then spawn a full compound on EVERY session
    start, for ever. `_index.jsonl` is the filter's last write, so together they
    read as "nothing has finished ingesting this project yet".
    """
    if not cfg["compound"]["on_first_sight"]:
        return None
    exchanges = gm.exchanges_dir(project, cfg)
    if count_exchanges(exchanges) or (exchanges / INDEX_NAME).exists():
        return None
    transcripts = gm.transcripts_dir(project)
    if not transcripts.is_dir():
        return None
    if gm.lock_held(float(cfg["compound"]["lock_stale_hours"])):
        return None
    argv = [
        gm.python_exe(),
        str(gm.SCRIPTS_DIR / "compound_run.py"),
        "--project", project,
        "--transcripts", str(transcripts),
        "--session", session_id or "-",
    ]
    pid = gm.spawn_detached(argv, cwd=project, env=gm.tool_env(cfg, project))
    if pid is None:
        return None
    gm.append_log(f"first sight: spawned a compound for {gm.project_token(project)} pid={pid}")
    return FIRST_SIGHT_LINE


def maybe_palace_seed(project: str, cfg: dict, session_id: str) -> str | None:
    """Mine an already-ingested project into its own palace, once.

    This is the 0.1.0 -> 0.2.0 upgrade path and nothing else. Four conditions,
    all of them necessary:

      * exchanges exist, so there is something to mine and first sight is not
        the right answer. (main() only calls this when first sight did not
        fire, so the two can never both spawn.)
      * this project's palace directory does not exist. MemPalace creates it on
        the first mine, so its absence means no mine has ever run against it -
        on an upgraded machine the drawers are in the old shared palace.
      * no marker. The marker is written on the spawn, not on success, because
        a mine that fails for a reason that will keep failing must not re-spawn
        on every session start for ever. `/graph-mempalace:kb-compound` is the
        retry, and deleting the marker re-arms this.
      * no lock, so this never runs beside another compound.

    Returns the line to print, or None. Writes the marker and one log line.
    """
    exchanges = gm.exchanges_dir(project, cfg)
    if not count_exchanges(exchanges):
        return None
    palace = gm.palace_path(project, cfg)
    if palace.exists():
        return None
    marker = gm.project_state_dir(project) / SEED_MARKER
    if marker.exists():
        return None
    if gm.lock_held(float(cfg["compound"]["lock_stale_hours"])):
        return None
    transcripts = gm.transcripts_dir(project)
    argv = [
        gm.python_exe(),
        str(gm.SCRIPTS_DIR / "compound_run.py"),
        "--project", project,
        "--transcripts", str(transcripts),
        "--session", session_id or "-",
        "--only", "mine",
    ]
    pid = gm.spawn_detached(argv, cwd=project, env=gm.tool_env(cfg, project))
    if pid is None:
        return None
    gm.write_json_atomic(marker, {"spawned": gm.now_stamp(), "pid": pid,
                                  "palace": str(palace)})
    gm.append_log(f"palace seed: spawned a mine for {gm.project_token(project)} "
                  f"into {palace} pid={pid}")
    return PALACE_SEED_LINE


def main() -> int:
    payload = read_payload()
    project = str(payload.get("cwd") or os.getcwd())
    session_id = str(payload.get("session_id") or "")
    source = str(payload.get("source") or "")
    cfg = gm.load_config(project)

    # Nothing else is worth saying until the two tools exist.
    if gm.find_tool("mempalace") is None or gm.find_tool("graphify") is None:
        print(PREREQ_LINE)
        return 0

    parts: list[str] = []
    if cfg["session"]["inject_instructions"]:
        parts.append(INSTRUCTIONS.format(s=scripts_path()))
    if cfg["session"]["show_status"]:
        parts.append(status_line(project, cfg))
    # At most one spawn per session start: first sight already mines, so the
    # seed is only asked about when first sight decided there was nothing to do.
    first = maybe_first_sight(project, cfg, session_id)
    if first:
        parts.append(first)
    else:
        seed = maybe_palace_seed(project, cfg, session_id)
        if seed:
            parts.append(seed)

    out = "\n".join(p for p in parts if p).strip()
    if len(out) > STDOUT_BUDGET:
        out = out[: STDOUT_BUDGET - 3].rstrip() + "..."
    if out:
        print(out)
    if source:
        gm.append_log(f"session start: source={source} project={gm.project_token(project)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # a hook never raises into the session
        gm.append_log(f"session_start error: {type(exc).__name__}: {exc}")
        sys.exit(0)
