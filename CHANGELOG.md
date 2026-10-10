# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are pinned: Claude Code treats `version` in `plugin.json` as the signal
that an update exists, so a release bumps it and adds an entry here in one commit.

## [Unreleased]

### Added

- `kb_resolve.py doctor` reports `no stale drawers`: drawers whose source file
  is gitignored or gone, counted with `mempalace sync --dry-run --wing <wing>`,
  with the exact `--apply` line as the fix. Skipped when the installed
  MemPalace prints no sync report.

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

[Unreleased]: https://github.com/rohan-g0re/graph-mempalace-plugin/compare/graph-mempalace--v0.1.0...HEAD
[0.1.0]: https://github.com/rohan-g0re/graph-mempalace-plugin/releases/tag/graph-mempalace--v0.1.0
