# Security

This plugin reads your conversation history and runs local code on every turn. Both
of those deserve a plain description, so here it is.

## Supported versions

| version | supported |
|---|---|
| latest `0.x` | yes |
| anything older | no — update first |

This is pre-1.0 software. Fixes land on the newest `0.x` release only. There are no
backports. Auto-update is off for third-party marketplaces, so updating is something
you do on purpose: `/plugin marketplace update graph-mempalace` then
`/plugin update graph-mempalace`.

## What the plugin reads and writes

From `docs/DESIGN.md` §8:

- **Reads** Claude Code transcripts for **the current project only**:
  `~/.claude/projects/<slug>/*.jsonl`. The slug is derived from the repository's
  absolute path, so a session in one project never reads another project's
  transcripts.
- **Reads** your repository, through Graphify's code extraction. In the default
  `graph_a.mode = "code-only"` that is tree-sitter, local, with no model involved.
- **Writes** derived files under `~/.graph-mempalace/`: the filtered exchanges, the
  chat graph's output, the configuration, one small token mark per session, a
  lock, a tool-path cache and `compound.log`.
- **Writes** MemPalace drawers into this project's own palace. The default is
  `~/.graph-mempalace/projects/<slug>/palace`, and `palace.path` overrides it. One
  palace per project means nothing in one project's memory can reach another
  project's. Before 0.2.0 a shared palace could be reached across projects, because
  MemPalace digs cross-wing tunnels after every mine, and both its search without
  `--wing` and its knowledge graph are palace-wide (verified 2026-10-10).
- **Writes** `graphify-out/` inside the repository for the code graph, and adds that
  path to your global git excludes during setup.

The configuration lives in `~/.graph-mempalace/config.toml` rather than in the
plugin's own data directory, specifically so you can find it, read it and delete it.

There is no telemetry and no analytics. The plugin makes no network call of its own.

## Hooks run local Python

The plugin installs three hooks: `SessionStart`, `Stop` and `PreCompact`. Each one
runs Python from the installed copy of the plugin, through a bundled shell launcher,
`scripts/py.sh` under `bash`. That means:

- **Installing or updating the plugin changes the code that runs on every turn, in
  every session, in every project.** The plugin root carries the version as a path
  segment, so an update is a different directory of code. Read a diff before taking
  an update if that matters to you.
- The hooks execute local Python by design. They are not sandboxed. They run with
  your user's permissions and can read anything you can read.
- They are written to exit 0 on every error and to log rather than print, so a
  failure is quiet. Quiet is good for your session and bad for noticing problems.
  `~/.graph-mempalace/compound.log` is where the evidence is.
- Long work is spawned detached, so a compound keeps running after the hook that
  started it returns, and after the session ends.

The plugin also declares two MCP servers, `mempalace` and `graphify`. Those are
third-party tools that `kb-setup` installs with `uv`. They are not maintained here,
and they run as separate local processes with your permissions.

Claude Code spawns the Graphify server directly — `graphify-mcp` from your PATH, or
the absolute path you set in `GM_GRAPHIFY_MCP_BIN` — with no shell in between. The
MemPalace server has one step in front of it. Claude Code runs the plugin's own
Python launcher, `scripts/mcp_launch.py`, with the interpreter named in `GM_PYTHON`.
The launcher reads `CLAUDE_PROJECT_DIR`, then runs `mempalace-mcp --palace <that
project's palace>`, so the server opens this project's palace and no other. It finds
that binary on your PATH, or at the absolute path you set in `GM_MEMPALACE_MCP_BIN`.
There is still no shell in between (verified 2026-10-10).

`/graph-mempalace:kb-setup --install` writes `env.GM_PYTHON` into
`~/.claude/settings.json`. That is a file of yours, outside the plugin, and the value
should be an absolute path to a Python 3.11 or newer. It takes effect in your next
session (verified 2026-10-10).

## Graph B sends exchange text through your own claude CLI

Building the chat graph needs a language model. The plugin uses Graphify's
`claude-cli` backend, which means **exchange text is sent to Claude through your own
`claude` CLI** — your account, your login, your terms (DESIGN §8). No API key is
used and none is stored. Nothing else leaves the machine.

Two consequences worth stating plainly:

- Whatever is in your conversation history goes through that call. If a session
  contained a secret, a token or a customer's data, extraction will send it. Review
  what is in the transcripts before turning this loose on a sensitive project.
  `.graphifyignore` narrows what Graphify extracts and nothing more; the next
  section covers the palace side.
- Mining has no write gating. Nothing reviews a drawer before it is filed, and
  MemPalace has no selective delete — `sync` only prunes drawers whose source file
  became ignored, deleted or moved. Getting ingestion right before the first mine is
  much cheaper than correcting it afterwards.

Setting `graph_a.mode = "full"` sends your repository's prose files through the same
path. The default does not.

## Anything you pasted into a conversation is kept verbatim

The filter that builds exchange files is structural, not a secret scanner. It drops
tool results, tool inputs, thinking blocks, images, attachments and very short turns.
It keeps user text and assistant text word for word. A key, a token or a password you
typed or pasted into a message is kept. So is one the model repeated back in prose.

- That text goes three places: an exchange file under `~/.graph-mempalace/`, a
  MemPalace drawer, and a call through your own `claude` CLI during Graph B
  extraction.
- `.graphifyignore` only narrows what Graphify extracts. It does not affect MemPalace
  mining, so it cannot keep anything out of the palace. MemPalace has no selective
  delete; `sync` prunes only drawers whose source file became ignored, deleted or
  moved.
- Treat any credential pasted into a Claude Code session as exposed, and rotate it.
  Deleting the exchange file and running another compound does not reach back into
  drawers that were already filed.

## Reporting a problem

Open a **GitHub issue** on `rohan-g0re/graph-mempalace-plugin`. There is no security
email address and no private disclosure channel.

Because of that, weigh what you put in the report. If you think the issue is
genuinely sensitive, open an issue that describes the shape of it — the file, the
function, the class of failure — without a working exploit, and say you have details
to share privately. Someone will reply on the thread.

Include where you can:

- what you did and what happened
- your OS and the output of `claude --version`
- the plugin version
- the relevant lines from `~/.graph-mempalace/compound.log`

Do not paste exchange text, palace contents, or anything from the log you would not
publish. The log records paths, counts and timings rather than conversation text,
but paths alone can be revealing.
