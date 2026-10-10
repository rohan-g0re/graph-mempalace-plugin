# graph-mempalace

A knowledge base of this project's past conversations and code that builds and
updates itself. Two graphs route; MemPalace and the repository answer.

One rule holds it together: **the graph never answers, the exchange does.** A node
carries a label, a community and a one-line description — enough to decide what to
look at. The words come from the exchange or the file. Behaviour claims here are the
contract in [docs/DESIGN.md](docs/DESIGN.md), dated where they depend on Claude Code.

## Install

In Claude Code:

```
/plugin marketplace add rohan-g0re/graph-mempalace-plugin
/plugin install graph-mempalace@graph-mempalace
```

Then, once per machine:

```
/graph-mempalace:kb-setup
```

**Restart the session afterwards.** A running session keeps the hooks and the MCP
servers it started with.

You need [`uv`](https://docs.astral.sh/uv/), git, Python 3.11 or newer, and the
`claude` CLI on your PATH — extraction runs through your own login, no API key.
Windows needs Git for Windows, because the hooks run in Git Bash; on ARM let `uv`
pick an x86_64 Python, as `onnxruntime` ships no arm64 wheel. Built and tested on
Claude Code 2.1.273 (2026-10-10). Other harnesses, such as Codex or Cursor:
[docs/manual-setup.md](docs/manual-setup.md).

## Your first session

Nothing to configure. At each session start the plugin tells the model the base
exists, then prints one status line: exchanges on disk, chat-graph nodes, and when
the last compound ran — or `not ingested yet`. On the first session in a project it
has never seen, it ingests in the background and says so: minutes for a short
history, up to about an hour for a long one. Nothing blocks. Coming from 0.1.0, you
also see this once:

```
graph-mempalace: moving this project's memory into its own palace in the background (one-time, a few minutes)
```

Watch any of it in `~/.graph-mempalace/compound.log`.

## Asking

```
/graph-mempalace:kb-ask why did we drop the replenishment model
```

You get the graph nodes that matched, then a `VERBATIM SOURCE` block quoting the
exchanges behind them, each with its id and the command that prints it in full.
Answer from the quoted text. Nodes marked `verification=unverified` are claims. The
model also consults the base on its own, so you often get cited history unasked.

| command | what it does |
|---|---|
| `/graph-mempalace:kb-ask <question>` | queries the chat graph, quotes the exchanges behind the nodes |
| `/graph-mempalace:kb-compound` | runs the four steps now, in the foreground, and reports |
| `/graph-mempalace:kb-doctor` | checks the prerequisites and every link in the chain |

Compounding otherwise happens by itself: every 100k tokens of session growth at a
settled turn, again at compaction, and once on first sight of a project.

## Configuring

Everything has a default, so the file is optional. Four layers, each overriding the
one above it key by key:

| layer | file | may set |
|---|---|---|
| built-in defaults | — | everything |
| yours, machine-wide | `~/.graph-mempalace/config.toml` | everything |
| yours, this project | `~/.graph-mempalace/projects/<slug>/config.toml` | everything |
| the repository's | `<repo>/.graph-mempalace.toml` | everything except `[palace]` |

`[palace]` decides where your memory is written, so a repository you cloned cannot
set it. `palace.path` is this project's palace directory, defaulting to
`~/.graph-mempalace/projects/<slug>/palace`. One environment variable matters too:
`GM_PYTHON`, an absolute path to a Python 3.11 or newer, which runs the hooks and
starts the MemPalace MCP server — `/graph-mempalace:kb-setup` writes it into
`~/.claude/settings.json` for you. Every key: [docs/configuration.md](docs/configuration.md).

## One palace per project

Each project gets its own building. Nothing in one project's memory can reach
another project's.

MemPalace keeps memory in a *palace*. Through 0.1.0 every project on a machine
shared one, and that was a leak: after each `mine` MemPalace digs tunnels between
wings, its search without `--wing` is palace-wide, so is its knowledge graph, and
MCP tools can walk tunnels. In a live run on one machine, two unrelated projects had
six tunnels between them, labelled as shared entities — Python, Chrome, Claude Code.

From 0.2.0 each project has its own palace directory, and every MemPalace call the
plugin makes names it. Tunnels and the knowledge graph live with that palace, so a
palace holding one project has nothing to tunnel to. Point two projects at the same
`palace.path` and they share one, deliberately.

One door is left open, and it is worth saying plainly: the Graphify MCP server reads
the current project's graph by default, and its `project_path` argument can name
another folder. The plugin does not close that.

Verified 2026-10-10. Longer version: [docs/architecture.md](docs/architecture.md).

## Upgrading

```
/plugin marketplace update graph-mempalace
/plugin update graph-mempalace
```

Restart the session; an update changes the code the hooks run.

From 0.1.0 there is nothing to do by hand. The first session after the upgrade moves
this project's memory into its own palace in the background — a mine-only compound,
a few minutes, graphs untouched. The old shared palace, usually
`~/.config/mempalace/palace` or `~/.mempalace/palace`, is left alone; ignore its old
wings. Your own global MemPalace MCP entry in `~/.claude.json`, if you keep one,
still points at the shared palace, and that one is outside the plugin.

## Fixing

`/graph-mempalace:kb-doctor` checks the prerequisites and every link in the chain,
and prints one fix beside each failing line — including the palace in use and the
wings it holds. One symptom per entry:
`skills/graph-mempalace/references/troubleshooting.md`.

## What it costs

The CLI route (`kb_resolve.py ask` and `get`) costs about 290 tokens of instructions
per session. The MemPalace MCP server exposes 45 tools, about 7,700 tokens of
schema, deferred by Claude Code's tool search on current models. Both ship;
`session.inject_instructions = false` turns the instruction block off.

## Privacy

The plugin reads Claude Code transcripts for **this project only**, writes derived
files under `~/.graph-mempalace/` and drawers into this project's own palace, and
sends exchange text to Claude through **your own `claude` CLI** during extraction.
Nothing else leaves the machine. Hooks never block a session: they exit 0 on every
error and log instead. Anything you or the model typed is kept word for word, so
rotate any key you pasted — [SECURITY.md](SECURITY.md) has the detail.

## More

[docs/architecture.md](docs/architecture.md) · [docs/DESIGN.md](docs/DESIGN.md) ·
[CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md). Uninstall with
`/plugin uninstall graph-mempalace`, then delete `~/.graph-mempalace/`. MIT
licensed — see [LICENSE](LICENSE).
