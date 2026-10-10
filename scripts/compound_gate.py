"""Stop and PreCompact gate: compound only when the session has really grown.

Claude Code has no "the context reached N tokens" event. `Stop` fires after
every turn, which is far too often to mine, and `PreCompact` fires only once the
window is nearly full, which on a 1M window is about once a session. So `Stop`
becomes a cheap tick: read the token count of the last assistant turn, compare
it against a watermark stored per session id, and do nothing unless the session
has grown by `compound.threshold_tokens` since the last compound.

The gate counts GROWTH, not the absolute context size, because compaction
resets the count. An absolute mark would fire twice and then never again.

Five reasons the gate stays quiet, each one deliberate:

  * First sight of a session id - seed the watermark and do not fire. A session
    the gate has never seen is not N tokens of new work; it is an older
    conversation that happens to be large.
  * The count dropped below the watermark - compaction happened. Re-seed, and
    count fresh from here, or "growth" stays negative for the rest of the
    session.
  * Growth under the threshold - nothing to do.
  * The last assistant block is a tool call - the turn is mid-loop and whatever
    it says is provisional. The watermark is deliberately NOT advanced, so the
    growth stays pending for the next settled turn.
  * A compound is already running - same again: skip without advancing, so the
    pending growth is picked up once the running chain finishes.

`--force` is the PreCompact backstop. It skips the growth and settledness
checks, because compaction is about to summarise the window away, but it still
honours the lock, and it only runs at all when `compound.on_precompact` is true.

The watermark is ONE SMALL FILE PER SESSION, `~/.graph-mempalace/sessions/
<session_id>.json`, holding `{tokens, at, note}`. A single shared file would be
read-modify-written by every session on the machine, so two sessions whose Stop
hooks fire at the same moment would drop one of the two entries, and the file
would grow for ever. Marks older than 30 days are pruned as they are passed.

Reads the hook payload as JSON on stdin (`session_id`, `transcript_path`,
`cwd`, `hook_event_name`, and `trigger` on PreCompact). Always prints `{}` and
exits 0, whatever happens: a hook that fails must never block a session. The
compound itself runs detached, so the turn never waits for it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gm_common as gm  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)

# One file per session, so two concurrent Stop hooks cannot overwrite each
# other's mark. Claude Code session ids are uuids; anything else is reduced to
# characters that are safe in a filename on every OS.
SESSIONS_DIR = gm.STATE_DIR / "sessions"
PRUNE_DAYS = 30
UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._-]")


def bail() -> None:
    """The hook contract: empty JSON on stdout, exit 0, never block the turn."""
    print("{}")
    sys.exit(0)


def last_usage_total(transcript_path: str, tail_bytes: int = 400_000) -> int | None:
    """Total context tokens of the most recent real assistant turn, or None.

    Reads only the tail of the transcript. Returns the first usable record
    found scanning backwards; it never accumulates across records, because
    streamed blocks repeat the identical usage numbers.
    """
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as fh:
            fh.seek(max(0, size - tail_bytes))
            chunk = fh.read()
    except Exception:
        return None
    for raw in reversed(chunk.split(b"\n")):
        raw = raw.strip()
        if not raw or not raw.startswith(b"{"):
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        # Subagent turns report their own context, not the main session's.
        if rec.get("isSidechain"):
            continue
        # Synthetic error rows ("No response requested.", model "<synthetic>")
        # carry ALL-ZERO usage. One of those at the tail would make this read 0
        # and silently skip the gate.
        if rec.get("isApiErrorMessage"):
            continue
        msg = rec.get("message") or {}
        if msg.get("model") == "<synthetic>":
            continue
        usage = msg.get("usage") or {}
        # This three-field sum is what Claude Code itself computes for the
        # context percentage.
        total = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        )
        if total > 0:
            return total
    return None


def is_settled(transcript_path: str, tail_bytes: int = 200_000) -> bool:
    """Cheap, local, no LLM: is the session at a natural boundary?

    Only work that has landed is worth mining, not a position mid-argument that
    gets reversed two turns later. The signal: the most recent assistant turn
    ended with TEXT, not with a `tool_use`. A turn that ended in a tool call is
    still inside a tool loop and whatever it says is provisional.

    A read error returns True: an unreadable transcript must not be able to
    block compounding for ever.
    """
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as fh:
            fh.seek(max(0, size - tail_bytes))
            chunk = fh.read()
    except Exception:
        return True
    for raw in reversed(chunk.split(b"\n")):
        raw = raw.strip()
        if not raw or not raw.startswith(b"{"):
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        if rec.get("isApiErrorMessage"):
            continue
        content = ((rec.get("message") or {}).get("content")) or []
        if isinstance(content, str):
            return True
        kinds = [b.get("type") for b in content if isinstance(b, dict)]
        if not kinds:
            continue
        return kinds[-1] == "text"
    return True


def transcript_dir(transcript_path: str, project_dir: str) -> str:
    """The per-project transcript directory holding every session of a project.

    A subagent transcript sits one level deeper, in `<slug>/<session-id>/`, so
    the parent is used whenever this file's directory is itself inside the
    project's slug directory.
    """
    here = Path(transcript_path).resolve().parent
    projects = gm.CLAUDE_PROJECTS_DIR
    try:
        if here.parent.name == gm.slug_for(project_dir) or here.parent.parent == projects:
            return str(here.parent)
    except Exception:
        pass
    return str(here)


def mark_path(session_id: str) -> Path:
    """`~/.graph-mempalace/sessions/<session_id>.json` for one session."""
    safe = UNSAFE_IN_NAME.sub("-", session_id)[:96] or "unknown"
    return SESSIONS_DIR / f"{safe}.json"


def read_mark(session_id: str) -> dict | None:
    """This session's `{tokens, at, note}`, or None when it has never been seen.

    Reads only this session's own file. The one exception is the migration: a
    session that was last marked by an older version has its entry in the
    shared `watermark.json`, so that entry is copied across once, and the old
    file is never read again for this session.
    """
    entry = gm.read_json(mark_path(session_id), None)
    if isinstance(entry, dict):
        return entry
    legacy = gm.read_json(gm.WATERMARK_PATH, {})
    old = legacy.get(session_id) if isinstance(legacy, dict) else None
    if isinstance(old, dict):
        write_mark(session_id, int(old.get("tokens", 0) or 0), "migrated from watermark.json")
        return old
    return None


def write_mark(session_id: str, tokens: int, note: str) -> None:
    """Record the watermark for one session, atomically, and say in it why."""
    gm.write_json_atomic(
        mark_path(session_id),
        {"tokens": int(tokens), "at": gm.now_stamp(), "note": note},
    )


def prune_marks(max_age_days: int = PRUNE_DAYS) -> None:
    """Delete session marks nobody will ever read again. Opportunistic: a
    failure here is not worth reporting, and the directory is tiny."""
    cutoff = time.time() - max_age_days * 86400
    # `*.json*`, not `*.json`: write_json_atomic's temp file is named
    # `<mark>.json<random>.tmp`, and one left behind by a process that died
    # mid-write would otherwise sit here for ever.
    try:
        stale = [p for p in SESSIONS_DIR.glob("*.json*") if p.stat().st_mtime < cutoff]
    except OSError:
        return
    for path in stale:
        try:
            path.unlink()
        except OSError:
            pass


def main() -> None:
    force = "--force" in sys.argv

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    # `stop_hook_active` is deliberately unused. It exists so a hook that
    # BLOCKS (exit 2) can tell it is looking at its own continuation and stop
    # looping. This hook never blocks - it always prints `{}` and exits 0 - so
    # there is no loop to detect, and reading the flag would only tempt a
    # future change into skipping a legitimate tick.
    transcript = str(payload.get("transcript_path") or "")
    session_id = str(payload.get("session_id") or "unknown")
    cwd = str(payload.get("cwd") or os.getcwd())
    event = str(payload.get("hook_event_name") or ("PreCompact" if force else "Stop"))
    trigger = str(payload.get("trigger") or "")

    cfg = gm.load_config(cwd)
    threshold = int(cfg["compound"]["threshold_tokens"])
    stale_hours = float(cfg["compound"]["lock_stale_hours"])

    # PreCompact can be switched off entirely; then this is not even a tick.
    if force and not cfg["compound"]["on_precompact"]:
        bail()

    if not transcript or not os.path.exists(transcript):
        bail()

    prune_marks()  # once per run, on the way past

    entry = read_mark(session_id)
    seen = entry is not None
    mark = int((entry or {}).get("tokens", 0) or 0)

    if force:
        current = last_usage_total(transcript) or mark
    else:
        current = last_usage_total(transcript)
        if current is None:
            bail()
        if not seen:
            write_mark(session_id, current, "seeded on first sight; no compound")
            bail()
        if current < mark:
            write_mark(session_id, current, "re-seeded after compaction; no compound")
            bail()
        if current - mark < threshold:
            bail()
        if cfg["compound"]["require_settled"] and not is_settled(transcript):
            bail()

    if gm.lock_held(stale_hours):
        gm.append_log(f"skipped: a compound is already running (lock: {gm.LOCK_PATH})")
        bail()

    argv = [
        gm.python_exe(),
        str(gm.SCRIPTS_DIR / "compound_run.py"),
        "--project", cwd,
        "--transcripts", transcript_dir(transcript, cwd),
        "--session", session_id,
    ]
    pid = gm.spawn_detached(argv, cwd=cwd, env=gm.tool_env(cfg))
    detail = f" trigger={trigger}" if trigger else ""
    gm.append_log(
        f"gate fired: event={event}{detail} project={gm.project_token(cwd)} "
        f"session={session_id[:8]} tokens={current} mark={mark} pid={pid}"
    )
    # A spawn that failed compounded nothing, so the growth stays pending.
    if pid is not None:
        write_mark(session_id, current, f"advanced after {event}; compound pid={pid}")
    bail()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # a hook never raises into the session
        gm.append_log(f"gate error: {type(exc).__name__}: {exc}")
        print("{}")
        sys.exit(0)
