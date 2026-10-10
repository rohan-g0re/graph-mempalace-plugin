# graph-mempalace

A knowledge base of your project's past conversations and code that builds and
updates itself. Two graphs route; MemPalace and the repo answer.

Every behavioural claim below is the contract in `docs/DESIGN.md`, cited inline as
*DESIGN §n*. Where a claim depends on how Claude Code behaves, the date it was
checked is given.

## What you get

- **Automatic ingestion of your Claude Code conversation history, per project.**
  The plugin reads this project's transcripts, splits them into one file per
  exchange with a stable id, and files them in MemPalace (DESIGN §1, §5.6).
- **Two Graphify graphs.** Graph A over the repository's code. Graph B over the
  filtered chat (DESIGN §1).
- **Verbatim answers with exchange ids.** `/graph-mempalace:kb-ask` runs the chat
  graph, then quotes the exchanges behind the nodes it found (DESIGN §6).
- **Compounding you do not have to think about.** Every 100k tokens of session
  growth, again at compaction, and once on the first session in a project the
  plugin has never seen (DESIGN §1, §5.5).

One rule holds the whole thing together: **the graph never answers, the exchange
does.** A node carries a label, a community and a one-line description — enough to
decide what to look at. The words come from the exchange or the file (DESIGN §1).

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

That installs `mempalace` and `graphify` with `uv`, patches graphify's Claude CLI
backend, sets the palace up, and adds `graphify-out/` to your global git excludes
(DESIGN §5.8). **Restart the session afterwards** — a running session keeps the MCP
servers and hooks it started with.

### Requirements

| need | why |
|---|---|
| Claude Code 2.1.273 or newer (built and tested on 2.1.273, 2026-10-10; validating the marketplace and plugin manifests together needs 2.1.289) | plugin, skill and hook behaviour this build relies on (DESIGN §4) |
| [`uv`](https://docs.astral.sh/uv/) | installs the two tools; `kb-setup` never installs `uv` itself (DESIGN §5.8) |
| Python 3.11 or newer, or let `uv` fetch one | the scripts use `tomllib`; standard library only (DESIGN §5.2) |
| `claude` on your PATH | Graph B extraction runs through your own Claude Code login. No API key (DESIGN §8) |
| git | ingestion rules are `.gitignore` rules |

**Windows: Git for Windows is required.** The plugin's hooks are shell-form
commands, and on Windows those run in Git Bash. Without it Claude Code falls back
to PowerShell for hooks and has no Bash tool at all (DESIGN §4). On macOS and Linux
the same commands run in `sh -c`.

**Windows on ARM:** works. `uv` picks an x86_64 Python, because `onnxruntime`
publishes no arm64 wheel and ChromaDB pulls it in. Do not swap in a native ARM64
Python; it feels right and breaks the install. Emulation costs speed, and the cost
lands on the embedding pass.

**Other harnesses** — Codex, pi, Cursor, anything else: see
[docs/manual-setup.md](docs/manual-setup.md).

### Upgrading

Auto-update is off for third-party marketplaces, so updates are two steps and you
choose when (DESIGN §4):

```
/plugin marketplace update graph-mempalace
/plugin update graph-mempalace
```

Restart the session afterwards. An update changes the code the hooks run, and a
running session keeps what it started with.

### Already set this up by hand?

If you wired the hooks and the MCP servers into a project yourself before the
plugin existed, remove those copies now. Hooks have no namespace, so a hook in both
your `settings.json` and the plugin fires twice (DESIGN §4, §8b). The
Troubleshooting entry below says exactly what to delete, and
[docs/configuration.md](docs/configuration.md) shows how to keep the palace wing
and exchanges you already built.

## Your first session

Nothing to configure. On every session start the plugin (DESIGN §5.4):

1. Prints a short block telling the model the knowledge base exists and how to
   query it.
2. Prints one status line: exchanges on disk, Graph B node count, and when the last
   compound ran — or `not ingested yet`.
3. On the **first session in a project it has never seen**, starts an ingest in the
   background and says so.

Watch it: `~/.graph-mempalace/compound.log`.

How long the first ingest takes depends on how much history you have — minutes for
a small project, up to about an hour for a large one. It runs detached, so your
session is never blocked (DESIGN §8).

## Asking

```
/graph-mempalace:kb-ask why did we drop the replenishment model
```

You get a `CHAT` block of graph nodes with their source files, then a
`VERBATIM SOURCE` block quoting the exchanges behind those nodes, each ending in
the command that prints the exchange in full:

```
========================================================================
CHAT  — Graph B decides what to look at, exchanges supply the words
========================================================================
  <nodes, with src=NNNN-<id>.md>

------------------------------------------------------------------------
VERBATIM SOURCE — 2 exchange(s) behind those nodes
------------------------------------------------------------------------

### accc5a03-0019  (2026-09-12)  <topic>
  <the exchange, word for word>
    full: kb_resolve.py get accc5a03-0019
```

Answer from the quoted text, not from a node's description. Nodes marked
`verification=unverified` are claims the extractor found no evidence for.

The model also consults the knowledge base on its own. The session-start block
tells it to look before answering about past work or decisions, so you often get
cited history without asking for it (DESIGN §5.4).

## How compounding works

Four triggers, one chain, one lock.

| trigger | fires when |
|---|---|
| `Stop` hook | 100k or more new tokens since the last compound, **and** the last assistant turn is prose rather than a tool call |
| `PreCompact` hook | always, before the window is summarised (forced; skips the growth and settled checks) |
| First sight | a project with no exchanges directory yet, at session start |
| `/graph-mempalace:kb-compound` | you ask for it |

The chain is four steps (DESIGN §5.6):

1. **Filter** — transcripts to one markdown file per exchange, plus an index.
2. **Mine** — exchanges into a MemPalace wing.
3. **Graph A** — refresh the code graph over the repo.
4. **Graph B** — rebuild the chat graph over the exchanges.

Graph A is written to `<repo>/graphify-out/`, and the plugin adds that path to the
repository's local `.git/info/exclude` — never to a tracked file — so `git status`
stays clean (DESIGN §5.2).

One lock, machine-wide. While a compound is running every other trigger logs that
it skipped and leaves the pending growth pending, so nothing is lost (DESIGN §5.5).
A lock older than three hours is treated as abandoned.

Why a growth gate rather than an absolute size: compaction resets the token count,
so an absolute mark fires twice and then never again. Why "settled": raw growth can
capture a position you reverse two turns later and file it with the same weight as
the conclusion.

## Commands

| command | what it does | when to use it |
|---|---|---|
| `/graph-mempalace:kb-ask <question>` | queries the chat graph and quotes the exchanges behind the nodes | you want history, with ids |
| `/graph-mempalace:kb-setup` | checks prerequisites, then installs and configures them | once per machine, and after an upgrade breaks something |
| `/graph-mempalace:kb-compound` | runs the four steps now, in the foreground, and reports | you just finished something worth remembering |
| `/graph-mempalace:kb-doctor` | checks prerequisites and verifies every link in the chain | an answer came back empty and you want to know why |
| `graph-mempalace` | the model-invoked skill: when to consult the base, how to cite it, how to write back | not typed; the model loads it |

## Configuration

Everything has a default, so the file is optional (DESIGN §5.3).

- Machine-wide: `~/.graph-mempalace/config.toml`
- Per project: `<repo>/.graph-mempalace.toml`, overriding the machine file key by key

The keys people actually change:

| key | default | effect |
|---|---|---|
| `compound.threshold_tokens` | `100000` | new tokens of session growth before the `Stop` hook fires |
| `compound.require_settled` | `true` | only compound when the last assistant block is prose, not a tool call |
| `compound.on_first_sight` | `true` | ingest history on the first session in a new project |
| `graph_a.mode` | `"code-only"` | `code-only` is tree-sitter and free; `full` sends prose files to the LLM too |
| `graph_b.concurrency` | `2` | parallel extraction workers. 4 starved a 16 GB machine |
| `graph_b.token_budget` | `15000` | chunk size for extraction. 60000 made the model drop files |

Every key, its type, and when to change it: [docs/configuration.md](docs/configuration.md).

## Privacy

From DESIGN §8, in plain words:

- The plugin reads Claude Code transcripts for **the current project only**:
  `~/.claude/projects/<slug>/*.jsonl`.
- It writes derived files under `~/.graph-mempalace/`, and the palace under
  `~/.config/mempalace/`.
- Graph B extraction sends exchange text to Claude through **your own `claude`
  CLI** — same account, same terms. Nothing else leaves the machine.
- Anything you or the model typed is kept word for word — the filter drops tool
  results, not message text — so an API key pasted into a message is filed into
  MemPalace and sent through that extraction call. Rotate anything you pasted;
  [SECURITY.md](SECURITY.md) has the detail.
- Hooks never block a session. They exit 0 on every error and log to
  `~/.graph-mempalace/compound.log`.
- Compounding runs detached in the background. A lock stops two at once.

## Troubleshooting

**"prerequisites missing (mempalace, graphify)"** — the session-start hook could not
find the two tools, so it did nothing else (DESIGN §5.4). Run
`/graph-mempalace:kb-setup`, then restart the session.

**MCP servers failed to connect** — `mempalace-mcp` and `graphify-mcp` are not on
the PATH Claude Code was started with (DESIGN §5.9). Typical when the editor was
launched from the Dock on macOS, where a GUI app gets a minimal PATH, or simply
before the session was restarted after `kb-setup` installed the tools.

In order: restart the session after `/graph-mempalace:kb-setup`. If the servers
still fail, run `/graph-mempalace:kb-doctor` and add the two `env` lines it prints
to `~/.claude/settings.json` — forward slashes, on Windows too:

```
{
  "env": {
    "GM_MEMPALACE_MCP_BIN": "C:/Users/you/.local/bin/mempalace-mcp.exe",
    "GM_GRAPHIFY_MCP_BIN": "C:/Users/you/.local/bin/graphify-mcp.exe"
  }
}
```

On Windows the plugin's hooks still run under bash, so Git for Windows must be
installed — Claude Code runs shell-form hooks in the Git Bash it finds itself.
That is a hook requirement, not an MCP one.

**Status still says "not ingested yet" after a long wait** — read the tail of
`~/.graph-mempalace/compound.log`. A first ingest on a large history can take about
an hour. If the log shows nothing recent, run `/graph-mempalace:kb-compound`, which
runs in the foreground and prints each step.

**"a compound is already running"** — the lock is held, so this trigger skipped
without advancing the watermark; the growth stays pending and the next settled turn
retries (DESIGN §5.5). Leave it alone while `mempalace mine` or a graphify
extraction is alive. After three hours the lock is treated as stale and removed.

**The palace shows two wings for one project** — wing names are derived from the
directory name, and a second project with the same name gets a hash suffix. If you
renamed or moved the repo, point the plugin back at the wing you want with
`palace.wing` in the config rather than letting it derive a new one. MemPalace has
no selective delete, so a duplicate wing is easier to avoid than to remove. See
[docs/configuration.md](docs/configuration.md).

**Graph B skipped some exchanges** — extraction is cached per file, so running
`/graph-mempalace:kb-compound` again retries only the files that produced no nodes;
two passes is normal. If many files are still empty, lower `graph_b.token_budget`:
a crowded chunk makes the model drop files from its answer. Those exchanges are
still in the palace and still findable by search; only the graph cannot route to
them.

**Hooks fire twice, or two compounds start at once, after migrating from the manual
setup** — you have two copies of everything. Hooks have no namespace, so a hook
present in both your own `settings.json` and the plugin runs once for each (DESIGN
§4). Delete the manual copies:

- the `SessionStart`, `Stop` and `PreCompact` entries from the project's
  `.claude/settings.json`
- the `mempalace` and `graphify` servers from the project's `.mcp.json`

The plugin provides all five. Keep the palace wing and the exchanges you already
built by setting `palace.wing` and `palace.exchanges_dir` in that project's
`.graph-mempalace.toml` — [docs/configuration.md](docs/configuration.md) shows the
exact file (DESIGN §8b). Restart the session after editing either file.

## Uninstall

```
/plugin uninstall graph-mempalace
```

Then delete `~/.graph-mempalace/` if you want the state gone. The MemPalace wing
stays until you remove it by hand (DESIGN §8).

## How it is built

- Standard-library Python only. Hooks run on machines nobody controls; a dependency
  is a failure mode.
- One shared module decides every path, name, config value and lock, so the hook,
  the gate, the chain and the resolver agree by construction.
- Hooks never block. They exit 0 on every error and log instead.
- Tests run on Linux, Windows and macOS, on Python 3.11 and 3.12. Portability is a
  test, not an intention.
- CI runs `claude plugin validate . --strict`, so unknown manifest keys fail
  loudly instead of loading silently.

More: [docs/architecture.md](docs/architecture.md), [docs/DESIGN.md](docs/DESIGN.md),
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
