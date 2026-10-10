"""gm_common: config merging, project identity, tool discovery, state, locks."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

WINDOWS = os.name == "nt"


# ---------------------------------------------------------------- load_config

def test_load_config_defaults_when_nothing_is_configured(gm, project):
    cfg = gm.load_config(project)
    assert cfg == gm.DEFAULTS
    assert cfg["compound"]["threshold_tokens"] == 100_000
    assert cfg["graph_a"]["mode"] == "code-only"
    assert cfg["graph_b"]["passes"] == 2
    assert cfg["session"]["inject_instructions"] is True


def test_load_config_returns_a_fresh_dict_each_call(gm, project):
    cfg = gm.load_config(project)
    cfg["compound"]["threshold_tokens"] = 1
    assert gm.DEFAULTS["compound"]["threshold_tokens"] == 100_000
    assert gm.load_config(project)["compound"]["threshold_tokens"] == 100_000


def test_user_config_overrides_the_default(gm, project, write_config):
    write_config("[compound]\nthreshold_tokens = 5000\n")
    cfg = gm.load_config(project)
    assert cfg["compound"]["threshold_tokens"] == 5000
    assert cfg["compound"]["require_settled"] is True  # untouched keys survive


def test_project_config_wins_over_user_config(gm, project, write_config,
                                              write_project_config):
    write_config("[compound]\nthreshold_tokens = 5000\n")
    write_project_config(project, "[compound]\nthreshold_tokens = 7000\n")
    assert gm.load_config(project)["compound"]["threshold_tokens"] == 7000
    # Without a project dir only the user file is read.
    assert gm.load_config()["compound"]["threshold_tokens"] == 5000


def _write_user_project_config(gm, project, text):
    """The user-side, per-project layer: ~/.graph-mempalace/projects/<slug>/config.toml."""
    path = gm.project_config_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_the_user_side_project_layer_sits_between_the_other_two(gm, project,
                                                                write_config,
                                                                write_project_config):
    assert gm.project_config_path(project) == \
        gm.project_state_dir(project) / "config.toml"
    write_config("[compound]\nthreshold_tokens = 5000\nlock_stale_hours = 1\n")
    _write_user_project_config(gm, project,
                              "[compound]\nthreshold_tokens = 6000\n"
                              "require_settled = false\n")
    write_project_config(project, "[compound]\nthreshold_tokens = 7000\n")
    cfg = gm.load_config(project)
    assert cfg["compound"]["threshold_tokens"] == 7000   # the repo file wins
    assert cfg["compound"]["require_settled"] is False   # only the middle layer set it
    assert cfg["compound"]["lock_stale_hours"] == 1      # only the user file set it


def test_the_user_side_project_layer_may_set_palace(gm, project, capsys, tmp_path):
    wanted = tmp_path / "kept-exchanges"
    _write_user_project_config(
        gm, project,
        '[palace]\nwing = "kept_wing"\nexchanges_dir = "%s"\n'
        % str(wanted).replace("\\", "/"))
    cfg = gm.load_config(project)
    assert capsys.readouterr().err == ""
    assert gm.wing_for(project, cfg) == "kept_wing"
    assert gm.exchanges_dir(project, cfg) == wanted


def test_unknown_section_warns_and_is_ignored(gm, project, write_config, capsys):
    write_config("[nonsense]\nkey = 1\n")
    cfg = gm.load_config(project)
    err = capsys.readouterr().err
    assert "unknown section [nonsense]" in err
    assert "nonsense" not in cfg


def test_unknown_key_warns_and_is_ignored(gm, project, write_config, capsys):
    write_config("[compound]\nthreshold_token = 10\n")
    cfg = gm.load_config(project)
    assert "unknown key compound.threshold_token" in capsys.readouterr().err
    assert "threshold_token" not in cfg["compound"]
    assert cfg["compound"]["threshold_tokens"] == 100_000


def test_section_that_is_not_a_table_warns(gm, project, write_config, capsys):
    write_config('compound = "yes"\n')
    cfg = gm.load_config(project)
    assert "[compound] must be a table" in capsys.readouterr().err
    assert cfg["compound"]["threshold_tokens"] == 100_000


def test_wrong_type_warns_and_keeps_the_default(gm, project, write_config, capsys):
    write_config('[compound]\nthreshold_tokens = "many"\n')
    cfg = gm.load_config(project)
    assert "compound.threshold_tokens should be int" in capsys.readouterr().err
    assert cfg["compound"]["threshold_tokens"] == 100_000


def test_int_given_as_whole_float_is_accepted(gm, project, write_config, capsys):
    write_config("[compound]\nthreshold_tokens = 2.0\n")
    cfg = gm.load_config(project)
    assert capsys.readouterr().err == ""
    assert cfg["compound"]["threshold_tokens"] == 2
    assert isinstance(cfg["compound"]["threshold_tokens"], int)


def test_int_given_as_fractional_float_is_rejected(gm, project, write_config, capsys):
    write_config("[compound]\nthreshold_tokens = 2.5\n")
    cfg = gm.load_config(project)
    assert "compound.threshold_tokens should be int" in capsys.readouterr().err
    assert cfg["compound"]["threshold_tokens"] == 100_000


def test_bool_is_not_accepted_for_an_int_key(gm, project, write_config, capsys):
    write_config("[compound]\nthreshold_tokens = true\n")
    cfg = gm.load_config(project)
    assert "compound.threshold_tokens should be int" in capsys.readouterr().err
    assert cfg["compound"]["threshold_tokens"] == 100_000


def test_int_is_not_accepted_for_a_bool_key(gm, project, write_config, capsys):
    write_config("[compound]\nrequire_settled = 0\n")
    cfg = gm.load_config(project)
    assert "compound.require_settled should be bool" in capsys.readouterr().err
    assert cfg["compound"]["require_settled"] is True


def test_a_warning_is_printed_once_per_process(gm, project, write_config, capsys):
    write_config("[compound]\nthreshold_token = 10\n")
    gm.load_config(project)
    first = capsys.readouterr().err
    gm.load_config(project)
    assert first.count("unknown key") == 1
    assert capsys.readouterr().err == ""   # a hook runs every turn; once is enough
    gm._WARNED.clear()
    gm.load_config(project)
    assert "unknown key compound.threshold_token" in capsys.readouterr().err


def test_malformed_toml_warns_and_falls_back_to_defaults(gm, project, write_config,
                                                         capsys):
    write_config("[compound\nthreshold_tokens = ???\n")
    cfg = gm.load_config(project)
    err = capsys.readouterr().err
    assert "could not parse" in err and "using defaults" in err
    assert cfg == gm.DEFAULTS


def test_invalid_graph_a_mode_falls_back_to_code_only(gm, project, write_config,
                                                      capsys):
    write_config('[graph_a]\nmode = "wild"\n')
    cfg = gm.load_config(project)
    assert "graph_a.mode must be" in capsys.readouterr().err
    assert cfg["graph_a"]["mode"] == "code-only"


def test_valid_graph_a_mode_full_is_kept(gm, project, write_config, capsys):
    write_config('[graph_a]\nmode = "full"\n')
    cfg = gm.load_config(project)
    assert capsys.readouterr().err == ""
    assert cfg["graph_a"]["mode"] == "full"


# -------------------------------------------------------------- project_token

def test_project_token_lowercases_the_basename(gm, tmp_path):
    d = tmp_path / "MyProject"
    d.mkdir()
    assert gm.project_token(d) == "myproject"


def test_project_token_drops_characters_outside_the_allowed_set(gm, tmp_path):
    d = tmp_path / "a b!c-d.e_f"
    d.mkdir()
    assert gm.project_token(d) == "abc-d.e_f"


def test_project_token_falls_back_to_project(gm, tmp_path):
    d = tmp_path / "!!!"
    try:
        d.mkdir()
    except OSError:  # pragma: no cover - filesystem refuses the name
        pytest.skip("this filesystem cannot hold a directory named '!!!'")
    assert gm.project_token(d) == "project"


# ------------------------------------------------------------------- slug_for

def test_slug_for_obeys_the_rule_on_a_real_path(gm, project):
    slug = gm.slug_for(project)
    resolved = str(Path(project).resolve())
    if resolved[1:2] == ":":
        resolved = resolved[0].lower() + resolved[1:]
    assert slug == re.sub(r"[^A-Za-z0-9]", "-", resolved)
    assert all(ch.isalnum() or ch == "-" for ch in slug)


@pytest.mark.skipif(not WINDOWS, reason="Windows drive-letter rule")
def test_slug_for_windows_path_lowercases_the_drive_letter(gm):
    assert gm.slug_for(r"C:\Users\x\proj") == "c--Users-x-proj"


@pytest.mark.skipif(WINDOWS, reason="POSIX absolute path")
def test_slug_for_posix_path(gm):
    raw = "/Users/x/my.app"
    if str(Path(raw).resolve()) != raw:
        pytest.skip("resolve() rewrites this path on this machine")
    assert gm.slug_for(raw) == "-Users-x-my-app"


def test_transcripts_dir_sits_under_claude_projects(gm, project):
    assert gm.transcripts_dir(project) == gm.CLAUDE_PROJECTS_DIR / gm.slug_for(project)
    assert gm.CLAUDE_PROJECTS_DIR == gm.HOME / ".claude" / "projects"


def test_claude_config_dir_moves_the_transcript_tree(gm_home, tmp_path, monkeypatch,
                                                     project):
    """CLAUDE_CONFIG_DIR is read at import time, so the module is reloaded."""
    elsewhere = tmp_path / "claude-elsewhere"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
    gm = importlib.reload(importlib.import_module("gm_common"))
    assert gm.CLAUDE_PROJECTS_DIR == elsewhere / "projects"
    assert gm.transcripts_dir(project) == elsewhere / "projects" / gm.slug_for(project)


def test_project_state_dir_sits_under_the_state_dir(gm, project):
    assert gm.project_state_dir(project) == gm.STATE_DIR / "projects" / gm.slug_for(project)


# -------------------------------------------------------------- exchanges_dir

def test_exchanges_dir_default_location(gm, project):
    assert gm.exchanges_dir(project) == gm.project_state_dir(project) / "exchanges"


def test_exchanges_dir_override_expands_a_tilde(gm, project, monkeypatch,
                                                write_config, tmp_path):
    fake_home = tmp_path / "userhome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    write_config('[palace]\nexchanges_dir = "~/gmex"\n')
    assert gm.exchanges_dir(project) == Path(os.path.expanduser("~/gmex"))


def test_a_relative_exchanges_dir_is_refused(gm, project, write_config, capsys):
    write_config('[palace]\nexchanges_dir = "somewhere/else"\n')
    cfg = gm.load_config(project)
    assert "must be an absolute path" in capsys.readouterr().err
    assert cfg["palace"]["exchanges_dir"] == ""
    assert gm.exchanges_dir(project, cfg) == gm.project_state_dir(project) / "exchanges"


def test_a_project_config_may_not_set_palace(gm, project, write_project_config,
                                             capsys):
    write_project_config(project, '[palace]\nwing = "hijacked"\n'
                                  'exchanges_dir = "/tmp/hijacked"\n')
    cfg = gm.load_config(project)
    err = capsys.readouterr().err
    assert "[palace] is read from" in err and "ignored here" in err
    assert cfg["palace"] == {"wing": "", "exchanges_dir": "", "path": ""}


def test_a_project_config_may_still_set_other_sections(gm, project,
                                                       write_project_config):
    write_project_config(project, "[graph_b]\nconcurrency = 4\n")
    assert gm.load_config(project)["graph_b"]["concurrency"] == 4


def test_graph_paths(gm, project):
    assert gm.graph_a_path(project) == Path(project) / "graphify-out" / "graph.json"
    assert gm.graph_b_path(project) == gm.exchanges_dir(project) / "graphify-out" / "graph.json"


# ---------------------------------------------------------------- palace_path
# One palace per project is the isolation guarantee: MemPalace's search, its
# knowledge graph and the tunnels it digs after a mine never leave a palace.

def test_palace_path_default_sits_inside_the_project_state_dir(gm, project):
    assert gm.palace_path(project) == gm.project_state_dir(project) / "palace"


def test_two_projects_get_two_palaces(gm, tmp_path):
    a, b = tmp_path / "alpha", tmp_path / "beta"
    a.mkdir(); b.mkdir()
    assert gm.palace_path(a) != gm.palace_path(b)


def test_palace_path_is_never_created_by_asking_for_it(gm, project):
    assert not gm.palace_path(project).exists()


def test_palace_path_override_from_the_user_config(gm, project, write_config, tmp_path):
    shared = tmp_path / "one-shared-palace"
    write_config(f'[palace]\npath = "{shared.as_posix()}"\n')
    assert gm.palace_path(project) == shared


def test_palace_path_override_from_the_per_project_user_layer(gm, project, tmp_path):
    pinned = tmp_path / "pinned"
    path = gm.project_config_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[palace]\npath = "{pinned.as_posix()}"\n', encoding="utf-8")
    assert gm.palace_path(project) == pinned


def test_a_relative_palace_path_is_refused(gm, project, write_config, capsys):
    write_config('[palace]\npath = "somewhere/else"\n')
    cfg = gm.load_config(project)
    assert "palace.path must be an absolute path" in capsys.readouterr().err
    assert cfg["palace"]["path"] == ""
    assert gm.palace_path(project, cfg) == gm.project_state_dir(project) / "palace"


def test_a_repo_config_cannot_move_the_palace(gm, project, write_project_config, capsys):
    write_project_config(project, '[palace]\npath = "/tmp/hijacked"\n')
    cfg = gm.load_config(project)
    assert "ignored here" in capsys.readouterr().err
    assert gm.palace_path(project, cfg) == gm.project_state_dir(project) / "palace"


def test_mempalace_argv_puts_the_global_palace_flag_before_the_subcommand(
        gm, project, tmp_path, monkeypatch, empty_path, make_tool):
    exe = make_tool(tmp_path / "bins", "mempalace")
    monkeypatch.setenv("GM_MEMPALACE_BIN", str(exe))
    gm._FIND_TOOL_CACHE.clear()
    argv = gm.mempalace_argv(project, None, "mine", "somewhere", "--wing", "w")
    assert argv == [str(exe), "--palace", str(gm.palace_path(project)),
                    "mine", "somewhere", "--wing", "w"]


def test_mempalace_argv_is_none_when_mempalace_is_missing(gm, project, empty_path):
    assert gm.mempalace_argv(project, None, "status") is None


def test_tool_env_names_the_palace_only_when_given_a_project(gm, project, monkeypatch):
    monkeypatch.delenv("MEMPALACE_PALACE_PATH", raising=False)
    assert "MEMPALACE_PALACE_PATH" not in gm.tool_env(gm.load_config(project))
    env = gm.tool_env(gm.load_config(project), project)
    assert env["MEMPALACE_PALACE_PATH"] == str(gm.palace_path(project))


# ------------------------------------------------------------------- wing_for

def test_wing_for_uses_the_plain_name_and_records_it(gm, project):
    wing = gm.wing_for(project, record=True)
    assert wing == "proj_dialogue"
    assert gm.read_json(gm.WINGS_PATH, {}) == {"proj_dialogue": gm.slug_for(project)}


def test_wing_for_does_not_record_when_asked_not_to(gm, project):
    assert gm.wing_for(project, record=False) == "proj_dialogue"
    assert not gm.WINGS_PATH.exists()


def test_wing_for_hashes_on_collision_with_another_project(gm, project):
    gm.write_json_atomic(gm.WINGS_PATH, {"proj_dialogue": "some-other-slug"})
    slug = gm.slug_for(project)
    expected = "proj_%s_dialogue" % hashlib.sha1(slug.encode("utf-8")).hexdigest()[:6]
    assert gm.wing_for(project, record=True) == expected
    assert gm.read_json(gm.WINGS_PATH, {})[expected] == slug


def test_wing_for_keeps_the_plain_name_for_its_own_project(gm, project):
    gm.write_json_atomic(gm.WINGS_PATH, {"proj_dialogue": gm.slug_for(project)})
    assert gm.wing_for(project, record=True) == "proj_dialogue"


def test_wing_for_override_wins_and_writes_nothing(gm, project, write_config):
    write_config('[palace]\nwing = "kept_wing"\n')
    assert gm.wing_for(project) == "kept_wing"
    assert not gm.WINGS_PATH.exists()


def test_an_unusable_wing_name_is_refused_and_derived_instead(gm, project,
                                                              write_config, capsys):
    write_config('[palace]\nwing = "not a wing/name"\n')
    cfg = gm.load_config(project)
    assert "palace.wing must match" in capsys.readouterr().err
    assert cfg["palace"]["wing"] == ""
    assert gm.wing_for(project, cfg) == "proj_dialogue"


# ------------------------------------------------------------------ find_tool

def test_find_tool_env_override_wins(gm, gm_home, tmp_path, monkeypatch, empty_path):
    target = tmp_path / "anywhere" / "widget"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GM_WIDGET_BIN", str(target))
    assert gm.find_tool("widget") == str(target)


def test_find_tool_env_key_replaces_non_alphanumerics(gm, tmp_path, monkeypatch,
                                                      empty_path):
    target = tmp_path / "mempalace-mcp-fake"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GM_MEMPALACE_MCP_BIN", str(target))
    assert gm.find_tool("mempalace-mcp") == str(target)


def test_find_tool_ignores_an_override_that_does_not_exist(gm, tmp_path, monkeypatch,
                                                           empty_path):
    monkeypatch.setenv("GM_WIDGET_BIN", str(tmp_path / "gone"))
    assert gm.find_tool("widget") is None


def test_find_tool_finds_local_bin_and_remembers_it(gm, gm_home, empty_path):
    local = gm_home / ".local" / "bin"
    local.mkdir(parents=True)
    name = "thing.exe" if WINDOWS else "thing"
    (local / name).write_text("x", encoding="utf-8")
    assert gm.find_tool("thing") == str(local / name)
    assert gm.read_json(gm.TOOLS_CACHE_PATH, {}) == {"thing": str(local / name)}


def test_find_tool_looks_inside_the_uv_tool_environment(gm, gm_home, empty_path):
    if WINDOWS:
        d = gm_home / "AppData" / "Roaming" / "uv" / "tools" / "graphifyy" / "Scripts"
        name = "graphify.exe"
    else:
        d = gm_home / ".local" / "share" / "uv" / "tools" / "graphifyy" / "bin"
        name = "graphify"
    d.mkdir(parents=True)
    (d / name).write_text("x", encoding="utf-8")
    assert gm.find_tool("graphify") == str(d / name)


@pytest.mark.parametrize("name,tool", [
    ("mempalace", "mempalace"),
    ("mempalace-mcp", "mempalace"),
    ("graphify", "graphifyy"),
    ("graphify-mcp", "graphifyy"),
])
def test_uv_tool_environment_for_a_name(gm, name, tool):
    assert gm._uv_tool_env_for(name) == tool


def test_an_unrelated_name_has_no_uv_tool_environment(gm):
    assert gm._uv_tool_env_for("uv") is None


def test_find_tool_uses_a_path_remembered_from_an_earlier_run(gm, tmp_path,
                                                              empty_path,
                                                              clear_tool_caches):
    target = tmp_path / "remembered-tool"
    target.write_text("x", encoding="utf-8")
    gm.write_json_atomic(gm.TOOLS_CACHE_PATH, {"remembered-tool": str(target)})
    clear_tool_caches(gm, keep_file_cache=True)
    assert gm.find_tool("remembered-tool") == str(target)


def test_a_remembered_path_that_has_vanished_is_ignored(gm, tmp_path, empty_path,
                                                        clear_tool_caches):
    gm.write_json_atomic(gm.TOOLS_CACHE_PATH, {"ghost": str(tmp_path / "gone")})
    clear_tool_caches(gm, keep_file_cache=True)
    assert gm.find_tool("ghost") is None


def test_an_env_override_is_not_written_to_the_cache(gm, tmp_path, monkeypatch,
                                                     empty_path):
    target = tmp_path / "widget"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GM_WIDGET_BIN", str(target))
    assert gm.find_tool("widget") == str(target)
    assert not gm.TOOLS_CACHE_PATH.exists()


def test_find_tool_returns_none_when_nothing_is_installed(gm, empty_path):
    assert gm.find_tool("definitely-not-installed-anywhere") is None


def test_find_tool_caches_its_answer(gm, tmp_path, monkeypatch, empty_path,
                                     clear_tool_caches):
    target = tmp_path / "cached-tool"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GM_CACHED_TOOL_BIN", str(target))
    assert gm.find_tool("cached-tool") == str(target)
    # The override goes away, but the memoised answer stands until it is cleared.
    monkeypatch.delenv("GM_CACHED_TOOL_BIN")
    assert gm.find_tool("cached-tool") == str(target)
    clear_tool_caches(gm)
    assert gm.find_tool("cached-tool") is None


# ------------------------------------------------------------------ uv_output

def test_uv_output_strips_colour_codes_and_asks_for_none(gm, tmp_path, make_tool):
    record = tmp_path / "uv-argv.jsonl"
    wanted = str(tmp_path / "uvtools" / "bin")
    uv = make_tool(tmp_path / "bins", "uv",
                   stdout="\x1b[36m" + wanted + "\x1b[39m\n", record=record)
    assert gm.uv_output([str(uv), "tool", "dir", "--bin"]) == wanted
    argv = json.loads(record.read_text(encoding="utf-8").splitlines()[0])
    assert argv == ["--color", "never", "tool", "dir", "--bin"]


def test_uv_output_unsets_force_color(gm, tmp_path, make_tool, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    uv = make_tool(tmp_path / "bins", "uv", body=(
        "sys.stdout.write('NO_COLOR=%s FORCE=%s CLICOLOR=%s' % ("
        "os.environ.get('NO_COLOR', ''), os.environ.get('FORCE_COLOR', 'unset'),"
        "os.environ.get('CLICOLOR_FORCE', 'unset')))"
    ))
    assert gm.uv_output([str(uv), "tool", "dir"]) == \
        "NO_COLOR=1 FORCE=unset CLICOLOR=unset"


def test_uv_output_is_empty_on_failure(gm, tmp_path, make_tool):
    broken = make_tool(tmp_path / "bins", "uv", stdout="path\n", rc=2)
    assert gm.uv_output([str(broken), "tool", "dir"]) == ""
    assert gm.uv_output([str(tmp_path / "no-such-uv"), "tool", "dir"]) == ""


def test_uv_tool_bin_is_asked_at_most_once(gm, tmp_path, make_tool, monkeypatch,
                                           clear_tool_caches):
    record = tmp_path / "uv-calls.jsonl"
    bins = tmp_path / "bins"
    make_tool(bins, "uv", stdout=str(tmp_path / "uvbin") + "\n", record=record)
    monkeypatch.setenv("PATH", str(bins))
    clear_tool_caches(gm)
    assert gm.find_tool("absent-one") is None
    assert gm.find_tool("absent-two") is None
    assert len(record.read_text(encoding="utf-8").splitlines()) == 1


# ------------------------------------------------------------------- tool_env

def test_tool_env_carries_the_graphify_settings(gm, project, monkeypatch):
    for key in ("GRAPHIFY_NO_AUTO_REFRESH", "GRAPHIFY_CLAUDE_CLI_PARALLEL",
                "GRAPHIFY_CLAUDE_CLI_MODEL", "PYTHONIOENCODING"):
        monkeypatch.delenv(key, raising=False)
    env = gm.tool_env(gm.load_config(project))
    assert env["GRAPHIFY_NO_AUTO_REFRESH"] == "1"
    assert env["GRAPHIFY_CLAUDE_CLI_PARALLEL"] == "1"
    assert env["GRAPHIFY_CLAUDE_CLI_MODEL"] == "sonnet"
    assert env["PYTHONIOENCODING"] == "utf-8"


# ----------------------------------------------------------------- json state

def test_write_json_atomic_round_trip_creates_the_parent(gm, tmp_path):
    path = tmp_path / "deep" / "deeper" / "state.json"
    gm.write_json_atomic(path, {"a": [1, 2], "b": "ü"})
    assert gm.read_json(path, None) == {"a": [1, 2], "b": "ü"}
    assert not list(path.parent.glob("*.tmp"))


def test_read_json_returns_the_default_when_missing_or_broken(gm, tmp_path):
    assert gm.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert gm.read_json(broken, "fallback") == "fallback"


def test_append_log_creates_the_state_dir_and_the_file(gm):
    assert not gm.LOG_PATH.exists()
    gm.append_log("hello log")
    text = gm.LOG_PATH.read_text(encoding="utf-8")
    assert text.endswith("hello log\n")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} hello log$", text.strip())


# ----------------------------------------------------------------------- lock

def test_lock_acquire_release_cycle(gm):
    assert gm.lock_held() is False
    assert gm.lock_acquire() is True
    assert gm.LOCK_PATH.exists()
    assert gm.lock_held() is True
    assert gm.lock_acquire() is False  # a second holder is refused
    gm.lock_release()
    assert not gm.LOCK_PATH.exists()
    assert gm.lock_held() is False


def test_lock_release_is_safe_when_there_is_no_lock(gm):
    gm.lock_release()  # must not raise
    assert not gm.LOCK_PATH.exists()


def test_stale_lock_is_removed_and_can_be_retaken(gm):
    assert gm.lock_acquire() is True
    old = time.time() - 4 * 3600
    os.utime(gm.LOCK_PATH, (old, old))
    assert gm.lock_held(3.0) is False
    assert not gm.LOCK_PATH.exists()
    assert gm.lock_acquire(3.0) is True


def test_the_lock_records_the_holders_pid(gm):
    assert gm.lock_owner_pid() is None
    assert gm.lock_acquire() is True
    assert gm.lock_owner_pid() == os.getpid()


def test_release_leaves_another_processs_lock_in_place(gm):
    gm.ensure_state_dir()
    gm.LOCK_PATH.write_text("2026-03-04T05:06:07 pid=999999\n", encoding="utf-8")
    gm.lock_release()
    assert gm.LOCK_PATH.exists()        # not ours, so not ours to delete
    gm.lock_release(force=True)
    assert not gm.LOCK_PATH.exists()


def test_lock_touch_keeps_a_long_compound_alive(gm):
    assert gm.lock_acquire() is True
    old = time.time() - 2 * 3600
    os.utime(gm.LOCK_PATH, (old, old))
    gm.lock_touch()
    assert time.time() - gm.LOCK_PATH.stat().st_mtime < 60


def test_fresh_lock_is_held_within_the_staleness_window(gm):
    assert gm.lock_acquire(3.0) is True
    old = time.time() - 3600
    os.utime(gm.LOCK_PATH, (old, old))
    assert gm.lock_held(3.0) is True


# ------------------------------------------------------------- spawn_detached

def test_spawn_detached_returns_a_pid_and_writes_to_the_log(gm, tmp_path):
    log = tmp_path / "spawn.log"
    pid = gm.spawn_detached([sys.executable, "-c", "print('spawn-marker')"],
                            log_path=log)
    assert isinstance(pid, int) and pid > 0
    deadline = time.time() + 5.0
    text = ""
    while time.time() < deadline:
        if log.exists():
            text = log.read_text(encoding="utf-8", errors="replace")
            if "spawn-marker" in text:
                break
        time.sleep(0.05)
    assert "spawn-marker" in text


def test_spawn_detached_returns_none_and_logs_when_the_exe_is_missing(gm, tmp_path):
    pid = gm.spawn_detached([str(tmp_path / "no-such-binary-xyz")])
    assert pid is None
    assert "spawn failed" in gm.LOG_PATH.read_text(encoding="utf-8")


def test_python_exe_is_this_interpreter(gm):
    assert gm.python_exe() == sys.executable
