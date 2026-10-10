# graph-mempalace — design and build contract

This is the contract every part of the plugin is built against. Architecture and
the memory-system rules were decided before this repo existed and are not up for
revision here; what this document fixes is how they are packaged as a Claude Code
plugin that installs in one command, needs no per-project setup, and behaves the
same on Windows, macOS and Linux.

Read `AGENTS.md` for the authoring standard (how to write skills, scripts, docs).
Read `docs/reference-memory-system-longform.md` for the research behind each rule.

## 1. What the plugin does

Two graphs for navigation, two stores for truth:

| domain | navigation | ground truth |
|---|---|---|
| code | Graph A, Graphify over the repo | the repository, `file:line` |
| chat | Graph B, Graphify over filtered dialogue | MemPalace drawers, keyed by exchange id |

The graph routes; it never answers. Code is never copied into MemPalace. The chat
ground truth is MemPalace drawers inside this project's own palace, and there is
one palace per project. The plugin ingests Claude Code's own transcripts for the
current project (one file per exchange, stable ids), mines them into MemPalace,
builds both graphs, and keeps them current by itself: every 100k tokens of session
growth, at compaction, and on the first session in a project it has never seen.

Zero per-project setup is a hard requirement. A user installs the plugin once per
machine, opens any repo, and the knowledge base starts accumulating.

## 2. Names

| thing | value |
|---|---|
| plugin `name` (plugin.json) | `graph-mempalace` |
| marketplace `name` (marketplace.json) | `graph-mempalace` (same repo, `source: "./"`) |
| install id | `graph-mempalace@graph-mempalace` |
| GitHub repo | `rohan-g0re/graph-mempalace-plugin` |
| skills | `graph-mempalace` (model-invoked), `kb-setup`, `kb-compound`, `kb-doctor`, `kb-ask` (user-invoked) |
| slash form | `/graph-mempalace:kb-ask ...` etc. |
| state dir | `~/.graph-mempalace/` |
| config | `~/.graph-mempalace/config.toml` (user, machine-wide) <- `~/.graph-mempalace/projects/<slug>/config.toml` (user, one project) <- `<repo>/.graph-mempalace.toml` (repo; no `[palace]`) |

No colons, no `claude`/`anthropic` in any name (validator rules). Kebab-case.

## 3. Repository layout

```
.claude-plugin/plugin.json          name, version, description, author, homepage, repository, license, keywords
.claude-plugin/marketplace.json     name graph-mempalace, owner, plugins: [{name: graph-mempalace, source: "./"}]
hooks/hooks.json                    WRAPPED in top-level "hooks": SessionStart, Stop, PreCompact
.mcp.json                           mempalace-mcp via ${GM_PYTHON:-python} + mcp_launch.py; graphify-mcp direct (${GM_GRAPHIFY_MCP_BIN:-graphify-mcp})
scripts/
  py.sh                             POSIX launcher: finds a Python >= 3.11 (python3/python/py -3, then uv), caches it
  gm_common.py                      shared: paths, slug, config, lock, log, tool discovery, detached spawn
  session_start.py                  SessionStart hook: inject instructions + status; first-sight compound
  compound_gate.py                  Stop/PreCompact hook: token-growth gate, settledness, lock, re-seed
  compound_run.py                   the chain: filter -> mine -> Graph A -> Graph B -> cluster -> sweep
  filter_transcripts.py             transcripts -> one markdown file per exchange + _index.jsonl
  kb_resolve.py                     ask / get / search / doctor (graph -> exchange / file)
  kb_setup.py                       prerequisites: check, install (uv tool), mempalace init, patch, gitignore
  patch_graphify.py                 idempotent --strict-mcp-config patch for graphify's claude-cli backend
  mcp_launch.py                     MCP launcher: reads CLAUDE_PROJECT_DIR, runs mempalace-mcp --palace <that project's palace>
skills/
  graph-mempalace/SKILL.md          model-invoked: how to use the KB; references/*.md for depth
  kb-setup/SKILL.md                 user-invoked (disable-model-invocation: true)
  kb-compound/SKILL.md              user-invoked
  kb-doctor/SKILL.md                user-invoked
  kb-ask/SKILL.md                   user-invoked
tests/                              pytest, stdlib-only code under test, runs on ubuntu/windows/macos
docs/
  DESIGN.md                         this file
  configuration.md                  every config key, default, effect
  architecture.md                   the two-domain design, condensed from the long form
  manual-setup.md                   harness-agnostic single-file setup (Codex, pi, anyone)
  reference-*.md                    the originating documents, kept verbatim
.github/workflows/ci.yml            claude plugin validate --strict; pytest matrix
README.md CHANGELOG.md CONTRIBUTING.md SECURITY.md LICENSE AGENTS.md
```

No `CLAUDE.md` at the plugin root: the validator warns on it and CI runs
`--strict`. No `bin/`: claude.ai and Cowork refuse plugins that ship one. No
`commands/`: skills are the recommended surface and act as slash commands.

## 4. Platform facts the build relies on (verified 2026-10-10)

- Manifest at `.claude-plugin/plugin.json`; every other file at plugin root. `name`
  is the only required key. `homepage` must parse as a URL or the plugin fails to
  load. Component paths must start with `./` and resolve inside the root.
- `hooks/hooks.json` needs the top-level `"hooks"` wrapper. Inline `hooks` in
  plugin.json is the bare event map. Do not mix the two shapes up.
- `${CLAUDE_PLUGIN_ROOT}` resolves in hook `command`/`args`, MCP `command`/`args`/
  `env`, and skill bodies. On Windows it expands with forward slashes. Quote it in
  shell-form commands: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" ...`. It is NOT in
  the environment of Bash-tool commands — skills therefore write the substituted
  path into the command text, where it is substituted.
- `${CLAUDE_PLUGIN_ROOT}` changes on every update (version is a path segment).
  Never store state there. We store state in `~/.graph-mempalace/`, not in
  `${CLAUDE_PLUGIN_DATA}`, because users must be able to find and edit the config.
- SessionStart: plain stdout is injected into the session. Matcher-less entry
  fires on startup, resume, clear and compact. Keep the injected block small; it
  is paid every session.
- Skills: `skills/<name>/SKILL.md`, frontmatter `name` must match the directory,
  `description` is paid in every session's context (keep it one tight sentence
  plus trigger phrases), `disable-model-invocation: true` makes a skill
  user-invoked only. Namespaced as `graph-mempalace:<skill>`.
- MCP `.mcp.json` at plugin root; `command` may be any binary on PATH or a bundled
  script; nothing installs it for you.
- Claude Code sets `CLAUDE_PROJECT_DIR`, and it starts a plugin's MCP server
  inside the project directory. That is how a per-project MCP server learns
  which project it serves.
- The `env` block in `~/.claude/settings.json` reaches `.mcp.json` variable
  expansion and the server process's own environment. It takes effect in the
  next session, not the running one.
- `/graph-mempalace:kb-setup --install` writes `env.GM_PYTHON` there for that
  reason.
- Marketplace in the same repo: `.claude-plugin/marketplace.json` with one entry,
  `source: "./"`, entry name identical to plugin name. Users:
  `/plugin marketplace add rohan-g0re/graph-mempalace-plugin` then
  `/plugin install graph-mempalace@graph-mempalace`.
- Versioning: `version` set and bumped per release (pin semantics). Validator
  warns when missing; `claude plugin tag` cuts `graph-mempalace--vX.Y.Z` tags.
- CI gate: `claude plugin validate . --strict`.
- `claude plugin details graph-mempalace` prints always-on token cost; check it.
- Shell-form hook commands run in `sh -c` on macOS/Linux and in Git Bash on
  Windows (PowerShell only when Git Bash is not installed — and then Claude Code
  has no Bash tool either). The `py.sh` launcher is the one place to change if
  that ever shifts. `shell` accepts only `"bash"` or `"powershell"`.
- Command-hook `timeout` is in seconds, default 600 (30 on UserPromptSubmit). A
  timed-out hook's output is discarded; it never blocks.
- SessionStart plain stdout and `additionalContext` are capped at 10,000
  characters, no override; over the cap Claude gets a file path and a 2,000-char
  preview and is not told to read the file. SessionStart matchers: `startup`,
  `resume`, `clear`, `compact`, `fork`. PreCompact: `manual`, `auto`.
- Stop hook input carries `session_id`, `transcript_path`, `cwd`,
  `stop_hook_active`, `last_assistant_message`; exit 0 with empty or `{}` stdout
  is a no-op; exit 2 blocks (never do that here); exit 1 is a non-blocking error.
- Hooks have no namespace: a hook present in both the user's `settings.json` and
  the plugin runs twice per event. Anyone migrating from the manual setup must
  delete the `settings.json` copies. README and `kb-doctor` say so.
- Skill frontmatter (all optional, kebab-case except `when_to_use`): `name`,
  `description`, `when_to_use`, `argument-hint`, `arguments`,
  `disable-model-invocation`, `user-invocable`, `allowed-tools`,
  `disallowed-tools`, `model`, `effort`, `context`, `agent`, `background`,
  `hooks`, `paths`, `shell`, `metadata`, `license`, `compatibility`. Unknown
  fields are ignored silently. Description is injected into the system prompt:
  third person, under 1,536 characters combined with `when_to_use`. Keep
  SKILL.md under 500 lines and put the instructions that matter at the top
  (compaction re-attaches only the first 5,000 tokens).
- Arguments in skill bodies: `$ARGUMENTS` (all), `$ARGUMENTS[N]` / `$N` are
  0-based (`$0` is the first argument). `` !`cmd` `` runs at render time and a
  failing command aborts the whole invocation — do not use it here.
- Plugin MCP servers register as `plugin:graph-mempalace:<server>`; their tools
  are `mcp__plugin_graph-mempalace_<server>__<tool>`. `.mcp.json` accepts
  `${VAR}` and `${VAR:-default}` from the environment.
- A marketplace added from a local path loads its plugins in place: edits take
  effect at the next session start or `/reload-plugins`, no version bump. That
  is the development loop. `claude --plugin-dir <path>` is the other.
- Auto-update is off for third-party marketplaces; users update with
  `/plugin marketplace update graph-mempalace` then `/plugin update graph-mempalace`.

## 5. Runtime design

### 5.1 Python launcher — `scripts/py.sh`

Hooks fire every turn, so the launcher must be cheap and must work where `python`
is missing (macOS) or where `python3` is a Store stub (Windows).

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/compound_gate.py" [args]
```

Algorithm: if `$GM_PYTHON` is set and runs, use it. Else read the cached path in
`~/.graph-mempalace/python_path`; if it still runs, use it. Else probe, in order
`python3`, `python`, `py -3` (Windows first tries `python`), accepting the first
that reports `sys.version_info >= (3, 11)`; cache it. Else fall back to
`uv run --no-project --python ">=3.11" python`. If nothing works, print one line to
stderr (`graph-mempalace: no Python >= 3.11 found; run /graph-mempalace:kb-setup`)
and exit 0 so the hook never blocks the session. Measured: bare interpreter
~200 ms, `uv run` ~700 ms on the reference laptop.

### 5.2 Shared module — `scripts/gm_common.py`

Everything path-, config- and state-related lives here, so the gate, the chain,
the resolver and the SessionStart hook agree by construction. Public API:

```
STATE_DIR, CONFIG_PATH, LOG_PATH, LOCK_PATH, WATERMARK_PATH, WINGS_PATH
load_config(project_dir) -> dict          defaults <- ~/.graph-mempalace/config.toml <- <repo>/.graph-mempalace.toml
project_token(project_dir) -> str         lower-case basename, [a-z0-9._-] only
slug_for(project_dir) -> str              Claude Code's ~/.claude/projects folder name
transcripts_dir(project_dir) -> Path
project_state_dir(project_dir) -> Path    ~/.graph-mempalace/projects/<slug>
exchanges_dir(project_dir, cfg) -> Path   override or project_state_dir/exchanges
palace_path(project_dir, cfg) -> Path     override or project_state_dir/palace; one palace per project
wing_for(project_dir, cfg) -> str         override, else <token>_dialogue, else <token>_<hash6>_dialogue on collision
mempalace_argv(project_dir, cfg, *sub) -> list[str] | None   mempalace --palace <palace> <sub...>; None when mempalace is missing
graph_a_path(project_dir) -> Path         <repo>/graphify-out/graph.json
graph_b_path(project_dir, cfg) -> Path    <exchanges_dir>/graphify-out/graph.json
find_tool(name) -> str | None             $GM_<NAME>_BIN, ~/.local/bin/<name>(.exe), `uv tool dir --bin`, PATH
tool_env(cfg, project_dir) -> dict        GRAPHIFY_* for graphify runs; MEMPALACE_PALACE_PATH for a project
lock_acquire() / lock_held(stale_hours) -> bool
lock_touch()                              heartbeat: the chain refreshes the lock between steps
lock_release(force=False)                 unlinks only when the lock file's pid is this process
read_json(path, default) / write_json_atomic(path, obj)
append_log(line)                          timestamped line to LOG_PATH
uv_output(argv)                           uv queries with --color never + ANSI stripped (FORCE_COLOR is set in Claude Code shells)
spawn_detached(argv, log_path)            Windows DETACHED_PROCESS|CREATE_NO_WINDOW, POSIX start_new_session
```

Rules that came out of the live run and the adversarial review (2026-10-10):

- `[palace]` is read from the user config only, all three keys: `wing`,
  `exchanges_dir` and `path`. A repository's `.graph-mempalace.toml` cannot
  choose which palace the plugin writes to, where it puts the exchanges, or
  which wing it mines into. `palace.wing` must match `[A-Za-z0-9._-]{1,64}`;
  `palace.exchanges_dir` and `palace.path` must be absolute (`~` allowed).
- One palace per project. Every MemPalace call the plugin makes carries the
  global `--palace` flag before the subcommand, and runs with
  `MEMPALACE_PALACE_PATH` set to the same path. The reason is isolation:
  MemPalace digs cross-wing tunnels after every mine, and its `search` without
  `--wing` and its knowledge graph are palace-wide. A palace that holds one
  project has nothing to tunnel to. MemPalace keeps `tunnels.json` beside the
  palace directory and its knowledge-graph sqlite inside it, so both are per
  project too.
- `find_tool` also looks in the uv tool environments themselves
  (`AppData/Roaming/uv/tools/<tool>/Scripts`, `.local/share/uv/tools/<tool>/bin`)
  and remembers resolved paths in `~/.graph-mempalace/tools.json`, so the
  SessionStart path never has to run `uv tool dir`.
- Transcripts come from `$CLAUDE_CONFIG_DIR/projects` when that variable is set.
- Children of the chain run in their own process group with no console
  (Windows), because a closing Claude Code session killed a Graph B worker with
  `STATUS_CONTROL_C_EXIT` in the live run. An interrupted pass is retried once.
- The chain refreshes the lock between steps; a long first-sight ingest can run
  past `lock_stale_hours` without losing its lock, and a chain can only release
  its own lock.
- The gate keeps one small file per session under `~/.graph-mempalace/sessions/`
  instead of one shared `watermark.json`, so two sessions stopping at once
  cannot drop each other's entry; files older than 30 days are pruned.
- First sight is decided by "no exchange files AND no `_index.jsonl` yet", not
  "no directory yet": the filter creates the directory before it parses
  anything, and writes `_index.jsonl` last, so the index's presence means a
  filter pass completed (possibly with zero exchanges) and its absence means
  the pass never finished. A project with nothing to ingest is compounded once,
  not on every session start.
- Exchange ids are stable because only the main transcript is read (sidechain
  transcripts under `<slug>/<session>/` are skipped — their results are already
  in the main transcript as task notifications) and the main transcript is
  append-only.
- The done marker ends with `status=ok` or `status=failed:<steps>`; SessionStart
  says when the last compound failed and what to run.
- Graph A lands in `<repo>/graphify-out/`; the chain adds that line to the
  repo's local `.git/info/exclude` (never to a tracked file) so `git status`
  stays clean. `kb_setup --install` also adds it to the global excludes.
- `.mcp.json` starts the MemPalace server through `scripts/mcp_launch.py`, with
  `command` set to `${GM_PYTHON:-python}`. A per-project palace path cannot be
  written into a static `.mcp.json`, so something has to work it out at spawn
  time. Graphify's server is still launched directly
  (`${GM_GRAPHIFY_MCP_BIN:-graphify-mcp}`). The launcher is Python, not a shell
  script: a bash launcher was tried and rejected, because on Windows `bash` on
  PATH is commonly the WSL/Store shim and the servers failed to connect that way
  in a live run.

Python >= 3.11 (tomllib). Standard library only. Every function has a docstring
that says what it reads and writes.

### 5.3 Configuration — `~/.graph-mempalace/config.toml`

TOML, parsed with the standard library. Missing file = all defaults. Three
layers, later wins: the user file above; a user-side per-project file
`~/.graph-mempalace/projects/<slug>/config.toml` (may set anything); the repo's
`.graph-mempalace.toml` (may set anything except `[palace]`). `[palace]` is
user-config only in all three keys — `wing`, `exchanges_dir` and `path`. Unknown
keys produce a single stderr warning naming the key; wrong types fall back to the
default with a warning. These are the keys and defaults:

```toml
[compound]
threshold_tokens = 100000     # new tokens since the last compound before the Stop hook fires
require_settled = true        # only compound when the last assistant block is prose, not a tool call
on_first_sight = true         # SessionStart compounds a project that has no exchanges dir yet
on_precompact = true          # PreCompact forces a compound before the window is summarised
lock_stale_hours = 3          # a lock older than this is treated as abandoned

[graph_a]
mode = "code-only"            # "code-only": tree-sitter only, free. "full": LLM over prose files too
cluster = false               # name communities after each update (LLM call, cheap)

[graph_b]
model = "sonnet"              # GRAPHIFY_CLAUDE_CLI_MODEL
concurrency = 2               # 4 stalled and starved the mine on a 16 GB machine
token_budget = 15000          # 60000 made the model drop files from crowded chunks
passes = 2                    # re-run once for files that produced no nodes
cluster = true                # name communities after extraction

[palace]
wing = ""                     # "" = derive; set to keep an existing wing name
exchanges_dir = ""            # "" = ~/.graph-mempalace/projects/<slug>/exchanges
path = ""                     # "" = ~/.graph-mempalace/projects/<slug>/palace; one palace per project

[session]
inject_instructions = true    # print the "how to use the KB" block at SessionStart
show_status = true            # print one status line (drawers, nodes, last compound)
```

### 5.4 SessionStart — `scripts/session_start.py`

Runs on every session start, resume, clear and compact. Budget: under 2,500
characters of stdout, under 2 seconds. Steps:

1. Read the hook JSON on stdin (`cwd`, `session_id`). Load config.
2. If `mempalace` or `graphify` cannot be found: print one line —
   `graph-mempalace: prerequisites missing (mempalace, graphify). Run /graph-mempalace:kb-setup once.` — and exit 0.
3. If `session.inject_instructions`: print the instruction block (below), with the
   resolver's absolute path filled in from `__file__`.
4. If `session.show_status`: one line — exchanges on disk, Graph B node count,
   last compound time from the log, or `not ingested yet`.
5. First sight: if `compound.on_first_sight` and the exchanges dir does not exist
   and the transcripts dir does, and no lock is held: spawn `compound_run.py`
   detached and print `first session here: ingesting history in the background`.
6. Exit 0 always. Never raise; wrap everything.

Instruction block (model-facing, plain, short):

```
graph-mempalace: this project has a knowledge base of its past conversations and code.
Before answering about past work or decisions, run:
  python "<abs>/kb_resolve.py" ask "<question>"            # chat history -> verbatim exchanges
  python "<abs>/kb_resolve.py" ask "<question>" --code     # code graph -> file:line
  python "<abs>/kb_resolve.py" get <exchange-id>           # one exchange in full
Answer from the quoted exchange or the file, never from a node's description.
Nodes marked verification=unverified are claims. If nothing comes back, say so.
Compounding is automatic; /graph-mempalace:kb-compound runs it now.
```

### 5.5 Gate — `scripts/compound_gate.py`

Unchanged logic from the proven version, re-pointed at `gm_common`:

- Stop: read `transcript_path`; take the last non-sidechain, non-synthetic,
  non-error assistant record's `input + cache_read + cache_creation` tokens.
- First sight of a session id: seed the watermark, do not fire.
- Count below watermark (compaction happened): re-seed, do not fire.
- Growth below `threshold_tokens`: exit.
- Not settled (last assistant block is `tool_use`): exit without advancing.
- Lock held: log `skipped: a compound is already running`, exit without advancing.
- Else spawn `compound_run.py` detached, advance watermark.
- `--force` (PreCompact): skip the growth/settled checks, still honour the lock,
  and only if `compound.on_precompact`.
- Always print `{}` and exit 0.

### 5.6 The chain — `scripts/compound_run.py`

Python, not a shell `&&` string, so it is identical on every OS and can log,
retry and clean up. Arguments: `--project <dir>` (default cwd), `--wait` (run in
the foreground and print a human summary; used by `/kb-compound`), `--no-graph-a`,
`--no-graph-b`, `--json`, `--only <steps>` (a comma list drawn from `filter`,
`mine`, `graph_a`, `graph_b`; runs only those steps). `--only` is for a pass that
needs part of the chain: the one-time 0.1.0 palace move runs `--only mine`.

1. `lock_acquire()`; if held and fresh, print and exit 0. Everything below is in
   `try/finally: lock_release()`.
2. Filter: `filter_transcripts.py <transcripts> <exchanges>`.
3. Mine: `mempalace --palace <palace> mine <exchanges> --mode projects --wing <wing>`.
   `--palace` is a global flag, so it comes before the subcommand.
4. Graph A: if `graphify-out/graph.json` is missing under the repo, `graphify . --code-only`;
   else `graphify update .`. If `graph_a.mode == "full"`, run `graphify . --backend claude-cli ...`
   instead. `cluster-only` if `graph_a.cluster`.
5. Graph B: in the exchanges dir, `graphify . --backend claude-cli --max-concurrency N --token-budget M`,
   repeated up to `graph_b.passes` times while the output reports files that
   produced no nodes. Then `cluster-only` if `graph_b.cluster`.
6. Sweep: kill `claude` worker processes older than 10 minutes whose parent is
   gone (Windows orphan on timeout).
7. Summary line to the log and, with `--wait`, to stdout: exchanges, drawers added,
   Graph A/B node counts, files still without nodes, duration.

Env for graphify: `GRAPHIFY_NO_AUTO_REFRESH=1`, `GRAPHIFY_CLAUDE_CLI_PARALLEL=1`,
`GRAPHIFY_CLAUDE_CLI_MODEL=<cfg>`. Never run the mine and a Graph B extraction
concurrently — the lock guarantees this within the plugin.

### 5.7 Resolver — `scripts/kb_resolve.py`

As shipped and verified: `ask`, `get`, `search`, `doctor`. Re-pointed at
`gm_common` for paths and tool discovery; stdout forced to UTF-8.

`doctor` checks the palace too (verified 2026-10-10). `palace` prints the path in
use, and reads `not built yet` until the first compound. `palace reachable` lists
that palace's wings. `dialogue wing present` carries the detail
`exists only in the shared 0.1.0 palace - fix: /graph-mempalace:kb-compound (moves it)`
when that applies. The fix line for `no stale drawers` now includes `--palace`.

### 5.8 Prerequisites — `scripts/kb_setup.py`

`--check` prints a table (uv, mempalace, graphify, python >= 3.11, mempalace
config, graphify patch, global gitignore, mcp launcher python) with OK/MISSING and
the fix for each, plus a `[NOTE]` line, `palace: <path>`.
`--install` performs the fixes it can: `uv tool install mempalace`,
`uv tool install "graphifyy[pdf,sql,mcp]"`, `mempalace init <cwd> --yes --no-llm`,
`patch_graphify.py`, append `graphify-out/` to the global git excludes file, and
write `env.GM_PYTHON` into `~/.claude/settings.json`. It never installs `uv`
itself; it prints the official one-liner for the user's OS.

### 5.9 MCP servers

`.mcp.json` declares `mempalace` and `graphify`.

The MemPalace server is started through `scripts/mcp_launch.py`. The `command` is
`${GM_PYTHON:-python}`; the launcher reads `CLAUDE_PROJECT_DIR` and runs
`mempalace-mcp --palace <that project's palace>`. A launcher is needed because a
per-project palace path cannot be written into a static `.mcp.json`, so something
has to compute it at spawn time. It is Python and not a shell script for one
reason: a bash launcher was tried first and rejected, because on Windows `bash` on
PATH is commonly the WSL/Store shim and the servers failed to connect in a live
run. `GM_PYTHON` should be an absolute path to a Python 3.11 or newer;
`/graph-mempalace:kb-setup --install` writes it into `~/.claude/settings.json`
under `"env"`, and it takes effect in the next session. The launcher finds
`mempalace-mcp` the same way every other tool is found, so `GM_MEMPALACE_MCP_BIN`
still names it by absolute path when PATH cannot reach it. Full `mempalace-mcp`,
not the light one: fetch-by-id and the KG write tools (`kg_add`, `kg_supersede`,
`kg_invalidate`) are the reason.

Graphify's server is unchanged: `${GM_GRAPHIFY_MCP_BIN:-graphify-mcp}`, no shell
in between. uv puts it on the user's PATH (`~/.local/bin`), which is what Claude
Code's MCP spawn sees on Windows and on a terminal-launched Claude Code on
macOS/Linux. When PATH is not enough (a GUI-launched editor on macOS has a minimal
PATH), the user sets `GM_GRAPHIFY_MCP_BIN` to an absolute path in
`~/.claude/settings.json` under `"env"`; `kb-doctor` prints the exact lines. It
reads the current project's graph by default, and its `project_path` argument can
name another folder. That is the one door the plugin does not close.

## 6. Skills

Five. Each SKILL.md is short, imperative, steps with completion criteria. Scripts
do the work; the skill runs them and presents results (script-first).

| skill | invocation | does |
|---|---|---|
| `graph-mempalace` | model | when to consult the KB, how to answer (quote exchanges, cite ids), how to write back via the MemPalace MCP tools, where config and troubleshooting live (`references/`) |
| `kb-setup` | user | `kb_setup.py --check`, then `--install` after showing the plan; tells the user to restart the session |
| `kb-compound` | user | `compound_run.py --wait` with optional `--no-graph-b`; then `kb_resolve.py doctor`; report |
| `kb-doctor` | user | `kb_setup.py --check` + `kb_resolve.py doctor`; one fix per FAIL |
| `kb-ask` | user | `kb_resolve.py ask "$ARGUMENTS"`; answer only from the VERBATIM SOURCE block; cite exchange ids; say when nothing found |

## 7. Tests and CI

`tests/` with pytest, no network, no real tools: fake transcripts, fake
`graphify`/`mempalace` executables on a temp PATH, temp HOME. Cover: filter
(grouping, ids, drops, index), gate (seed, re-seed, threshold, settled, lock,
force), config (defaults, override, bad type, unknown key), common (slug on
Windows and POSIX paths, token, wing collision), chain (plan of commands for each
config, lock release on failure), resolver (id extraction, doctor against a fake
tree). CI: `claude plugin validate . --strict` on ubuntu; pytest on
ubuntu/windows/macos × Python 3.11/3.12.

## 8. Privacy and safety statements (must appear in README and SECURITY.md)

- The plugin reads Claude Code transcripts for the current project only
  (`~/.claude/projects/<slug>/*.jsonl`) and writes derived files under
  `~/.graph-mempalace/` and this project's own palace,
  `~/.graph-mempalace/projects/<slug>/palace` by default. Nothing in one
  project's memory can reach another project's.
- Graph B extraction sends exchange text to Claude through the user's own
  `claude` CLI session (same account, same terms); nothing else leaves the machine.
- Hooks never block a session: they exit 0 on every error and log to
  `~/.graph-mempalace/compound.log`.
- Compounding runs detached in the background; a lock prevents two at once.
- Uninstall: `/plugin uninstall graph-mempalace`; delete `~/.graph-mempalace/` by
  hand if wanted, which takes the default palace with it. A palace moved
  elsewhere with `palace.path` has to be deleted by hand.

## 8b. Migrating from the manual setup (the originating project)

The manual setup wired the same three hooks in `.claude/settings.json` and the
same two servers in `.mcp.json`, with scripts under `~/.claude/skills/kb-setup/`.
With the plugin installed those copies must go, or every hook fires twice and
every server starts twice. The palace wing (`statusneo_assignment_dialogue`) and
the exchanges folder (`~/.mempalace/dialogue/<slug>`) are kept by setting
`palace.wing` and `palace.exchanges_dir` in the user-side per-project file
`~/.graph-mempalace/projects/<slug>/config.toml` — not in the repository's
`.graph-mempalace.toml`, which may not set `[palace]`. `docs/configuration.md`
shows the exact file.

Upgrading from 0.1.0 also seeds this project's own palace. The first session after
the upgrade moves that project's memory into it in the background. There is
nothing to do by hand. It is a mine-only compound (`compound_run.py --only mine`),
so the graphs are left alone, and the session announces it with one line:

```
graph-mempalace: moving this project's memory into its own palace in the background (one-time, a few minutes)
```

The old shared palace — MemPalace's default, usually `~/.config/mempalace/palace`
or `~/.mempalace/palace` — is left untouched, and its old wings can be ignored. A
user's own global MemPalace MCP server entry in `~/.claude.json`, if they keep
one, still points at the shared palace. That is outside the plugin.

## 9. Out of scope for v0.2

Cross-harness installers (Codex, Cursor) beyond `docs/manual-setup.md`; an eval
suite; a stub MCP server when prerequisites are missing; moving a palace wing
anywhere a user asks (`[palace]` is the config override, and the one-time 0.1.0
palace move is the only migration the plugin runs by itself).
