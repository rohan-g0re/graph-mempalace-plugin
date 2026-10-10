# Manual setup — for harnesses that are not Claude Code

The plugin is packaged for Claude Code. The machinery underneath is not: it is
standard-library Python that reads Claude Code's transcript files and drives two
command-line tools. You can run all of it from Codex, pi, Cursor, or anything else.

What you lose is the automatic part. Be clear about that before you start — the
honest list is at the bottom.

## What you need first

- Python 3.11 or newer (the scripts use `tomllib`).
- [`uv`](https://docs.astral.sh/uv/).
- git.
- The `claude` CLI on your PATH, logged in. The chat graph's extraction runs through
  it. Not having it means no chat graph, though the filter and the palace still work.
- On Windows: Git for Windows, for a POSIX shell.
- Claude Code transcripts to ingest. This is the real constraint: the filter reads
  `~/.claude/projects/<slug>/*.jsonl`, which only Claude Code writes. Another
  harness's history is in another format and the filter does not read it.

## 1. Clone

```bash
git clone https://github.com/rohan-g0re/graph-mempalace-plugin.git
cd graph-mempalace-plugin
```

You are using the repository as a toolbox, not installing it. Nothing is copied
anywhere.

## 2. Install the prerequisites

```bash
python scripts/kb_setup.py --install
```

That installs `mempalace` and `graphify` with `uv`, initialises the palace, patches
graphify's Claude CLI backend, and appends `graphify-out/` to your global git
excludes. It never installs `uv` itself; if `uv` is missing it prints the official
one-liner for your OS and stops.

Check it first if you prefer to see the plan:

```bash
python scripts/kb_setup.py --check
```

That prints a table of every prerequisite with OK or MISSING and the fix for each.

## 3. Ingest the history

```bash
python scripts/compound_run.py --project /path/to/your/repo --wait
```

`--wait` runs in the foreground and prints a summary at the end: exchanges found,
drawers added, both graphs' node counts, files that still produced no nodes, and
how long it took. Without `--wait` it detaches and logs to
`~/.graph-mempalace/compound.log`.

This is the same four-step chain the plugin's hooks run: filter the transcripts into
one file per exchange, mine those into a MemPalace wing, refresh the code graph,
rebuild the chat graph. On a large history it can take about an hour, almost all of
it the chat graph.

Then verify:

```bash
python scripts/kb_resolve.py doctor
```

Every line should read `[OK ]`. The last check is the only one that proves anything
— ask it something only the chat history knows:

```bash
python scripts/kb_resolve.py ask "a decision you remember making in a past session"
```

You want a block of graph nodes, then a `VERBATIM SOURCE` block quoting the
exchanges behind them. If the quoted text is not what you remember, the knowledge
base is working and your memory is wrong. That is the point of it.

## 4. Tell your harness the knowledge base exists

Your model will not use the base unless something tells it to. Add the text of
`skills/graph-mempalace/SKILL.md` to whatever instruction file your harness always
loads:

| harness | file |
|---|---|
| Codex | `AGENTS.md` |
| Cursor | `.cursorrules`, or a rule file under `.cursor/rules/` |
| pi | the agent's instruction file |
| anything else | whatever it reads on every session |

If you would rather paste the gist than the whole skill, it is three things:

1. **Consult the base before answering about past work or decisions.** Do not answer
   from memory when the history is on disk.
2. **The three commands**, with absolute paths filled in:

```
python <clone>/scripts/kb_resolve.py ask "<question>"           # chat history -> verbatim exchanges
python <clone>/scripts/kb_resolve.py ask "<question>" --code     # code graph -> files to read
python <clone>/scripts/kb_resolve.py get <exchange-id>           # one exchange in full
```

3. **Never answer from a node's description — always from the quoted exchange or the
   file.** Nodes marked `verification=unverified` are claims. If nothing comes back,
   say so.

Use absolute paths. Harnesses differ on what the working directory is, and a
relative path that works in one fails in another.

### MCP servers, if your harness speaks MCP

`mempalace-mcp` and `graphify-mcp` are installed by step 2 and land next to the
other binaries (`uv tool dir --bin`, usually `~/.local/bin`). Declare them however
your harness declares stdio servers. Two known bugs: do not pass `--palace <path>`
(it crashes the server; the path comes from the palace config anyway), and
`graphify-mcp` needs the `mcp` extra, which `kb_setup.py` installs.

## 5. Schedule the compound yourself

**This is the part the plugin does for you and your harness does not.** The
token-growth gate reads `transcript_path` out of a Claude Code `Stop` hook payload
and sums three usage fields from the transcript. There is no equivalent anywhere
else, so `compound_gate.py` is Claude-Code-only. Run the chain on a schedule
instead.

The command is the same one from step 3, without `--wait`:

```bash
python <clone>/scripts/compound_run.py --project /path/to/your/repo
```

**cron** (Linux, macOS) — nightly at 03:00:

```cron
0 3 * * * /usr/bin/python3 /path/to/clone/scripts/compound_run.py --project /path/to/repo
```

**Task Scheduler** (Windows) — daily:

```powershell
schtasks /create /tn "graph-mempalace compound" /sc daily /st 03:00 ^
  /tr "python C:/path/to/clone/scripts/compound_run.py --project C:/path/to/repo"
```

**Your harness's own hooks**, if it has them. Codex reads `.codex/hooks.json`; pi
has extensions. Either can call the chain at session end or on a timer. Check your
harness's current documentation for the event names and the payload shape before
wiring anything — these interfaces move.

A nightly schedule is the simplest option with zero false triggers. Its cost is
latency: a decision made this afternoon is not in the palace until tonight. That was
the alternative the token gate was chosen over, and it is a perfectly reasonable
trade when the gate is not available.

The lock is machine-wide, so a scheduled run and a manual one cannot collide. A
scheduled run that finds the lock held logs that it skipped and exits.

## 6. Configure, if you want to

Same files, same keys as the plugin: `~/.graph-mempalace/config.toml` machine-wide,
`<repo>/.graph-mempalace.toml` per project. See `configuration.md`. The `[session]`
keys do nothing here, because there is no session-start hook to print anything.

If you already have a palace wing or an exchanges folder from an earlier setup, pin
them with `palace.wing` and `palace.exchanges_dir` rather than letting the scripts
derive new names. `configuration.md` has the exact file.

## What is not automatic here

Plainly:

- **No token gate.** It needs Claude Code's `Stop` hook payload. You schedule
  instead.
- **No compaction backstop.** `PreCompact` is a Claude Code event.
- **No first-sight ingest.** You run step 3 by hand, once, per project.
- **No status line and no injected instruction block.** Nothing prints at session
  start; that is why step 4 exists.
- **No slash commands.** `/graph-mempalace:kb-ask` and the rest are Claude Code
  skills. You call the scripts directly, or wrap them in whatever your harness calls
  a command.
- **Updates are `git pull`.** No marketplace, no version pinning, no notification
  that something changed.
- **Only Claude Code transcripts can be ingested.** The filter reads Claude Code's
  `.jsonl` format. If your coding happens in Codex or Cursor, that history is not
  what gets mined — a knowledge base built this way covers whatever Claude Code
  sessions you have, and the other harness reads it rather than feeding it.

That last one is the real limit, and it is worth sitting with before you invest the
hour. Reading the base from anywhere works well. Filling it still needs Claude Code.

## The fully embedded variant

`reference-single-file-setup.md` is the whole setup in one document: the design, the
install, every script in full, every config block, the commands in order and the
checks that prove it works. It predates the plugin and uses angle-bracket
placeholders you substitute before writing anything.

Use it when you want no clone and no dependency on this repository — one file, read
top to bottom, writing the scripts out as you go. Parts 0, 6 and 7 are the setup
path; parts 8 to 11 are how to use it, how it compounds, the MCP wiring and the
traps.
