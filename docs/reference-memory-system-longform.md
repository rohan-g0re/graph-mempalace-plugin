# The memory system

Complete documentation of the MemPalace + Graphify knowledge base: what it is,
why it is shaped this way, how to stand it up on a new machine or a new project,
and every trap that cost us a rebuild.

Written for: an engineer (or an agent) setting this up from nothing.

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
      "Bash(C:/Users/<you>/.local/bin/mempalace.exe:*)",
      "Bash(C:/Users/<you>/.local/bin/graphify.exe:*)"
    ]
  }
}
```

Both forms: hooks call the `.exe` by absolute path, interactive use goes through PATH.

---

## 4. The ingestion rules — get these right before the first mine

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

## 5. Stand it up

```bash
# 0. one-time
cd <project>
mempalace init . --yes --no-llm        # detects rooms from folder structure

# 1. chat -> filtered exchanges (free, no LLM)
python <skill>/scripts/filter_transcripts.py \
    ~/.claude/projects/<slug>/ \
    ~/.mempalace/dialogue/<slug>/

# 2. exchanges -> MemPalace
mempalace mine ~/.mempalace/dialogue/<slug>/ \
    --mode projects --wing <project>_dialogue

# 3. Graph A - the code (tree-sitter, no LLM, instant)
graphify . --code-only
graphify cluster-only . --backend claude-cli --batch-size 100 --max-concurrency 2

# 4. Graph B - the chat
cd ~/.mempalace/dialogue/<slug>/
GRAPHIFY_CLAUDE_CLI_MODEL=sonnet GRAPHIFY_CLAUDE_CLI_PARALLEL=1 \
  graphify . --backend claude-cli --max-concurrency 2 --token-budget 15000
graphify cluster-only . --backend claude-cli --batch-size 100 --max-concurrency 2

# 5. verify every link
python <skill>/scripts/kb_resolve.py doctor
```

**Do not mine code into the palace.** §1 explains why. The repo is the store.

### Wing naming

Derive from the project directory so a script can compute the same name without
being told: `<dirname>_dialogue`. Get this wrong once and you get a duplicate
wing that **cannot be deleted** — we did, and it forced a full rebuild.

### Which LLM backend

Code is extracted locally with tree-sitter and needs no LLM. **Prose does**, and
with no key `graphify .` fails outright (exiting 0, writing only `cache/`).

Backends: `gemini | kimi | claude | openai | deepseek | ollama | claude-cli`.
The first five want an API key — `--backend claude` specifically requires
`ANTHROPIC_API_KEY` and will not fall back.

**`--backend claude-cli` needs no key**, but be realistic about it. Measured:

| configuration | one call |
|---|---|
| trivial prompt, in a project with 4 MCP servers + a SessionStart hook | 19.1 s |
| trivial prompt, `--strict-mcp-config` | 10.5 s |
| trivial prompt, clean cwd + `--strict-mcp-config` → **startup floor** | **8.3 s** |
| realistic 8 KB chunk, **default model (Opus)** | 108.6 s |
| realistic 8 KB chunk, **Haiku** | 41.8 s |

Two levers, both configuration rather than fate:

1. **Graphify defaults `claude-cli` to Opus** — its own source calls that
   "overkill for the structured-JSON extraction graphify performs." Set
   `GRAPHIFY_CLAUDE_CLI_MODEL=sonnet` (haiku is faster but silently omitted 6 of
   15 files on our first build).
2. **Every spawn re-loads your MCP servers and fires SessionStart** — ~11 s per
   call of pure waste. Graphify does not pass `--strict-mcp-config`; either
   patch that into `cli_args` in `graphify/llm.py` or run extraction from a
   directory with no `.mcp.json` and no `.claude/`.

Concurrency above 1 needs an opt-in, because parallel Claude Code subprocesses
can conflict over session state:

```bash
export GRAPHIFY_CLAUDE_CLI_PARALLEL=1
export GRAPHIFY_CLAUDE_CLI_MODEL=sonnet
graphify . --backend claude-cli --max-concurrency 2 --token-budget 15000
```

**Two, not four, on a 16 GB ARM laptop.** Four Claude processes under x64
emulation stalled at one chunk per 25 minutes and starved a concurrent palace
mine into `MemoryError`. Two finished the same work. If the run ends with
`N/471 dispatched file(s) produced no nodes`, run the same command again — it is
cached per file and retries only those.

**Token budget 15000, not the default 60000.** A 60000-token chunk holds dozens
of exchanges and the model silently drops some of them from its answer — 187 of
471 files on the first pass, 80 of 182 on the second. At 15000 the third pass sent
the 80 leftovers and dropped 18, at the same ~22 seconds per file. Small chunks
cost nothing extra and keep more of the chat routable.

Extraction **checkpoints every chunk**, so an interrupted run keeps completed
work and resumes.

> **Patch warning:** `uv tool install --force` silently wipes any local patch to
> `llm.py`. Re-apply after every upgrade.

---

## 6. Using it

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

## 7. Compounding — keeping it current

Three hooks, one of which does real work.

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command",
      "command": "<bin>/mempalace.exe hook run --hook session-start --harness claude-code"}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "<python> <skill>/scripts/compound_gate.py --harness claude-code"}]}],
    "PreCompact": [{"hooks": [{"type": "command",
      "command": "<python> <skill>/scripts/compound_gate.py --harness claude-code --force"}]}]
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
| **Scheduled compound** | a cron/launchd job running `/kb-compound` nightly or hourly over *settled* history only — the pattern in `docs/reference/pi-harness-report.md` §4 (03:00 `launchd`, "only settled, non-sensitive excerpts") | simplest, zero false positives; but a decision made this afternoon is not in the palace until tonight |
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

## 8. MCP wiring

```json
{
  "mcpServers": {
    "mempalace": { "command": "<bin>/mempalace-mcp.exe", "args": [] },
    "graphify":  { "command": "<bin>/graphify-mcp.exe",  "args": [] }
  }
}
```

Two bugs you will hit:

- **`--palace <path>` crashes `mempalace-*-mcp`**: `AttributeError: property
  'palace_path' of 'MempalaceConfig' object has no setter`. Omit it; the path
  comes from `config.json` anyway.
- **`graphify-mcp` needs the `mcp` extra** or it dies with
  `ModuleNotFoundError: No module named 'mcp'`.

`mempalace-mcp` exposes the full tool surface (~45 tools, including fetch-by-id);
`mempalace-light-mcp` is 3 consolidated tools with ~92% fewer schema tokens.
Prefer the full server when you need the back-pointer dereference.

**Editing `.mcp.json` requires a session restart** — a running session keeps the
servers it started with.

---

## 9. Traps, collected

1. **Graphify output feeds MemPalace** unless excluded in *both* places. §4.
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
7. **Keep Python x64 on ARM.** §2.
8. **`uv tool install --force` wipes local patches.**
9. **Don't trust the benchmark headline.** MemPalace's "96.6% R@5" measures
   ChromaDB's default embedding model; the palace structure plays no part. Judge
   it on the temporal KG and the free, rule-based write path, which are real.
10. **Review what gets filed.** There is no write gating.
11. **Secrets:** use `.graphifyignore`, and check what mining picked up before
    sharing a palace.
12. **Two compounds collide.** The PreCompact backstop and a manual run share
    one palace and one `graphify-out/`. The lock in §7 is the guard; never
    delete it while a `mempalace mine` or `graphify extract` process is alive.
13. **Compaction strands a growth watermark** unless the gate re-seeds when
    the count drops below it. §7.
14. **A timed-out Claude CLI call leaves an orphan on Windows.** Graphify runs
    `claude.cmd -p ...` with `timeout=600` (`GRAPHIFY_API_TIMEOUT`). The
    timeout kills the `cmd.exe` wrapper, not the `claude.exe` child, which
    keeps a slot's worth of RAM while Graphify has already retried the chunk.
    Symptom: a `claude.exe` older than 10 minutes whose parent PID is gone.
    Sweep: `Get-CimInstance Win32_Process | ? { $_.CommandLine -match
    'claude.exe.*--json-schema' }`, check `ParentProcessId`, kill the orphans.

---

## 10. What is and is not built

Final state on 2026-10-07 22:30, after the full re-ingest.

**Built and verified**

- **Graph A — code.** 6,108 nodes, 13,687 edges, 282 communities.
  781 of the nodes come from the repo's own markdown (design notes, ledger,
  walkthroughs), so Graph A covers the repo's documents as well as its source.
  Untouched by the chat-side redesign.
- **Exchanges.** `filter_transcripts.py` → **484 exchanges** across 18 sessions,
  `_index.jsonl`, and the `.gitignore` that stops self-ingestion.
- **Palace.** One wing, `statusneo_assignment_dialogue`, **4,354 drawers** —
  9.0 per exchange, inside the healthy 5–15 band. Mine verified complete
  (`Files processed: 0, skipped 485`). No code wing, by design (§1).
- **Graph B — chat.** **1,106 nodes, 1,241 edges, 281 named communities**, 87%
  EXTRACTED. **461 of 484 exchanges** carry at least one node. 23 produced no
  nodes after three passes — mostly one run of operational turns on 2026-09-17
  ("run it", "test them simultaneously") — and 8 nodes are
  `verification=unverified`. Those 23 exchanges are still in the palace and
  reachable by `search`; the graph simply cannot route to them.
- **`kb_resolve.py doctor`**: all links resolve; every Graph B source except the
  graph's own root carries an exchange id; zero self-ingestion.
- **`kb_resolve.py ask`** returns verbatim exchanges. Two tests: "why did we
  reject the replenishment model" → `accc5a03-0019` (the Way 1 / Way 2 answer);
  "what did we decide about compounding frequency" → `7e5dedc5-0023` ("Let's do
  it at 100k for now").
- **Gate.** Settledness check, first-sight seeding, compaction re-seed, compound
  lock, concurrency 2, token budget 15000. `/kb-compound` runs the identical
  steps under the same lock.
- Both MCP servers answer `initialize`; full `mempalace-mcp` wired.

**How the three Graph B passes went** — build this in on a new machine instead
of rediscovering it:

| pass | token budget | files sent | produced no nodes | nodes after |
|---|---|---|---|---|
| 1 | 60000 | 471 | 187 | 768 |
| 2 | 60000 | 182 | 80 | 942 |
| 3 | 15000 | 80 | 18 | 1,106 |

Per-file time was the same (~22 s) either way. The small budget just stops the
model dropping files from a crowded chunk. 15000 is now the default everywhere.

**Open**

- The 23 exchanges without nodes. A fourth pass might recover a few; not worth
  the hour unless a question lands on one of them.
- Subagent `<task-notification>` exchanges keep the XML envelope as their
  `topic` line. Fixing that in the filter changes file content, which means a
  re-mine — decision pending.
- Graph A `cluster-only` has not been re-run since the last code change;
  `graphify update .` runs free on every compound, labelling only when asked.

**Lessons recorded in §9:** never mine and extract at the same time (12),
compaction strands a watermark (13), timed-out CLI calls leave orphans (14).

Run `kb_resolve.py doctor` for the current state rather than trusting this list.

---

## 11. Bootstrap checklist

```
[ ] python -c "import sysconfig;print(sysconfig.get_platform())" == win-amd64
[ ] uv tool install mempalace ; uv tool install "graphifyy[pdf,sql,mcp]"
[ ] ~/.local/bin on PATH ; onnxruntime providers print
[ ] permissions allowlisted in .claude/settings.local.json
[ ] .gitignore carries the ingestion rules INCLUDING graphify-out/
[ ] mempalace init . --yes --no-llm   -> review rooms
[ ] filter_transcripts.py -> check _index.jsonl exists and ids look like xxxxxxxx-NNNN
[ ] mempalace mine <dialogue> --mode projects --wing <proj>_dialogue
[ ] drawers-per-file sanity: code rooms 18-55, no outliers
[ ] graphify . --code-only ; cluster-only
[ ] graphify <dialogue> --backend claude-cli (sonnet, concurrency 2) ; re-run if files produced no nodes ; cluster-only
[ ] .mcp.json: mempalace-mcp + graphify-mcp, NO --palace flag
[ ] .claude/settings.json: SessionStart + gated Stop + PreCompact
[ ] restart the session so MCP loads
[ ] kb_resolve.py doctor -> all links resolve
[ ] kb_resolve.py ask "<something only the chat knows>" -> verbatim exchange comes back
```

The last line is the only one that proves anything. Everything above it is
plumbing.
