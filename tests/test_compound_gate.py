"""compound_gate: one test per reason the Stop/PreCompact gate fires or stays quiet."""
from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SESSION = "b49fc7be-1111-2222-3333-444444444444"
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compound_gate.py"


def assistant_row(tokens: int, *, settled: bool = True, **extra) -> dict:
    """One assistant transcript record with a usable usage block."""
    content = [{"type": "text", "text": "here is the answer"}]
    if not settled:
        content.append({"type": "tool_use", "id": "tu1", "name": "Read", "input": {}})
    row = {
        "type": "assistant",
        "timestamp": "2026-03-04T05:06:07.000Z",
        "message": {
            "model": "claude-opus-4",
            "usage": {"input_tokens": tokens,
                      "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0},
            "content": content,
        },
    }
    row.update(extra)
    return row


@pytest.fixture
def gate(gm_home, project, monkeypatch, empty_path, write_jsonl):
    """compound_gate with spawn_detached replaced by a recorder."""
    mod = importlib.import_module("compound_gate")
    spawns: list = []

    def fake_spawn(argv, log_path=None, cwd=None, env=None):
        spawns.append(SimpleNamespace(argv=list(argv), cwd=cwd, env=env))
        return 9999

    monkeypatch.setattr(mod.gm, "spawn_detached", fake_spawn)
    transcript = mod.gm.transcripts_dir(project) / f"{SESSION}.jsonl"

    def write(tokens, *, settled=True, rows=None):
        write_jsonl(transcript, rows if rows is not None
                    else [assistant_row(tokens, settled=settled)])
        return transcript

    def call(payload=None, argv=("compound_gate.py",)):
        if payload is None:
            payload = {"session_id": SESSION, "transcript_path": str(transcript),
                       "cwd": str(project), "hook_event_name": "Stop"}
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        monkeypatch.setattr(sys, "argv", list(argv))
        monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 0

    def watermark(session_id=SESSION):
        """This session's own mark file, read without the legacy migration."""
        return mod.gm.read_json(mod.mark_path(session_id), None)

    def log():
        if not mod.gm.LOG_PATH.exists():
            return ""
        return mod.gm.LOG_PATH.read_text(encoding="utf-8", errors="replace")

    return SimpleNamespace(mod=mod, gm=mod.gm, spawns=spawns, project=project,
                           transcript=transcript, write=write, call=call,
                           watermark=watermark, log=log)


@pytest.fixture
def small_threshold(write_config):
    write_config("[compound]\nthreshold_tokens = 1000\n")


# --------------------------------------------------------- the five quiet paths

def test_first_sight_of_a_session_seeds_the_watermark_without_firing(gate,
                                                                    small_threshold):
    gate.write(5000)
    gate.call()
    assert gate.spawns == []
    entry = gate.watermark()
    assert entry["tokens"] == 5000
    assert "first sight" in entry["note"]


def test_a_count_below_the_watermark_is_re_seeded_without_firing(gate, small_threshold):
    gate.write(5000)
    gate.call()                      # seed at 5000
    gate.write(120)                  # compaction dropped the context
    gate.call()
    assert gate.spawns == []
    entry = gate.watermark()
    assert entry["tokens"] == 120
    assert "re-seeded after compaction" in entry["note"]


def test_growth_below_the_threshold_does_nothing_at_all(gate, small_threshold):
    gate.write(5000)
    gate.call()
    before = gate.watermark()
    gate.write(5500)                 # +500, under the 1000 threshold
    gate.call()
    assert gate.spawns == []
    assert gate.watermark() == before


def test_an_unsettled_turn_does_not_fire_and_does_not_advance(gate, small_threshold):
    gate.write(5000)
    gate.call()
    before = gate.watermark()
    gate.write(9000, settled=False)  # ends in a tool_use: still mid-loop
    gate.call()
    assert gate.spawns == []
    assert gate.watermark() == before


def test_a_held_lock_skips_without_advancing_and_says_so(gate, small_threshold):
    gate.write(5000)
    gate.call()
    before = gate.watermark()
    gate.write(9000)
    assert gate.gm.lock_acquire() is True
    gate.call()
    assert gate.spawns == []
    assert gate.watermark() == before
    assert "skipped: a compound is already running" in gate.log()


# ------------------------------------------------------------- the firing path

def test_settled_growth_over_the_threshold_spawns_the_chain(gate, small_threshold,
                                                            samefile):
    gate.write(5000)
    gate.call()
    gate.write(9000)
    gate.call()
    assert len(gate.spawns) == 1
    argv = gate.spawns[0].argv
    assert argv[0] == gate.gm.python_exe()
    assert argv[1].endswith("compound_run.py")
    assert argv[2] == "--project" and samefile(argv[3], gate.project)
    assert argv[4] == "--transcripts" and samefile(argv[5], gate.transcript.parent)
    assert argv[6:] == ["--session", SESSION]
    assert samefile(gate.spawns[0].cwd, gate.project)
    entry = gate.watermark()
    assert entry["tokens"] == 9000
    assert entry["note"] == "advanced after Stop; compound pid=9999"
    assert "gate fired: event=Stop" in gate.log()


def test_require_settled_false_fires_on_an_unsettled_turn(gate, write_config):
    write_config("[compound]\nthreshold_tokens = 1000\nrequire_settled = false\n")
    gate.write(5000)
    gate.call()
    gate.write(9000, settled=False)
    gate.call()
    assert len(gate.spawns) == 1


def test_a_failed_spawn_leaves_the_growth_pending(gate, small_threshold, monkeypatch):
    gate.write(5000)
    gate.call()
    before = gate.watermark()
    monkeypatch.setattr(gate.gm, "spawn_detached", lambda *a, **k: None)
    gate.write(9000)
    gate.call()
    assert gate.watermark() == before


# -------------------------------------------------------------------- --force

def test_force_fires_without_any_growth(gate, write_config):
    write_config("[compound]\nthreshold_tokens = 1000000\n")
    gate.write(5000)
    gate.call(argv=("compound_gate.py", "--force"))
    assert len(gate.spawns) == 1
    assert gate.watermark()["tokens"] == 5000


def test_force_does_nothing_when_on_precompact_is_off(gate, write_config):
    write_config("[compound]\non_precompact = false\n")
    gate.write(5000)
    gate.call(argv=("compound_gate.py", "--force"))
    assert gate.spawns == []
    assert gate.watermark() is None


def test_force_still_honours_the_lock(gate, small_threshold):
    gate.write(5000)
    assert gate.gm.lock_acquire() is True
    gate.call(argv=("compound_gate.py", "--force"))
    assert gate.spawns == []
    assert "skipped: a compound is already running" in gate.log()


def test_the_event_name_defaults_to_precompact_under_force(gate, small_threshold):
    gate.write(5000)
    gate.call(payload={"session_id": SESSION, "transcript_path": str(gate.transcript),
                       "cwd": str(gate.project), "trigger": "auto"},
              argv=("compound_gate.py", "--force"))
    assert "event=PreCompact trigger=auto" in gate.log()


# ------------------------------------------------------------- the hook contract

@pytest.mark.parametrize("payload", ["", "not json at all", "[]", "null", "{}"])
def test_garbage_stdin_still_prints_empty_json_and_exits_zero(gate, payload, capsys):
    gate.call(payload=payload)
    assert capsys.readouterr().out.strip() == "{}"
    assert gate.spawns == []


def test_a_missing_transcript_is_not_an_error(gate, capsys):
    gate.call(payload={"session_id": SESSION, "cwd": str(gate.project),
                       "transcript_path": str(gate.project / "gone.jsonl")})
    assert capsys.readouterr().out.strip() == "{}"
    assert gate.spawns == []


def test_a_transcript_with_no_usable_usage_bails(gate, small_threshold, capsys):
    gate.write(0, rows=[{"type": "user", "message": {"content": "hello"}}])
    gate.call()
    assert capsys.readouterr().out.strip() == "{}"
    assert gate.spawns == []
    assert gate.watermark() is None


def test_the_firing_path_also_prints_only_empty_json(gate, small_threshold, capsys):
    gate.write(5000)
    gate.call()
    capsys.readouterr()
    gate.write(9000)
    gate.call()
    assert capsys.readouterr().out.strip() == "{}"


def test_the_script_as_a_whole_prints_empty_json_and_exits_zero(gm_home, project):
    """End to end, through the __main__ guard, the way the hook really runs it."""
    env = dict(os.environ)
    env["GM_HOME"] = str(gm_home)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--force"],
        input="this is not json", capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "{}"


# ------------------------------------------------------------- the mark files

def test_each_session_gets_its_own_mark_file(gate, small_threshold):
    gate.write(5000)
    gate.call()
    expected = gate.gm.STATE_DIR / "sessions" / f"{SESSION}.json"
    assert gate.mod.mark_path(SESSION) == expected
    assert expected.is_file()


def test_a_session_id_with_unsafe_characters_is_made_into_a_file_name(gate):
    path = gate.mod.mark_path("../../etc/pa ss:wd")
    assert path.parent == gate.gm.STATE_DIR / "sessions"
    # dots survive (they are legal in a file name); separators do not
    assert path.name == "..-..-etc-pa-ss-wd.json"
    assert "/" not in path.name and "\\" not in path.name


def test_an_empty_session_id_still_has_a_mark_file(gate):
    assert gate.mod.mark_path("").name == "unknown.json"


def test_a_mark_in_the_old_shared_file_is_migrated_once(gate, small_threshold):
    gate.gm.write_json_atomic(gate.gm.WATERMARK_PATH,
                              {SESSION: {"tokens": 5000, "at": "2026-03-01T00:00:00"}})
    gate.write(5500)                  # +500: under the threshold, so no spawn
    gate.call()
    assert gate.spawns == []
    entry = gate.watermark()
    assert entry["tokens"] == 5000
    assert "migrated from watermark.json" in entry["note"]


def test_a_mark_nobody_will_read_again_is_pruned(gate):
    stale = gate.mod.mark_path("old-session")
    fresh = gate.mod.mark_path("new-session")
    gate.gm.write_json_atomic(stale, {"tokens": 1})
    gate.gm.write_json_atomic(fresh, {"tokens": 2})
    old = time.time() - 31 * 86400
    os.utime(stale, (old, old))
    gate.mod.prune_marks()
    assert not stale.exists()
    assert fresh.exists()


# ------------------------------------------------------- last_usage_total details

def test_last_usage_total_sums_the_three_context_fields(gate):
    path = gate.write(0, rows=[{
        "type": "assistant",
        "message": {"model": "m", "content": [{"type": "text", "text": "x"}],
                    "usage": {"input_tokens": 1, "cache_read_input_tokens": 20,
                              "cache_creation_input_tokens": 300}}}])
    assert gate.mod.last_usage_total(str(path)) == 321


def test_last_usage_total_skips_sidechain_synthetic_and_error_rows(gate):
    path = gate.write(0, rows=[
        assistant_row(5000),
        assistant_row(999999, isSidechain=True),
        assistant_row(888888, isApiErrorMessage=True),
        {"type": "assistant", "message": {"model": "<synthetic>", "content": [],
                                          "usage": {"input_tokens": 777777}}},
    ])
    assert gate.mod.last_usage_total(str(path)) == 5000


def test_last_usage_total_is_none_for_an_unreadable_transcript(gate, tmp_path):
    assert gate.mod.last_usage_total(str(tmp_path / "missing.jsonl")) is None


# ------------------------------------------------------------ is_settled details

def test_is_settled_false_when_the_last_block_is_a_tool_call(gate):
    path = gate.write(100, settled=False)
    assert gate.mod.is_settled(str(path)) is False


def test_is_settled_true_when_the_last_block_is_text(gate):
    path = gate.write(100)
    assert gate.mod.is_settled(str(path)) is True


def test_is_settled_true_for_string_content(gate):
    path = gate.write(0, rows=[{"type": "assistant",
                                "message": {"model": "m", "content": "just text"}}])
    assert gate.mod.is_settled(str(path)) is True


def test_is_settled_true_when_the_transcript_cannot_be_read(gate, tmp_path):
    assert gate.mod.is_settled(str(tmp_path / "missing.jsonl")) is True


# -------------------------------------------------------- transcript_dir details

def test_transcript_dir_of_a_main_session_is_the_slug_dir(gate, samefile):
    here = gate.gm.transcripts_dir(gate.project)
    got = gate.mod.transcript_dir(str(here / "sess.jsonl"), str(gate.project))
    assert samefile(got, here)


def test_transcript_dir_of_a_subagent_session_is_the_parent(gate, samefile):
    here = gate.gm.transcripts_dir(gate.project)
    deep = here / "some-session-id"
    deep.mkdir(parents=True, exist_ok=True)
    got = gate.mod.transcript_dir(str(deep / "sub.jsonl"), str(gate.project))
    assert samefile(got, here)
