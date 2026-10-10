"""compound_run: the planned command sequence for each configuration, and the lock."""
from __future__ import annotations

import importlib
import json
import os
from types import SimpleNamespace

import pytest


class FakeProc:
    """Stands in for a Popen. `communicate` returns on the first heartbeat tick,
    so `run_step`'s wait loop finishes at once and never sleeps or kills."""

    def __init__(self, out="", rc=0):
        self._out = out
        self.returncode = rc
        self.killed = False

    def communicate(self, timeout=None):
        return self._out, ""

    def kill(self):
        self.killed = True


def install_runner(monkeypatch, cr, stdout=None, rc=None):
    """Replace the subprocess launcher with a recorder returning canned output."""
    calls: list = []

    def fake_popen(argv, **kwargs):
        argv = list(argv)
        cwd = kwargs.get("cwd")
        # env matters as much as argv now: it carries MEMPALACE_PALACE_PATH, which
        # is what keeps a mempalace child process in this project's palace.
        calls.append(SimpleNamespace(argv=argv, cwd=cwd, env=kwargs.get("env") or {}))
        return FakeProc(stdout(argv, cwd) if stdout else "",
                        rc(argv, cwd) if rc else 0)

    monkeypatch.setattr(cr.subprocess, "Popen", fake_popen)
    return calls


def plan(calls):
    """The recorded commands, minus the Windows-only orphan sweep."""
    return [c for c in calls
            if os.path.basename(c.argv[0]).lower().split(".")[0] != "powershell"]


def step_names(result):
    return [s["name"] for s in result["steps"] if s["name"] != "sweep"]


def is_extraction(argv):
    return "--token-budget" in argv and "cluster-only" not in argv


@pytest.fixture
def chain(gm_home, project, empty_path, tmp_path, monkeypatch):
    """A project with transcripts, one exchange file, and fake tool binaries."""
    bins = tmp_path / "bins"
    bins.mkdir()
    mempalace = bins / "mempalace-fake"
    graphify = bins / "graphify-fake"
    for path in (mempalace, graphify):
        path.write_text("not a real tool", encoding="utf-8")
    monkeypatch.setenv("GM_MEMPALACE_BIN", str(mempalace))
    monkeypatch.setenv("GM_GRAPHIFY_BIN", str(graphify))

    gm = importlib.import_module("gm_common")
    cfg = gm.load_config(project)
    transcripts = gm.transcripts_dir(project)
    transcripts.mkdir(parents=True, exist_ok=True)
    exchanges = gm.exchanges_dir(project, cfg)
    exchanges.mkdir(parents=True, exist_ok=True)
    (exchanges / "2026-03-04-b49fc7be-0001.md").write_text("one exchange",
                                                           encoding="utf-8")
    cr = importlib.import_module("compound_run")

    def write_graph(path, nodes=2):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"nodes": [{"id": i} for i in range(nodes)]}),
                        encoding="utf-8")
        return path

    def log():
        if not gm.LOG_PATH.exists():
            return ""
        return gm.LOG_PATH.read_text(encoding="utf-8", errors="replace")

    return SimpleNamespace(gm=gm, cr=cr, project=project, transcripts=transcripts,
                           exchanges=exchanges, mempalace=str(mempalace),
                           graphify=str(graphify), log=log, write_graph=write_graph,
                           wing="proj_dialogue",
                           palace=str(gm.palace_path(project, cfg)))


def result_of(chain, capsys, extra=()):
    """Run the chain with --json and return the parsed summary."""
    rc = chain.cr.main(["--project", str(chain.project), *extra, "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    return json.loads(out)


# -------------------------------------------------------------- the whole chain

def test_default_plan_for_a_first_build(chain, capsys, monkeypatch, samefile):
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)

    # No graph file exists yet, so neither cluster step is worth running.
    assert step_names(result) == ["filter", "mine", "graph A", "graph B pass 1"]
    cmds = plan(calls)
    assert cmds[0].argv == [chain.gm.python_exe(),
                            str(chain.gm.SCRIPTS_DIR / "filter_transcripts.py"),
                            str(chain.transcripts), str(chain.exchanges)]
    assert cmds[1].argv == [chain.mempalace, "--palace", chain.palace,
                            "mine", str(chain.exchanges),
                            "--mode", "projects", "--wing", chain.wing]
    assert cmds[2].argv == [chain.graphify, ".", "--code-only"]
    assert samefile(cmds[2].cwd, chain.project)
    assert cmds[3].argv == [chain.graphify, ".", "--backend", "claude-cli",
                            "--max-concurrency", "2", "--token-budget", "15000"]
    assert samefile(cmds[3].cwd, chain.exchanges)
    assert result["exchanges"] == 1
    assert result["wing"] == chain.wing
    assert result["failed"] == []


def test_an_existing_graph_a_is_updated_not_rebuilt(chain, capsys, monkeypatch):
    chain.write_graph(chain.gm.graph_a_path(chain.project), nodes=3)
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    argvs = [c.argv for c in plan(calls)]
    assert [chain.graphify, "update", "."] in argvs
    assert [chain.graphify, ".", "--code-only"] not in argvs
    assert result["graph_a_nodes"] == 3


def test_graph_a_mode_full_uses_the_llm_backend(chain, capsys, monkeypatch,
                                                write_config):
    write_config('[graph_a]\nmode = "full"\n')
    calls = install_runner(monkeypatch, chain.cr)
    result_of(chain, capsys)
    argvs = [c.argv for c in plan(calls)]
    assert [chain.graphify, ".", "--backend", "claude-cli", "--max-concurrency", "2",
            "--token-budget", "15000"] in argvs
    assert [chain.graphify, ".", "--code-only"] not in argvs


def test_graph_a_cluster_runs_once_a_code_graph_exists(chain, capsys, monkeypatch,
                                                       write_config, samefile):
    write_config("[graph_a]\ncluster = true\n")
    chain.write_graph(chain.gm.graph_a_path(chain.project))
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    assert step_names(result) == ["filter", "mine", "graph A", "graph A cluster",
                                  "graph B pass 1"]
    cluster = [c for c in plan(calls) if "cluster-only" in c.argv]
    assert cluster[0].argv == [chain.graphify, "cluster-only", ".", "--backend",
                               "claude-cli", "--batch-size", "100",
                               "--max-concurrency", "2"]
    assert samefile(cluster[0].cwd, chain.project)


def test_graph_a_cluster_is_skipped_when_there_is_no_graph(chain, capsys, monkeypatch,
                                                           write_config):
    write_config("[graph_a]\ncluster = true\n")
    install_runner(monkeypatch, chain.cr)
    assert "graph A cluster" not in step_names(result_of(chain, capsys))


def test_graph_b_cluster_runs_once_a_chat_graph_exists(chain, capsys, monkeypatch,
                                                       samefile):
    chain.write_graph(chain.gm.graph_b_path(chain.project))
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    assert step_names(result) == ["filter", "mine", "graph A", "graph B pass 1",
                                  "graph B cluster"]
    cluster = [c for c in plan(calls) if "cluster-only" in c.argv]
    assert samefile(cluster[0].cwd, chain.exchanges)


def test_graph_b_cluster_can_be_switched_off(chain, capsys, monkeypatch,
                                             write_config):
    write_config("[graph_b]\ncluster = false\n")
    chain.write_graph(chain.gm.graph_b_path(chain.project))
    install_runner(monkeypatch, chain.cr)
    assert "graph B cluster" not in step_names(result_of(chain, capsys))


def test_no_graph_a_skips_the_code_graph(chain, capsys, monkeypatch):
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys, ["--no-graph-a"])
    assert "graph A" not in step_names(result)
    assert "graph A skipped (--no-graph-a)" in result["notes"]
    assert all("--code-only" not in c.argv for c in plan(calls))


def test_no_graph_b_skips_the_chat_graph(chain, capsys, monkeypatch):
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys, ["--no-graph-b"])
    assert step_names(result) == ["filter", "mine", "graph A"]
    assert "graph B skipped (--no-graph-b)" in result["notes"]
    assert not [c for c in plan(calls) if is_extraction(c.argv)]


def test_a_second_pass_runs_for_files_that_produced_no_nodes(chain, capsys,
                                                             monkeypatch):
    def stdout(argv, cwd):
        return "5/10 dispatched file(s) produced no nodes\n" if is_extraction(argv) else ""

    calls = install_runner(monkeypatch, chain.cr, stdout)
    result = result_of(chain, capsys)
    assert step_names(result) == ["filter", "mine", "graph A", "graph B pass 1",
                                  "graph B pass 2"]
    assert "5 file(s) produced no nodes; running pass 2" in result["notes"]
    assert result["omitted"] == 5
    # passes = 2 by default, so it stops after the second attempt
    assert len([c for c in plan(calls) if is_extraction(c.argv)]) == 2


def test_no_second_pass_when_nothing_was_omitted(chain, capsys, monkeypatch):
    def stdout(argv, cwd):
        return "0/10 dispatched file(s) produced no nodes\n" if is_extraction(argv) else ""

    install_runner(monkeypatch, chain.cr, stdout)
    result = result_of(chain, capsys)
    assert "graph B pass 2" not in step_names(result)
    assert result["omitted"] == 0


def test_a_single_pass_when_configured(chain, capsys, monkeypatch, write_config):
    write_config("[graph_b]\npasses = 1\n")

    def stdout(argv, cwd):
        return "9/10 dispatched file(s) produced no nodes\n" if is_extraction(argv) else ""

    install_runner(monkeypatch, chain.cr, stdout)
    result = result_of(chain, capsys)
    assert step_names(result) == ["filter", "mine", "graph A", "graph B pass 1"]
    assert result["omitted"] == 9


def test_the_mine_reports_the_drawers_it_filed(chain, capsys, monkeypatch):
    def stdout(argv, cwd):
        return "Drawers filed: 1,234\n" if "mine" in argv else ""

    install_runner(monkeypatch, chain.cr, stdout)
    assert result_of(chain, capsys)["drawers_added"] == 1234


# ----------------------------------------------------------------- step failures

def test_a_failing_graph_a_is_recorded_as_failed(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr,
                   rc=lambda argv, cwd: 3 if argv[1:2] == ["."] and "--code-only" in argv else 0)
    result = result_of(chain, capsys)
    assert result["failed"] == ["graph A"]
    assert any("graph A failed (rc=3)" in note for note in result["notes"])
    assert "status=failed:graph_A ===" in chain.log()


def test_a_failing_graph_b_without_a_graph_is_recorded_as_failed(chain, capsys,
                                                                 monkeypatch):
    install_runner(monkeypatch, chain.cr,
                   rc=lambda argv, cwd: 2 if is_extraction(argv) else 0)
    result = result_of(chain, capsys)
    assert step_names(result) == ["filter", "mine", "graph A", "graph B pass 1"]
    assert any("graph B pass 1 failed (rc=2)" in note for note in result["notes"])
    assert result["failed"] == ["graph B"]
    assert any("run /graph-mempalace:kb-compound to retry" in n for n in result["notes"])


def test_an_interrupted_graph_b_pass_is_retried_once(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr,
                   rc=lambda argv, cwd: 130 if is_extraction(argv) else 0)
    result = result_of(chain, capsys)
    # The retry reuses the pass number, so the same name appears twice.
    assert step_names(result) == ["filter", "mine", "graph A", "graph B pass 1",
                                  "graph B pass 1"]
    assert any("interrupted (rc=130); retrying once" in n for n in result["notes"])
    assert result["failed"] == ["graph B"]


def test_a_failing_graph_b_that_still_wrote_a_graph_is_not_a_failure(chain, capsys,
                                                                     monkeypatch):
    chain.write_graph(chain.gm.graph_b_path(chain.project))
    install_runner(monkeypatch, chain.cr,
                   rc=lambda argv, cwd: 2 if is_extraction(argv) else 0)
    result = result_of(chain, capsys)
    assert result["failed"] == []
    assert "status=ok ===" in chain.log()


# ------------------------------------------------------------------- skip paths

def test_a_held_lock_stops_the_chain_before_anything_runs(chain, capsys, monkeypatch):
    calls = install_runner(monkeypatch, chain.cr)
    assert chain.gm.lock_acquire() is True
    assert chain.cr.main(["--project", str(chain.project)]) == 0
    assert "a compound is already running" in capsys.readouterr().out
    assert calls == []
    assert "skipped: a compound is already running" in chain.log()


def test_the_lock_is_released_even_when_a_step_raises(chain, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("step exploded")

    monkeypatch.setattr(chain.cr, "run_chain", boom)
    assert chain.cr.main(["--project", str(chain.project), "--json"]) == 0
    assert not chain.gm.LOCK_PATH.exists()
    assert "compound error: RuntimeError: step exploded" in chain.log()
    result = json.loads(capsys.readouterr().out)
    assert result["failed"] == ["chain"]
    assert result["status"] == "failed:chain"


def test_every_step_touches_the_lock_so_a_long_chain_keeps_it(chain, monkeypatch):
    touches: list = []
    monkeypatch.setattr(chain.gm, "lock_touch", lambda: touches.append(1))
    monkeypatch.setattr(chain.cr, "run_step",
                        lambda name, argv, **kw: chain.cr.Step(name, 0, "", 0.0, False))
    chain.cr.step("probe", ["anything"])
    assert len(touches) == 2  # one before the step, one after


def test_missing_tools_stop_the_chain_and_log_the_fix(gm_home, project, empty_path,
                                                      capsys, monkeypatch):
    gm = importlib.import_module("gm_common")
    gm.transcripts_dir(project).mkdir(parents=True, exist_ok=True)
    cr = importlib.import_module("compound_run")
    calls = install_runner(monkeypatch, cr)
    assert cr.main(["--project", str(project), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    # The palace line is noted before anything else, so the notes are
    # [palace, stopped]; the stop is the last word on what happened.
    assert result["notes"][-1] == (
        "stopped: cannot find mempalace, graphify; run /graph-mempalace:kb-setup")
    assert result["notes"][0].startswith("palace: ")
    assert result["failed"] == ["prerequisites"]
    assert result["status"] == "failed:prerequisites"
    assert calls == []
    assert "cannot find mempalace, graphify" in gm.LOG_PATH.read_text(encoding="utf-8")


def test_absent_transcripts_are_a_note_not_a_failure(chain, capsys, monkeypatch):
    for leftover in chain.transcripts.iterdir():
        leftover.unlink()
    chain.transcripts.rmdir()
    install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    assert any("nothing to filter or mine" in note for note in result["notes"])
    assert "filter" not in step_names(result)


def test_an_empty_exchanges_dir_skips_the_mine(chain, capsys, monkeypatch):
    for md in chain.exchanges.glob("*.md"):
        md.unlink()
    install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    assert any("skipped the mine" in note for note in result["notes"])
    assert "mine" not in step_names(result)
    assert result["exchanges"] == 0


# ----------------------------------------------------------------- the reporting

def test_the_done_marker_is_written_to_the_log(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr)
    result_of(chain, capsys)
    line = [ln for ln in chain.log().splitlines() if "=== compound done" in ln][-1]
    assert "project=proj" in line
    assert "exchanges=1" in line
    assert "drawers_added=?" in line      # the fake mine printed no count
    assert "graph_b_nodes=?" in line
    assert line.rstrip().endswith("status=ok ===")


def test_json_output_is_one_parseable_document(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    assert set(result) == {"project", "session", "transcripts", "exchanges_dir",
                           "wing", "palace", "skipped", "exchanges", "drawers_added",
                           "graph_a_nodes", "graph_b_nodes", "omitted", "seconds",
                           "steps", "notes", "failed", "status", "log"}
    assert result["project"] == "proj"
    assert result["session"] == "-"
    assert result["status"] == "ok"
    assert result["palace"] == chain.palace
    assert result["skipped"] == []


def test_wait_prints_a_human_summary(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr)
    assert chain.cr.main(["--project", str(chain.project), "--session", "abc",
                          "--wait"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("compound finished in ")
    assert "exchanges     1" in out
    assert f"wing          {chain.wing}" in out


def test_a_project_that_is_not_a_directory_exits_one(chain, capsys, tmp_path):
    assert chain.cr.main(["--project", str(tmp_path / "nope")]) == 1
    assert "is not a directory" in capsys.readouterr().err


def test_graph_b_node_count_is_reported_when_the_graph_exists(chain, capsys,
                                                              monkeypatch):
    chain.write_graph(chain.gm.graph_b_path(chain.project), nodes=2)
    install_runner(monkeypatch, chain.cr)
    assert result_of(chain, capsys)["graph_b_nodes"] == 2


# ------------------------------------------------------------- helper functions

def test_run_step_reports_a_missing_executable_as_127(chain, tmp_path):
    step = chain.cr.run_step("probe", [str(tmp_path / "no-such-exe-xyz")], timeout=30)
    assert step.rc == 127
    assert step.timed_out is False
    assert "--- step probe: rc=127" in chain.log()


def test_omitted_from_reads_graphifys_summary_line(chain):
    assert chain.cr.omitted_from("18/80 dispatched file(s) produced no nodes") == 18
    assert chain.cr.omitted_from("nothing to see") is None


@pytest.mark.parametrize("line,expected", [
    ("Drawers filed: 60", 60),
    ("Drawers filed: 1,234", 1234),
    ("  drawers filed:   7  ", 7),
    ("Files processed: 60", None),
    ("no count here", None),
    ("", None),
])
def test_drawers_from_mine_reads_the_drawer_count(chain, line, expected):
    assert chain.cr.drawers_from_mine(line) == expected


def test_node_count_is_none_for_absent_or_broken_graphs(chain, tmp_path):
    assert chain.cr.node_count(tmp_path / "missing.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    assert chain.cr.node_count(broken) is None
    good = tmp_path / "good.json"
    good.write_text('{"nodes": [1, 2]}', encoding="utf-8")
    assert chain.cr.node_count(good) == 2


def test_human_summary_names_the_steps_that_failed(chain):
    text = chain.cr.human_summary({
        "project": "proj", "seconds": 125, "wing": "w", "exchanges": 3,
        "failed": ["graph B"],
        "steps": [{"name": "mine", "rc": 1, "timed_out": False},
                  {"name": "graph B pass 1", "rc": 124, "timed_out": True}],
        "notes": ["stopped: cannot find mempalace; run /graph-mempalace:kb-setup"],
    })
    assert "compound finished in 2m 05s" in text
    assert "FAILED: graph B (see the log, then re-run)" in text
    assert "mine: rc=1" in text
    assert "graph B pass 1: rc=124 (timed out)" in text
    assert "fix: run /graph-mempalace:kb-setup" in text


@pytest.mark.parametrize("failed,expected", [
    ([], "ok"),
    (["graph B"], "failed:graph_B"),
    (["graph A", "graph B"], "failed:graph_A+graph_B"),
])
def test_status_summarises_the_failures(chain, failed, expected):
    assert chain.cr._status(failed) == expected


def test_graph_a_is_hidden_from_git_in_the_repositorys_private_ignore(chain):
    info = chain.project / ".git" / "info"
    info.mkdir(parents=True)
    (info / "exclude").write_text("# existing rules\n*.tmp\n", encoding="utf-8",
                                  newline="\n")
    line = chain.cr.hide_graph_a_from_git(chain.project)
    assert "added graphify-out/" in line
    content = (info / "exclude").read_text(encoding="utf-8")
    assert content.splitlines() == ["# existing rules", "*.tmp", "graphify-out/"]
    # idempotent, and the tracked .gitignore is never written
    assert chain.cr.hide_graph_a_from_git(chain.project) is None
    assert not (chain.project / ".gitignore").exists()


def test_the_private_ignore_is_created_when_it_is_missing(chain):
    (chain.project / ".git").mkdir()
    assert chain.cr.hide_graph_a_from_git(chain.project) is not None
    assert (chain.project / ".git" / "info" / "exclude").read_text(
        encoding="utf-8") == "graphify-out/\n"


def test_a_project_without_a_git_directory_is_left_alone(chain):
    assert chain.cr.hide_graph_a_from_git(chain.project) is None


def test_a_git_file_means_a_worktree_and_is_skipped(chain):
    (chain.project / ".git").write_text("gitdir: ../elsewhere\n", encoding="utf-8")
    assert chain.cr.hide_graph_a_from_git(chain.project) is None


def test_sweep_is_windows_only(chain, monkeypatch):
    # The real sweep kills orphaned claude.exe workers, so it is never executed
    # here: only the decision to run it, and the command it would run.
    seen: list = []
    monkeypatch.setattr(chain.cr, "run_step",
                        lambda name, argv, **kw: seen.append((name, argv)))
    chain.cr.sweep_orphans()
    if os.name == "nt":
        assert seen[0][0] == "sweep"
        assert seen[0][1][:4] == ["powershell", "-NoProfile", "-NonInteractive",
                                  "-Command"]
        assert "claude.exe" in seen[0][1][4]
    else:
        assert seen == []


# ------------------------------------------------- waiting out a held palace

HELD = ("mempalace: palace C:/Users/x/.mempalace is held by PID 4242 "
        "(mempalace mine ...)\n")


@pytest.fixture
def no_waiting(monkeypatch):
    """Make the palace-held wait instant, so the retry loop is testable."""
    cr = importlib.import_module("compound_run")
    monkeypatch.setattr(cr, "PALACE_HELD_WAIT_SECONDS", 0)
    return cr


def test_a_held_palace_is_waited_out_and_the_mine_retried(chain, capsys,
                                                          monkeypatch, no_waiting):
    attempts: list = []

    def stdout(argv, cwd):
        if "mine" not in argv:
            return ""
        attempts.append(1)
        return HELD if len(attempts) == 1 else "Drawers filed: 9\n"

    def rc(argv, cwd):
        return 1 if ("mine" in argv and len(attempts) == 1) else 0

    install_runner(monkeypatch, chain.cr, stdout, rc)
    result = result_of(chain, capsys)
    assert len(attempts) == 2                      # it tried again
    assert result["drawers_added"] == 9
    assert result["failed"] == []
    assert result["status"] == "ok"
    assert any("palace held by another process; waiting 0 s (attempt 1/10)" in n
               for n in result["notes"])


def test_a_palace_held_for_the_whole_wait_is_a_failure(chain, capsys,
                                                       monkeypatch, no_waiting):
    attempts: list = []

    def stdout(argv, cwd):
        if "mine" not in argv:
            return ""
        attempts.append(1)
        return HELD

    install_runner(monkeypatch, chain.cr, stdout,
                   lambda argv, cwd: 1 if "mine" in argv else 0)
    result = result_of(chain, capsys)
    assert len(attempts) == 1 + chain.cr.PALACE_HELD_RETRIES
    assert result["failed"] == ["mine"]
    assert result["status"] == "failed:mine"
    assert any("palace held by another process for 10 minutes" in n
               for n in result["notes"])
    assert "status=failed:mine ===" in chain.log()


def test_a_mine_that_fails_for_another_reason_is_not_retried(chain, capsys,
                                                             monkeypatch, no_waiting):
    attempts: list = []

    def stdout(argv, cwd):
        if "mine" not in argv:
            return ""
        attempts.append(1)
        return "mempalace: no such wing\n"

    install_runner(monkeypatch, chain.cr, stdout,
                   lambda argv, cwd: 1 if "mine" in argv else 0)
    result = result_of(chain, capsys)
    assert len(attempts) == 1                      # waiting would not help
    assert result["failed"] == []


def test_the_wait_refreshes_the_lock_and_returns_at_once_for_zero(chain, monkeypatch):
    touches: list = []
    monkeypatch.setattr(chain.gm, "lock_touch", lambda: touches.append(1))
    monkeypatch.setattr(chain.cr.time, "sleep",
                        lambda s: pytest.fail("a zero wait must not sleep"))
    chain.cr.wait_holding_lock(0)
    assert touches == [1]


# -------------------------------------------- the heartbeat inside a long step

def test_a_long_step_refreshes_the_lock_while_its_child_runs(chain, monkeypatch):
    """The lock has to move DURING a step, not only between steps: one Graph B
    pass can outlast lock_stale_hours on its own."""
    touches: list = []
    monkeypatch.setattr(chain.gm, "lock_touch", lambda: touches.append(1))
    ticks = [0]

    class SlowProc(FakeProc):
        def communicate(self, timeout=None):
            ticks[0] += 1
            if ticks[0] <= 3:                      # three heartbeats, then done
                raise chain.cr.subprocess.TimeoutExpired("cmd", timeout)
            return "finished", ""

    monkeypatch.setattr(chain.cr.subprocess, "Popen",
                        lambda argv, **kw: SlowProc("finished", 0))
    step = chain.cr.run_step("slow", ["anything"], timeout=10_000)
    assert step.rc == 0 and step.timed_out is False
    assert step.out == "finished"                  # no output is lost
    assert len(touches) == 3                       # one per tick, mid-step


def test_a_step_that_outlives_its_timeout_is_killed_and_returns_124(chain,
                                                                    monkeypatch):
    proc = FakeProc("partial", 0)

    def never_finishes(timeout=None):
        raise chain.cr.subprocess.TimeoutExpired("cmd", timeout)

    proc.communicate = never_finishes
    monkeypatch.setattr(chain.cr.subprocess, "Popen", lambda argv, **kw: proc)
    step = chain.cr.run_step("hangs", ["anything"], timeout=-1)   # already over
    assert step.rc == 124
    assert step.timed_out is True
    assert proc.killed is True


# ------------------------------------------------ a marker on every exit path

def test_the_prerequisites_stop_still_writes_a_done_marker(gm_home, project,
                                                           empty_path, capsys,
                                                           monkeypatch):
    gm = importlib.import_module("gm_common")
    gm.transcripts_dir(project).mkdir(parents=True, exist_ok=True)
    cr = importlib.import_module("compound_run")
    install_runner(monkeypatch, cr)
    assert cr.main(["--project", str(project), "--json"]) == 0
    capsys.readouterr()
    line = [ln for ln in gm.LOG_PATH.read_text(encoding="utf-8").splitlines()
            if "=== compound done" in ln][-1]
    assert "exchanges=?" in line and "drawers_added=?" in line
    assert line.rstrip().endswith("status=failed:prerequisites ===")


def test_a_chain_that_raised_still_writes_a_done_marker(chain, capsys, monkeypatch):
    monkeypatch.setattr(chain.cr, "run_chain",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert chain.cr.main(["--project", str(chain.project), "--json"]) == 0
    capsys.readouterr()
    line = [ln for ln in chain.log().splitlines() if "=== compound done" in ln][-1]
    assert line.rstrip().endswith("status=failed:chain ===")


# ------------------------------------------ one palace per project (0.2.0)

def mine_call(calls):
    """The recorded `mempalace ... mine ...` command."""
    return [c for c in plan(calls) if "mine" in c.argv][0]


def test_the_mine_is_scoped_to_this_projects_palace(chain, capsys, monkeypatch):
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    # --palace is a GLOBAL option of the mempalace CLI, so it has to come
    # BEFORE the subcommand or mempalace rejects it.
    assert mine_call(calls).argv[:4] == [chain.mempalace, "--palace",
                                         chain.palace, "mine"]
    assert result["palace"] == chain.palace
    assert f"palace: {chain.palace}" in result["notes"]
    assert f"palace: {chain.palace}" in chain.log()


def test_the_steps_carry_the_palace_in_their_environment(chain, capsys, monkeypatch):
    """A mempalace child that forgets --palace still has to land in this palace."""
    calls = install_runner(monkeypatch, chain.cr)
    result_of(chain, capsys)
    assert mine_call(calls).env["MEMPALACE_PALACE_PATH"] == chain.palace


def test_a_configured_palace_path_is_used_instead(chain, capsys, monkeypatch,
                                                  write_config, tmp_path):
    elsewhere = tmp_path / "shared-palace"
    write_config(f'[palace]\npath = "{elsewhere.as_posix()}"\n')
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys)
    assert result["palace"] == str(elsewhere)
    assert mine_call(calls).argv[1:3] == ["--palace", str(elsewhere)]


# -------------------------------------------------------------- --only STEPS

def test_only_mine_runs_the_mine_and_nothing_else(chain, capsys, monkeypatch):
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys, ["--only", "mine"])
    assert step_names(result) == ["mine"]
    assert len(plan(calls)) == 1
    assert result["skipped"] == ["filter", "graph_a", "graph_b"]
    # Skipped is not failed: the marker still says the compound was fine.
    assert result["failed"] == []
    assert result["status"] == "ok"
    assert "status=ok ===" in chain.log()
    for label in ("filter", "graph A", "graph B"):
        assert f"{label} skipped (not in --only)" in result["notes"]


def test_only_graph_b_skips_the_filter_and_the_mine(chain, capsys, monkeypatch):
    calls = install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys, ["--only", "graph_b"])
    assert step_names(result) == ["graph B pass 1"]
    assert result["skipped"] == ["filter", "mine", "graph_a"]
    assert not [c for c in plan(calls) if "mine" in c.argv]
    assert not [c for c in plan(calls) if "filter_transcripts.py" in " ".join(c.argv)]
    assert result["status"] == "ok"


def test_only_graph_a_still_clusters_with_its_own_graph(chain, capsys, monkeypatch,
                                                        write_config):
    """Clustering is not a step of its own: it belongs to the graph it names."""
    write_config("[graph_a]\ncluster = true\n")
    chain.write_graph(chain.gm.graph_a_path(chain.project))
    install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys, ["--only", "graph_a"])
    assert step_names(result) == ["graph A", "graph A cluster"]


def test_no_graph_b_is_reported_as_a_skipped_step(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr)
    result = result_of(chain, capsys, ["--no-graph-b"])
    assert result["skipped"] == ["graph_b"]
    assert "graph B skipped (--no-graph-b)" in result["notes"]


def test_the_default_run_skips_nothing(chain, capsys, monkeypatch):
    install_runner(monkeypatch, chain.cr)
    assert result_of(chain, capsys)["skipped"] == []


def test_the_human_summary_names_the_palace_and_the_skipped_steps(chain, capsys,
                                                                  monkeypatch):
    install_runner(monkeypatch, chain.cr)
    assert chain.cr.main(["--project", str(chain.project), "--only", "mine",
                          "--wait"]) == 0
    out = capsys.readouterr().out
    assert f"palace        {chain.palace}" in out
    assert "skipped       filter, graph_a, graph_b" in out


def test_an_unknown_step_name_is_an_argument_error(chain, capsys):
    with pytest.raises(SystemExit) as exc:
        chain.cr.main(["--project", str(chain.project), "--only", "graphs"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid step 'graphs'" in err
    assert "choose from filter, mine, graph_a, graph_b" in err


@pytest.mark.parametrize("value,expected", [
    ("mine", ["mine"]),
    ("graph_b,filter", ["filter", "graph_b"]),      # chain order, not typed order
    (" mine , graph_a ", ["mine", "graph_a"]),
    ("filter,mine,graph_a,graph_b", ["filter", "mine", "graph_a", "graph_b"]),
])
def test_parse_only_normalises_the_step_list(chain, value, expected):
    assert chain.cr.parse_only(value) == expected


def test_parse_only_rejects_an_empty_list(chain, capsys):
    with pytest.raises(SystemExit) as exc:
        chain.cr.parse_only(" , ")
    assert exc.value.code == 2
    assert "an empty list" in capsys.readouterr().err


@pytest.mark.parametrize("only,no_a,no_b,expected", [
    (None, False, False, ["filter", "mine", "graph_a", "graph_b"]),
    (None, True, False, ["filter", "mine", "graph_b"]),
    (["mine", "graph_b"], False, True, ["mine"]),
    (["graph_a"], True, False, []),
])
def test_selected_steps_resolves_both_switches(chain, only, no_a, no_b, expected):
    args = SimpleNamespace(only=only, no_graph_a=no_a, no_graph_b=no_b)
    assert chain.cr.selected_steps(args) == expected
