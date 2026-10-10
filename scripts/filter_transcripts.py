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


def run(transcript_dir: str, out_dir: str) -> int:
    """Turn every transcript under transcript_dir into exchange files under out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    ensure_gitignore(out_dir)

    # Top-level session transcripts only. Subagent transcripts under
    # <transcript-dir>/<session>/ are deliberately left out: they are written
    # after the exchange they belong to, so merging them in and sorting by
    # timestamp renumbered every later exchange in the session, and an id that
    # renumbers is not a join key. Their results are in the main transcript.
    files = sorted(glob.glob(os.path.join(transcript_dir, "*.jsonl")))

    by_session: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for fp in files:
        sess = os.path.splitext(os.path.basename(fp))[0]
        by_session[sess].extend(harvest(fp))

    written = kept_turns = kept_bytes = 0
    index = []
    for sess, turns in sorted(by_session.items()):
        if not turns:
            continue
        turns.sort(key=lambda x: x[0])
        s8 = sess[:8]
        for n, ex in enumerate(to_exchanges(turns), 1):
            body_chars = sum(len(t[2]) for t in ex)
            if body_chars < MIN_EXCHANGE_CHARS:
                continue
            eid = f"{s8}-{n:04d}"
            start = ex[0][0]
            day = start[:10] or "unknown"
            first_user = next((t[2] for t in ex if t[1].startswith("USER")), ex[0][2])
            topic = " ".join(first_user.split())[:120]

            doc = [
                "---",
                f"id: {eid}",
                f"session: {sess}",
                f"date: {day}",
                f"started: {start}",
                f"turns: {len(ex)}",
                f"topic: {json.dumps(topic)}",
                "---",
                "",
                f"# Exchange {eid}",
                "",
            ]
            for ts, role, txt in ex:
                doc += [f"## {role} — {ts}", "", txt, ""]
            out = "\n".join(doc)

            fn = os.path.join(out_dir, f"{day}-{eid}.md")
            with open(fn, "w", encoding="utf-8", newline="\n") as f:
                f.write(out)
            index.append({"id": eid, "session": sess, "date": day,
                          "file": os.path.basename(fn), "turns": len(ex),
                          "chars": body_chars, "topic": topic})
            written += 1
            kept_turns += len(ex)
            kept_bytes += len(out)

    # The index is the resolver's lookup table: id -> file, and back.
    with open(os.path.join(out_dir, "_index.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for row in index:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    sessions = len(by_session)
    print(f"sessions         : {sessions}")
    print(f"exchanges written: {written}")
    print(f"turns kept       : {kept_turns}")
    print(f"dialogue size    : {kept_bytes/1e6:.2f} MB")
    print(f"index            : {os.path.join(out_dir, '_index.jsonl')}")
    # The one line other scripts parse. Keep its shape.
    print(f"filter: {written} exchanges, {kept_turns} turns, {sessions} sessions -> {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the two positional arguments and run. 2 means the arguments were wrong."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return run(args[0], args[1])


if __name__ == "__main__":
    sys.exit(main())
