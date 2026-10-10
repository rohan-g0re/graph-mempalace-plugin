"""Shared fixtures for the graph-mempalace suite.

Two invariants hold for every test in this directory:

  * GM_HOME points at a directory under tmp_path, so nothing reads or writes
    the developer's real ~/.graph-mempalace, palace, or config. gm_common reads
    GM_HOME at IMPORT time, so the plugin modules are purged from sys.modules
    before and after every test and imported fresh inside it.
  * No real `mempalace`, `graphify` or `uv` is ever run. Tools are either fake
    executables created in tmp_path, or absent because PATH was emptied.

Everything is standard library plus pytest, and no test touches the network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Every module under scripts/ that a test may import. They each cache paths
# derived from GM_HOME at import time, so they are all re-imported per test.
PLUGIN_MODULES = (
    "gm_common",
    "filter_transcripts",
    "compound_gate",
    "compound_run",
    "session_start",
    "kb_resolve",
    "kb_setup",
    "patch_graphify",
)

WINDOWS = os.name == "nt"


def _purge_plugin_modules() -> None:
    for name in PLUGIN_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _fresh_imports(monkeypatch):
    """Make `import gm_common` work, and make it re-execute for every test."""
    _purge_plugin_modules()
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    yield
    _purge_plugin_modules()


@pytest.fixture
def gm_home(tmp_path, monkeypatch):
    """A throw-away GM_HOME, with ~/.claude/projects present but empty."""
    home = tmp_path / "gmhome"
    (home / ".claude" / "projects").mkdir(parents=True)
    # Drop any GM_* override the developer happens to have exported, so the
    # suite behaves the same on a laptop and on CI.
    for key in [k for k in os.environ if k.startswith("GM_")]:
        monkeypatch.delenv(key, raising=False)
    # CLAUDE_CONFIG_DIR moves the transcript tree; the suite pins it to GM_HOME.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("GM_HOME", str(home))
    return home


@pytest.fixture
def gm(gm_home):
    """gm_common, imported fresh against the temporary GM_HOME."""
    import importlib

    return importlib.import_module("gm_common")


@pytest.fixture
def clear_tool_caches():
    """Forget memoised tool lookups.

    find_tool memoises per process AND remembers paths in
    ~/.graph-mempalace/tools.json, and `uv tool dir --bin` is asked once per
    process, so a test that changes GM_*_BIN or PATH and asks again has to
    clear all three first.
    """

    def _clear(module, *, keep_file_cache=False):
        module._FIND_TOOL_CACHE.clear()
        module._UV_TOOL_BIN_CACHE.clear()
        if not keep_file_cache:
            try:
                module.TOOLS_CACHE_PATH.unlink()
            except OSError:
                pass

    return _clear


@pytest.fixture
def project(tmp_path):
    """A stand-in repository directory, resolved the way the scripts resolve it."""
    p = tmp_path / "proj"
    p.mkdir()
    return p.resolve()


@pytest.fixture
def empty_path(tmp_path, monkeypatch):
    """PATH with one empty directory on it: no tool is discoverable by name."""
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(nowhere))
    return nowhere


@pytest.fixture
def write_config(gm_home):
    """Write ~/.graph-mempalace/config.toml (the user-level config)."""

    def _write(text: str) -> Path:
        path = gm_home / ".graph-mempalace" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    return _write


@pytest.fixture
def write_project_config():
    """Write <project>/.graph-mempalace.toml (the per-project config)."""

    def _write(project_dir: Path, text: str) -> Path:
        path = Path(project_dir) / ".graph-mempalace.toml"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    return _write


@pytest.fixture
def make_tool():
    """Create a runnable fake CLI tool and return its path.

    On Windows the launcher is a .cmd (CreateProcess runs those through the
    command interpreter); elsewhere it is a /bin/sh script with the exec bit.
    Either way the real work is done by a generated Python file run with
    sys.executable, so the fake needs nothing on PATH.
    """

    def _make(directory, name, *, stdout="", stderr="", rc=0, record=None, body=""):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        impl = directory / ("_%s_impl.py" % name.replace("-", "_").replace(".", "_"))
        impl.write_text(
            "import json, os, sys\n"
            "REC = %r\n"
            "OUT = %r\n"
            "ERR = %r\n"
            "RC = %r\n"
            "if REC:\n"
            "    with open(REC, 'a', encoding='utf-8') as fh:\n"
            "        fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if OUT:\n"
            "    sys.stdout.write(OUT)\n"
            "if ERR:\n"
            "    sys.stderr.write(ERR)\n"
            "%s\n"
            "sys.exit(RC)\n"
            % (str(record) if record else "", stdout, stderr, int(rc), body or "pass"),
            encoding="utf-8",
            newline="\n",
        )
        if WINDOWS:
            launcher = directory / f"{name}.cmd"
            launcher.write_text(
                '@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, impl),
                encoding="utf-8",
            )
        else:
            launcher = directory / name
            launcher.write_text(
                '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, impl),
                encoding="utf-8",
                newline="\n",
            )
            launcher.chmod(0o755)
        return launcher

    return _make


@pytest.fixture
def make_sh_script():
    """Create a /bin/sh script with LF endings and the exec bit (for bash tests)."""

    def _make(path, body: str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        return path

    return _make


@pytest.fixture
def write_jsonl():
    """Write a list of dicts as a .jsonl transcript, LF endings."""

    def _write(path, rows) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return path

    return _write


def same_path(a, b) -> bool:
    """Path equality that survives Windows case and short-name differences."""
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(
        os.path.realpath(str(b))
    )


@pytest.fixture
def samefile():
    return same_path
