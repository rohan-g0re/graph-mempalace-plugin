"""session_start: what the SessionStart hook prints, and when it compounds."""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def ss(gm_home, project, monkeypatch, empty_path):
    """session_start with spawn_detached replaced, and no tools found yet."""
    mod = importlib.import_module("session_start")
    spawns: list = []

    def fake_spawn(argv, log_path=None, cwd=None, env=None):
        spawns.append(SimpleNamespace(argv=list(argv), cwd=cwd, env=env))
        return 4242

    monkeypatch.setattr(mod.gm, "spawn_detached", fake_spawn)

    bins = gm_home / "fakebins"
    bins.mkdir()

    def install_tools():
        """Make find_tool succeed without anything executable being present."""
        for name in ("mempalace", "graphify"):
            path = bins / name
            path.write_text("not a real tool", encoding="utf-8")
            monkeypatch.setenv("GM_%s_BIN" % name.upper(), str(path))
        mod.gm._FIND_TOOL_CACHE.clear()

    def run(payload=None):
        raw = payload if isinstance(payload, str) else json.dumps(
            payload if payload is not None
            else {"cwd": str(project), "session_id": "sess-1", "source": "startup"})
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        return mod.main()

    return SimpleNamespace(mod=mod, gm=mod.gm, spawns=spawns, project=project,
                           install_tools=install_tools, run=run)


def test_missing_prerequisites_print_one_line_and_nothing_else(ss, capsys):
    assert ss.run() == 0
    out = capsys.readouterr().out
    assert out.strip() == ss.mod.PREREQ_LINE
    assert "kb-setup" in out
    assert ss.spawns == []


def test_the_instruction_block_names_the_resolver_and_the_scripts_dir(ss, capsys):
    ss.install_tools()
    assert ss.run() == 0
    out = capsys.readouterr().out
    scripts = str(ss.gm.SCRIPTS_DIR).replace("\\", "/")
    assert scripts in out
    assert f'"{scripts}/kb_resolve.py" ask' in out
    assert f'bash "{scripts}/py.sh"' in out
    assert "get <exchange-id>" in out
    assert "--code" in out


def test_instructions_can_be_switched_off(ss, capsys, write_config):
    write_config("[session]\ninject_instructions = false\n")
    ss.install_tools()
    ss.run()
    out = capsys.readouterr().out
    assert "kb_resolve.py" not in out
    assert out.strip() == "graph-mempalace: not ingested yet"


def test_the_status_line_counts_the_exchanges_on_disk(ss, capsys):
    exchanges = ss.gm.exchanges_dir(ss.project)
    exchanges.mkdir(parents=True)
    for n in range(3):
        (exchanges / f"2026-03-04-b49fc7be-000{n}.md").write_text("x", encoding="utf-8")
    ss.install_tools()
    ss.run()
    assert "graph-mempalace: 3 exchanges" in capsys.readouterr().out


def test_the_status_line_adds_graph_b_nodes_and_the_last_compound(ss, capsys):
    exchanges = ss.gm.exchanges_dir(ss.project)
    exchanges.mkdir(parents=True)
    (exchanges / "2026-03-04-b49fc7be-0001.md").write_text("x", encoding="utf-8")
    graph = ss.gm.graph_b_path(ss.project)
    graph.parent.mkdir(parents=True)
    graph.write_text(json.dumps({"nodes": [{"id": 1}, {"id": 2}, {"id": 3}]}),
                     encoding="utf-8")
    ss.gm.ensure_state_dir()
    ss.gm.LOG_PATH.write_text(
        "2026-03-04T05:06:07 === compound done project=proj exchanges=1 status=ok ===\n",
        encoding="utf-8")
    ss.install_tools()
    ss.run()
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert "1 exchanges" in line
    assert "Graph B 3 nodes" in line
    assert "last compound 2026-03-04 05:06" in line


def test_the_status_line_can_be_switched_off(ss, capsys, write_config):
    write_config("[session]\nshow_status = false\n")
    ss.install_tools()
    ss.run()
    assert "not ingested yet" not in capsys.readouterr().out


def test_first_sight_spawns_one_detached_compound(ss, capsys, samefile):
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    ss.install_tools()
    ss.run()
    out = capsys.readouterr().out
    assert "first session here" in out
    assert len(ss.spawns) == 1
    argv = ss.spawns[0].argv
    assert argv[0] == ss.gm.python_exe()
    assert argv[1].endswith("compound_run.py")
    assert argv[2] == "--project" and samefile(argv[3], ss.project)
    assert argv[4] == "--transcripts"
    assert samefile(argv[5], ss.gm.transcripts_dir(ss.project))
    assert argv[6:] == ["--session", "sess-1"]
    assert "first sight: spawned a compound" in ss.gm.LOG_PATH.read_text(encoding="utf-8")


def test_first_sight_is_skipped_when_switched_off(ss, capsys, write_config):
    write_config("[compound]\non_first_sight = false\n")
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    ss.install_tools()
    ss.run()
    assert ss.spawns == []
    assert "first session here" not in capsys.readouterr().out


def test_first_sight_is_skipped_while_a_compound_holds_the_lock(ss, capsys):
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    assert ss.gm.lock_acquire() is True
    ss.install_tools()
    ss.run()
    assert ss.spawns == []
    assert "first session here" not in capsys.readouterr().out


def test_first_sight_is_skipped_once_an_exchange_file_exists(ss, capsys):
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    exchanges = ss.gm.exchanges_dir(ss.project)
    exchanges.mkdir(parents=True)
    (exchanges / "2026-03-04-b49fc7be-0001.md").write_text("x", encoding="utf-8")
    ss.install_tools()
    ss.run()
    assert ss.spawns == []


def test_an_empty_exchanges_directory_still_gets_a_first_sight(ss, capsys):
    """A filter that died half way leaves the directory behind; that must not
    disable first sight for ever."""
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    ss.gm.exchanges_dir(ss.project).mkdir(parents=True)
    ss.install_tools()
    ss.run()
    assert len(ss.spawns) == 1
    assert "first session here" in capsys.readouterr().out


def test_first_sight_is_skipped_when_there_are_no_transcripts(ss, capsys):
    ss.install_tools()
    ss.run()
    assert ss.spawns == []
    assert "first session here" not in capsys.readouterr().out


def test_a_failed_spawn_prints_no_first_sight_line(ss, capsys, monkeypatch):
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    monkeypatch.setattr(ss.gm, "spawn_detached", lambda *a, **k: None)
    ss.install_tools()
    ss.run()
    assert "first session here" not in capsys.readouterr().out


def test_stdout_stays_inside_the_budget(ss, capsys):
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    ss.install_tools()
    ss.run()
    out = capsys.readouterr().out
    assert 0 < len(out) < 10_000
    assert len(out) <= ss.mod.STDOUT_BUDGET + 1  # +1 for print's newline


def test_an_oversized_block_is_truncated(ss, capsys, monkeypatch):
    monkeypatch.setattr(ss.mod, "INSTRUCTIONS", "x" * 9000)
    ss.install_tools()
    ss.run()
    out = capsys.readouterr().out.strip()
    assert len(out) == ss.mod.STDOUT_BUDGET
    assert out.endswith("...")


@pytest.mark.parametrize("payload", ["", "not json", "[]", "null", "{}"])
def test_garbage_stdin_still_exits_zero(ss, payload, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert ss.run(payload) == 0
    assert capsys.readouterr().out.strip() == ss.mod.PREREQ_LINE


def test_the_session_source_is_logged(ss, capsys):
    ss.install_tools()
    ss.run({"cwd": str(ss.project), "session_id": "s", "source": "resume"})
    assert "session start: source=resume project=proj" in \
        ss.gm.LOG_PATH.read_text(encoding="utf-8")


def test_no_log_line_without_a_source(ss, capsys):
    ss.install_tools()
    ss.run({"cwd": str(ss.project), "session_id": "s"})
    assert not ss.gm.LOG_PATH.exists()


# ---------------------------------------------------------------- the helpers

def test_nothing_is_known_about_the_last_compound_without_a_log(ss):
    assert ss.mod.last_done_line() is None
    assert ss.mod.last_compound() is None
    assert ss.mod.last_status() is None


def test_last_compound_reads_the_newest_marker(ss):
    ss.gm.ensure_state_dir()
    ss.gm.LOG_PATH.write_text(
        "2026-03-01T01:02:03 === compound done project=proj status=ok ===\n"
        "2026-03-02T04:05:06 --- step mine: rc=0\n"
        "2026-03-03T07:08:09 === compound done project=proj status=ok ===\n",
        encoding="utf-8")
    assert ss.mod.last_compound() == "2026-03-03 07:08"
    assert ss.mod.last_status() == "ok"


def test_a_marker_without_a_status_field_still_gives_the_time(ss):
    ss.gm.ensure_state_dir()
    ss.gm.LOG_PATH.write_text(
        "2026-03-03T07:08:09 === compound done project=proj seconds=12 ===\n",
        encoding="utf-8")
    assert ss.mod.last_compound() == "2026-03-03 07:08"
    assert ss.mod.last_status() is None


def test_an_unstamped_marker_is_not_read(ss):
    ss.gm.ensure_state_dir()
    ss.gm.LOG_PATH.write_text("=== compound done project=proj status=ok ===\n",
                              encoding="utf-8")
    assert ss.mod.last_done_line() is None


@pytest.mark.parametrize("status,expected", [
    (None, ""),
    ("ok", ""),
    ("failed:graph_B", "last compound FAILED (graph B)"),
    ("failed:graph_A+graph_B", "last compound FAILED (graph A, graph B)"),
    ("failed", "last compound FAILED (unknown)"),
])
def test_failure_note_names_the_steps_that_failed(ss, status, expected):
    note = ss.mod.failure_note(status)
    if expected:
        assert expected in note
        assert "/graph-mempalace:kb-compound" in note
    else:
        assert note == ""


def test_the_status_line_reports_a_failed_compound(ss, capsys):
    ss.gm.ensure_state_dir()
    ss.gm.LOG_PATH.write_text(
        "2026-03-03T07:08:09 === compound done project=proj status=failed:graph_B ===\n",
        encoding="utf-8")
    ss.install_tools()
    ss.run()
    assert "not ingested yet - last compound FAILED (graph B)" in capsys.readouterr().out


def test_node_count_ignores_an_unparsable_graph(ss, tmp_path):
    broken = tmp_path / "graph.json"
    broken.write_text("{nope", encoding="utf-8")
    assert ss.mod.node_count(broken) is None
    assert ss.mod.node_count(tmp_path / "missing.json") is None


def test_scripts_path_uses_forward_slashes(ss):
    assert "\\" not in ss.mod.scripts_path()
    assert ss.mod.scripts_path().endswith("/scripts")


def test_the_script_as_a_whole_exits_zero_on_garbage(gm_home, empty_path):
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "session_start.py"
    env = dict(os.environ)
    env["GM_HOME"] = str(gm_home)
    proc = subprocess.run([sys.executable, str(script)], input="nonsense",
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0
    assert len(proc.stdout) < 10_000


# ------------------------------------------- first sight needs BOTH conditions

def test_first_sight_runs_again_after_a_filter_that_never_finished(ss, capsys):
    """An exchanges directory with no files and no index means the filter died
    half way; that used to disable first sight for ever."""
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    ss.gm.exchanges_dir(ss.project).mkdir(parents=True)
    ss.install_tools()
    ss.run()
    assert "first session here" in capsys.readouterr().out
    assert len(ss.spawns) == 1


def test_first_sight_stops_once_a_filter_pass_has_finished(ss, capsys):
    """A project whose transcripts yield nothing worth keeping still gets an
    `_index.jsonl`. Without that check every session start would compound it."""
    ss.gm.transcripts_dir(ss.project).mkdir(parents=True)
    exchanges = ss.gm.exchanges_dir(ss.project)
    exchanges.mkdir(parents=True)
    (exchanges / ss.mod.INDEX_NAME).write_text("", encoding="utf-8")
    ss.install_tools()
    for _ in range(3):
        ss.run()
    assert ss.spawns == []
    assert "first session here" not in capsys.readouterr().out


# ------------------------------------------- prerequisites get their own advice

def test_a_compound_that_could_not_start_points_at_kb_setup(ss, capsys):
    ss.gm.ensure_state_dir()
    ss.gm.LOG_PATH.write_text(
        "2026-03-03T07:08:09 === compound done project=proj exchanges=? "
        "drawers_added=? graph_a_nodes=? graph_b_nodes=? omitted=? seconds=0 "
        "status=failed:prerequisites ===\n", encoding="utf-8")
    ss.install_tools()
    ss.run()
    out = capsys.readouterr().out
    assert "last compound could not start (prerequisites missing)" in out
    assert "kb-setup" in out
    assert "kb-compound runs it now" in out        # only the instruction block
    assert "FAILED" not in out


@pytest.mark.parametrize("status,expected", [
    ("failed:prerequisites", " - last compound could not start (prerequisites "
                             "missing); run /graph-mempalace:kb-setup"),
    ("failed:mine", " - last compound FAILED (mine); "
                    "run /graph-mempalace:kb-compound"),
])
def test_failure_note_picks_the_fix_that_matches(ss, status, expected):
    assert ss.mod.failure_note(status) == expected
