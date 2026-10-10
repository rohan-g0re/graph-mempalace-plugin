"""mcp_launch.py, run as a real subprocess.

The launcher is only ever started by Claude Code as an MCP server, so every test
here starts it the same way: `sys.executable scripts/mcp_launch.py mempalace`,
with pipes on stdin and stdout and a temporary GM_HOME in its environment. The
server it would start is replaced by a fake that records its own argv, the
MEMPALACE_PALACE_PATH it was given, its cwd and its pid, then echoes stdin back
to stdout until the pipe closes.

What is being protected:

  * the palace in `--palace` is THIS project's palace (0.2.0: one per project),
  * stdout carries nothing but the server's own bytes, on every path,
  * closing stdin ends the server rather than leaking it.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

LAUNCHER = str(Path(__file__).resolve().parent.parent / "scripts" / "mcp_launch.py")
WINDOWS = os.name == "nt"

# The fake server's body, appended inside the generated python file that
# conftest's make_tool writes (json, os and sys are already imported there).
FAKE_BODY = """with open({rec!r}, 'w', encoding='utf-8') as fh:
    json.dump({{'argv': sys.argv[1:],
               'palace_env': os.environ.get('MEMPALACE_PALACE_PATH'),
               'cwd': os.getcwd(),
               'pid': os.getpid()}}, fh)
src = sys.stdin.buffer
out = sys.stdout.buffer
while True:
    line = src.readline()
    if not line:
        break
    out.write(line)
    out.flush()
"""


@pytest.fixture
def fake_server(tmp_path, make_tool):
    """A stand-in `mempalace-mcp`: records how it was started, echoes stdin."""
    record = tmp_path / "server_call.json"
    exe = make_tool(tmp_path / "bin", "mempalace-mcp",
                    body=FAKE_BODY.format(rec=str(record)))
    return SimpleNamespace(exe=exe, record=record)


def launch(args, *, gm_home, env_extra=None, cwd=None, stdin=b"", timeout=25):
    """Run the launcher to completion. Returns the CompletedProcess."""
    return subprocess.run([sys.executable, LAUNCHER, *args], input=stdin,
                          capture_output=True, env=child_env(gm_home, env_extra),
                          cwd=str(cwd) if cwd else None, timeout=timeout)


def child_env(gm_home, env_extra=None):
    """This machine's environment minus every GM_* / CLAUDE_* override, plus GM_HOME.

    gm_common reads GM_HOME at import time and the launcher is a separate
    process, so the temporary home has to be handed over explicitly.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GM_", "CLAUDE_"))}
    env["GM_HOME"] = str(gm_home)
    env.update(env_extra or {})
    return env


def read_record(record, timeout=10):
    """The fake server's JSON record, waiting for it to appear and be complete."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            return json.loads(record.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError) as exc:
            last = exc
            time.sleep(0.05)
    raise AssertionError(f"the fake server never wrote {record}: {last}")


def log_text(gm_home) -> str:
    path = gm_home / ".graph-mempalace" / "compound.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def alive(pid: int) -> bool:
    """Windows: is this pid still a running process?"""
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, timeout=20)
    for row in csv.reader(io.StringIO(out.stdout or "")):
        if len(row) > 1 and row[1].strip() == str(pid):
            return True
    return False


# ---------------------------------------------------------------------------
# which palace
# ---------------------------------------------------------------------------
def test_default_palace_is_this_project_s_own(gm, gm_home, project, fake_server, samefile):
    expected = gm.palace_path(project)

    res = launch(["mempalace"], gm_home=gm_home, env_extra={
        "CLAUDE_PROJECT_DIR": str(project),
        "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
    })

    assert res.returncode == 0, res.stderr.decode(errors="replace")
    rec = read_record(fake_server.record)
    assert rec["argv"][0] == "--palace"
    assert samefile(rec["argv"][1], expected), (rec["argv"], str(expected))
    assert samefile(rec["palace_env"], expected)
    # One palace per project: under GM_HOME, keyed by this project's slug.
    assert str(expected).startswith(str(gm_home))
    assert gm.slug_for(project) in str(expected)
    # The parent is ours to create; the palace itself is MemPalace's, on first mine.
    assert expected.parent.is_dir()
    assert not expected.exists()
    # One log line, naming where it pointed the server and for which project.
    assert f"mcp: mempalace -> {expected}" in log_text(gm_home)
    assert f"(project={gm.project_token(project)})" in log_text(gm_home)


def test_per_project_config_can_move_the_palace(gm, gm_home, project, tmp_path,
                                                fake_server, samefile):
    override = tmp_path / "deep" / "chosen palace"  # a space, and a missing parent
    cfg = gm.project_config_path(project)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[palace]\npath = "%s"\n' % str(override).replace("\\", "/"),
                   encoding="utf-8", newline="\n")

    res = launch(["mempalace"], gm_home=gm_home, env_extra={
        "CLAUDE_PROJECT_DIR": str(project),
        "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
    })

    assert res.returncode == 0, res.stderr.decode(errors="replace")
    rec = read_record(fake_server.record)
    assert samefile(rec["argv"][1], override), (rec["argv"], str(override))
    assert samefile(rec["palace_env"], override)
    assert override.parent.is_dir()  # the launcher created the parent
    assert not override.exists()


def test_claude_project_dir_decides_the_project_not_cwd(gm, gm_home, project, tmp_path,
                                                        fake_server, samefile):
    other = (tmp_path / "other-repo").resolve()
    other.mkdir()

    res = launch(["mempalace"], gm_home=gm_home, cwd=other, env_extra={
        "CLAUDE_PROJECT_DIR": str(project),
        "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
    })
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    rec = read_record(fake_server.record)
    assert samefile(rec["argv"][1], gm.palace_path(project))
    assert gm.slug_for(other) not in rec["argv"][1]
    assert samefile(rec["cwd"], other)  # the server still runs where it was started

    # Without the variable, cwd is the project - which is why the variable has
    # to win: Claude Code sets both, and only one of them is authoritative.
    fake_server.record.unlink()
    res = launch(["mempalace"], gm_home=gm_home, cwd=other, env_extra={
        "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
    })
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    rec = read_record(fake_server.record)
    assert samefile(rec["argv"][1], gm.palace_path(other))


# ---------------------------------------------------------------------------
# the protocol stream
# ---------------------------------------------------------------------------
def test_bytes_pass_through_in_both_directions(gm_home, project, fake_server):
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                       "params": {}}, separators=(",", ":")).encode() + b"\n"

    res = launch(["mempalace"], gm_home=gm_home, stdin=line, env_extra={
        "CLAUDE_PROJECT_DIR": str(project),
        "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
    })

    assert res.returncode == 0, res.stderr.decode(errors="replace")
    assert res.stdout == line  # the server's bytes, nothing added, nothing lost


# ---------------------------------------------------------------------------
# failure paths: stdout stays empty on every one of them
# ---------------------------------------------------------------------------
def test_missing_server_binary_exits_1_and_names_the_fix(gm_home, project, empty_path):
    res = launch(["mempalace"], gm_home=gm_home, env_extra={
        "CLAUDE_PROJECT_DIR": str(project),
        "PATH": str(empty_path),
    })

    assert res.returncode == 1
    assert res.stdout == b""  # stdout is the protocol stream, even when nothing starts
    log = log_text(gm_home)
    assert "mempalace-mcp not found" in log
    assert "kb-setup" in log
    err = res.stderr.decode(errors="replace").strip()
    assert err.count("\n") == 0 and "kb-setup" in err


@pytest.mark.parametrize("args", [["graphify"], ["mempalace-light"], ["mempalace", "extra"], []])
def test_only_mempalace_is_launchable(gm_home, project, fake_server, args):
    res = launch(args, gm_home=gm_home, env_extra={
        "CLAUDE_PROJECT_DIR": str(project),
        "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
    })

    if args[:1] == ["mempalace"]:  # a trailing argument is ignored, not refused
        assert res.returncode == 0, res.stderr.decode(errors="replace")
        return
    assert res.returncode == 2
    assert res.stdout == b""
    assert not fake_server.record.exists()  # nothing was started
    assert "refused server name" in log_text(gm_home)


# ---------------------------------------------------------------------------
# process lifetime
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not WINDOWS, reason="the stdin pump exists only where there is no exec")
def test_closing_stdin_ends_the_server(gm_home, project, fake_server):
    proc = subprocess.Popen([sys.executable, LAUNCHER, "mempalace"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            env=child_env(gm_home, {
                                "CLAUDE_PROJECT_DIR": str(project),
                                "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
                            }))
    try:
        line = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        proc.stdin.write(line)
        proc.stdin.flush()
        assert proc.stdout.readline() == line  # the server is up and answering
        pid = read_record(fake_server.record)["pid"]
        assert alive(pid)

        proc.stdin.close()  # Claude Code exiting looks exactly like this
        assert proc.wait(timeout=8) == 0
        deadline = time.time() + 5
        while alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not alive(pid), f"the server (pid {pid}) outlived the launcher"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()


@pytest.mark.skipif(WINDOWS, reason="os.execve replaces the process only on POSIX")
def test_the_server_replaces_the_launcher(gm_home, project, fake_server):
    proc = subprocess.Popen([sys.executable, LAUNCHER, "mempalace"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            env=child_env(gm_home, {
                                "CLAUDE_PROJECT_DIR": str(project),
                                "GM_MEMPALACE_MCP_BIN": str(fake_server.exe),
                            }))
    out, _ = proc.communicate(input=b"", timeout=25)
    assert proc.returncode == 0
    assert out == b""
    # Same pid: signals and the exit status reach the server with nothing between.
    assert read_record(fake_server.record)["pid"] == proc.pid
