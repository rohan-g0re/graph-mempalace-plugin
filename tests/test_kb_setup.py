"""kb_setup --check: the JSON shape, the fixes, and each prerequisite probe.

`--install` is never run here: it would install real tools on the machine.
One test proves --check cannot reach it.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EXPECTED_CHECKS = [
    "uv",
    "python >= 3.11",
    "mempalace",
    "graphify",
    "graphify extras (pdf, sql, mcp)",
    "mcp servers",
    "mempalace config",
    "graphify patch (--strict-mcp-config)",
    "global git excludes (graphify-out/)",
]

WINDOWS = os.name == "nt"


@pytest.fixture
def kbs(gm_home, project, empty_path, tmp_path, monkeypatch, make_tool):
    gm = importlib.import_module("gm_common")
    mod = importlib.import_module("kb_setup")
    bins = tmp_path / "bins"

    def install(name, **kwargs):
        path = make_tool(bins, name, **kwargs)
        monkeypatch.setenv("GM_%s_BIN" % name.upper().replace("-", "_"), str(path))
        gm._FIND_TOOL_CACHE.clear()
        return path

    def report(argv=("--check", "--json")):
        rc = mod.main([*argv, "--cwd", str(project)])
        return rc

    return SimpleNamespace(gm=gm, mod=mod, project=project, bins=bins,
                           install=install, report=report)


def json_report(kbs, capsys, argv=("--check", "--json")):
    rc = kbs.report(argv)
    return rc, json.loads(capsys.readouterr().out)


# ------------------------------------------------------------------ the shape

def test_the_json_report_has_the_documented_shape(kbs, capsys):
    rc, rep = json_report(kbs, capsys)
    assert rc == 1
    assert set(rep) == {"ok", "checks", "advisories"}
    assert rep["ok"] is False
    assert [c["check"] for c in rep["checks"]] == EXPECTED_CHECKS
    for check in rep["checks"]:
        assert set(check) == {"check", "ok", "detail", "fix"}
        assert isinstance(check["ok"], bool)
    assert isinstance(rep["advisories"], list)


def test_every_missing_row_carries_a_fix(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    missing = [c for c in rep["checks"] if not c["ok"]]
    assert missing, "with no tools installed something must be missing"
    for check in missing:
        assert check["fix"].strip(), check["check"]


def test_the_interpreter_running_the_suite_passes_the_python_check(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    python = next(c for c in rep["checks"] if c["check"] == "python >= 3.11")
    assert python["ok"] is True
    assert sys.executable in python["detail"]


def test_the_uv_fix_is_the_official_installer_for_this_os(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    uv = next(c for c in rep["checks"] if c["check"] == "uv")
    assert uv["ok"] is False
    assert uv["fix"] == (kbs.mod.UV_INSTALL_WINDOWS if WINDOWS
                         else kbs.mod.UV_INSTALL_POSIX)


def test_found_tools_are_reported_with_their_path(kbs, capsys):
    mempalace = kbs.install("mempalace")
    graphify = kbs.install("graphify")
    _, rep = json_report(kbs, capsys)
    found = {c["check"]: c for c in rep["checks"]}
    assert found["mempalace"]["ok"] is True
    assert found["mempalace"]["detail"] == str(mempalace)
    assert found["graphify"]["ok"] is True
    assert found["graphify"]["detail"] == str(graphify)
    assert rep["ok"] is False  # the rest is still missing


def test_the_table_prints_a_fix_under_every_missing_line(kbs, capsys):
    assert kbs.report(("--check",)) == 1
    out = capsys.readouterr().out
    assert "graph-mempalace prerequisites" in out
    assert "[MISSING] uv" in out
    assert "fix:" in out
    assert "verdict: missing pieces above" in out


def test_check_never_reaches_the_installer(kbs, monkeypatch, capsys):
    def never(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("--check must not install anything")

    monkeypatch.setattr(kbs.mod, "do_install", never)
    assert kbs.report(("--check", "--json")) == 1


def test_the_arm_advisory_matches_the_machine(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    if platform.machine().upper() in ("ARM64", "AARCH64"):
        assert len(rep["advisories"]) == 1
        assert "ARM" in rep["advisories"][0]
    else:
        assert rep["advisories"] == []


# ------------------------------------------------------------ everything green

def test_every_check_can_pass(kbs, capsys, monkeypatch, tmp_path, make_tool):
    kbs.install("uv")
    kbs.install("mempalace")
    kbs.install("graphify")
    kbs.install("mempalace-mcp")
    kbs.install("graphify-mcp")

    # The one interpreter that can import graphify is faked: it answers the
    # extras probe with rc 0 and the package-dir probe with a real directory.
    pkg = tmp_path / "graphify_pkg"
    pkg.mkdir()
    (pkg / "llm.py").write_text('argv = ["--strict-mcp-config"]\n', encoding="utf-8")
    tool_python = make_tool(kbs.bins, "toolpython", stdout=str(pkg) + "\n")
    monkeypatch.setattr(kbs.mod, "tool_python",
                        lambda uv, tool=kbs.mod.GRAPHIFY_TOOL: Path(tool_python))

    kbs.mod.MEMPALACE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    kbs.mod.MEMPALACE_CONFIG.write_text("{}", encoding="utf-8")
    excludes = kbs.mod.DEFAULT_GIT_EXCLUDES
    excludes.parent.mkdir(parents=True, exist_ok=True)
    excludes.write_text("graphify-out/\n", encoding="utf-8")

    rc, rep = json_report(kbs, capsys)
    assert [c["check"] for c in rep["checks"] if not c["ok"]] == []
    assert rep["ok"] is True
    assert rc == 0


def test_the_table_says_ready_when_nothing_is_missing(kbs, capsys, monkeypatch):
    monkeypatch.setattr(kbs.mod, "collect",
                        lambda project_dir: kbs.mod.Report(
                            checks=[kbs.mod.Check("uv", True, "/bin/uv")]))
    assert kbs.report(("--check",)) == 0
    assert "verdict: ready" in capsys.readouterr().out


# ------------------------------------------------------------- the MCP servers

def test_the_mcp_row_lists_both_servers_and_both_install_commands(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    row = next(c for c in rep["checks"] if c["check"] == "mcp servers")
    assert row["ok"] is False
    assert row["detail"] == "not found: mempalace-mcp, graphify-mcp"
    assert "uv tool install mempalace" in row["fix"]
    assert 'uv tool install "%s"' % kbs.mod.GRAPHIFY_SPEC in row["fix"]


def test_one_found_server_is_named_with_its_path_and_only_the_other_fix(kbs, capsys):
    mcp = kbs.install("mempalace-mcp")
    _, rep = json_report(kbs, capsys)
    row = next(c for c in rep["checks"] if c["check"] == "mcp servers")
    assert row["ok"] is False                      # graphify-mcp is still absent
    assert "not found: graphify-mcp" in row["detail"]
    assert "mempalace-mcp at %s" % mcp in row["detail"]
    assert row["fix"] == 'uv tool install "%s"' % kbs.mod.GRAPHIFY_SPEC


def test_both_servers_installed_passes_with_both_paths(kbs, capsys):
    mem = kbs.install("mempalace-mcp")
    gra = kbs.install("graphify-mcp")
    _, rep = json_report(kbs, capsys)
    row = next(c for c in rep["checks"] if c["check"] == "mcp servers")
    assert row["ok"] is True
    assert row["fix"] == ""
    assert str(mem) in row["detail"] and str(gra) in row["detail"]


# ---------------------------------------------------- the mcp on PATH advisory

def test_a_server_that_is_installed_but_off_path_is_an_advisory_not_a_failure(
        kbs, capsys):
    """find_tool finds it, `which` does not: installed, but the spawn still fails."""
    mem = kbs.install("mempalace-mcp")
    gra = kbs.install("graphify-mcp")
    _, rep = json_report(kbs, capsys)
    assert next(c for c in rep["checks"] if c["check"] == "mcp servers")["ok"] is True
    lines = [a for a in rep["advisories"] if a.startswith("mcp on PATH")]
    assert len(lines) == 2
    for name, path in (("mempalace-mcp", mem), ("graphify-mcp", gra)):
        line = next(a for a in lines if name in a)
        key = kbs.mod.env_key_for(name)
        assert "%s is installed at %s" % (name, kbs.mod.fwd(path)) in line
        assert "not on PATH for Claude Code's MCP spawn" in line
        assert '"env": {"%s": "%s"}' % (key, kbs.mod.fwd(path)) in line
        assert "\\" not in line                  # forward slashes, Windows too


def test_a_server_on_path_needs_no_advisory(kbs, capsys, monkeypatch):
    kbs.install("mempalace-mcp")
    kbs.install("graphify-mcp")
    monkeypatch.setenv("PATH", str(kbs.bins))      # now `which` finds them too
    _, rep = json_report(kbs, capsys)
    assert [a for a in rep["advisories"] if "mcp on PATH" in a] == []


def test_a_server_that_is_absent_gets_no_advisory_only_the_failing_row(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    assert [a for a in rep["advisories"] if "mcp on PATH" in a] == []
    assert rep["checks"][5]["check"] == "mcp servers"
    assert rep["checks"][5]["ok"] is False


@pytest.mark.parametrize("tool,key", [
    ("mempalace-mcp", "GM_MEMPALACE_MCP_BIN"),
    ("graphify-mcp", "GM_GRAPHIFY_MCP_BIN"),
    ("graphify", "GM_GRAPHIFY_BIN"),
])
def test_env_key_for_builds_the_key_find_tool_and_mcp_json_read(kbs, tool, key):
    assert kbs.mod.env_key_for(tool) == key


def test_fwd_turns_a_windows_path_into_forward_slashes(kbs):
    assert kbs.mod.fwd(r"C:\Users\you\.local\bin\mempalace-mcp.exe") == \
        "C:/Users/you/.local/bin/mempalace-mcp.exe"


# ------------------------------------------------------------- the probes alone

def test_uv_tool_dir_falls_back_to_the_documented_default(kbs):
    assert kbs.mod.uv_tool_dir(None) is None
    guess = (kbs.gm.HOME / "AppData" / "Roaming" / "uv" / "tools") if WINDOWS \
        else (kbs.gm.HOME / ".local" / "share" / "uv" / "tools")
    guess.mkdir(parents=True)
    assert kbs.mod.uv_tool_dir(None) == guess


def test_uv_tool_dir_asks_uv_and_strips_its_colour_codes(kbs, tmp_path, make_tool):
    wanted = tmp_path / "uv-tools"
    uv = make_tool(kbs.bins, "uv-probe",
                   stdout="\x1b[36m" + str(wanted) + "\x1b[39m\n")
    assert kbs.mod.uv_tool_dir(str(uv)) == wanted


def test_tool_python_points_inside_the_tool_environment(kbs, tmp_path, make_tool):
    tools = tmp_path / "uv-tools"
    uv = make_tool(kbs.bins, "uv-probe2", stdout=str(tools) + "\n")
    assert kbs.mod.tool_python(str(uv)) is None
    cand = (tools / "graphifyy" / "Scripts" / "python.exe") if WINDOWS \
        else (tools / "graphifyy" / "bin" / "python")
    cand.parent.mkdir(parents=True)
    cand.write_text("x", encoding="utf-8")
    assert kbs.mod.tool_python(str(uv)) == cand


def test_git_excludes_path_prefers_the_configured_file(kbs, tmp_path, make_tool,
                                                       monkeypatch):
    wanted = tmp_path / "my-excludes"
    make_tool(kbs.bins, "git", stdout=str(wanted) + "\n")
    monkeypatch.setenv("PATH", str(kbs.bins))
    assert kbs.mod.git_excludes_path() == wanted


def test_git_excludes_path_defaults_without_git(kbs):
    assert kbs.mod.git_excludes_path() == kbs.mod.DEFAULT_GIT_EXCLUDES


@pytest.mark.parametrize("text,expected", [
    ("graphify-out/\n", True),
    ("graphify-out\n", True),
    ("  graphify-out/  \n", True),
    ("other/\nnode_modules/\n", False),
    ("", False),
])
def test_excludes_has_rule(kbs, tmp_path, text, expected):
    path = tmp_path / "ignore"
    path.write_text(text, encoding="utf-8")
    assert kbs.mod.excludes_has_rule(path) is expected


def test_excludes_has_rule_is_false_for_a_missing_file(kbs, tmp_path):
    assert kbs.mod.excludes_has_rule(tmp_path / "nope") is False


def test_graphify_pkg_dir_is_none_without_an_interpreter(kbs):
    assert kbs.mod.graphify_pkg_dir(None) is None
