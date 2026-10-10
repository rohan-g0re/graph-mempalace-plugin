# Knowledge base bootstrap — MemPalace + Graphify, one file

*Written for a coding agent (Claude Code) standing this up on a new machine or a new
project. Built 2026-10-07 from the working setup in `statusneo_assignment`; every
embedded file is a byte-for-byte copy of what runs there.*

**Scope note, 2026-10-10.** This file is a dated snapshot and is left as it was
written, so following it builds one MemPalace palace shared by every project. From
plugin version 0.2.0 the plugin instead gives each project its own palace, and every
`mempalace` call it makes carries the global `--palace <path>` flag before the
subcommand, with `MEMPALACE_PALACE_PATH` set for any process that cannot take the
flag. The warnings further down against passing `--palace` were recorded against the
MCP servers on MemPalace 3.10.0, not against the CLI. Stand this up and later
install the plugin and you will have two palace layouts; `docs/configuration.md`
explains the current one.

---

## 0. How to use this file

This is the whole knowledge-base setup in one file: the design, the install, every
script in full, every config block, the exact commands in order, and the checks that
prove it works. Follow it top to bottom. Stop at any check that fails and fix that
before going on; nothing later works if an earlier check is red.

What you are building, in one paragraph: two Graphify graphs for *navigation* —
Graph A over the code, Graph B over the chat history — and two *ground-truth* stores
behind them: the repository itself for code (`file:line`) and MemPalace for chat
(one drawer set per exchange, keyed by a stable exchange id). The graph decides what
to look at; the store supplies the words. **Never answer from a graph node; always
from the exchange or the file.** Code is not copied into MemPalace. Compounding is
automatic: a gated `Stop` hook re-ingests every 100k tokens of session growth.

### Placeholders

Angle-bracket, ALL-CAPS names are values you compute once and substitute everywhere
— **including inside the embedded files** — before writing anything. Lower-case
angle-bracket words such as `<q>` or `<exchange-id>` are ordinary documentation and
stay as they are. Use forward slashes in every path, even on Windows.

| placeholder | meaning | how to get it |
|---|---|---|
| `<HOME>` | the user's home directory | `python -c "import os;print(os.path.expanduser('~').replace(chr(92),'/'))"` |
| `<PROJECT_DIR>` | repository root | `pwd` from inside the repo |
| `<PROJECT_TOKEN>` | lower-case directory name, only `[a-z0-9._-]` | `python -c "import os;b=os.path.basename(os.getcwd()).lower();print(''.join(c for c in b if c.isalnum() or c in '._-'))"` |
| `<SLUG>` | the folder name Claude Code uses for this project under `~/.claude/projects/` | `python -c "import os,re;p=os.path.abspath('.');p=(p[0].lower()+p[1:]) if p[1:2]==':' else p;print(re.sub(r'[^A-Za-z0-9]','-',p))"` — then confirm `ls <HOME>/.claude/projects/<SLUG>` exists |
| `<TRANSCRIPTS>` | where Claude Code writes this project's `.jsonl` transcripts | `<HOME>/.claude/projects/<SLUG>` |
| `<DIALOGUE>` | where the filtered exchanges and Graph B live | `<HOME>/.mempalace/dialogue/<SLUG>` |
| `<SCRIPTS>` | where the three scripts live | `<HOME>/.claude/skills/kb-setup/scripts` |
| `<BIN>` | where `uv tool install` puts executables | `uv tool dir --bin` — normally `<HOME>/.local/bin`. On Windows the files end in `.exe`; on macOS/Linux drop `.exe` wherever it appears below |
| `<PY_GATE>` | the Python interpreter inside the `mempalace` tool venv (hooks must not depend on PATH) | Windows: `<HOME>/AppData/Roaming/uv/tools/mempalace/Scripts/python.exe`; macOS/Linux: `<HOME>/.local/share/uv/tools/mempalace/bin/python`. `uv tool dir` prints the parent |

Worked example, the machine this was built on: `<HOME>` = `C:/Users/rohan`,
`<PROJECT_DIR>` = `C:/Users/rohan/Desktop/Work/STUFF/Projects/statusneo_assignment`,
`<PROJECT_TOKEN>` = `statusneo_assignment`,
`<SLUG>` = `c--Users-rohan-Desktop-Work-STUFF-Projects-statusneo-assignment`.

### Writing the embedded files

Part 4 holds every file. Each starts with a heading of the form `### FILE: `path``
(write the file, creating directories) or `### MERGE INTO: `path`` / `### APPEND TO:
`path`` (the target already exists in most projects — merge the keys or append the
block by hand). Each file sits between a line of four backticks plus a language tag
and a closing line of four backticks.

Either write them by hand, or save this snippet as `extract.py`, fill the dict,
and run `python extract.py KB_SETUP.md` — it writes every `FILE:` block with the
placeholders substituted and lists the `MERGE INTO` / `APPEND TO` blocks for you to
do by hand:

```python
import os, re, sys
VALUES = {  # fill in from the table above; forward slashes
    "<HOME>": "", "<PROJECT_DIR>": "", "<PROJECT_TOKEN>": "", "<SLUG>": "",
    "<TRANSCRIPTS>": "", "<DIALOGUE>": "", "<SCRIPTS>": "", "<BIN>": "", "<PY_GATE>": "",
}
doc = open(sys.argv[1], encoding="utf-8").read()
blocks = re.findall(r"^### (FILE|MERGE INTO|APPEND TO): `([^`]+)`\n\n````\w*\n(.*?)\n````\n",
                    doc, re.M | re.S)
for mode, path, body in blocks:
    for k, v in VALUES.items():
        path, body = path.replace(k, v), body.replace(k, v)
    if mode != "FILE":
        print(mode, "->", path, "(do by hand)"); continue
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8", newline="\n").write(body + "\n")
    print("wrote", path)
```

### Order of work

1. Part 2 — platform check. On Windows-on-ARM this decides whether to continue at all.
2. Part 3 — install the two tools, patch Graphify, allow the permissions.
3. Part 4 — write the files.
4. Part 5 — read the ingestion rules; adapt the `.gitignore` block to the project.
5. Part 6 — stand it up, in order. Restart the Claude Code session when told to.
6. Part 7 — verify. The `ask` check at the end is the only one that proves anything.
7. Parts 8–11 — how to use, how it compounds, the MCP wiring, the traps. Read once.
8. Part 13 — where everything comes from (repos, docs, design references).

---

## 1. The idea in one page

Two domains. Each gets a **navigation layer** that decides *what to look at*,
and a **ground-truth layer** that supplies *what was actually said or written*.

| domain | navigation | ground truth |
|---|---|---|
| **code** | **Graph A** — Graphify over the repo | **the repository itself** (`file:line`) |
| **chat** | **Graph B** — Graphify over filtered dialogue | **MemPalace** (drawer, keyed by exchange id) |

Code needs no database because **it already has one**: the working tree is
always current, line-addressable, and never drifts. Chat has no such store,
which is the entire reason MemPalace exists.

This asymmetry is the single most important design decision here, and it took
us a while to see it. Mining code into MemPalace produces drawers that duplicate
the filesystem badly and go stale on the next edit, while looking authoritative.

**The graph never answers. It routes.** Every production system works this way —
Graphiti's `EntityEdge.episodes → EpisodicNode.content`, GraphRAG's
`Entity.text_unit_ids → TextUnit.text`, session-graph's
`devkg:extractedFrom → sioc:content`. The node carries enough to decide *is this
the thing* (a label, a community, a description); the back-pointer gets you the
words.

### The three components

```
                 question
                    │
         ┌──────────┴───────────┐
         ▼                      ▼
   ┌───────────┐          ┌───────────┐
   │  Graph A  │          │  Graph B  │      navigation
   │   code    │          │   chat    │
   └─────┬─────┘          └─────┬─────┘
         │ source_file          │ exchange id
         ▼                      ▼
   ┌───────────┐          ┌───────────┐
   │ the repo  │          │ MemPalace │      ground truth
   │ file:line │          │  drawers  │
   └───────────┘          └───────────┘
```

`kb_resolve.py` is the wiring: it runs the graph query, reads the back-pointer
off the returned nodes, and dereferences it.

### What lives where — and the one rule that follows

| layer | holds | used for |
|---|---|---|
| **graph node** | label, community, one-line description, `EXTRACTED`/`INFERRED` tags, and the `source_file` back-pointer | deciding *what to look at* — cheap, structured, low bloat |
| **MemPalace drawer** | the exchange, **verbatim** | the actual answer |
| **MemPalace search** | hybrid cosine + BM25 over drawers | finding an exchange when you do not know its id |

**Never answer from the node. Always answer from the exchange.** The node's
description is an LLM summary, and the 2026 memory survey's sharpest warning
applies to it directly: *"lossy consolidation can delete precise identifiers
while leaving summaries that still score well."* A summary that reads fluently
about a decision you later reversed is worse than no memory. The summary
routes; the verbatim text answers. This is the hybrid every production system
converges on, and it is also what the reference harness does one layer down —
compact summary resident, every original preserved in an index,
`context_tree_query` to fetch it on demand.

---

## 2. Platform feasibility — read first on any new machine

Works on **Windows on ARM**, but only one way.

`onnxruntime` publishes **no `win_arm64` wheel** on PyPI, and ChromaDB pulls it
transitively, so **native ARM64 Python cannot install the stack**. The fix is an
**x86_64 CPython** running under emulation. `uv` does this by default.

```bash
python -c "import sysconfig; print(sysconfig.get_platform())"   # want win-amd64
```

If that prints `win-arm64`, stop and rebuild the venv with uv. Do **not**
"upgrade" to native ARM64 Python — it feels right and it breaks ChromaDB.

Proof it works, after install:

```bash
python -c "import onnxruntime as o; print(o.get_available_providers())"
# ['AzureExecutionProvider', 'CPUExecutionProvider'] = the native lib loaded
```

Emulation costs speed, and the cost lands on the embedding pass, which is the
slowest part of mining.

---

## 3. Install

```bash
uv tool install mempalace
uv tool install "graphifyy[pdf,sql,mcp]"    # double-y; "graphify" is a different package
uv tool list
```

Install the extras up front:

- without `pdf`, PDF text extraction is skipped with a warning
- without `sql`, `.sql` files contribute nothing (`tree_sitter_sql not installed`)
- without `mcp`, `graphify-mcp` dies with `ModuleNotFoundError: No module named 'mcp'`

All three are warnings or startup failures, not errors, so a graph that looks
fine can be missing whole file types.

Binaries land in `~/.local/bin` — `mempalace`, `mempalace-mcp`,
`mempalace-light-mcp`, `graphify`, `graphify-mcp`. Put it on PATH.

**Expect a side effect:** the first `graphify` call auto-refreshes its own Claude
skill, backing up the previous `SKILL.md` to `SKILL.md.bak`. Set
`GRAPHIFY_NO_AUTO_REFRESH=1` to stop that.

### Permissions

Claude Code's auto mode classifies a freshly installed third-party tool reading
the whole codebase as **Untrusted Code Integration** and blocks it. Allowlist in
`.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(mempalace:*)",
      "Bash(graphify:*)",
      "Bash(<BIN>/mempalace.exe:*)",
      "Bash(<BIN>/graphify.exe:*)"
    ]
  }
}
```

Both forms: hooks call the `.exe` by absolute path, interactive use goes through PATH.


### Patch Graphify's Claude CLI call (one line, saves ~11 s per call)

Graphify drives extraction through `claude -p`. Every spawn re-loads the calling
project's MCP servers and fires its `SessionStart` hooks — measured at 19.1 s → 8.3 s
on a trivial prompt once stopped. Extraction needs no MCP tools. Save this as
`patch_graphify.py` and run it with the **graphifyy tool's own Python** (Windows:
`<HOME>/AppData/Roaming/uv/tools/graphifyy/Scripts/python.exe`; macOS/Linux:
`<HOME>/.local/share/uv/tools/graphifyy/bin/python`). It is idempotent. **Re-run it
after any `uv tool install --force` or upgrade — that wipes the patch silently.**

```python
import graphify, os, sys
p = os.path.join(os.path.dirname(graphify.__file__), "llm.py")
s = open(p, encoding="utf-8").read()
if "--strict-mcp-config" in s:
    print("already patched:", p); sys.exit(0)
anchor = '        "--no-session-persistence",\n'
assert s.count(anchor) == 1, "anchor not unique; patch by hand (see KB_SETUP Part 3)"
patch = anchor + ('        # kb-setup: skip the caller\'s MCP servers; opt out with GRAPHIFY_KEEP_MCP=1\n'
                  '        *([] if os.environ.get("GRAPHIFY_KEEP_MCP", "").strip() == "1"\n'
                  '          else ["--strict-mcp-config"]),\n')
open(p, "w", encoding="utf-8").write(s.replace(anchor, patch)); print("patched:", p)
```

The permissions file is in Part 4 (`MERGE INTO: <PROJECT_DIR>/.claude/settings.local.json`).

---

## 4. Files to create

Three scripts, one slash command, and five config blocks. The scripts are
generic — they derive every path from the working directory and the hook
payload — so they carry no placeholders. Write them exactly as shown.

### FILE: `<SCRIPTS>/filter_transcripts.py`

````python
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

Keeps: user text and assistant text.
Drops: tool_result, tool_use inputs, thinking, images, attachments, meta lines,
       and compaction summaries (lossy restatement of turns already kept).

Usage:
    python filter_transcripts.py <transcript-dir> <output-dir>

<transcript-dir> is ~/.claude/projects/<url-encoded-project-path>/
Afterwards:
    mempalace mine <output-dir> --mode projects --wing <proj>_dialogue
"""
import json, glob, os, sys, re
from collections import defaultdict

if len(sys.argv) < 3:
    sys.exit(__doc__.strip())
TD = sys.argv[1]
OUT = sys.argv[2]

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


def blocks(msg):
    c = (msg or {}).get("content")
    if isinstance(c, str):
        yield c
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                yield b.get("text", "")


def clean(t):
    t = (t or "").strip()
    if not t or len(t) < MIN_CHARS:
        return None
    if NOISE.match(t):
        return None
    return t


def harvest(path):
    """Yield (timestamp, role, text) for real dialogue turns only."""
    turns = []
    for line in open(path, encoding="utf-8", errors="replace"):
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
        msg = o.get("message") or {}
        if msg.get("model") == "<synthetic>":
            continue
        role = "USER" if o["type"] == "user" else "CLAUDE"
        if o.get("isSidechain"):
            role += " (subagent)"
        ts = (o.get("timestamp") or "")[:19].replace("T", " ")
        for txt in blocks(msg):
            c = clean(txt)
            if c:
                turns.append((ts, role, c))
    return turns


def to_exchanges(turns):
    """Group into [user turn + the assistant turns answering it]."""
    out, cur = [], []
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


def main():
    os.makedirs(OUT, exist_ok=True)
    # Graphify writes its graph into graphify-out/ inside this very directory,
    # and MemPalace would then mine the graph as if it were dialogue - 11k+
    # drawers of pure noise. `mine` honours a .gitignore even with no git repo
    # present, so drop one here. Without this the two tools feed each other.
    gi = os.path.join(OUT, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write("graphify-out/\n.graphify_*\n")

    files = sorted(glob.glob(os.path.join(TD, "*.jsonl")))
    # subagent transcripts live in per-session subdirectories
    files += sorted(glob.glob(os.path.join(TD, "*", "*.jsonl")))

    by_session = defaultdict(list)
    for fp in files:
        sess = os.path.basename(os.path.dirname(fp))
        if sess == os.path.basename(TD):
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

            fn = os.path.join(OUT, f"{day}-{eid}.md")
            with open(fn, "w", encoding="utf-8") as f:
                f.write(out)
            index.append({"id": eid, "session": sess, "date": day,
                          "file": os.path.basename(fn), "turns": len(ex),
                          "chars": body_chars, "topic": topic})
            written += 1
            kept_turns += len(ex)
            kept_bytes += len(out)

    # The index is the resolver's lookup table: id -> file, and back.
    with open(os.path.join(OUT, "_index.jsonl"), "w", encoding="utf-8") as f:
        for row in index:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"sessions         : {len(by_session)}")
    print(f"exchanges written: {written}")
    print(f"turns kept       : {kept_turns}")
    print(f"dialogue size    : {kept_bytes/1e6:.2f} MB")
    print(f"index            : {os.path.join(OUT, '_index.jsonl')}")
    print(f"output dir       : {OUT}")


if __name__ == "__main__":
    main()
````

### FILE: `<SCRIPTS>/compound_gate.py`

````python
"""Token-gated compounding hook for Claude Code -> MemPalace.

Why a gate: there is no "context reached N tokens" hook event. `Stop` fires
every turn (too often to mine) and `PreCompact` fires only at the auto-compact
threshold, which on a 1M window is roughly once a session. So `Stop` becomes a
cheap tick: read the last assistant turn's token usage, compare against a
stored watermark, and do nothing unless the session has grown by THRESHOLD
tokens since the last compound.

Keyed to GROWTH, not absolute context size, because compaction resets the
context count - an absolute mark would fire twice in a row and then never
again.

Usage (from .claude/settings.json):
    Stop:       compound_gate.py --harness claude-code
    PreCompact: compound_gate.py --harness claude-code --force

Reads the hook payload as JSON on stdin, always prints "{}" and exits 0.
Never blocks the turn: the actual compound runs detached.
"""
import json, os, sys, subprocess, time

THRESHOLD = 100_000  # new tokens since last compound

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".mempalace", "compound_watermark.json")
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
FILTER = os.path.join(SCRIPTS, "filter_transcripts.py")
LOG = os.path.join(HOME, ".mempalace", "compound.log")
# One compound at a time. Two chains writing the same palace and the same
# graphify-out/ collided on 2026-10-07 (PreCompact backstop vs a manual run).
LOCK = os.path.join(HOME, ".mempalace", "compound.lock")
LOCK_STALE_S = 3 * 3600  # a chain that died mid-way must not block forever


def bail():
    print("{}")
    sys.exit(0)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def compound_running():
    """True while another compound chain holds the lock (and it is fresh)."""
    try:
        age = time.time() - os.path.getmtime(LOCK)
    except OSError:
        return False
    if age > LOCK_STALE_S:
        try:
            os.remove(LOCK)
        except OSError:
            pass
        return False
    return True


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, STATE)


def last_usage_total(transcript_path, tail_bytes=400_000):
    """Total context tokens from the most recent assistant turn."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except Exception:
        return None
    best = None
    for raw in reversed(chunk.split(b"\n")):
        raw = raw.strip()
        if not raw or not raw.startswith(b"{"):
            continue
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if o.get("type") != "assistant":
            continue
        # Subagent turns report their own context, not the main session's.
        if o.get("isSidechain"):
            continue
        # Synthetic error rows ("No response requested.", model "<synthetic>")
        # carry ALL-ZERO usage. One at the tail would make this read 0 and
        # silently skip the gate. Nine of them exist in this project alone.
        if o.get("isApiErrorMessage"):
            continue
        msg = o.get("message") or {}
        if msg.get("model") == "<synthetic>":
            continue
        u = msg.get("usage") or {}
        # The three-field sum is byte-for-byte what Claude Code computes for
        # used_percentage (decompiled from the shipped bundle). Never
        # accumulate across rows: streamed blocks repeat the identical usage.
        total = (
            (u.get("input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0)
        )
        if total > 0:
            best = total
            break
    return best


def is_settled(transcript_path, tail_bytes=200_000):
    """Cheap, local, no-LLM: is the session at a natural boundary?

    We only want to compound work that has landed, not a position mid-argument
    that gets reversed two turns later. The signal: the most recent assistant
    turn ended with TEXT, not a tool_use. A turn that ended in a tool call is
    still inside a tool loop, and whatever it is saying is provisional.

    This is the cheapest possible version of what compact-adviser spends an
    API call deciding ("is the work unit finished"). Two alternatives are
    documented in docs/memory_system.md §7 if this proves too crude:
    a scheduled compound, or a compact-adviser-style judge.
    """
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except Exception:
        return True  # cannot tell; do not block compounding on a read error
    for raw in reversed(chunk.split(b"\n")):
        raw = raw.strip()
        if not raw or not raw.startswith(b"{"):
            continue
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        if o.get("isApiErrorMessage"):
            continue
        content = ((o.get("message") or {}).get("content")) or []
        if isinstance(content, str):
            return True
        kinds = [b.get("type") for b in content if isinstance(b, dict)]
        if not kinds:
            continue
        # settled if the final block is prose; unsettled if it is a tool call
        return kinds[-1] == "text"
    return True


def project_token(cwd):
    base = os.path.basename(os.path.normpath(cwd or os.getcwd()))
    return "".join(c for c in base.lower() if c.isalnum() or c in "._-") or "project"


def transcript_dir(transcript_path):
    """The per-project transcript directory holding all sessions."""
    d = os.path.dirname(os.path.abspath(transcript_path))
    # a sidechain transcript sits one level deeper, in <slug>/<session>/
    parent = os.path.dirname(d)
    if os.path.basename(parent).startswith("c--"):
        return parent
    return d


def mempalace_bin():
    """Absolute path to the mempalace CLI; hooks do not inherit an interactive PATH."""
    env = os.environ.get("MEMPALACE_BIN")
    if env and os.path.exists(env):
        return env
    for cand in (
        os.path.join(HOME, ".local", "bin", "mempalace.exe"),
        os.path.join(HOME, ".local", "bin", "mempalace"),
    ):
        if os.path.exists(cand):
            return cand
    return "mempalace"  # last resort: hope it is on PATH


def graphify_bin():
    env = os.environ.get("GRAPHIFY_BIN")
    if env and os.path.exists(env):
        return env
    for cand in (
        os.path.join(HOME, ".local", "bin", "graphify.exe"),
        os.path.join(HOME, ".local", "bin", "graphify"),
    ):
        if os.path.exists(cand):
            return cand
    return "graphify"


def spawn_compound(transcript_path, cwd):
    """Compound both domains: chat into the palace + Graph B, code into Graph A.

    Code is NOT mined into the palace. The repository is already the
    ground-truth store for code - always current, line-addressable - and
    drawers mined from it duplicate the filesystem badly and go stale on the
    next edit while looking authoritative. Graph A is the code's navigation
    layer; the back-pointer from a code node is file:line, resolved by reading
    the file. See docs/memory_system.md §1.

    Order matters: Graph B reads the filtered dialogue, so it follows the
    filter. Detached, so the turn never waits.
    """
    td = transcript_dir(transcript_path)
    out = os.path.join(HOME, ".mempalace", "dialogue", os.path.basename(td))
    tok = project_token(cwd)
    py = sys.executable
    mp = mempalace_bin()
    gf = graphify_bin()
    # No per-step shell redirect: a DETACHED_PROCESS child has no console, so
    # cmd.exe's `>>` silently discards the tools' output. Redirect at the
    # Popen level instead, by handing the child the log file handle directly.
    steps = [
        # 1. chat history -> one markdown file per EXCHANGE, stable ids, _index.jsonl
        f'"{py}" "{FILTER}" "{td}" "{out}"',
        # 2. exchanges -> palace (the ground-truth store for chat)
        f'"{mp}" mine "{out}" --mode projects --wing {tok}_dialogue',
        # 3. Graph A - code navigation. tree-sitter only, no LLM, no cost.
        f'cd /d "{cwd}" && "{gf}" update .',
        # 4. Graph B - chat navigation. Cached per file; only changed exchanges.
        # 2, not 4: four Claude processes under x64 emulation stalled at one
        # chunk per 25 min and starved the mine into MemoryError on 16 GB.
        # token budget 15000, not the default 60000: a crowded chunk makes the
        # model drop files silently (187 of 471 omitted at 60000, 18 of 80 at 15000).
        f'cd /d "{out}" && "{gf}" . --backend claude-cli --max-concurrency 2 --token-budget 15000',
    ]
    # `(a && b) & del lock` - the lock goes away even when a step fails,
    # otherwise one broken run would silence the gate for LOCK_STALE_S.
    cmd = "(" + " && ".join(steps) + f') & del /q "{LOCK}"'
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    # Graph B goes through the Claude Code CLI. Without these it falls back to
    # Opus at concurrency 1, which is ~3x slower for identical output.
    env = dict(os.environ)
    env.setdefault("GRAPHIFY_CLAUDE_CLI_MODEL", "sonnet")
    env.setdefault("GRAPHIFY_CLAUDE_CLI_PARALLEL", "1")
    env.setdefault("GRAPHIFY_NO_AUTO_REFRESH", "1")
    try:
        with open(LOG, "a", encoding="utf-8") as lf:
            lf.write(
                f"\n=== compound {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"project={tok} (exchanges -> palace, graph A, graph B) ===\n"
            )
        logf = open(LOG, "a", encoding="utf-8", errors="replace")
        with open(LOCK, "w", encoding="utf-8") as lk:
            lk.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
        subprocess.Popen(cmd, shell=True, creationflags=flags, env=env,
                         stdin=subprocess.DEVNULL,
                         stdout=logf,
                         stderr=subprocess.STDOUT)
        logf.close()  # the child holds its own duplicated handle
    except Exception:
        pass


def main():
    force = "--force" in sys.argv
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    tp = payload.get("transcript_path") or ""
    sid = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or os.getcwd()

    if not tp or not os.path.exists(tp):
        bail()

    state = load_state()
    seen = sid in state
    mark = (state.get(sid) or {}).get("tokens", 0)

    if force:
        # PreCompact backstop: compound whatever is there, settled or not,
        # because compaction is about to summarise it away from the window.
        current = last_usage_total(tp) or mark
    else:
        current = last_usage_total(tp)
        if current is None:
            bail()
        # A session the gate has never seen is not N tokens of NEW work - it is
        # an existing conversation that happens to be large. Seed the watermark
        # and wait for growth the gate itself observes. Without this, every
        # fresh session on the project fires a full compound on its first turn.
        if not seen:
            state[sid] = {"tokens": current,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "note": "seeded on first sight; no compound"}
            save_state(state)
            bail()
        if current < mark:
            # Compaction shrank the window below the watermark. PreCompact
            # already compounded everything before it, so count fresh from
            # here - otherwise "growth" is negative for the rest of the session.
            state[sid] = {"tokens": current,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "note": "re-seeded after compaction; no compound"}
            save_state(state)
            bail()
        if current < mark:
            # Compaction shrank the window below the watermark. PreCompact
            # already compounded everything before it, so count fresh from
            # here - otherwise "growth" is negative for the rest of the session.
            state[sid] = {"tokens": current,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "note": "re-seeded after compaction; no compound"}
            save_state(state)
            bail()
        if current - mark < THRESHOLD:
            bail()
        # Over threshold - but only compound a session at a natural boundary.
        # If the last assistant turn ended in a tool call we are mid-loop, and
        # the next turn will re-check. The watermark is deliberately NOT
        # advanced here, so the growth stays pending rather than being lost.
        if not is_settled(tp):
            bail()

    if compound_running():
        # Growth stays pending: the watermark is not advanced, so the next
        # settled turn after the running chain finishes will compound it.
        try:
            with open(LOG, "a", encoding="utf-8") as lf:
                lf.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} skipped: "
                         f"a compound is already running ({LOCK})\n")
        except Exception:
            pass
        bail()

    spawn_compound(tp, cwd)
    state[sid] = {"tokens": current, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_state(state)
    bail()


if __name__ == "__main__":
    main()
````

### FILE: `<SCRIPTS>/kb_resolve.py`

````python
"""kb_resolve - the wiring between the graphs and the stores.

The problem this solves
-----------------------
A Graphify node tells you WHAT is relevant. It does not tell you what was
actually said or written. Every production system solves this with a stored
back-pointer from the node to its source record:

    Graphiti   EntityEdge.episodes -> EpisodicNode.content
    GraphRAG   Entity.text_unit_ids -> TextUnit.text
    session-graph  devkg:extractedFrom -> message, sioc:content

We had the graphs and the stores but no pointer between them. This is it.

The architecture it implements
------------------------------
Two domains, each with a navigation layer and a ground-truth layer:

    code   Graph A  ->  file:line      ->  the repository    (always current)
    chat   Graph B  ->  exchange id    ->  the exchange file / MemPalace drawer

Code needs no database: the repo IS the store, and it never goes stale.
Chat has no readable store of its own, which is why MemPalace exists.

Resolution path
---------------
    question
      -> graphify query   (BFS over the graph; decides WHAT to look at)
      -> node.source_file (the stored back-pointer)
      -> _index.jsonl     (id -> file, for chat)
      -> verbatim text    (the exchange file, or the source file for code)

Usage
-----
    kb_resolve.py ask "<question>"        # chat graph, then verbatim exchanges
    kb_resolve.py ask "<question>" --code # code graph, then source files
    kb_resolve.py ask "<question>" --both
    kb_resolve.py get <exchange-id>       # dereference one id, e.g. b49fc7be-0007
    kb_resolve.py search "<text>"         # semantic search via MemPalace, ids back
    kb_resolve.py doctor                  # verify every link in the chain

Nothing here writes to a store. It is read-only by construction.
"""
import json, os, re, subprocess, sys, textwrap

# Windows consoles default to cp1252; exchange text is UTF-8 and full of arrows.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".local", "bin")


def _tool(name):
    """uv puts tools in ~/.local/bin; .exe on Windows, bare elsewhere. PATH as fallback."""
    for cand in (os.path.join(BIN, name + ".exe"), os.path.join(BIN, name)):
        if os.path.exists(cand):
            return cand
    return name


GRAPHIFY = _tool("graphify")
MEMPALACE = _tool("mempalace")


def project_token(cwd=None):
    base = os.path.basename(os.path.normpath(cwd or os.getcwd()))
    return "".join(c for c in base.lower() if c.isalnum() or c in "._-") or "project"


def slug_for(cwd=None):
    """The ~/.claude/projects directory name for a working directory."""
    p = os.path.abspath(cwd or os.getcwd())
    if p[1:2] == ":":  # Claude Code lower-cases the Windows drive letter
        p = p[0].lower() + p[1:]
    # one dash per non-alphanumeric character: C:/x -> c--x, /Users/x -> -Users-x
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def dialogue_dir(cwd=None):
    return os.path.join(HOME, ".mempalace", "dialogue", slug_for(cwd))


def graph_b(cwd=None):
    return os.path.join(dialogue_dir(cwd), "graphify-out", "graph.json")


def graph_a(cwd=None):
    return os.path.join(os.path.abspath(cwd or os.getcwd()), "graphify-out", "graph.json")


def load_index(cwd=None):
    """id -> row, from the filter's _index.jsonl."""
    idx = {}
    p = os.path.join(dialogue_dir(cwd), "_index.jsonl")
    if not os.path.exists(p):
        return idx
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        idx[row["id"]] = row
    return idx


def run(cmd, timeout=180):
    env = dict(os.environ)
    env.setdefault("GRAPHIFY_NO_AUTO_REFRESH", "1")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", env=env)
        return p.stdout or ""
    except Exception as e:
        return f"[kb_resolve] command failed: {e}\n"


# ---------------------------------------------------------------- id handling

EXCHANGE_ID = re.compile(r"\b([0-9a-f]{8}-\d{4})\b")


def ids_from_graph_output(text):
    """Exchange ids referenced by graphify's node lines (src=<file>)."""
    out = []
    for m in re.finditer(r"src=([^\s\]]+)", text):
        hit = EXCHANGE_ID.search(m.group(1))
        if hit and hit.group(1) not in out:
            out.append(hit.group(1))
    for m in EXCHANGE_ID.finditer(text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def files_from_graph_output(text):
    """Repo-relative source files referenced by code-graph node lines."""
    out = []
    for m in re.finditer(r"src=([^\s\]]+)", text):
        f = m.group(1)
        if EXCHANGE_ID.search(f):
            continue
        if f not in out:
            out.append(f)
    return out


# ------------------------------------------------------------------ commands

def cmd_get(exchange_id, cwd=None, chars=4000):
    idx = load_index(cwd)
    row = idx.get(exchange_id)
    if not row:
        print(f"no exchange {exchange_id} in the index "
              f"({os.path.join(dialogue_dir(cwd), '_index.jsonl')})")
        return 1
    path = os.path.join(dialogue_dir(cwd), row["file"])
    print(f"=== {exchange_id}  {row['date']}  turns={row['turns']} ===")
    print(f"file: {path}")
    print(f"topic: {row['topic']}\n")
    if os.path.exists(path):
        body = open(path, encoding="utf-8", errors="replace").read()
        print(body[:chars])
        if len(body) > chars:
            print(f"\n... [{len(body)-chars} more chars; open the file for the rest]")
    else:
        print("(file missing - re-run filter_transcripts.py)")
    return 0


def cmd_search(text, cwd=None, results=5):
    """Semantic search for when you do not know the id. Returns ids."""
    wing = f"{project_token(cwd)}_dialogue"
    out = run([MEMPALACE, "search", text, "--wing", wing, "--results", str(results)])
    out = "\n".join(l for l in out.splitlines()
                    if "EmbedderIdentity" not in l and "_enforce_embedder" not in l)
    print(out.strip() or "(no results - is the palace mined?)")
    ids = ids_from_graph_output(out)
    if ids:
        print("\nexchange ids: " + ", ".join(ids))
        print("dereference with:  kb_resolve.py get <id>")
    return 0


def cmd_ask(question, cwd=None, want_code=False, want_chat=True, budget=1200, snippet=1200):
    rc = 0
    if want_chat:
        gb = graph_b(cwd)
        print("=" * 72)
        print("CHAT  — Graph B decides what to look at, exchanges supply the words")
        print("=" * 72)
        if not os.path.exists(gb):
            print(f"no chat graph at {gb}")
        else:
            out = run([GRAPHIFY, "query", question, "--budget", str(budget), "--graph", gb])
            out = "\n".join(l for l in out.splitlines() if not l.startswith("  warning: skill"))
            print(out.strip())
            idx = load_index(cwd)
            ids = [i for i in ids_from_graph_output(out) if i in idx]
            if ids:
                print("\n" + "-" * 72)
                print(f"VERBATIM SOURCE — {len(ids)} exchange(s) behind those nodes")
                print("-" * 72)
                for eid in ids[:4]:
                    row = idx[eid]
                    path = os.path.join(dialogue_dir(cwd), row["file"])
                    print(f"\n### {eid}  ({row['date']})  {row['topic'][:80]}")
                    if os.path.exists(path):
                        body = open(path, encoding="utf-8", errors="replace").read()
                        body = body.split("---", 2)[-1].strip()
                        print(textwrap.shorten(body.replace("\n\n", "\n"),
                                               width=snippet, placeholder=" …"))
                    print(f"    full: kb_resolve.py get {eid}")
            else:
                print("\n(no exchange ids on those nodes — Graph B may predate "
                      "per-exchange filtering; rebuild it to get sharp pointers)")
    if want_code:
        ga = graph_a(cwd)
        print("\n" + "=" * 72)
        print("CODE  — Graph A decides what to look at, the repo is the source")
        print("=" * 72)
        if not os.path.exists(ga):
            print(f"no code graph at {ga}")
        else:
            out = run([GRAPHIFY, "query", question, "--budget", str(budget), "--graph", ga])
            out = "\n".join(l for l in out.splitlines() if not l.startswith("  warning: skill"))
            print(out.strip())
            files = files_from_graph_output(out)[:8]
            if files:
                print("\n" + "-" * 72)
                print("READ THESE — the repo is current; the palace is not")
                print("-" * 72)
                for f in files:
                    print(f"  {f}")
    return rc


def cmd_doctor(cwd=None):
    """Verify every link in the chain, and say which one is broken."""
    ok = True
    print("=" * 72)
    print("kb doctor")
    print("=" * 72)

    def chk(label, cond, detail=""):
        nonlocal ok
        print(f"  [{'OK ' if cond else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
        if not cond:
            ok = False
        return cond

    dd = dialogue_dir(cwd)
    chk("dialogue dir exists", os.path.isdir(dd), dd)
    idx = load_index(cwd)
    chk("exchange index present", bool(idx), f"{len(idx)} exchanges")
    chk("dialogue .gitignore (stops graph self-ingestion)",
        os.path.exists(os.path.join(dd, ".gitignore")))

    ga, gb = graph_a(cwd), graph_b(cwd)
    chk("Graph A (code) exists", os.path.exists(ga), ga)
    chk("Graph B (chat) exists", os.path.exists(gb), gb)

    linked = 0
    if os.path.exists(gb):
        try:
            g = json.load(open(gb, encoding="utf-8"))
            nodes = g.get("nodes", [])
            srcs = {str(n.get("source_file") or "") for n in nodes}
            linked = sum(1 for s in srcs if EXCHANGE_ID.search(s))
            chk("Graph B nodes carry exchange ids", linked > 0,
                f"{linked} of {len(srcs)} distinct sources")
            bad = [s for s in srcs if "graphify-out" in s]
            chk("Graph B free of self-ingestion", not bad, f"{len(bad)} bad sources")
        except Exception as e:
            chk("Graph B parses", False, str(e)[:80])

    out = run([MEMPALACE, "status"])
    wings = re.findall(r"WING:\s+(\S+)", out)
    chk("palace reachable", bool(wings), ", ".join(wings) or "no wings")
    chk("dialogue wing present",
        any(w.endswith("_dialogue") for w in wings))

    print()
    if linked == 0 and idx:
        print("  NEXT: Graph B predates the per-exchange ids. Rebuild it:")
        print(f"    graphify {dd} --backend claude-cli --max-concurrency 4")
    print("  verdict:", "all links resolve" if ok else "chain is broken above")
    return 0 if ok else 1


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip())
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "get" and rest:
        return cmd_get(rest[0])
    if cmd == "search" and rest:
        return cmd_search(" ".join(a for a in rest if not a.startswith("--")))
    if cmd == "ask" and rest:
        q = " ".join(a for a in rest if not a.startswith("--"))
        code = "--code" in rest or "--both" in rest
        chat = "--code" not in rest or "--both" in rest
        return cmd_ask(q, want_code=code, want_chat=chat)
    if cmd == "doctor":
        return cmd_doctor()
    sys.exit(__doc__.strip())


if __name__ == "__main__":
    sys.exit(main() or 0)
````

### FILE: `<PROJECT_DIR>/.claude/commands/kb-compound.md`

````markdown
---
name: kb-compound
description: Compound this project into the knowledge base - filter chat into exchanges, mine them into MemPalace, refresh the code graph and rebuild the chat graph. Same steps the automatic 100k-token gate runs.
argument-hint: "[--no-graph-b]"
allowed-tools: Bash, Read
---

# /kb-compound

Bring the knowledge base up to date. The architecture (docs/KB_SETUP.md Part 1;
long form in docs/memory_system.md, section 1):

| domain | navigation | ground truth |
|---|---|---|
| code | Graph A | the repository, `file:line` |
| chat | Graph B | MemPalace drawers, keyed by exchange id |

**Code is not mined into the palace.** The repo is already the store for code
and never goes stale; drawers mined from it would. So a compound is four steps,
and the automatic gate runs the identical four, so "I compounded" means one
thing regardless of who triggered it.

`$ARGUMENTS` — pass `--no-graph-b` to skip the chat graph rebuild. It is the
only step that costs LLM calls; everything else is free.

## Steps — run in order, report what each changed

### 0. One compound at a time

```bash
test -f ~/.mempalace/compound.lock && echo "RUNNING since $(cat ~/.mempalace/compound.lock)" || date +%Y-%m-%dT%H:%M:%S > ~/.mempalace/compound.lock
```

If it prints `RUNNING`, the automatic gate (or another manual run) is already
compounding — wait for it; do not start a second chain. Two chains write the
same palace and the same `graphify-out/`, and on 2026-10-07 that collision
starved the mine into `MemoryError`. A lock older than 3 hours is stale —
delete it and proceed.

### 1. Chat → one file per exchange (free, no LLM)

```bash
python "<SCRIPTS>/filter_transcripts.py" \
  "<TRANSCRIPTS>" \
  "<DIALOGUE>"
```

One file per user-turn-plus-replies, stable id `<session8>-<NNNN>` in filename
and frontmatter, `_index.jsonl` mapping id → file. Drops tool results, tool
calls, thinking, attachments, metadata, compaction summaries and synthetic
rows — ~91% of bytes, none of the meaning. Also writes the `.gitignore` that
stops MemPalace eating Graph B's output.

### 2. Exchanges → MemPalace (free, rule-based)

```bash
mempalace mine "<DIALOGUE>" \
  --mode projects --wing <PROJECT_TOKEN>_dialogue
```

Incremental — already-filed files are skipped. The wing name is load-bearing:
the gate derives `<PROJECT_TOKEN>_dialogue` from the directory name, and a
mismatch creates a parallel wing that cannot be deleted.

### 3. Graph A — the code graph (free, tree-sitter)

```bash
graphify update .
graphify cluster-only . --backend claude-cli --batch-size 100 --max-concurrency 2
```

`update` re-extracts changed code. `cluster-only` renames communities and
rewrites `GRAPH_REPORT.md`; labelling batches 100 per call, so it is cheap.

### 4. Graph B — the chat graph (skip only with `--no-graph-b`)

```bash
export GRAPHIFY_CLAUDE_CLI_MODEL=sonnet
export GRAPHIFY_CLAUDE_CLI_PARALLEL=1
cd "<DIALOGUE>"
graphify . --backend claude-cli --max-concurrency 2 --token-budget 15000
graphify cluster-only . --backend claude-cli --batch-size 100 --max-concurrency 2
```

Cached per file, so only new or changed exchanges are extracted. Sonnet, not
Haiku — Haiku silently omitted 6 of 15 files on the first build. Concurrency
2, not 4 — four Claude processes under x64 emulation stalled at one chunk per
25 minutes on this 16 GB machine. If Graphify warns `N file(s) produced no
nodes`, run the same command again; it retries only those. Keep `--token-budget
15000`: at the default 60000 a chunk holds dozens of exchanges and the model
silently drops some (187 of 471 on the first pass); at 15000 the same files
took the same time per file and 18 of 80 were dropped.

### 5. Verify the chain

```bash
python "<SCRIPTS>/kb_resolve.py" doctor
```

Every link must report OK — in particular `Graph B nodes carry exchange ids`.

### 6. Release the lock

```bash
rm -f ~/.mempalace/compound.lock
```

### 7. Report

- exchanges written, drawers added, new palace total from `mempalace status`
- **drawers-per-file** for the dialogue room — healthy is roughly 5–15 per
  exchange; an outlier means something non-dialogue got in
- node / edge / community counts for each graph rebuilt
- any file Graphify warned "produced no nodes" — those need a re-run
- any node flagged `verification=unverified` — a claim, not a fact
- the `doctor` verdict

<critical_requirement>
Never mine the repository into MemPalace. The repo is the ground-truth store
for code; a palace copy goes stale on the next edit while looking authoritative.

Never run `mempalace compress` — lossy, ~12 points of retrieval accuracy.

Never point MemPalace or Graphify at `docs/knowledge_graph.jsonl`; it is the
agent layer's grounding config, not a knowledge base.
</critical_requirement>
````

The next three are JSON merges: add these keys to whatever the file already
holds (`.mcp.json` usually has other servers; `settings.json` may already
have hooks). **Do not wire MemPalace's own `stop` / `session-end` /
`precompact` hooks** — Part 9 explains the deadlock.

### MERGE INTO: `<PROJECT_DIR>/.claude/settings.json`

````json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<BIN>/mempalace.exe hook run --hook session-start --harness claude-code"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<PY_GATE> <SCRIPTS>/compound_gate.py --harness claude-code"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<PY_GATE> <SCRIPTS>/compound_gate.py --harness claude-code --force"
          }
        ]
      }
    ]
  }
}
````

### MERGE INTO: `<PROJECT_DIR>/.claude/settings.local.json`

````json
{
  "permissions": {
    "allow": [
      "Bash(mempalace:*)",
      "Bash(graphify:*)",
      "Bash(<BIN>/mempalace.exe:*)",
      "Bash(<BIN>/graphify.exe:*)",
      "Bash(uv tool:*)",
      "Bash(uv pip:*)"
    ]
  }
}
````

### MERGE INTO: `<PROJECT_DIR>/.mcp.json`

````json
{
  "mcpServers": {
    "mempalace": {
      "command": "<BIN>/mempalace-mcp.exe",
      "args": []
    },
    "graphify": {
      "command": "<BIN>/graphify-mcp.exe",
      "args": []
    }
  }
}
````

### APPEND TO: `<PROJECT_DIR>/.gitignore`

````gitignore
# --- knowledge-base ingestion rules (MemPalace / Graphify) ---
# `mempalace mine` honours .gitignore and `mempalace sync` prunes drawers whose
# source became ignored. The repo itself is NOT mined (Part 1), so these rules
# exist for Graphify and for anyone who mines it anyway. Adapt the project-specific
# lines; keep graphify-out/ and the MemPalace per-project files no matter what.
mempalace.yaml
entities.json
graphify-out/
**/__pycache__/
# project-specific examples from the reference project — replace with yours:
# data/dumps/
# data/generated/
# data/*.sqlite*
# tests/fixtures/
````

The CLAUDE.md block is what makes the agent *use* the knowledge base: consult
before answering about past work, never answer from a node, how to write
back. Append it as a section.

### APPEND TO: `<PROJECT_DIR>/CLAUDE.md`

````markdown
Knowledge base -- MemPalace + Graphify

Full documentation: docs/KB_SETUP.md. Consult the knowledge base BEFORE
answering about past work or decisions.

Two domains, each with a navigation layer and a ground-truth layer:

    code   Graph A  (graphify-out/graph.json)        -> the repo, file:line
    chat   Graph B  (<DIALOGUE>/   -> MemPalace drawer,
                     graphify-out/graph.json)            keyed by exchange id

The graph ROUTES; it never answers. A node carries a label, a community, a
one-line description, EXTRACTED/INFERRED/AMBIGUOUS edge tags and a
`source_file` back-pointer. For code that pointer is a file -- read the file,
the repo is always current. For chat it is an exchange id like
`b49fc7be-0007` -- dereference it to the verbatim exchange. NEVER answer from
a node's description; ALWAYS from the exchange. Nodes marked
`verification=unverified` are claims, not facts.

Code is NOT mined into MemPalace. The repo is the store for code and never
goes stale; a palace copy would. MemPalace holds chat only, in wing
`<PROJECT_TOKEN>_dialogue`.

The wiring:

    python <SCRIPTS>/kb_resolve.py ask "<q>"         # chat
    python <SCRIPTS>/kb_resolve.py ask "<q>" --code  # code
    python <SCRIPTS>/kb_resolve.py get <exchange-id>
    python <SCRIPTS>/kb_resolve.py search "<text>"   # when you lack an id
    python <SCRIPTS>/kb_resolve.py doctor            # verify every link

Underneath: `graphify query/path/explain/affected/god-nodes --graph <path>`,
`mempalace search --wing <PROJECT_TOKEN>_dialogue [--since D]`. Quote
stored content verbatim; if the palace has nothing, say so.

Writing: `palace_exec ADD` (durable outcome), `palace_exec KG ADD` (new fact),
`palace_exec KG SUPERSEDE` (single-valued fact CHANGED; old stays queryable),
`palace_exec KG INVALIDATE` (fact ENDED, no replacement). There is no
automatic contradiction detection -- supersession is an explicit call.

Compounding runs itself: a gated `Stop` hook compounds every 100k tokens of
session growth, only at a settled turn, with `PreCompact` as the backstop.
`/kb-compound` runs the identical steps by hand. Never run `mempalace
compress`. Never mine the repo into the palace. Never point either tool at a
generated file the project rebuilds.

Setup for a new machine or project: docs/KB_SETUP.md (single file, scripts
embedded).
````

---

## 5. The ingestion rules — get these right before the first mine

A knowledge base built without these is roughly **85% noise**, measured. And
MemPalace has **no selective delete**: `sync` only prunes drawers whose *source
file* became gitignored, deleted or moved. Anything wrongly filed from a file
that still exists can only be removed by rebuilding.

### Rule 1 — chat: keep dialogue, drop everything else

Census of a real 17-session corpus, 475 MB of `.jsonl`:

| bucket | blocks | MB | verdict |
|---|---|---|---|
| tool_result | 4,312 | 31.05 | **drop** |
| isMeta / attachments / queue-ops | 4,263 | 28.55 | **drop** |
| tool_use (Read/Grep/Bash/…) | 3,333 | 3.46 | **drop** |
| tool_use (Write/Edit) inputs | 967 | 4.16 | **drop** — it is code; Graph A has it |
| assistant text | 2,057 | 1.13 | **keep** |
| user text | 715 | 0.91 | **keep** |

**475 MB → 2.2 MB.** Nothing of value is lost. Thinking blocks are free to
ignore: the transcript stores only a signature, not the text.

Also drop **compaction summaries** (`isCompactSummary`). They restate turns the
filter already keeps verbatim, so they double-count, and a graph built partly
from summaries encodes the summary's framing rather than what was said. Nothing
is lost — compaction shrinks the model's context window, never the transcript
file.

And drop **synthetic rows**: `isApiErrorMessage: true` or
`message.model == "<synthetic>"`. They carry no content and all-zero usage.

Do **not** point `mine --mode convos` at a raw transcript directory. It files
drawers like `<local-command-stdout>Login successful</local-command-stdout>` as
standalone memories, and its 800-char chunker splits transcript JSON
mid-structure.

### Rule 1b — chat granularity: one file per EXCHANGE

An **exchange** is one user turn plus every assistant turn answering it.

- **per-session** (what we had) gives a back-pointer resolving to an 805 KB
  file. Useless for "show me what was said."
- **per-turn** strips the question from the answer, so an extractor sees a reply
  with no idea what it replies to.
- **per-exchange** keeps them together and makes the pointer ~100× sharper.

No published system states a rationale for turn-vs-exchange — Graphiti and
session-graph both work per *message*. This is our judgement, not a finding.

Each file carries a **stable id** in its filename and frontmatter:

```
<session8>-<NNNN>        e.g. b49fc7be-0007
```

That id is the join key, and `_index.jsonl` maps id → file. `filter_transcripts.py`
writes both.

### Rule 2 — code: exclude generated artifacts via `.gitignore`

`mine` respects `.gitignore` and `sync` can later prune anything that becomes
ignored, so `.gitignore` **is** the ingestion rule — declarative and durable.

```gitignore
# --- knowledge-base ingestion rules ---
data/dumps/
data/generated/
data/*.sqlite
decision_tree/leaves.json
tests/fixtures/
**/__pycache__/
graphify-out/
```

Adapt the paths; the principle is **machine output is not knowledge**. On this
project that took the code wing from 88,414 drawers to 19,437 — a 78% cut.

Verify with `--no-index`, because plain `git check-ignore` stays silent on
already-tracked files and will make correct patterns look broken:

```bash
git check-ignore --no-index -q <path> && echo IGNORED || echo kept
mempalace mine . --dry-run          # the real check: read the room table
```

### Rule 2c — the two tools will feed each other if you let them

**The most expensive mistake in this document.** Graphify writes `graphify-out/`
*inside the directory it indexed*. MemPalace then mines that directory. So
unless you exclude it, **MemPalace ingests Graphify's graph as prose**.

Measured: `graph.json` alone produced **11,469 drawers**, a stale copy in
Graphify's dated backup folder added **9,000** more, plus `graph.html` (507) and
the analysis files. **22,120 junk drawers — 71% of the code wing.** It survived
two "clean" rebuilds unnoticed because the totals looked plausible.

It bites in **two** places, and the second has no git repo to protect it:

1. **The repo** — `graphify-out/` must be in `.gitignore`. Ignoring only
   `graphify-out/cache/` is not enough; that was the actual bug.
2. **The dialogue directory** — Graph B writes `graphify-out/` next to the
   filtered markdown, and that directory is **not a git repo at all**.

For (2): `mempalace mine` honours a `.gitignore` **even with no git repository
present** — verified. `filter_transcripts.py` writes one automatically.

**Detection.** A room whose drawer count cannot be explained by its file count.
101 Python files cannot yield 14,449 drawers — that is 112 KB per file. Compute
drawers-per-file after every mine; healthy code rooms run 18–55.

**Recovery.** Gitignore the path, then `mempalace sync --wing <w> --dry-run`,
read it, then `--apply`. But **pruning is not sufficient**: `sync` removes
drawers and leaves `hallways.json` / `tunnels.json` untouched, so the
entity-link layer stays derived from the contaminated set. If their mtimes
predate the prune, rebuild.

---

## 6. Stand it up — exact order

Run these in order, from a shell with `<BIN>` on PATH. Each step says what to expect.
Steps 2 and 4 must **never overlap** — a palace mine and a Graph B extraction
together exhausted a 16 GB machine (Part 11, trap 12).

```bash
# 0. one-time per machine: palace config.
#    Writes <HOME>/.config/mempalace/config.json (palace path) and drops two
#    per-project files, mempalace.yaml + entities.json, which .gitignore covers.
cd <PROJECT_DIR>
mempalace init . --yes --no-llm

# 1. chat -> one file per exchange (free, no LLM, seconds)
python <SCRIPTS>/filter_transcripts.py <TRANSCRIPTS> <DIALOGUE>
#    expect: "N exchanges, M turns" and, in <DIALOGUE>: NNNN-<id>.md files,
#    _index.jsonl, .gitignore. Ids look like 7e5dedc5-0023.

# 2. exchanges -> MemPalace (free, rule-based; ~1 min per 500 exchanges under emulation)
mempalace mine <DIALOGUE> --mode projects --wing <PROJECT_TOKEN>_dialogue
mempalace status
#    expect: ONE wing, <PROJECT_TOKEN>_dialogue; drawers / exchanges between 5 and 15.
#    The wing name is load-bearing: the gate derives it the same way, and a
#    mismatch creates a parallel wing that cannot be deleted.

# 3. Graph A - the repo. tree-sitter for code (free), the LLM for prose files.
export GRAPHIFY_NO_AUTO_REFRESH=1 GRAPHIFY_CLAUDE_CLI_PARALLEL=1 GRAPHIFY_CLAUDE_CLI_MODEL=sonnet
graphify . --backend claude-cli --max-concurrency 2 --token-budget 15000
#    cheap first pass instead:  graphify . --code-only     (code only, no LLM, seconds)
graphify cluster-only . --backend claude-cli --batch-size 100 --max-concurrency 2
#    expect: graphify-out/graph.json, GRAPH_REPORT.md, named communities.
#    optional: refresh Graph A on every commit, independent of the gate -
graphify hook install
#    installs post-commit + post-checkout hooks and a graph.json merge driver
#    (one line in .gitattributes: "graphify-out/graph.json merge=graphify").

# 4. Graph B - the chat. Wait for step 2 to finish first.
cd <DIALOGUE>
graphify . --backend claude-cli --max-concurrency 2 --token-budget 15000
#    if it ends with "N/M dispatched file(s) produced no nodes": run the same line
#    again - it is cached per file and retries only those. Two passes is normal.
graphify cluster-only . --backend claude-cli --batch-size 100 --max-concurrency 2
#    expect: <DIALOGUE>/graphify-out/graph.json; every node's source_file is an
#    exchange file, so every node carries an exchange id.

# 5. verify every link (Part 7)
cd <PROJECT_DIR>
python <SCRIPTS>/kb_resolve.py doctor
```

Then **restart the Claude Code session**. A running session keeps the MCP servers
and hooks it started with; `.mcp.json` and `settings.json` edits take effect on the
next start. On the first turn of the new session the gate *seeds* a watermark for
the session and does not compound — that is by design (Part 9).

### Timings from the reference build (16 GB Windows-on-ARM laptop, x64 emulation)

| step | size | time |
|---|---|---|
| filter | 18 sessions → 484 exchanges, 2.3 MB | seconds |
| mine | 484 exchanges → 4,354 drawers | ~1 min |
| Graph B pass 1 | 471 files, budget 60000 | ~70 min, 187 files dropped |
| Graph B pass 2 | 182 files, budget 60000 | ~70 min, 80 dropped |
| Graph B pass 3 | 80 files, budget 15000 | ~30 min, 18 dropped |
| cluster-only | 281 communities | ~1 min |

The budget, not the pass count, is what fixed the dropped files. Start at 15000.

---

## 7. Verify

Four checks. Each one proves a different link; do all four.

**1. `doctor` — every link resolves.**

```bash
python <SCRIPTS>/kb_resolve.py doctor
```

Expected, every line `[OK ]`:

```
  [OK ] dialogue dir exists - <DIALOGUE>
  [OK ] exchange index present - N exchanges
  [OK ] dialogue .gitignore (stops graph self-ingestion)
  [OK ] Graph A (code) exists - <PROJECT_DIR>/graphify-out/graph.json
  [OK ] Graph B (chat) exists - <DIALOGUE>/graphify-out/graph.json
  [OK ] Graph B nodes carry exchange ids - K of K+1 distinct sources
  [OK ] Graph B free of self-ingestion - 0 bad sources
  [OK ] palace reachable - <PROJECT_TOKEN>_dialogue
  [OK ] dialogue wing present
  verdict: all links resolve
```

"K of K+1" is right: the one source without an id is the graph's own root entry.
A `FAIL` on "carry exchange ids" means Graph B was built over something other than
the per-exchange files — rebuild it from `<DIALOGUE>`.

**2. `ask` — the graph routes, the exchange answers.** Ask something only the chat
history knows:

```bash
python <SCRIPTS>/kb_resolve.py ask "<a decision you remember making in a past session>"
```

Expected: a `CHAT` block listing nodes with `src=<date>-<id>.md`, then a
`VERBATIM SOURCE — N exchange(s)` block quoting the exchanges word for word, each
ending `full: kb_resolve.py get <id>`. If you get nodes but
`(no exchange ids on those nodes ...)`, Graph B predates the per-exchange filter.
If `VERBATIM SOURCE` quotes something that is *not* what you remember, the
knowledge base is working and your memory is wrong — that is the point of it.

**3. Drawers per exchange.** From `mempalace status`, drawers ÷ exchanges should be
5–15 (reference: 4,354 ÷ 484 = 9.0). Far above that means something that is not
dialogue got mined — Graphify's own output is the usual culprit (Part 5, rule 2c).

**4. Nothing left running.** After the stand-up: no `mempalace mine`, no `graphify
extract`, no `claude.exe ... --json-schema` older than ten minutes (Part 11, trap
14), and no `<HOME>/.mempalace/compound.lock`. On Windows:

```powershell
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'graphify|mempalace|json-schema' } |
  select ProcessId, ParentProcessId, CommandLine
```

---

## 8. Using it

```bash
kb_resolve.py ask "why did we reject the replenishment model"   # chat
kb_resolve.py ask "how does the crosswalk resolve ids" --code    # code
kb_resolve.py ask "..." --both
kb_resolve.py get b49fc7be-0007        # dereference one exchange id
kb_resolve.py search "crosswalk"       # semantic search, returns ids
kb_resolve.py doctor                   # verify every link in the chain
```

Underneath:

```bash
graphify query "<q>" --budget 1500 --graph <graph.json>
graphify path "A" "B"
graphify explain "X"
graphify affected "X" --depth 2        # what breaks if X changes
graphify god-nodes --top 10            # architectural hubs

mempalace search "<q>" --wing <w> [--room r] [--since D] [--before D]
mempalace status
mempalace hallways
```

### Reading a graph answer honestly

Every edge is tagged `EXTRACTED`, `INFERRED` or `AMBIGUOUS`. Nodes may carry
`verification=unverified`, meaning the extractor found **no evidence** for them
in the source — those are claims, not facts. Our Graph B had 11 of them.

Graphify also drops "out-of-scope" nodes attributed to files it never
dispatched: the model sees a filename quoted *inside* the dialogue and mints a
node against it. Correctly dropped, but it shows the extractor cannot always
tell "a file we discussed" from "a file we read."

### Recording decisions

| operation | use when |
|---|---|
| `palace_exec ADD` | a durable outcome |
| `palace_exec KG ADD` | a new fact |
| `palace_exec KG SUPERSEDE` | a single-valued fact **changed** — old stays queryable |
| `palace_exec KG INVALIDATE` | a fact **ended** with no replacement |

`SUPERSEDE` vs `INVALIDATE` is the IETF *Obsoletes* vs *Updates* distinction:
"read the new one instead" against "the old one is simply over." Keep them apart.

**There is no automatic contradiction detection.** Deduplication only blocks
identical triples. You decide what supersedes what — which is the right default,
since the alternative is a model deciding which of your decisions is dead.

---

## 9. Compounding — keeping it current

Three hooks, one of which does real work.

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command",
      "command": "<BIN>/mempalace.exe hook run --hook session-start --harness claude-code"}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "<PY_GATE> <SCRIPTS>/compound_gate.py --harness claude-code"}]}],
    "PreCompact": [{"hooks": [{"type": "command",
      "command": "<PY_GATE> <SCRIPTS>/compound_gate.py --harness claude-code --force"}]}]
  }
}
```

**Never wire MemPalace's own `stop` / `session-end` / `precompact` hooks.** All
three mine the **raw** transcript, which Rule 1 says is 91% noise, and the `stop`
one does it every turn. Observed failure: two stacked processes holding the
palace lock, and the lock **survives the holder's death**, blocking all writes
until cleared from `~/.mempalace/locks/`.

### The gate

There is no "context reached N tokens" hook event. `Stop` fires every turn (too
often to mine); `PreCompact` fires once per session at the auto-compact
threshold. So `Stop` becomes a cheap tick with a gate:

1. read the last `assistant` line from `transcript_path`
2. sum `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
3. compare against a per-session watermark in `~/.mempalace/compound_watermark.json`
4. under threshold → print `{}` and exit (~390 ms); over → spawn detached, advance

**Gate on GROWTH, not absolute size.** Compaction resets the count, so an
absolute mark fires twice then never again. Default **100,000** tokens.

**First sight seeds, does not fire.** A session the gate has never seen is not
N tokens of *new* work — it is an existing conversation that happens to be
large. Without this rule every fresh session on the project triggers a full
compound (including a Graph B rebuild) on its first turn. We hit exactly that.

**Compaction re-seeds.** `PreCompact --force` compounds the whole window and
leaves the watermark at the pre-compaction count. After compaction the count is
a fraction of that, so "growth" is negative and a growth gate would never fire
again in that session. When the gate sees the count *below* the watermark it
re-seeds to the new count and waits for fresh growth. We hit this too: a session
parked at 767k after compacting to 80k.

**One compound at a time.** The gate writes `~/.mempalace/compound.lock` before
spawning the chain and the chain deletes it when it ends, pass or fail
(`(steps) & del lock`). While the lock exists and is under three hours old the
gate skips *without* advancing the watermark, so the growth stays pending.
`/kb-compound` takes the same lock. Observed 2026-10-07: the PreCompact backstop
fired while a manual Graph B run was in flight — a mine and six Claude processes
sharing 700 MB of free RAM, both writing the same `graphify-out/`.

### Settledness — compound only at a natural boundary

Raw growth is a bad trigger on its own: it can capture a position you reverse
two turns later and file it with the same weight as the conclusion. That is
the "stale commitment and belief drift" failure the 2026 memory survey names as
the field's open problem, and it is what compact-adviser spends an API call
avoiding.

The gate's default is the **cheapest possible local check**: is the most recent
assistant turn's final content block *text*, or a *tool_use*? Prose means the
turn concluded; a tool call means we are mid-loop and whatever is being said is
provisional. If unsettled, the gate bails **without advancing the watermark**,
so the pending growth is re-checked next turn rather than lost. No LLM, no API
key, a 200 KB tail read.

It is crude by design. If it proves too crude, two documented alternatives:

| option | what it is | trade |
|---|---|---|
| **Scheduled compound** | a cron/launchd job running `/kb-compound` nightly or hourly over *settled* history only — the pattern in `docs/reference/pi-harness-report.md` Part 5 (03:00 `launchd`, "only settled, non-sensitive excerpts") | simplest, zero false positives; but a decision made this afternoon is not in the palace until tonight |
| **Judge-based trigger** | a compact-adviser-style classifier — "is this work unit finished?" — scored against a usage-dependent floor (0.90 when context is empty, relaxing to 0.50 near full). Reference implementation: https://github.com/kunchenguid/compact-adviser (TypeScript, in-process function hooks, needs `TYPESAFE_API_KEY`). Port the *floor* logic, not the runtime | best precision; costs an LLM call per settled turn, and the reference needs Node 22+ plus a paid key |

The current check sits between them: more responsive than a schedule, free
unlike a judge. Change it in `is_settled()` in `compound_gate.py`.

That three-field sum is not an approximation — it is byte-for-byte what Claude
Code computes for `used_percentage`, decompiled from the shipped bundle.

**Filters the sum requires**, each learned the hard way:

- skip `isSidechain` — subagent turns
- skip `isApiErrorMessage` and `message.model == "<synthetic>"` — these carry
  **all-zero usage**, and one at the tail makes the sum read 0, silently
  skipping the gate
- **never accumulate** — consecutive records repeat the identical usage object
  per streamed block. Take the last row whose sum is > 0.

**A compound means both halves**: filter chat → mine dialogue → refresh Graph A
(`graphify update`, free) → rebuild Graph B. The manual `/kb-compound` runs the
same steps, so "I compounded" means one thing.

### What survives compaction

Official, and it decides where things belong:

| mechanism | after compaction |
|---|---|
| system prompt, root CLAUDE.md, **auto memory**, plan file | re-injected from disk |
| files Claude read | up to **five**, most recent first; >5,000 tokens returns a bare path |
| skill bodies | re-injected, 5,000 tokens each / 25,000 total, **truncated from the end** |
| tool results, reasoning, earlier hook injections | **summarised away** |
| `SessionStart` hooks matching `compact` | re-run, output added |

So **anything important enough to survive belongs in auto memory or CLAUDE.md**,
not a snapshot file — a snapshot only helps if something reads it back, and the
only reader is capped at 10,000 characters.

**`PreCompact` cannot inject anything.** Its stdout is not shown to Claude and it
has no `additionalContext`. Several published repos assume otherwise and write
into the void. The only supported re-injection is `SessionStart` with matcher
`compact`. PreCompact *can* block (exit 2 or `{"decision":"block"}`), and
`PostCompact` hands you `compact_summary` free.

Auto-compaction fires at `window − min(maxOutputTokens, 20000) − 13000` — a
33,000-token reserve. Note the trap: the status line's `used_percentage` is
denominated in the **model** window, so a 1M session dies at ~77% on the meter
and never reaches 100%.

---

## 10. MCP wiring

```json
{
  "mcpServers": {
    "mempalace": { "command": "<BIN>/mempalace-mcp.exe", "args": [] },
    "graphify":  { "command": "<BIN>/graphify-mcp.exe",  "args": [] }
  }
}
```

Two bugs you will hit:

- **`--palace <path>` on `mempalace-*-mcp`** once crashed with `AttributeError:
  property 'palace_path' of 'MempalaceConfig' object has no setter`. Verified
  working on MemPalace 3.10.0 (entry point `mempalace.mcp_proxy`) on 2026-10-10;
  the plugin's launcher relies on it. In this snapshot the path comes from
  `config.json`.
- **`graphify-mcp` needs the `mcp` extra** or it dies with
  `ModuleNotFoundError: No module named 'mcp'`.

`mempalace-mcp` exposes the full tool surface (~45 tools, including fetch-by-id);
`mempalace-light-mcp` is 3 consolidated tools with ~92% fewer schema tokens.
Prefer the full server when you need the back-pointer dereference.

**Editing `.mcp.json` requires a session restart** — a running session keeps the
servers it started with.

---

## 11. Traps, collected

1. **Graphify output feeds MemPalace** unless excluded in *both* places. Part 5.
2. **No selective delete.** `sync` prunes only by source-file state. Get
   ingestion right before the first mine.
3. **`sync` leaves hallways/tunnels stale.** Check mtimes; rebuild if they
   predate the prune.
4. **Mining a subdirectory collapses every room to `general`** — rooms are keyed
   to paths relative to the project root.
5. **Wing names must be derivable**, or an automated compound creates a
   parallel wing you cannot delete.
6. **Never run `mempalace compress`** — lossy, ~12 points of retrieval accuracy
   (96.6% → 84.2%). The "zero information loss" claim is not supported by the code.
7. **Keep Python x64 on ARM.** Part 2.
8. **`uv tool install --force` wipes local patches.**
9. **Don't trust the benchmark headline.** MemPalace's "96.6% R@5" measures
   ChromaDB's default embedding model; the palace structure plays no part. Judge
   it on the temporal KG and the free, rule-based write path, which are real.
10. **Review what gets filed.** There is no write gating.
11. **Secrets:** use `.graphifyignore`, and check what mining picked up before
    sharing a palace.
12. **Two compounds collide.** The PreCompact backstop and a manual run share
    one palace and one `graphify-out/`. The lock in Part 9 is the guard; never
    delete it while a `mempalace mine` or `graphify extract` process is alive.
13. **Compaction strands a growth watermark** unless the gate re-seeds when
    the count drops below it. Part 9.
14. **A timed-out Claude CLI call leaves an orphan on Windows.** Graphify runs
    `claude.cmd -p ...` with `timeout=600` (`GRAPHIFY_API_TIMEOUT`). The
    timeout kills the `cmd.exe` wrapper, not the `claude.exe` child, which
    keeps a slot's worth of RAM while Graphify has already retried the chunk.
    Symptom: a `claude.exe` older than 10 minutes whose parent PID is gone.
    Sweep: `Get-CimInstance Win32_Process | ? { $_.CommandLine -match
    'claude.exe.*--json-schema' }`, check `ParentProcessId`, kill the orphans.

---

## 12. Bootstrap checklist

```
[ ] python -c "import sysconfig;print(sysconfig.get_platform())" == win-amd64
[ ] uv tool install mempalace ; uv tool install "graphifyy[pdf,sql,mcp]"
[ ] ~/.local/bin on PATH ; onnxruntime providers print
[ ] permissions allowlisted in .claude/settings.local.json
[ ] .gitignore carries the ingestion rules INCLUDING graphify-out/
[ ] mempalace init . --yes --no-llm
[ ] Part 4 files written; patch_graphify.py run with the graphifyy python
[ ] filter_transcripts.py -> check _index.jsonl exists and ids look like xxxxxxxx-NNNN
[ ] mempalace mine <DIALOGUE> --mode projects --wing <PROJECT_TOKEN>_dialogue
[ ] drawers per exchange 5-15 (mempalace status)
[ ] graphify . --backend claude-cli --max-concurrency 2 --token-budget 15000 ; cluster-only
[ ] graphify <DIALOGUE> --backend claude-cli (sonnet, concurrency 2) ; re-run if files produced no nodes ; cluster-only
[ ] .mcp.json: mempalace-mcp + graphify-mcp, NO --palace flag
[ ] .claude/settings.json: SessionStart + gated Stop + PreCompact
[ ] restart the session so MCP loads
[ ] kb_resolve.py doctor -> all links resolve
[ ] kb_resolve.py ask "<something only the chat knows>" -> verbatim exchange comes back
```

The last line is the only one that proves anything. Everything above it is
plumbing.

---

## 13. Resources

**Tools**

- **MemPalace** — PyPI `mempalace` (3.10.0 here). Source:
  https://github.com/MemPalace/mempalace. CLI used: `init`, `mine`, `sync`,
  `status`, `search`, `hook run`. Two MCP servers ship with it: `mempalace-mcp`
  (full, ~45 tools including fetch-by-id and the KG write tools) and
  `mempalace-light-mcp` (3 consolidated tools, ~92% fewer schema tokens). The
  palace lives at `<HOME>/.config/mempalace/palace`: ChromaDB for vectors, SQLite
  for the temporal knowledge graph.
- **Graphify** — PyPI `graphifyy` (0.9.79 here; double *y* — `graphify` is a
  different package). Source: https://github.com/Graphify-Labs/graphify, docs:
  https://docs.graphify.com. Extras `pdf,sql,mcp`. tree-sitter for code (no
  LLM), an LLM for prose; `--backend claude-cli` uses Claude Code itself as that
  LLM, so no API key. MCP server: `graphify-mcp`.
- **uv** — installs both as isolated tools (`uv tool install`) and, on
  Windows-on-ARM, pins the x86_64 CPython the stack needs (Part 2).
- **Claude Code** — the hooks `SessionStart`, `Stop`, `PreCompact`; the project
  `.mcp.json`; slash commands in `.claude/commands/`; transcripts as
  `<TRANSCRIPTS>/*.jsonl`. Those transcripts are the raw material for the
  whole chat side.

**Design references** (in the originating repository, `statusneo_assignment`)

- `docs/memory_system.md` — the long-form version of this file, with the
  research and the failures behind each rule.
- `docs/graph_source_linkage_research.md` — how Graphiti
  (`EntityEdge.episodes → EpisodicNode.content`), Microsoft GraphRAG
  (`Entity.text_unit_ids → TextUnit.text`) and session-graph
  (`devkg:extractedFrom`) keep the node → source back-pointer. The exchange id
  here plays exactly that role.
- `docs/reference/pi-harness-report.md` — the reference harness whose pattern
  Part 1 follows: compact summary resident in context, every original preserved
  in an index, fetched on demand.
- compact-adviser — https://github.com/kunchenguid/compact-adviser — the
  in-process judge pattern listed in Part 9 as an alternative to the cheap
  settledness check.

**The MCP choice, in short**

Wired: the **full** `mempalace-mcp` and `graphify-mcp`, both with `args: []`.
The full server rather than the light one because it exposes fetch-by-id
(`get_drawer`) and the knowledge-graph write tools (`kg_add`, `kg_supersede`,
`kg_invalidate`), which the light server folds away; the extra schema tokens
are the price. No `--palace` flag in this snapshot (Part 10 records the crash
seen at the time; the flag works on 3.10.0 as of 2026-10-10 and the plugin uses
it); here the path comes from `config.json`, written by `mempalace init`. Any earlier
project-local "memory" MCP server over a generated file should be removed —
one memory system, not three. `.mcp.json` changes need a session restart.

---
