# Configuration

Every key has a default, so the config file is optional. A missing file means all
defaults (DESIGN §5.3). The values below come from `DEFAULTS` in
`scripts/gm_common.py`, which is the source of truth.

## Where the files live, and which wins

Four layers. Each one overrides the one before it, key by key — not section by
section, and not file by file.

```
built-in defaults        (gm_common.DEFAULTS)
        <- ~/.graph-mempalace/config.toml                  yours, machine-wide
            <- ~/.graph-mempalace/projects/<slug>/config.toml  yours, this project only
                <- <repo>/.graph-mempalace.toml                the repository's
```

At most three files are read per call. A key you do not set keeps the value from
the layer below, so a file holding one key changes one thing.

One exception, and it is deliberate: **`[palace]` may not come from the
repository's file.** A `[palace]` table in `<repo>/.graph-mempalace.toml` is
dropped with a warning on stderr — `[palace] is read from <user config> only;
ignored here`. Its three keys — `wing`, `exchanges_dir` and `path` — decide which
palace the plugin writes into, where the exchanges live and which wing it mines
into, and a repository you cloned must not be able to decide that for your
machine. Set them in either of your own two files: the machine-wide one, or the
per-project one under `~/.graph-mempalace/projects/<slug>/` (outside the
repository, so a clone cannot plant it). Every other section can be set in any of
the three files.

The format is TOML, parsed with the standard library's `tomllib`. That is why the
plugin needs Python 3.11 or newer.

## `[compound]` — when a compound happens

| key | default | type | effect | change it when |
|---|---|---|---|---|
| `threshold_tokens` | `100000` | integer | New tokens of session growth before the `Stop` hook fires a compound. The gate measures *growth* since the last compound, not absolute context size. | You want history captured more often (lower) or you find compounds intrusive (raise). Below about 20000 you will compound several times an hour. |
| `require_settled` | `true` | boolean | Only compound when the last assistant block is prose. A `tool_use` block means the turn is mid-loop and whatever is being said is provisional. When unsettled the gate exits without advancing the watermark, so the pending growth is re-checked next turn. | Almost never. Set `false` only if your sessions end on tool calls so consistently that nothing ever compounds. |
| `on_first_sight` | `true` | boolean | At session start, a project with no exchange files and no `_index.jsonl` (the filter never completed) but an existing transcripts directory gets a background ingest. A project whose filter completed with zero exchanges is not re-ingested every session. | Set `false` on a machine where you want to choose when the first (longest) ingest runs. |
| `on_precompact` | `true` | boolean | `PreCompact` forces a compound before the context window is summarised, skipping the growth and settled checks. | Set `false` if compaction happens at awkward moments. You lose the backstop: whatever grew since the last compound waits for the next trigger. |
| `lock_stale_hours` | `3.0` | float | A lock file older than this is treated as abandoned and removed, so one crashed compound cannot block every later one. Fractions are allowed (`0.5` = thirty minutes); `0` disables the stale check. The running chain refreshes the lock between steps and during long steps, so this only matters for a chain that died. | Rarely. Lowering it risks a second compound starting beside a chain that is still alive; `0` leaves a crashed chain's lock in place until you delete it, and no compound runs until you do. |

## `[graph_a]` — the code graph

| key | default | type | effect | change it when |
|---|---|---|---|---|
| `mode` | `"code-only"` | string | `"code-only"` extracts with tree-sitter: local, free, seconds. `"full"` also sends the repository's prose files through the `claude` CLI. Any other value warns and falls back to `"code-only"`. | Set `"full"` when your design notes and docs matter as much as your source. It costs real time on every compound and sends those files to Claude. |
| `cluster` | `false` | boolean | Name the graph's communities after each update. One extra model call, cheap. | Turn on if you read the code graph directly and want readable community names. Off by default because Graph A updates run free. |

## `[graph_b]` — the chat graph

| key | default | type | effect | change it when |
|---|---|---|---|---|
| `model` | `"sonnet"` | string | `GRAPHIFY_CLAUDE_CLI_MODEL` for extraction. Graphify's own default is Opus, which its source calls overkill for structured-JSON extraction. | Rarely. Haiku is faster and silently omitted 6 of 15 files on a reference build, so it trades completeness for speed. |
| `concurrency` | `2` | integer | Parallel extraction workers. Four stalled to one chunk per 25 minutes on a 16 GB machine and starved a concurrent mine into `MemoryError`; two finished the same work. | Raise only on a machine with plenty of free RAM, and watch the first run. |
| `token_budget` | `15000` | integer | Chunk size sent to the model per call. The default of 60000 put dozens of exchanges in one chunk and the model dropped some of them from its answer. Per-file time was the same either way. | Lower it if files keep producing no nodes. Raising it costs completeness and buys nothing measurable. |
| `passes` | `2` | integer | How many times to re-run extraction for files that produced no nodes. Extraction is cached per file, so a second pass retries only the empty ones. | Raise to 3 if a run still leaves many files empty. Two passes is normal. |
| `cluster` | `true` | boolean | Name the chat graph's communities after extraction. | Leave on. Community names are what make a chat-graph answer readable. |

## `[palace]` — where this project's memory lives

**This section is read from your own files only** — `~/.graph-mempalace/config.toml`
(machine-wide) or `~/.graph-mempalace/projects/<slug>/config.toml` (one project).
Setting it in a repository's `.graph-mempalace.toml` warns and changes nothing, and
that rule covers all three keys below, `path` included.
For adopting one project's history use the per-project file; the machine-wide one
would point every project at the same palace, wing and folder.

| key | default | type | effect | change it when |
|---|---|---|---|---|
| `wing` | `""` | string | Empty means derive the name: `<directory-name>_dialogue`, or `<directory-name>_<hash6>_dialogue` if that name already belongs to a different project. Set it to pin an exact wing. Must match `[A-Za-z0-9._-]{1,64}`: letters, digits, dot, underscore and dash, 1 to 64 characters. Anything else warns and the name is derived instead. | You already have a wing from a manual setup, or you renamed or moved the repository and want to keep writing to the old wing. |
| `exchanges_dir` | `""` | string | Empty means `~/.graph-mempalace/projects/<slug>/exchanges`. A path here is used instead. It must be absolute; a leading `~` is expanded and counts as absolute. A relative path warns and the default is used. Graph B is built in `graphify-out/` inside whichever directory this resolves to. | You already have a filtered-exchanges folder you do not want to rebuild. |
| `path` | `""` | string | The MemPalace palace this project writes into. Empty means `~/.graph-mempalace/projects/<slug>/palace` — this project's own palace. An absolute path pins it instead; a leading `~` is expanded and counts as absolute. A relative path warns and the default is used — the same path rule as `exchanges_dir`. | You want the palace somewhere else, for example on another drive. Or you want two projects to share one palace deliberately. |

`wing` and `exchanges_dir` are the supported way to adopt an existing setup
(DESIGN §9). MemPalace has no selective delete and a duplicate wing cannot be
removed, so pinning the name is much cheaper than discovering a second wing later.

Several projects given the same `path` share one palace, on purpose. Give each
project its own path to keep them apart. The palace is the unit that keeps them
apart, not the wing: MemPalace digs cross-wing tunnels after every mine, a search
without `--wing` is palace-wide, so is the knowledge graph, and the MCP tools can
walk tunnels. A palace that holds one project has nothing to tunnel to (verified
2026-10-10). `docs/architecture.md` has the long version.

The restrictions all guard the same thing. A wing name reaches a MemPalace command
line and a path on disk, so it is limited to characters that mean nothing to a
shell or a filesystem. A relative `exchanges_dir` or `path` would resolve against
whatever directory the hook happened to start in, which is not the same directory
twice. And a cloned repository cannot set any of the three, so checking a file into
a repository cannot redirect someone else's palace.

## `[session]` — what the session-start hook prints

| key | default | type | effect | change it when |
|---|---|---|---|---|
| `inject_instructions` | `true` | boolean | Print the short block that tells the model the knowledge base exists and how to query it. Its stdout is injected into the session, so it is paid every session. | Set `false` only if you will always ask with `/graph-mempalace:kb-ask`. With it off the model will not consult the base on its own. |
| `show_status` | `true` | boolean | Print one status line: exchanges on disk, Graph B node count, last compound time, or `not ingested yet`. | Set `false` once you trust it and want the session to start silent. |

## A complete example file

Every key, set to its default. Copy it to `~/.graph-mempalace/config.toml` and
change what you need; delete the lines you leave alone.

```toml
[compound]
threshold_tokens = 100000
require_settled = true
on_first_sight = true
on_precompact = true
lock_stale_hours = 3.0

[graph_a]
mode = "code-only"
cluster = false

[graph_b]
model = "sonnet"
concurrency = 2
token_budget = 15000
passes = 2
cluster = true

[palace]
wing = ""
exchanges_dir = ""
path = ""

[session]
inject_instructions = true
show_status = true
```

## Migrating from the manual setup

The manual setup wired the same three hooks into a project's
`.claude/settings.json` and the same two MCP servers into its `.mcp.json`, with the
scripts under `~/.claude/skills/kb-setup/`. With the plugin installed, **those
copies must go** — hooks have no namespace, so every hook would fire twice and every
server would start twice (DESIGN §4, §8b).

Delete, in the project:

- the `SessionStart`, `Stop` and `PreCompact` entries in `.claude/settings.json`
- the `mempalace` and `graphify` servers in `.mcp.json`

Then keep the palace wing and the exchanges folder you already built. The two keys
that do it live in `[palace]`, so they go in **your own per-project file**, not in
the repository. For a project at `C:/Users/you/Projects/myproject`, that file is:

```toml
# ~/.graph-mempalace/projects/c--Users-you-Projects-myproject/config.toml
[palace]
wing = "myproject_dialogue"
exchanges_dir = "~/.mempalace/dialogue/c--Users-you-Projects-myproject"
```

Substitute your own wing name and your own slug. The slug is the folder name Claude
Code uses under `~/.claude/projects/`: the repository's absolute path with every
non-alphanumeric character replaced by a dash, and the drive letter lower-cased on
Windows — and it is the same slug that names the per-project folder above. Restart
the session after editing `settings.json` or `.mcp.json`; a running session keeps
the hooks and servers it started with.

A `<repo>/.graph-mempalace.toml` carrying that same `[palace]` table is ignored with
a warning, so the migration cannot be done from inside the repository. The
machine-wide `~/.graph-mempalace/config.toml` would also work, but it would point
every project on the machine at that one wing and folder — use it only on a
machine with a single project.

Without the overrides the plugin derives its own names, builds a parallel wing, and
re-ingests the history you already have.

One thing changed in 0.2.0: the palace is per project now. A project migrating from
the manual setup gets its own palace directory, `~/.graph-mempalace/projects/<slug>/palace`,
and there is nothing to do by hand. The first session after the upgrade prints

```
graph-mempalace: moving this project's memory into its own palace in the background (one-time, a few minutes)
```

and files the exchanges you already have into the new palace in the background.
Graphs are not rebuilt for it. The old shared palace — MemPalace's own default,
usually `~/.config/mempalace/palace` or `~/.mempalace/palace` — is left untouched,
and its old wings can be ignored (verified 2026-10-10).

To keep writing into that old shared palace instead, set `palace.path` to it, in the
same per-project file as the two keys above. That puts several projects back in one
palace, which is the thing a per-project palace exists to prevent.

## Environment variables

Config decides behaviour; these decide which programs run and where state lives.
They are read from the environment of the process that starts them, so a hook sees
what the Claude Code process inherited — set them as user or system variables, or
in the shell you start `claude` from. The last two are not yours to set: Claude
Code sets `CLAUDE_PROJECT_DIR`, and the plugin sets `MEMPALACE_PALACE_PATH` on
every MemPalace call.

| variable | read by | effect |
|---|---|---|
| `GM_HOME` | `gm_common`, `scripts/py.sh` | Stands in for your home directory, so the state directory becomes `$GM_HOME/.graph-mempalace/` and the launcher looks there too. The test suite uses it to run against a temporary home. |
| `GM_PYTHON` | `scripts/py.sh`, `.mcp.json` | The interpreter the hooks run. Tried first, before the cached path and the probe. It is also the `command` for the MemPalace MCP server, which starts through `scripts/mcp_launch.py`. Point it at an absolute path to a Python 3.11 or newer, for example `C:/Python312/python.exe`. `/graph-mempalace:kb-setup --install` writes `env.GM_PYTHON` into `~/.claude/settings.json`; it takes effect in the next session. |
| `GM_<TOOL>_BIN` | `find_tool` in `gm_common` | A full path to one tool, tried before every other lookup. The name is the tool's own, upper-cased, with every non-alphanumeric character as `_`: `GM_MEMPALACE_BIN`, `GM_GRAPHIFY_BIN`, `GM_MEMPALACE_MCP_BIN`, `GM_GRAPHIFY_MCP_BIN`. Use one when a tool is installed somewhere the plugin does not look. |
| `GM_GRAPHIFY_MCP_BIN` | `.mcp.json` | A full path to the Graphify MCP server's binary, substituted straight into that server's `command`. Unset, `.mcp.json` falls back to the bare name `graphify-mcp`, and the server has to be on `PATH`. It is the same name `find_tool` already reads, so setting it fixes both the MCP spawn and the scripts. |
| `CLAUDE_CONFIG_DIR` | `gm_common` | Claude Code's config directory, which is where it keeps transcripts. The plugin follows it: transcripts come from `$CLAUDE_CONFIG_DIR/projects/<slug>`, and from `~/.claude/projects/<slug>` when it is unset. |
| `CLAUDE_PROJECT_DIR` | `scripts/mcp_launch.py` | Claude Code sets it to the project directory. The launcher reads it to learn which project's palace to pass to `mempalace-mcp`. |
| `MEMPALACE_PALACE_PATH` | MemPalace | This project's palace. The plugin sets it on every MemPalace call, so a child process that forgets `--palace` still lands in the right palace. |

Each one fails differently, which is worth knowing before you set it.
`GM_PYTHON` has two jobs, and they do not fail the same way. For the hooks it is
only a first guess: `scripts/py.sh` tries it and carries on with its normal probe
when it does not answer, so a wrong value costs nothing there, and it accepts a
two-word form, `GM_PYTHON="py -3"`. For the MemPalace MCP server it is the
`command` itself, with no probe behind it — unset, `.mcp.json` falls back to bare
`python` — so a `GM_PYTHON` that does not run means the server does not start. That
is the harder failure of the two. One absolute path to a Python 3.11 or newer covers
both jobs. A `GM_<TOOL>_BIN` path has to exist, or
it is ignored and the normal lookup runs — that is the rung `GM_MEMPALACE_MCP_BIN`
sits on, through `find_tool`. `GM_GRAPHIFY_MCP_BIN` has no fallback once set —
`.mcp.json` substitutes whatever is there — so a wrong path is a server that does
not start. `/graph-mempalace:kb-doctor` prints the tool paths it resolved, which is
the quickest way to see what took effect.

You need these variables when Claude Code cannot see the `PATH` you installed into.
It spawns MCP servers with the `PATH` it was started with, so an editor launched
from a GUI — on macOS especially, where that environment is minimal — may never see
`~/.local/bin`, and a server fails to connect while the same binary runs fine in a
terminal. Set the full paths under `"env"` in `~/.claude/settings.json`;
`/graph-mempalace:kb-doctor` prints the exact lines to paste.

```json
{
  "env": {
    "GM_PYTHON": "C:/Python312/python.exe",
    "GM_MEMPALACE_MCP_BIN": "C:/Users/you/.local/bin/mempalace-mcp.exe",
    "GM_GRAPHIFY_MCP_BIN": "C:/Users/you/.local/bin/graphify-mcp.exe"
  }
}
```

## Unknown keys and wrong types

Nothing fails. Every problem produces a single warning line on **stderr** — never on
stdout, which is model-facing — and the default is kept:

| problem | what you see | what happens |
|---|---|---|
| unknown section | `unknown section [typo] ignored` | the whole section is skipped |
| unknown key | `unknown key compound.treshold_tokens ignored` | that key is skipped; the rest of the section still applies |
| a section that is not a table | `[compound] must be a table; ignored` | the section is skipped |
| wrong type | `compound.threshold_tokens should be int; keeping default` | the default is kept |
| `graph_a.mode` is not `code-only` or `full` | `graph_a.mode must be 'code-only' or 'full'; keeping default` | reset to `code-only` |
| `[palace]` in a repository's file | `<repo>/.graph-mempalace.toml: [palace] is read from <user config> only; ignored here` | the section is dropped; your own config's values stand |
| `palace.wing` with other characters | `palace.wing must match [A-Za-z0-9._-]{1,64}; deriving the wing instead` | the name is derived as if the key were empty |
| a relative `palace.exchanges_dir` | `palace.exchanges_dir must be an absolute path; using the default` | the default exchanges directory is used |
| a relative `palace.path` | `palace.path must be an absolute path; using the default` | the default palace directory is used |
| malformed TOML | `could not parse <path>: ...; using defaults` | the whole file is ignored |
| Python older than 3.11 | `... has no tomllib; config at <path> ignored` | the whole file is ignored |

Two details worth knowing. A float that is a whole number is accepted where an
integer is wanted, so `concurrency = 2.0` works. A boolean is **not** accepted where
an integer is wanted, so `threshold_tokens = true` warns and keeps `100000`.

Because the warnings go to stderr, a typo in a config file is easy to miss during a
normal session. `/graph-mempalace:kb-doctor` is the quickest way to see them.
