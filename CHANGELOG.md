# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are pinned: Claude Code treats `version` in `plugin.json` as the signal
that an update exists, so a release bumps it and adds an entry here in one commit.

## [Unreleased]

### Fixed

- The chat graph (Graph B) could stop updating for good while every compound
  reported `status=ok`. An exchange filed while its session was still running
  kept growing. Graphify then re-extracted it, got fewer nodes on some runs,
  and its shrink guard refused to write the graph, every run after that.
- `filter_transcripts.py` holds back the last exchange of a session whose
  transcript changed in the last 30 minutes. A later run files it under the same
  id. Exchange files whose text did not change are no longer rewritten.
- `compound_run.py` re-runs a Graph B pass once with `--allow-partial` when
  Graphify's shrink guard refuses it.
- `compound_run.py` marks Graph B as failed when no pass wrote the graph this
  run, even if an older graph is on disk. The SessionStart line then says
  `last compound FAILED (graph B)` instead of hiding a stale chat graph.

## [0.2.0] - 2026-10-10

### Added

- `palace.path` configuration key, set in the user-side layers only:
  `~/.graph-mempalace/config.toml` for the machine, or
  `~/.graph-mempalace/projects/<slug>/config.toml` for one project.
- `compound_run.py --only STEPS` — run only the named steps. `STEPS` is a comma
  list drawn from `filter`, `mine`, `graph_a` and `graph_b`.
- `scripts/mcp_launch.py` — starts the MemPalace MCP server in this project's
  palace.
- `kb_resolve.py doctor` reports `palace`: the palace path in use. It reads
  "not built yet" until the first compound.
- `kb_resolve.py doctor` reports `palace reachable`: the wings that palace holds.
- `kb_resolve.py doctor` reports `dialogue wing present`, with the detail
  `exists only in the shared 0.1.0 palace - fix: /graph-mempalace:kb-compound (moves it)`
  when the wing is still in the old palace.
- `kb_resolve.py doctor` reports `no stale drawers`: drawers whose source file
  is gitignored or gone, counted with `mempalace sync --dry-run --wing <wing>`,
  with the exact `--apply` line as the fix. That fix line now carries
  `--palace`. Skipped when the installed MemPalace prints no sync report.
- `kb_setup.py --check` prints an `mcp launcher python` row and a
  `palace: <path>` note.
- Upgrading from 0.1.0 needs no hand work. The first session after the upgrade
  says it is moving this project's memory, then runs a mine-only compound in the
  background that files the existing exchanges into the new palace. Graphs are
  left alone, and the old shared palace is untouched.

### Changed

- One MemPalace palace per project, instead of one palace for the machine. The
  default is `~/.graph-mempalace/projects/<slug>/palace`, and `palace.path`
  overrides it. A shared palace let a session in one project reach another
  project's memory: MemPalace digs cross-wing tunnels after every mine, and both
  `mempalace search` without `--wing` and its knowledge graph are palace-wide.
- Every MemPalace call the plugin makes passes the global `--palace <path>` flag
  before the subcommand, and runs with `MEMPALACE_PALACE_PATH` set to the same
  path.
- The MemPalace MCP server is launched through the plugin, by
  `scripts/mcp_launch.py`, instead of being spawned directly. Its `.mcp.json`
  `command` is `${GM_PYTHON:-python}`. The launcher reads `CLAUDE_PROJECT_DIR`
  and runs `mempalace-mcp --palace <that project's palace>`. The Graphify MCP
  server is unchanged.
- `/graph-mempalace:kb-setup --install` writes `env.GM_PYTHON` into
  `~/.claude/settings.json`. It takes effect in the next session.

### Fixed

- CI runs `claude plugin validate . --strict` without `--json`; the flag is
  not on every CLI version.
- The SessionStart status line reports only this project's last compound; the
  log is shared by every project on the machine.
- `py.sh` caches the interpreter's absolute path, so a hook with an empty or
  Windows-format PATH does not fall through to `uv`.

## [0.1.0] - 2026-10-10

First release. Everything below is new.

### Added

- Claude Code plugin packaging: `plugin.json` manifest and a single-entry
  `marketplace.json` in the same repository, installed as
  `graph-mempalace@graph-mempalace`.
- Three hooks in `hooks/hooks.json`: `SessionStart` (inject instructions, print
  status, ingest on first sight), `Stop` (token-growth gate) and `PreCompact`
  (forced compound before the window is summarised).
- `scripts/gm_common.py` — the one place paths, project identity, configuration,
  tool discovery, the lock, the log and detached spawning are decided.
- `scripts/py.sh` — POSIX launcher that finds a Python 3.11 or newer, caches the
  path, and falls back to `uv run`.
- `scripts/session_start.py` — the SessionStart hook.
- `scripts/compound_gate.py` — the Stop and PreCompact gate: watermark seeding,
  growth threshold, settledness check, compaction re-seed, lock.
- `scripts/compound_run.py` — the compound chain: filter, mine, Graph A, Graph B,
  cluster, orphan sweep.
- `scripts/filter_transcripts.py` — transcripts to one markdown file per exchange,
  with stable ids and an index.
- `scripts/kb_resolve.py` — `ask`, `get`, `search` and `doctor`.
- `scripts/kb_setup.py` — prerequisite check and install.
- `scripts/patch_graphify.py` — idempotent `--strict-mcp-config` patch for
  graphify's Claude CLI backend.
- `.mcp.json` declaring the `mempalace` and `graphify` stdio servers, launching
  the binaries directly as `${GM_MEMPALACE_MCP_BIN:-mempalace-mcp}` and
  `${GM_GRAPHIFY_MCP_BIN:-graphify-mcp}`, with no shell in between.
- Five skills: `graph-mempalace` (model-invoked), plus `kb-setup`, `kb-compound`,
  `kb-doctor` and `kb-ask` (user-invoked).
- Configuration through `~/.graph-mempalace/config.toml`, overridden per project by
  `<repo>/.graph-mempalace.toml`.
- Test suite covering the filter, the gate, configuration, the shared module, the
  chain and the resolver, with fake tools on a temporary PATH.
- CI: `claude plugin validate . --strict --json` on Linux, and pytest on Linux,
  Windows and macOS across Python 3.11 and 3.12.
- Documentation: `README.md`, `docs/DESIGN.md`, `docs/architecture.md`,
  `docs/configuration.md`, `docs/manual-setup.md`, `AGENTS.md`, `CONTRIBUTING.md`
  and `SECURITY.md`, plus the two reference documents the design came from.

[Unreleased]: https://github.com/rohan-g0re/graph-mempalace-plugin/compare/graph-mempalace--v0.2.0...HEAD
[0.2.0]: https://github.com/rohan-g0re/graph-mempalace-plugin/compare/graph-mempalace--v0.1.0...graph-mempalace--v0.2.0
[0.1.0]: https://github.com/rohan-g0re/graph-mempalace-plugin/releases/tag/graph-mempalace--v0.1.0
