"""The status line's "last compound" must come from THIS project's marker.

The log is shared by every project on the machine. A marker written by another
project's run must not be reported as this project's last compound, and when
this project has no marker at all the status line says nothing about one.
"""
from __future__ import annotations

import importlib
import io
import json
import sys

import pytest


@pytest.fixture
def ss(gm_home, project, monkeypatch, empty_path):
    mod = importlib.import_module("session_start")
    monkeypatch.setattr(mod.gm, "spawn_detached", lambda *a, **k: 4242)
    bins = gm_home / "fakebins"
    bins.mkdir()
    for name in ("mempalace", "graphify"):
        path = bins / name
        path.write_text("not a real tool", encoding="utf-8")
        monkeypatch.setenv("GM_%s_BIN" % name.upper(), str(path))
    mod.gm._FIND_TOOL_CACHE.clear()

    exchanges = mod.gm.exchanges_dir(project)
    exchanges.mkdir(parents=True)
    (exchanges / "2026-10-01-abcd1234-0001.md").write_text("# x\n", encoding="utf-8")
    (exchanges / "_index.jsonl").write_text("", encoding="utf-8")

    def run():
        payload = {"cwd": str(project), "session_id": "sess-1", "source": "startup"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        mod.main()

    def marker(token, stamp, status="ok"):
        mod.gm.append_log(
            f"=== compound done project={token} exchanges=1 drawers_added=1 "
            f"graph_a_nodes=? graph_b_nodes=? omitted=0 seconds=1 status={status} ==="
        )
        # append_log stamps "now"; rewrite the line with the stamp the test wants
        text = mod.gm.LOG_PATH.read_text(encoding="utf-8").splitlines()
        text[-1] = stamp + text[-1][len(stamp):]
        mod.gm.LOG_PATH.write_text("\n".join(text) + "\n", encoding="utf-8")

    mine = mod.gm.project_token(project)
    return mod, run, marker, mine


def status_line(capsys):
    out = capsys.readouterr().out
    return next(l for l in out.splitlines() if l.startswith("graph-mempalace: ") and "exchange" in l)


def test_another_projects_marker_is_not_reported(ss, capsys):
    mod, run, marker, mine = ss
    marker("some-other-project", "2026-10-09T09:00:00")
    run()
    line = status_line(capsys)
    assert "last compound" not in line
    assert "FAILED" not in line


def test_this_projects_marker_wins_over_a_newer_foreign_one(ss, capsys):
    mod, run, marker, mine = ss
    marker(mine, "2026-10-08T08:30:00")
    marker("some-other-project", "2026-10-09T09:00:00")
    run()
    line = status_line(capsys)
    assert "last compound 2026-10-08 08:30" in line
    assert "2026-10-09" not in line


def test_this_projects_failure_is_reported_not_another_projects(ss, capsys):
    mod, run, marker, mine = ss
    marker("some-other-project", "2026-10-09T09:00:00", status="failed:graph_B")
    marker(mine, "2026-10-09T10:00:00", status="ok")
    run()
    line = status_line(capsys)
    assert "FAILED" not in line
    assert "last compound 2026-10-09 10:00" in line
