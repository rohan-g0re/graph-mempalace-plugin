# Architecture

The design in short. `docs/DESIGN.md` is the contract;
`docs/reference-memory-system-longform.md` holds the research each rule came from.

## Two domains, two layers each

There are two kinds of thing to remember: the code and the conversation. Each gets a
**navigation layer** that decides what to look at, and a **ground-truth layer** that
supplies what was actually said or written.

| domain | navigation | ground truth |
|---|---|---|
| code | Graph A — Graphify over the repo | the repository itself, `file:line` |
| chat | Graph B — Graphify over filtered dialogue | MemPalace drawers, keyed by exchange id |

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

`kb_resolve.py` is the wiring. It runs the graph query, reads the back-pointer off
each node it gets, and dereferences it.

## The one rule

**The graph never answers. It routes.**

A node holds a label, a community, a one-line description, edge tags
(`EXTRACTED`, `INFERRED`, `AMBIGUOUS`) and a `source_file` back-pointer. That is
enough to decide *is this the thing I want*. It is not the answer.

The description is a model's summary. A summary that reads fluently about a decision
you later reversed is worse than no memory at all, because it still scores well in
search. The summary routes; the verbatim text answers. Every production graph memory
system works this way.

Nodes marked `verification=unverified` are claims: the extractor found no evidence
for them in the source. Treat them as leads, not facts.

## What lives where

| layer | holds | used for |
|---|---|---|
| graph node | label, community, one-line description, edge tags, `source_file` | deciding what to look at — cheap, structured |
| MemPalace drawer | the exchange, verbatim | the actual answer |
| MemPalace search | hybrid vector + keyword search over drawers | finding an exchange when you have no id |
| the repository | the code and the docs, current by definition | the actual answer, for code |

## The exchange id is the back-pointer

An **exchange** is one user turn plus every assistant turn answering it. The filter
writes one markdown file per exchange, each carrying a stable id in its filename and
its frontmatter:

```
<first 8 chars of session id>-<NNNN>        e.g. b49fc7be-0007
```

`_index.jsonl` maps id to file. That id is the join key between the chat graph and
the palace, and it is what `kb_resolve.py get <id>` takes.

The id is stable, and that rests on reading one file per session: the top-level
`<transcript-dir>/*.jsonl`. A transcript is append-only, so a later compound only
appends new exchanges and never renumbers the ones already filed. Subagent
transcripts are skipped for the same reason, as is any row marked `isSidechain`.
Claude Code writes them into a per-session subdirectory, `<transcript-dir>/<session>/`,
after the exchange they belong to — merging them in and numbering exchanges by
timestamp shifted the number of every later exchange in that session. **An id that
renumbers is not a join key**, and this one has to join a palace drawer to a Graph B
node across every compound. Nothing is lost by skipping them: the main transcript
already carries each subagent's result as a task-notification turn.

Granularity was a real choice. A whole session resolves to a file hundreds of
kilobytes long — useless for "show me what was said." A single turn strips the
question from the answer, so an extractor sees a reply with no idea what it replies
to. One file per exchange keeps them together and makes the pointer roughly a
hundred times sharper. No published system argues turn against exchange; this is a
judgement, not a finding.

## Why code is not mined into the palace

Code already has a database: the working tree. It is always current,
line-addressable, and never drifts. Chat has no such store, which is the entire
reason MemPalace is here.

Mining code into the palace produces drawers that duplicate the filesystem badly and
go stale on the next edit, while looking authoritative. So the palace holds chat
only, and a code answer sends you to a file.

This asymmetry is the most important decision in the design, and it took a while to
see.

## Graph B and the token budget

Graph B needs a model, because prose has no tree-sitter. Extraction runs through the
`claude` CLI — your own login, no API key.

The thing to know going in is chunk size. A large chunk holds dozens of exchanges
and the model silently drops some of them from its answer. Three passes on the
reference build, 484 exchanges:

| pass | token budget | files sent | produced no nodes | nodes after |
|---|---|---|---|---|
| 1 | 60000 | 471 | 187 | 768 |
| 2 | 60000 | 182 | 80 | 942 |
| 3 | 15000 | 80 | 18 | 1,106 |

Per-file time was about 22 seconds either way. The small budget is what fixed the
dropped files, not the extra pass. **15000 is the default everywhere** — see
`docs/configuration.md`.

Extraction is cached per file and checkpoints every chunk, so re-running retries only
the files that produced nothing, and an interrupted run keeps its work. Exchanges
that never produce a node are still in the palace and still findable by search; only
the graph cannot route to them.

## Traps

Fourteen things that each cost a rebuild. The long form has the detail; this is the
list.

1. **Graphify's output feeds MemPalace** unless excluded in both the repo and the
   exchanges directory. It writes `graphify-out/` inside the directory it indexed.
2. **No selective delete.** `sync` prunes only by source-file state, so get
   ingestion right before the first mine.
3. **`sync` leaves the entity-link layer stale.** Check `hallways.json` and
   `tunnels.json` mtimes; rebuild if they predate the prune.
4. **Mining a subdirectory collapses every room to `general`** — rooms are keyed to
   paths relative to the project root.
5. **Wing names must be derivable**, or an automated compound creates a parallel
   wing that cannot be deleted.
6. **Never run `mempalace compress`** — lossy, about 12 points of retrieval accuracy.
7. **Keep Python x86_64 on Windows-on-ARM.** `onnxruntime` has no arm64 wheel and
   ChromaDB needs it.
8. **`uv tool install --force` wipes local patches**, including the graphify patch.
   Re-apply after every upgrade.
9. **Do not trust MemPalace's benchmark headline.** The quoted recall figure measures
   the default embedding model, not the palace structure.
10. **Review what gets filed.** There is no write gating on mining.
11. **Secrets leak through mining.** Use `.graphifyignore`, and check what was
    picked up before sharing a palace.
12. **Two compounds collide.** A forced compound and a manual run share one palace
    and one `graphify-out/`. The lock is the guard; never delete it while a mine or
    an extraction is alive.
13. **Compaction strands a growth watermark** unless the gate re-seeds when the token
    count drops below it.
14. **A timed-out Claude CLI call leaves an orphan on Windows.** The timeout kills
    the wrapper, not the child. Symptom: a `claude.exe` older than ten minutes whose
    parent is gone. The compound chain sweeps these at the end of every run.

## How the plugin packages all this

- **One shared module.** `scripts/gm_common.py` decides every path, name, config
  value, lock and log line, so the session hook, the gate, the chain and the resolver
  agree by construction rather than by review.
- **Standard-library Python only**, 3.11 or newer. Hooks run on machines nobody
  controls; a dependency is a failure mode.
- **Hooks never block.** Every hook exits 0 on every error and logs to
  `~/.graph-mempalace/compound.log`. Long work is spawned detached.
- **State is in `~/.graph-mempalace/`, not in the plugin directory.** The plugin root
  carries the version as a path segment, so it changes on every update — and users
  need to be able to find and edit their own config.
- **One lock, machine-wide.** A mine and an extraction must never run at once.
