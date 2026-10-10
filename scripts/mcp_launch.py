"""mcp_launch.py - start a bundled MCP server on THIS project's palace.

Why this exists
---------------
From 0.2.0 every project gets its own MemPalace palace. A palace is the unit of
isolation: MemPalace's search and its knowledge graph are palace-wide, and it
digs cross-wing tunnels after every mine, so two projects sharing a palace leak
into each other. `gm_common.palace_path()` decides where a project's palace
lives; every `mempalace` CLI call the plugin makes goes through
`gm_common.mempalace_argv()` and carries `--palace`.

The MCP server is the one caller that cannot. It is declared once in
`.mcp.json`, started once per session by Claude Code, and given no arguments
that depend on the project. Without this launcher it would open MemPalace's
shared default palace and the model would read every project's memory.

So `.mcp.json` starts this script instead of the server binary, and the script
works out which palace to open and execs the real server with `--palace`:

    "mempalace": {
      "command": "${GM_PYTHON:-python}",
      "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/mcp_launch.py", "mempalace"]
    }

The stdout rule
---------------
stdout is the MCP protocol stream. A single stray byte on it - a warning, a
traceback, a `print()` left in - desynchronises the JSON-RPC framing and Claude
Code drops the server. NOTHING in this file writes to stdout, ever, including
every error path: diagnostics go to `~/.graph-mempalace/compound.log` through
`gm.append_log`, and at most one line goes to stderr (which Claude Code shows in
`/mcp` and in its logs). `gm._warn` for bad config is already stderr-only.

How the process tree is arranged
--------------------------------
POSIX: `os.execve` - the server REPLACES this launcher, so Claude Code's pipes,
signals and exit status reach the server with nothing in between.

Windows has no exec. There the launcher stays, spawns the server with the
launcher's own stdout and stderr handles (so the server writes protocol bytes
straight to Claude Code) and pumps stdin into the child itself. Stdin EOF is the
shutdown signal: Claude Code closing the pipe means the session is gone, so the
launcher closes the child's stdin, waits 5 s, then terminates and kills. The
launcher exits with the child's return code.

Verified facts this design rests on (2026-10-10, Claude Code on Windows 11)
---------------------------------------------------------------------------
* A plugin's stdio MCP server process starts with cwd = the PROJECT directory,
  and with `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA`
  in its environment. Hence `CLAUDE_PROJECT_DIR or os.getcwd()`: either answer
  is the project, and the variable is preferred because it survives any later
  change of directory.
* `${VAR:-default}` inside the plugin's `.mcp.json` is expanded from the Claude
  Code process environment, and an `env` block in `settings.json` (user or
  project level) is visible both to that expansion and to the server process.
  That is how `GM_PYTHON` and `GM_MEMPALACE_MCP_BIN` work for a GUI-launched
  editor with a minimal PATH.
* `mempalace-mcp` (entry point `mempalace.mcp_proxy:main`) accepts
  `--palace PATH` and also honours `MEMPALACE_PALACE_PATH`. Both are set here:
  the flag for the server, the variable for anything it spawns.
* An MCP `command` must be a binary or `python`. A bash launcher was tried
  first and rejected: on Windows `bash` on PATH is commonly the WSL/Store shim
  and the servers failed to connect in a live run.

A palace that does not exist yet
--------------------------------
A project's palace is created by MemPalace on the first mine, so for one or more
sessions this launcher points the server at a directory that is not there. That
is safe, and it was measured rather than assumed (mempalace 3.10.0, server
`{"name": "mempalace", "version": "3.10.0"}`, 2026-10-10):

* the server starts and stays alive; `initialize` succeeds exactly as against a
  real palace, and `tools/list` returns the full set of 45 tools - the tool list
  is not gated on the palace existing;
* `mempalace_status` is NOT a JSON-RPC error and carries no `isError`: it is a
  successful tool result whose text is a JSON document reporting
  `"error": "Chroma database missing"`,
  `"details": "Could not open missing database at <palace>\\chroma.sqlite3."`,
  `"hint": "Run: mempalace status or mempalace repair-status for diagnostics."`,
  with no `wings` and no `total_drawers` key. So "no palace yet" reads as either
  an `error` key or a missing `wings` key, and the model gets a sentence it can
  act on instead of a dead server;
* neither startup nor the status call creates anything on disk - not the palace,
  not its parent. Hence the one directory this launcher does create: the
  palace's PARENT, so a fresh machine has somewhere for the first mine to write.

Pointing a server at a palace and asking for status is therefore read-only.

The same probe confirmed that `MEMPALACE_PALACE_PATH` alone resolves the palace
(identical status output, `sqlite_integrity.palace` naming the chosen directory),
and that a palace holding one wing reports only that wing - which is the whole
point of one palace per project.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gm_common as gm  # noqa: E402  (also reconfigures stdout/stderr to UTF-8)

# Server name (as .mcp.json and the model see it) -> the executable to run.
# Only `mempalace` needs a launcher: graphify's server is told which graph to
# read per call, so .mcp.json starts its binary directly.
SERVERS = {"mempalace": "mempalace-mcp"}

SETUP_HINT = "/graph-mempalace:kb-setup"
CHUNK = 65536
CREATE_NO_WINDOW = 0x08000000
EOF_GRACE_SECONDS = 5.0
TERMINATE_GRACE_SECONDS = 2.0


def resolve_binary(tool: str) -> str | None:
    """Absolute path of the server executable, or None.

    `$GM_<TOOL>_BIN` wins when it points at a file that exists; otherwise
    `gm.find_tool` looks in ~/.local/bin, the uv tool environment and PATH, and
    remembers what it found in ~/.graph-mempalace/tools.json.
    """
    key = "GM_" + re.sub(r"[^A-Za-z0-9]", "_", tool).upper() + "_BIN"
    override = os.environ.get(key)
    if override and Path(override).exists():
        return override
    return gm.find_tool(tool)


def _close(stream) -> None:
    """Close a pipe end, ignoring a peer that has already gone away."""
    if stream is None:
        return
    try:
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def reap(child: subprocess.Popen) -> int:
    """Wait for the server, escalating: 5 s, terminate, 2 s, kill."""
    try:
        return child.wait(timeout=EOF_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    child.terminate()
    try:
        return child.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait()


def pump(child: subprocess.Popen) -> int:
    """Copy this process's stdin into the server's, then shut the server down.

    Reads one chunk at a time (`read1`, so a short JSON-RPC line is forwarded
    the moment it arrives instead of waiting for a full buffer) on the main
    thread - there is nothing else for it to do. Returns the server's exit code.
    """
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:  # no stdin at all (pythonw): nothing to forward
        return reap(child)
    read = getattr(stream, "read1", None) or stream.read
    try:
        while True:
            try:
                chunk = read(CHUNK)
            except (BrokenPipeError, OSError, ValueError, KeyboardInterrupt):
                break
            if not chunk:
                break  # EOF: Claude Code closed the pipe, so the session is over
            try:
                child.stdin.write(chunk)
                child.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                break  # the server exited first; stop feeding it
    finally:
        _close(child.stdin)
    return reap(child)


def main(argv: list[str]) -> int:
    """Resolve the palace, then become (or host) the server. Never writes stdout."""
    name = argv[1] if len(argv) > 1 else ""
    tool = SERVERS.get(name)
    known = ", ".join(sorted(SERVERS))
    if tool is None:
        gm.append_log(f"mcp: refused server name {name!r}; this launcher only starts: {known}")
        sys.stderr.write(f"graph-mempalace: mcp_launch.py takes one server name ({known})\n")
        return 2

    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = gm.load_config(project)
    palace = gm.palace_path(project, cfg)
    gm.append_log(f"mcp: {name} -> {palace} (project={gm.project_token(project)})")

    # The palace itself is MemPalace's to create on the first mine; its parent
    # is ours, so a fresh machine has somewhere to put it.
    try:
        palace.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        gm.append_log(f"mcp: could not create {palace.parent}: {type(exc).__name__}: {exc}")

    binary = resolve_binary(tool)
    if binary is None:
        gm.append_log(
            f"mcp: {tool} not found - run {SETUP_HINT} once, or set "
            f"GM_MEMPALACE_MCP_BIN to its absolute path in ~/.claude/settings.json under \"env\""
        )
        sys.stderr.write(f"graph-mempalace: {tool} not found; run {SETUP_HINT} once\n")
        return 1

    server_argv = [binary, "--palace", str(palace)]
    env = dict(os.environ)
    env["MEMPALACE_PALACE_PATH"] = str(palace)

    if os.name != "nt":
        sys.stderr.flush()
        os.execve(binary, server_argv, env)  # replaces this process; never returns
        return 1  # pragma: no cover

    child = subprocess.Popen(
        server_argv,
        stdin=subprocess.PIPE,
        stdout=None,  # inherited: the server writes protocol bytes to Claude Code
        stderr=None,  # inherited: its diagnostics land in Claude Code's MCP log
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    return pump(child)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except BrokenPipeError:
        gm.append_log("mcp: stdin/stdout pipe closed by the client")
        sys.exit(0)
    except Exception as exc:
        gm.append_log(f"mcp error: {type(exc).__name__}: {exc}")
        sys.stderr.write(f"graph-mempalace: mcp_launch.py failed: {type(exc).__name__}: {exc}\n")
        sys.exit(1)
