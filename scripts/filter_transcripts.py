"""Flatten Claude Code transcripts to dialogue-only markdown, ONE FILE PER EXCHANGE.

An "exchange" is one user turn plus every assistant turn that answers it, up to
the next user turn. That is the unit a decision actually lives in: the question
and its answer together.

Why per-exchange and not per-session or per-turn:
  - per-session (the old behaviour) gave a back-pointer that resolved to an
    805 KB file - useless for "show me what was actually said".
  - per-turn strips the question away from the answer, so an extractor sees a
    reply with no idea what it replies to.
  - per-exchange keeps them together and makes the pointer ~100x sharper.

Every file carries a STABLE ID in frontmatter and in its filename:
    <session8>-<NNNN>        e.g. b49fc7be-0007
That id is the join key. A Graphify node extracted from the file carries it as
`source_file`; a MemPalace drawer mined from the same file carries it in its
source metadata. Resolving a node to its verbatim source is then an exact
lookup, not a text search.

The id is stable across compounds, and that rests on reading ONE file per
session: the top-level `<transcript-dir>/<session>.jsonl`. A transcript is
append-only, so a later compound adds exchanges at the end and never renumbers
the ones already filed. Subagent transcripts, which Claude Code writes into the
per-session subdirectory `<transcript-dir>/<session>/`, are skipped for the same
reason: they are written after the exchange they belong to, so merging them in
and ordering by timestamp shifted the number of every later exchange in that
session. Nothing is lost - the main transcript already carries each subagent's
result as a task-notification turn.

A session's LAST exchange is held back while that session may still be running.
Every other exchange is final: the next user turn closed it, and the transcript
only ever grows at the end. The last one is still growing while the session is
live, and the chain does run then (on PreCompact mid-turn, on a manual compound
mid-turn, and from other sessions). Filing it early means later runs rewrite it
with more content, Graphify re-extracts it, and Graphify's shrink guard refuses
to save the whole chat graph whenever that re-extraction yields fewer nodes for
the file than last time. LLM output varies run to run, so the graph can stay
stuck for days. So: when the transcript was modified within
OPEN_EXCHANGE_QUIET_SECONDS of now, its last exchange is "open" and this run
neither writes nor overwrites its file. If an earlier run already wrote that
file, the file is left exactly as it is and keeps its `_index.jsonl` row, so the
resolver never loses a file that exists. A session quiet for longer than the
window is treated as finished and its last exchange is written. Holding back
never changes a number: the exchange keeps its id and is filed under it later.

An exchange file is only rewritten when its content changed. Rewriting every
file on every run churned their mtimes for nothing.

Keeps: user text and assistant text.
Drops: tool_result, tool_use inputs, thinking, images, attachments, meta lines,
       sidechain (subagent) rows, and compaction summaries (lossy restatement
       of turns already kept).

Writes, into <output-dir>:
  <date>-<id>.md    one file per kept exchange, with frontmatter
  _index.jsonl      id -> file lookup table, read by kb_resolve.py
  .gitignore        graphify-out/, .graphify_* and _index.jsonl so neither the graph nor the lookup table is mined.
                    An existing file is kept; only the missing lines are appended.

The last line of stdout is stable and parseable:
    filter: <N> exchanges, <M> turns, <K> sessions -> <output-dir>
<N> is the number of exchanges `_index.jsonl` lists after this run, which is
the number of exchange files on disk that it stands behind: files left
unchanged count, and so does a held-back exchange's file from an earlier run.
<M> is the turns in those exchanges. A held-back exchange with no file yet is in
neither; the `held back` line above the summary counts it.

Usage:
    python filter_transcripts.py <transcript-dir> <output-dir>

<transcript-dir> is ~/.claude/projects/<url-encoded-project-path>/
Afterwards:
    mempalace mine <output-dir> --mode projects --wing <proj>_dialogue

Exit codes: 0 on success, 2 on bad arguments.
"""
from __future__ import annotations

import os
import sys

# gm_common first: it reconfigures stdout/stderr to UTF-8, which Windows
# consoles need before any exchange text (arrows, dashes) is printed.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gm_common  # noqa: E402,F401  (imported for its UTF-8 stream setup)

import glob  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from collections import defaultdict  # noqa: E402

# Noise that survives the structural filter: hook injections, local command
# plumbing, and system reminders that are not conversation.
NOISE = re.compile(
    r"^\s*(<local-command-stdout>|<local-command-name>|<command-name>|<command-message>"
    r"|<command-args>|<system-reminder>|Caveat: The messages below|\[Request interrupted"
    r"|UserPromptSubmit hook additional context:)",
    re.I,
)
MIN_CHARS = 40          # a turn shorter than this carries no retrievable content
MIN_EXCHANGE_CHARS = 200  # an exchange below this is not worth a node

# How long a transcript must have gone unmodified before its session counts as
# finished, so its last exchange can be filed. A live session writes to its
# transcript on every turn and every tool call, so half an hour of silence means
# nobody is adding to that exchange. Too short a window files an exchange that
# is still growing, which is what jams Graphify's shrink guard; too long only
# delays filing the last exchange of a session that really did end.
OPEN_EXCHANGE_QUIET_SECONDS = 30 * 60

# What the exchanges directory must hide from `mempalace mine`: Graphify's own
# output and the resolver's lookup table.
GITIGNORE_LINES = ("graphify-out/", ".graphify_*", "_index.jsonl")


def blocks(msg: dict | None):
    """Yield the text blocks of one transcript message, skipping every other type."""
    c = (msg or {}).get("content")
    if isinstance(c, str):
        yield c
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                yield b.get("text", "")


def clean(t: str | None) -> str | None:
    """Return the trimmed turn text, or None when it is too short or is noise."""
    t = (t or "").strip()
    if not t or len(t) < MIN_CHARS:
        return None
    if NOISE.match(t):
        return None
    return t


def harvest(path: str) -> list[tuple[str, str, str]]:
    """Read one .jsonl transcript. Returns (timestamp, role, text) for real turns only.

    One unreadable file costs one line on stderr and the turns read before the
    error, never the run: `_index.jsonl` is written last, so an abort here would
    leave the directory full of exchange files with no index for the resolver to
    look them up in. A transcript can be locked by the session still writing it,
    or truncated mid-write.
    """
    turns: list[tuple[str, str, str]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") not in ("user", "assistant"):
                    continue
                if o.get("isMeta"):
                    continue
                # tool results are replayed as user turns - drop them
                if o.get("toolUseResult") is not None:
                    continue
                # Compaction summaries restate turns we already keep verbatim.
                if o.get("isCompactSummary"):
                    continue
                # Synthetic error rows carry no content and all-zero usage.
                if o.get("isApiErrorMessage"):
                    continue
                # Subagent turns. The main transcript carries each subagent's
                # result as a task-notification turn, and a sidechain row that
                # lands late renumbers every exchange after it.
                if o.get("isSidechain"):
                    continue
                msg = o.get("message") or {}
                if msg.get("model") == "<synthetic>":
                    continue
                role = "USER" if o["type"] == "user" else "CLAUDE"
                ts = (o.get("timestamp") or "")[:19].replace("T", " ")
                for txt in blocks(msg):
                    c = clean(txt)
                    if c:
                        turns.append((ts, role, c))
    except Exception as exc:
        print(f"filter: could not read {path}: {exc}", file=sys.stderr)
    return turns


def to_exchanges(turns: list[tuple[str, str, str]]) -> list[list[tuple[str, str, str]]]:
    """Group turns into [user turn + the assistant turns answering it]."""
    out: list[list[tuple[str, str, str]]] = []
    cur: list[tuple[str, str, str]] = []
    for t in turns:
        role = t[1]
        if role.startswith("USER") and cur:
            out.append(cur)
            cur = [t]
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def ensure_gitignore(out_dir: str) -> None:
    """Make sure out_dir/.gitignore carries every line in GITIGNORE_LINES.

    Graphify writes its graph into graphify-out/ inside this very directory, and
    MemPalace would then mine the graph as if it were dialogue - 11k+ drawers of
    pure noise. `mine` honours a .gitignore even with no git repo present, so one
    lives here. Without it the two tools feed each other.

    An existing file is kept and only the missing lines are appended, so a
    directory written before `_index.jsonl` joined the list picks it up on the
    next compound instead of staying one line short for ever.
    """
    gi = os.path.join(out_dir, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"{line}\n" for line in GITIGNORE_LINES))
        return
    try:
        with open(gi, encoding="utf-8", errors="replace") as f:
            existing = f.read()
    except OSError as exc:
        print(f"filter: could not read {gi}: {exc}", file=sys.stderr)
        return
    have = {line.strip() for line in existing.splitlines()}
    missing = [line for line in GITIGNORE_LINES if line not in have]
    if not missing:
        return
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with open(gi, "a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + "".join(f"{line}\n" for line in missing))


def write_if_changed(path: str, text: str) -> bool:
    """Write text to path unless the file already holds exactly these bytes.

    Returns True when the file was written. An unchanged file keeps its mtime,
    so nothing downstream sees it as touched. The new bytes go to a temporary
    file beside the target and are moved over it in one step, so a reader (the
    resolver, Graphify, the mine) never sees a half-written exchange.
    """
    data = text.encode("utf-8")
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass  # missing or unreadable: write it
    # A dot name, so a temp file left by a crash is not taken for an exchange.
    # open() rather than mkstemp: the file then gets the usual umask mode, as
    # the exchange files always have, not mkstemp's owner-only 0600.
    tmp = os.path.join(os.path.dirname(path) or ".",
                       f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        # Windows refuses to replace a file another process has open. Writing
        # in place is what this script always did, so fall back to that rather
        # than fail the run.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        with open(path, "wb") as f:
            f.write(data)
    return True


def read_index(out_dir: str) -> dict[str, dict]:
    """id -> row from the _index.jsonl an earlier run wrote. Empty when absent.

    Read before this run replaces it, so a held-back exchange whose file an
    earlier run wrote can keep the row that describes that file exactly.
    """
    rows: dict[str, dict] = {}
    try:
        with open(os.path.join(out_dir, "_index.jsonl"), encoding="utf-8",
                  errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    rows[row["id"]] = row
    except OSError:
        pass
    return rows


def turns_in_file(path: str) -> int | None:
    """The `turns:` count from an exchange file's frontmatter, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("turns: "):
                    return int(line[len("turns: "):].strip())
                if line.startswith("# "):
                    break  # past the frontmatter
    except (OSError, ValueError):
        pass
    return None


def session_is_live(path: str, now: float) -> bool:
    """True when the transcript was modified within the quiet window.

    A transcript whose mtime cannot be read counts as live: holding an
    exchange back costs one run of delay, while filing one that is still
    growing can jam the chat graph.
    """
    try:
        return now - os.path.getmtime(path) < OPEN_EXCHANGE_QUIET_SECONDS
    except OSError:
        return True


def render(eid: str, sess: str, day: str, topic: str,
           ex: list[tuple[str, str, str]]) -> str:
    """The markdown for one exchange: frontmatter, a title, then each turn."""
    doc = [
        "---",
        f"id: {eid}",
        f"session: {sess}",
        f"date: {day}",
        f"started: {ex[0][0]}",
        f"turns: {len(ex)}",
        f"topic: {json.dumps(topic)}",
        "---",
        "",
        f"# Exchange {eid}",
        "",
    ]
    for ts, role, txt in ex:
        doc += [f"## {role} — {ts}", "", txt, ""]
    return "\n".join(doc)


def run(transcript_dir: str, out_dir: str, now: float | None = None) -> int:
    """Turn every transcript under transcript_dir into exchange files under out_dir.

    `now` is the clock the quiet window is measured against; tests pass it so
    they need not sleep. It defaults to the real time.
    """
    now = time.time() if now is None else now
    os.makedirs(out_dir, exist_ok=True)
    ensure_gitignore(out_dir)
    previous_index = read_index(out_dir)

    # Top-level session transcripts only. Subagent transcripts under
    # <transcript-dir>/<session>/ are deliberately left out: they are written
    # after the exchange they belong to, so merging them in and sorting by
    # timestamp renumbered every later exchange in the session, and an id that
    # renumbers is not a join key. Their results are in the main transcript.
    files = sorted(glob.glob(os.path.join(transcript_dir, "*.jsonl")))

    by_session: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    live: dict[str, bool] = {}
    for fp in files:
        sess = os.path.splitext(os.path.basename(fp))[0]
        by_session[sess].extend(harvest(fp))
        live[sess] = session_is_live(fp, now)

    indexed = changed = held_back = kept_turns = kept_bytes = 0
    index = []
    for sess, turns in sorted(by_session.items()):
        if not turns:
            continue
        turns.sort(key=lambda x: x[0])
        s8 = sess[:8]
        exchanges = to_exchanges(turns)
        for n, ex in enumerate(exchanges, 1):
            eid = f"{s8}-{n:04d}"
            day = ex[0][0][:10] or "unknown"
            first_user = next((t[2] for t in ex if t[1].startswith("USER")), ex[0][2])
            topic = " ".join(first_user.split())[:120]
            fn = os.path.join(out_dir, f"{day}-{eid}.md")

            # The last exchange of a live session is still growing. Its size is
            # not final either, so this check comes before the size floor: a
            # short last exchange is still the open one, and the one before it
            # is final.
            if n == len(exchanges) and live.get(sess, True):
                held_back += 1
                if not os.path.exists(fn):
                    continue  # never filed, so nothing to index yet
                # Filed by an earlier run: leave the file alone and keep the row
                # describing it, so the resolver can still find it. Prefer that
                # run's own row; failing that, the transcript is append-only, so
                # the file holds this exchange's first `turns:` turns.
                row = previous_index.get(eid)
                if not row or row.get("file") != os.path.basename(fn):
                    k = turns_in_file(fn)
                    part = ex[:k] if k and 0 < k <= len(ex) else ex
                    row = {"id": eid, "session": sess, "date": day,
                           "file": os.path.basename(fn), "turns": len(part),
                           "chars": sum(len(t[2]) for t in part), "topic": topic}
                index.append(row)
                indexed += 1
                kept_turns += int(row.get("turns") or 0)
                try:
                    kept_bytes += os.path.getsize(fn)
                except OSError:
                    pass  # only feeds the size line; never worth the run
                continue

            body_chars = sum(len(t[2]) for t in ex)
            if body_chars < MIN_EXCHANGE_CHARS:
                continue
            out = render(eid, sess, day, topic, ex)
            if write_if_changed(fn, out):
                changed += 1
            index.append({"id": eid, "session": sess, "date": day,
                          "file": os.path.basename(fn), "turns": len(ex),
                          "chars": body_chars, "topic": topic})
            indexed += 1
            kept_turns += len(ex)
            kept_bytes += len(out)

    # The index is the resolver's lookup table: id -> file, and back. It is
    # written last on every run, even when unchanged, because its presence is
    # how session_start tells "a filter pass finished" from "never ingested".
    with open(os.path.join(out_dir, "_index.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for row in index:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    sessions = len(by_session)
    print(f"sessions         : {sessions}")
    print(f"exchanges written: {indexed}")
    print(f"files changed    : {changed}")
    print(f"held back        : {held_back} open exchange(s)")
    print(f"turns kept       : {kept_turns}")
    print(f"dialogue size    : {kept_bytes/1e6:.2f} MB")
    print(f"index            : {os.path.join(out_dir, '_index.jsonl')}")
    # The one line other scripts parse. Keep its shape.
    print(f"filter: {indexed} exchanges, {kept_turns} turns, {sessions} sessions -> {out_dir}")
    return 0


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    """Parse the two positional arguments and run. 2 means the arguments were wrong.

    `now` is passed through to run() for tests; the command line never sets it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return run(args[0], args[1], now=now)


if __name__ == "__main__":
    sys.exit(main())
