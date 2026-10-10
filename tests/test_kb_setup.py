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
    "mcp launcher python",
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
    # The palace note is always there; the ARM note only on an ARM machine.
    others = [a for a in rep["advisories"] if not a.startswith("palace: ")]
    if platform.machine().upper() in ("ARM64", "AARCH64"):
        assert len(others) == 1
        assert "ARM" in others[0]
    else:
        assert others == []


# ------------------------------------------------------------ everything green

def test_every_check_can_pass(kbs, capsys, monkeypatch, tmp_path, make_tool):
    # The launcher check probes ${GM_PYTHON:-python}; PATH is empty here, so the
    # pin is what makes it resolvable - which is the pin --install writes.
    monkeypatch.setenv("GM_PYTHON", sys.executable)
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

def test_only_graphify_mcp_gets_a_path_advisory(kbs, capsys):
    """find_tool finds it, `which` does not: installed, but the spawn still fails.

    Only for graphify-mcp. mempalace-mcp is spawned by scripts/mcp_launch.py,
    which resolves the binary by absolute path itself, so PATH cannot break it.
    """
    kbs.install("mempalace-mcp")
    gra = kbs.install("graphify-mcp")
    _, rep = json_report(kbs, capsys)
    assert next(c for c in rep["checks"] if c["check"] == "mcp servers")["ok"] is True
    lines = [a for a in rep["advisories"] if a.startswith("mcp on PATH")]
    assert len(lines) == 1
    line = lines[0]
    assert "mempalace-mcp" not in line
    key = kbs.mod.env_key_for("graphify-mcp")
    assert key == "GM_GRAPHIFY_MCP_BIN"
    assert "graphify-mcp is installed at %s" % kbs.mod.fwd(gra) in line
    assert "not on PATH for Claude Code's MCP spawn" in line
    assert '"env": {"%s": "%s"}' % (key, kbs.mod.fwd(gra)) in line
    assert "\\" not in line                      # forward slashes, Windows too


def test_a_server_on_path_needs_no_advisory(kbs, capsys, monkeypatch):
    kbs.install("mempalace-mcp")
    kbs.install("graphify-mcp")
    monkeypatch.setenv("PATH", str(kbs.bins))      # now `which` finds them too
    _, rep = json_report(kbs, capsys)
    assert [a for a in rep["advisories"] if "mcp on PATH" in a] == []


def test_a_server_that_is_absent_gets_no_advisory_only_the_failing_row(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    assert [a for a in rep["advisories"] if "mcp on PATH" in a] == []
    row = next(c for c in rep["checks"] if c["check"] == "mcp servers")
    assert row["ok"] is False


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


# ------------------------------------------------- the MCP launcher's Python

def launcher_row(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    return next(c for c in rep["checks"] if c["check"] == kbs.mod.LAUNCHER_CHECK)


def test_a_pinned_interpreter_new_enough_passes(kbs, capsys, monkeypatch, make_tool):
    """GM_PYTHON names it and it reports 3.12: .mcp.json can start the launcher."""
    fake = make_tool(kbs.bins, "fakepython", stdout="(3, 12)\n")
    monkeypatch.setenv("GM_PYTHON", str(fake))
    row = launcher_row(kbs, capsys)
    assert row["ok"] is True
    assert kbs.mod.fwd(fake) in row["detail"]
    assert "3.12" in row["detail"]
    assert row["fix"] == ""


def test_an_interpreter_that_is_too_old_fails_with_the_install_fix(
        kbs, capsys, monkeypatch, make_tool):
    fake = make_tool(kbs.bins, "oldpython", stdout="(3, 9)\n")
    monkeypatch.setenv("GM_PYTHON", str(fake))
    row = launcher_row(kbs, capsys)
    assert row["ok"] is False
    assert "3.9" in row["detail"] and "too old" in row["detail"]
    assert row["fix"] == kbs.mod.LAUNCHER_FIX
    assert "GM_PYTHON" in row["fix"]


def test_nothing_resolvable_fails(kbs, capsys):
    """Empty PATH and no GM_PYTHON: `${GM_PYTHON:-python}` resolves to nothing."""
    assert kbs.mod.launcher_interpreter() is None
    row = launcher_row(kbs, capsys)
    assert row["ok"] is False
    assert "not found" in row["detail"]
    assert "GM_PYTHON" in row["detail"]
    assert row["fix"] == kbs.mod.LAUNCHER_FIX


def test_an_interpreter_that_cannot_run_fails(kbs, capsys, monkeypatch, tmp_path):
    missing = tmp_path / "no-such-python"
    monkeypatch.setenv("GM_PYTHON", str(missing))
    row = launcher_row(kbs, capsys)
    assert row["ok"] is False
    assert "did not run" in row["detail"]


# ------------------------------------------------- pinning GM_PYTHON for 0.2.0

def settings_file(kbs):
    return kbs.mod.settings_path()


def test_the_settings_file_is_under_the_claude_config_dir(kbs, gm_home):
    assert settings_file(kbs) == gm_home / ".claude" / "settings.json"


def test_gm_python_is_written_and_every_other_key_survives(kbs, capsys):
    path = settings_file(kbs)
    path.write_text(json.dumps({"model": "opus",
                                "permissions": {"allow": ["Bash(ls:*)"]},
                                "env": {"OTHER": "keep me"}}, indent=2),
                    encoding="utf-8")
    assert kbs.mod.ensure_gm_python_setting() == "written"

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    data = json.loads(text)
    assert data["env"]["GM_PYTHON"] == kbs.mod.fwd(sys.executable)
    assert data["env"]["OTHER"] == "keep me"
    assert data["model"] == "opus"
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
    out = capsys.readouterr().out
    assert "GM_PYTHON" in out
    assert kbs.mod.RESTART_LINE in out


def test_gm_python_is_written_when_there_is_no_settings_file_yet(kbs, capsys):
    path = settings_file(kbs)
    assert not path.exists()
    assert kbs.mod.ensure_gm_python_setting() == "written"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "env": {"GM_PYTHON": kbs.mod.fwd(sys.executable)}}


def test_an_env_block_that_is_not_an_object_is_replaced_not_crashed_on(kbs, capsys):
    path = settings_file(kbs)
    path.write_text(json.dumps({"env": "nonsense", "model": "opus"}), encoding="utf-8")
    assert kbs.mod.ensure_gm_python_setting() == "written"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"] == {"GM_PYTHON": kbs.mod.fwd(sys.executable)}
    assert data["model"] == "opus"


def test_an_existing_pin_is_left_exactly_as_it_is(kbs, capsys):
    path = settings_file(kbs)
    before = json.dumps({"env": {"GM_PYTHON": "D:/py311/python.exe"}}, indent=2)
    path.write_text(before, encoding="utf-8")
    assert kbs.mod.ensure_gm_python_setting() == "present"
    assert path.read_text(encoding="utf-8") == before
    assert capsys.readouterr().out == ""


def test_nothing_is_written_when_gm_python_is_already_in_the_environment(
        kbs, capsys, monkeypatch):
    monkeypatch.setenv("GM_PYTHON", sys.executable)
    assert kbs.mod.ensure_gm_python_setting() == "env"
    assert not settings_file(kbs).exists()
    assert capsys.readouterr().out == ""


def test_an_unparsable_settings_file_is_not_touched_and_the_snippet_is_printed(
        kbs, capsys):
    path = settings_file(kbs)
    broken = '{"model": "opus",\n'          # a hand-edit that lost a brace
    path.write_text(broken, encoding="utf-8")
    assert kbs.mod.ensure_gm_python_setting() == "unparsable"
    assert path.read_text(encoding="utf-8") == broken
    out = capsys.readouterr().out
    assert kbs.mod.gm_python_snippet() in out
    assert '"GM_PYTHON": "%s"' % kbs.mod.fwd(sys.executable) in out
    assert "\\" not in kbs.mod.gm_python_snippet()


def test_a_settings_file_that_is_a_json_list_is_not_touched(kbs, capsys):
    path = settings_file(kbs)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert kbs.mod.ensure_gm_python_setting() == "unparsable"
    assert path.read_text(encoding="utf-8") == "[1, 2, 3]"


# ----------------------------------------------------- one palace per project

def test_the_palace_note_names_this_projects_palace(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    expected = kbs.gm.palace_path(str(kbs.project))
    note = next(a for a in rep["advisories"] if a.startswith("palace: "))
    assert note == "palace: %s" % expected
    assert str(kbs.gm.project_state_dir(str(kbs.project))) in note


def test_the_palace_note_follows_the_config_override(kbs, capsys, write_config,
                                                     tmp_path):
    elsewhere = tmp_path / "pinned-palace"
    write_config('[palace]\npath = "%s"\n' % str(elsewhere).replace("\\", "/"))
    _, rep = json_report(kbs, capsys)
    note = next(a for a in rep["advisories"] if a.startswith("palace: "))
    assert note == "palace: %s" % elsewhere


def test_the_palace_note_prints_as_a_note_and_never_fails_the_run(kbs, capsys,
                                                                  monkeypatch):
    monkeypatch.setattr(kbs.mod, "collect",
                        lambda project_dir: kbs.mod.Report(
                            checks=[kbs.mod.Check("uv", True, "/bin/uv")],
                            advisories=["palace: /tmp/palace"]))
    assert kbs.report(("--check",)) == 0
    out = capsys.readouterr().out
    assert "[NOTE   ] palace: /tmp/palace" in out
    assert "verdict: ready" in out


def test_the_mempalace_config_fix_names_the_project_palace(kbs, capsys):
    _, rep = json_report(kbs, capsys)
    row = next(c for c in rep["checks"] if c["check"] == "mempalace config")
    assert row["ok"] is False
    assert "--palace" in row["fix"]
    assert str(kbs.gm.palace_path(str(kbs.project))) in row["fix"]
