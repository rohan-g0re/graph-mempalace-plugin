# Troubleshooting

One symptom, one fix. Everything the plugin does is logged to
`~/.graph-mempalace/compound.log`, so start there when a symptom is not listed.

## The session says prerequisites are missing

SessionStart printed
`graph-mempalace: prerequisites missing (mempalace, graphify)`. The two tools
are not installed, or they are installed somewhere the hook's PATH does not
reach. Run `/graph-mempalace:kb-setup`, then restart the session.

## The status line says `not ingested yet`

Nothing has been filed for this project yet. The first session in a project
spawns a background compound, which takes a few minutes on a long history. Wait
for it, or run `/graph-mempalace:kb-compound` to do it in the foreground and
watch the numbers.

## A compound says it is already running

One compound runs at a time, machine-wide. The holder is
`~/.graph-mempalace/compound.lock`; it names the time and the pid that took it.
A lock older than three hours counts as abandoned and is cleared on the next
attempt. While a `mempalace mine` or a `graphify` process is alive, let the lock
stand — two compounds sharing one palace and one `graphify-out/` is the failure
the lock exists to prevent. `~/.graph-mempalace/compound.log` shows what the
holder is doing.

## Answers come from a second, empty wing

Two wings for one project means the derived wing name changed, usually because
the project directory was renamed or copied. Pin the wing you want in the
per-project file `~/.graph-mempalace/projects/<slug>/config.toml` (the slug is
the folder name under `~/.claude/projects/` for this repository):

```toml
[palace]
wing = "myproject_dialogue"
```

`[palace]` cannot be set from a repository's `.graph-mempalace.toml` — a cloned
repo must not choose where the plugin writes. `kb-doctor` prints the name in
force.

## `ask` returns nodes but no exchange ids

Graph B was built before the per-exchange filter existed, so its nodes point at
whole-session files rather than exchange ids. Rebuild it with
`/graph-mempalace:kb-compound`.

## Hooks fire twice

Claude Code gives hooks no namespace, so a hook listed in both the user's
`settings.json` and the plugin runs twice per event. Anyone migrating from the
manual setup has both: delete the `settings.json` copies and keep the plugin's.

## A hook prints `no Python >= 3.11 found`

Git Bash on Windows sometimes receives an unconverted Windows PATH under Claude
Code. The launchers resolve tools by absolute path, so this surfaces only for
the interpreter itself. Point at it directly with an environment variable, for
example `GM_PYTHON=C:/Python312/python.exe`, or run
`/graph-mempalace:kb-setup`, which installs a Python that the launcher finds.

## A `claude.exe` is pinned at idle on Windows

Graphify runs the Claude CLI with a timeout; the timeout kills the `cmd.exe`
wrapper and leaves the `claude.exe` child holding memory while Graphify has
already retried the chunk. The symptom is a `claude.exe` older than ten minutes
whose parent pid is gone. Each compound sweeps these at the end of its run. To
clear them by hand:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'claude.exe.*--json-schema' } | Select-Object ProcessId, ParentProcessId, CreationDate
```

Check the parent pid, then stop the orphans.

## An MCP server failed to connect

`.mcp.json` launches the two server binaries directly, so nothing in between
reports a reason: Claude Code shows the connection failure and that server's
tools are absent from the session. It is one of two things.

The binary is not on disk. `mempalace-mcp` ships with MemPalace and
`graphify-mcp` with Graphify's `mcp` extra, and `/graph-mempalace:kb-setup`
installs both. Run it, then restart the session — a running session keeps the
servers it started with, so the fix shows up only in the next one.

The binary is on disk but not on the PATH Claude Code was started with. That is
typical when an editor was launched from the Dock on macOS, where a GUI app gets
a minimal PATH. Run `/graph-mempalace:kb-doctor`: it prints the path it resolved
for each server, and for one that PATH cannot reach, the exact line to paste
into `~/.claude/settings.json`:

```json
{
  "env": {
    "GM_MEMPALACE_MCP_BIN": "C:/Users/you/.local/bin/mempalace-mcp.exe",
    "GM_GRAPHIFY_MCP_BIN": "C:/Users/you/.local/bin/graphify-mcp.exe"
  }
}
```

The plugin's hooks still run under bash, so Git for Windows has to be installed
on Windows. That is a hook requirement, not an MCP one.

## The transcripts live somewhere else (`CLAUDE_CONFIG_DIR`)

Claude Code keeps transcripts under its config directory, and
`CLAUDE_CONFIG_DIR` moves that directory. The plugin follows the variable:
transcripts are read from `$CLAUDE_CONFIG_DIR/projects/<slug>` when it is set,
and from `~/.claude/projects/<slug>` when it is not. The catch is that hooks see
the environment the Claude Code process has, so set it where that process
inherits it — a user or system environment variable, or the shell you start
`claude` from — not in a per-command prefix. When a status line says
`not ingested yet` on a project with plenty of history, check that the hooks see
the same value you do.
