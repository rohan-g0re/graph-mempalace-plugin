"""filter_transcripts: exchange grouping, stable ids, drops, index, summary line."""
from __future__ import annotations

import importlib
import json
import os
import time

import pytest

SESSION = "b49fc7be-1111-2222-3333-444444444444"
S8 = "b49fc7be"
DAY = "2026-03-04"

# Under 120 characters, so the topic line is this text in full.
USER_1 = ("Please walk me through the compound gate logic, from the Stop hook down "
          "to the watermark.")
CLAUDE_1 = ("The gate reads the token count of the last assistant turn and compares "
            "it with a per-session watermark, and does nothing at all unless the "
            "session has grown past the threshold.")
SUBAGENT_1 = ("SUBAGENT_MARKER: the subagent read the gate and reports that the "
              "watermark advances only on a settled turn.")
USER_2 = ("Now explain how the lock prevents two compounds from running at the same "
          "time on one machine, and what happens when it goes stale.")
CLAUDE_2 = ("One machine-wide lock file under the state directory, with a staleness "
            "window of three hours by default.")

PAD = " padded out so this line is comfortably longer than forty characters."

GITIGNORE_LINES = ["graphify-out/", ".graphify_*", "_index.jsonl"]


def _quiet_now(ft):
    """A clock past the quiet window, so every session counts as finished and
    its last exchange is filed. Tests that are not about the hold-back use it."""
    return time.time() + ft.OPEN_EXCHANGE_QUIET_SECONDS + 60

DROPPED_MARKERS = (
    "UNIQUE_TOOL_USE_MARKER",
    "UNIQUE_TOOL_RESULT_MARKER",
    "UNIQUE_META_MARKER",
    "UNIQUE_COMPACT_MARKER",
    "UNIQUE_APIERR_MARKER",
    "UNIQUE_SYNTHETIC_MARKER",
    "UNIQUE_NOISE_MARKER",
    "SUBAGENT_MARKER",
    "thanks",
)


def _rows():
    def ts(sec):
        return f"{DAY}T05:06:{sec:02d}.000Z"

    return [
        {"type": "user", "timestamp": ts(7),
         "message": {"role": "user", "content": USER_1}},
        {"type": "assistant", "timestamp": ts(20),
         "message": {"model": "claude-opus-4",
                     "content": [{"type": "text", "text": CLAUDE_1}]}},
        # a tool call: no text block at all, so no turn
        {"type": "assistant", "timestamp": ts(25),
         "message": {"model": "claude-opus-4",
                     "content": [{"type": "tool_use", "id": "tu1", "name": "Read",
                                  "input": {"file_path": "UNIQUE_TOOL_USE_MARKER" + PAD}}]}},
        # a tool result replayed as a user turn
        {"type": "user", "timestamp": ts(26), "toolUseResult": {"stdout": "x"},
         "message": {"role": "user",
                     "content": [{"type": "text",
                                  "text": "UNIQUE_TOOL_RESULT_MARKER" + PAD}]}},
        {"type": "user", "timestamp": ts(27), "isMeta": True,
         "message": {"content": "UNIQUE_META_MARKER" + PAD}},
        {"type": "assistant", "timestamp": ts(28), "isCompactSummary": True,
         "message": {"model": "claude-opus-4",
                     "content": [{"type": "text", "text": "UNIQUE_COMPACT_MARKER" + PAD}]}},
        {"type": "assistant", "timestamp": ts(29), "isApiErrorMessage": True,
         "message": {"model": "claude-opus-4",
                     "content": [{"type": "text", "text": "UNIQUE_APIERR_MARKER" + PAD}]}},
        {"type": "assistant", "timestamp": ts(29),
         "message": {"model": "<synthetic>",
                     "content": [{"type": "text", "text": "UNIQUE_SYNTHETIC_MARKER" + PAD}]}},
        # a subagent turn: dropped, because it lands late and would renumber
        # every exchange after it
        {"type": "assistant", "timestamp": ts(30), "isSidechain": True,
         "message": {"model": "claude-sonnet",
                     "content": [{"type": "text", "text": SUBAGENT_1}]}},
        # too short to carry retrievable content
        {"type": "user", "timestamp": ts(31), "message": {"content": "thanks"}},
        # structural noise that survives the type filter
        {"type": "user", "timestamp": ts(32),
         "message": {"content": "<local-command-stdout>UNIQUE_NOISE_MARKER" + PAD
                                + "</local-command-stdout>"}},
        {"type": "user", "timestamp": f"{DAY}T05:10:00.000Z",
         "message": {"role": "user", "content": USER_2}},
        {"type": "assistant", "timestamp": f"{DAY}T05:10:30.000Z",
         "message": {"model": "claude-opus-4",
                     "content": [{"type": "text", "text": CLAUDE_2}]}},
    ]


@pytest.fixture
def filtered(gm_home, tmp_path, write_jsonl, capsys):
    """Run the filter over one synthetic session and hand back the result."""
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "transcripts"
    odir = tmp_path / "exchanges"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    rc = ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    captured = capsys.readouterr()
    return {"rc": rc, "out": captured.out, "err": captured.err, "odir": odir,
            "tdir": tdir, "mod": ft}


def test_two_exchanges_are_written_with_stable_ids(filtered):
    odir = filtered["odir"]
    assert filtered["rc"] == 0
    names = sorted(p.name for p in odir.glob("*.md"))
    assert names == [f"{DAY}-{S8}-0001.md", f"{DAY}-{S8}-0002.md"]


def test_frontmatter_carries_the_join_key_and_the_counts(filtered):
    body = (filtered["odir"] / f"{DAY}-{S8}-0001.md").read_text(encoding="utf-8")
    head = body.split("---")[1].strip().splitlines()
    keys = [line.split(":", 1)[0] for line in head]
    assert keys == ["id", "session", "date", "started", "turns", "topic"]
    assert f"id: {S8}-0001" in head
    assert f"session: {SESSION}" in head
    assert f"date: {DAY}" in head
    assert f"started: {DAY} 05:06:07" in head
    assert "turns: 2" in head
    assert f"topic: {json.dumps(USER_1)}" in head
    assert body.startswith("---\n")
    assert f"# Exchange {S8}-0001" in body


def test_each_turn_is_a_heading_with_its_role_and_timestamp(filtered):
    body = (filtered["odir"] / f"{DAY}-{S8}-0001.md").read_text(encoding="utf-8")
    assert f"## USER — {DAY} 05:06:07" in body
    assert f"## CLAUDE — {DAY} 05:06:20" in body
    assert USER_1 in body and CLAUDE_1 in body


def test_the_second_exchange_holds_only_its_own_turns(filtered):
    body = (filtered["odir"] / f"{DAY}-{S8}-0002.md").read_text(encoding="utf-8")
    assert "turns: 2" in body
    assert USER_2 in body and CLAUDE_2 in body
    assert USER_1 not in body


@pytest.mark.parametrize("marker", DROPPED_MARKERS)
def test_dropped_rows_never_reach_an_exchange_file(filtered, marker):
    text = "\n".join(p.read_text(encoding="utf-8") for p in filtered["odir"].glob("*.md"))
    assert marker not in text


def test_index_maps_every_id_to_its_file(filtered):
    rows = [json.loads(line) for line
            in (filtered["odir"] / "_index.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert [r["id"] for r in rows] == [f"{S8}-0001", f"{S8}-0002"]
    for row in rows:
        assert set(row) == {"id", "session", "date", "file", "turns", "chars", "topic"}
        assert (filtered["odir"] / row["file"]).exists()
        assert row["session"] == SESSION
    assert [r["turns"] for r in rows] == [2, 2]


def test_gitignore_hides_the_graph_and_the_index_from_the_mine(filtered):
    lines = (filtered["odir"] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines == GITIGNORE_LINES


def test_an_existing_gitignore_keeps_its_own_lines_and_gains_the_missing_ones(
        gm_home, tmp_path, write_jsonl):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    odir.mkdir()
    (odir / ".gitignore").write_text("custom\ngraphify-out/\n", encoding="utf-8",
                                     newline="\n")
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)])
    lines = (odir / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines == ["custom", "graphify-out/", ".graphify_*", "_index.jsonl"]


def test_an_unterminated_gitignore_gains_a_newline_first(gm_home, tmp_path,
                                                         write_jsonl):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    odir.mkdir()
    (odir / ".gitignore").write_text("custom", encoding="utf-8", newline="\n")
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)])
    assert (odir / ".gitignore").read_text(encoding="utf-8") == \
        "custom\ngraphify-out/\n.graphify_*\n_index.jsonl\n"


def test_a_complete_gitignore_is_not_touched(gm_home, tmp_path, write_jsonl):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    odir.mkdir()
    gi = odir / ".gitignore"
    gi.write_text("".join(f"{line}\n" for line in GITIGNORE_LINES),
                  encoding="utf-8", newline="\n")
    before = gi.read_bytes()
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)])
    assert gi.read_bytes() == before


def test_the_last_line_of_stdout_is_the_parseable_summary(filtered):
    lines = [line for line in filtered["out"].splitlines() if line.strip()]
    assert lines[-1] == f"filter: 2 exchanges, 4 turns, 1 sessions -> {filtered['odir']}"
    assert "exchanges written: 2" in filtered["out"]
    assert "turns kept       : 4" in filtered["out"]


def test_output_is_lf_only(filtered):
    for path in list(filtered["odir"].glob("*.md")) + [filtered["odir"] / "_index.jsonl",
                                                       filtered["odir"] / ".gitignore"]:
        assert b"\r\n" not in path.read_bytes(), path.name


def test_an_exchange_below_the_character_floor_is_skipped(gm_home, tmp_path,
                                                         write_jsonl, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", [
        {"type": "user", "timestamp": f"{DAY}T01:00:00.000Z",
         "message": {"content": "A question just over the forty character floor."}},
        {"type": "user", "timestamp": f"{DAY}T02:00:00.000Z",
         "message": {"content": USER_2}},
        {"type": "assistant", "timestamp": f"{DAY}T02:00:30.000Z",
         "message": {"model": "m", "content": [{"type": "text", "text": CLAUDE_2}]}},
    ])
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    # The first exchange is under MIN_EXCHANGE_CHARS, so only the second lands -
    # and it keeps its ordinal, 0002, because the ids must not renumber.
    assert [p.name for p in odir.glob("*.md")] == [f"{DAY}-{S8}-0002.md"]
    assert capsys.readouterr().out.splitlines()[-1].startswith(
        "filter: 1 exchanges, 2 turns, 1 sessions")


def test_a_subagent_subdirectory_is_not_read_at_all(gm_home, tmp_path, write_jsonl,
                                                    capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    write_jsonl(tdir / SESSION / "inner.jsonl", [
        {"type": "user", "timestamp": f"{DAY}T06:00:00.000Z",
         "message": {"content": "SUBDIR_MARKER " + USER_2}},
        {"type": "assistant", "timestamp": f"{DAY}T06:00:30.000Z",
         "message": {"model": "m", "content": [{"type": "text", "text": CLAUDE_2}]}},
    ])
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    assert capsys.readouterr().out.splitlines()[-1].startswith(
        "filter: 2 exchanges, 4 turns, 1 sessions")
    text = "\n".join(p.read_text(encoding="utf-8") for p in odir.glob("*.md"))
    assert "SUBDIR_MARKER" not in text


def test_an_unreadable_transcript_costs_one_stderr_line_not_the_run(
        gm_home, tmp_path, write_jsonl, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    # A directory where a transcript is expected: open() fails on every OS.
    (tdir / "locked-session.jsonl").mkdir()
    assert ft.main([str(tdir), str(odir)], now=_quiet_now(ft)) == 0
    captured = capsys.readouterr()
    assert "filter: could not read" in captured.err
    assert len(list(odir.glob("*.md"))) == 2
    assert (odir / "_index.jsonl").read_text(encoding="utf-8").strip()


def test_an_empty_transcript_dir_still_writes_the_scaffolding(gm_home, tmp_path, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "empty"
    tdir.mkdir()
    odir = tmp_path / "o"
    assert ft.main([str(tdir), str(odir)]) == 0
    assert (odir / "_index.jsonl").read_text(encoding="utf-8") == ""
    assert (odir / ".gitignore").exists()
    assert capsys.readouterr().out.splitlines()[-1] == \
        f"filter: 0 exchanges, 0 turns, 0 sessions -> {odir}"


@pytest.mark.parametrize("argv", [[], ["only-one"], ["-h"], ["--help"]])
def test_bad_arguments_exit_2(gm_home, argv, capsys):
    ft = importlib.import_module("filter_transcripts")
    assert ft.main(argv) == 2
    assert "Usage" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the open last exchange, and leaving unchanged files alone
# ---------------------------------------------------------------------------
ID_1 = f"{DAY}-{S8}-0001.md"
ID_2 = f"{DAY}-{S8}-0002.md"


def _age(path, seconds):
    """Set a file's mtime `seconds` into the past."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def _index_rows(odir):
    return [json.loads(line) for line
            in (odir / "_index.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _append(path, rows):
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


EXTRA_REPLY = ("A further reply that lands while the second exchange is still open, "
               "so the file an earlier run wrote no longer matches the transcript.")


def _extra_reply():
    return [{"type": "assistant", "timestamp": f"{DAY}T05:11:00.000Z",
             "message": {"model": "claude-opus-4",
                         "content": [{"type": "text", "text": EXTRA_REPLY}]}}]


def test_a_live_session_holds_back_its_last_exchange(gm_home, tmp_path, write_jsonl,
                                                     capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())   # mtime is now: live
    assert ft.main([str(tdir), str(odir)]) == 0
    assert sorted(p.name for p in odir.glob("*.md")) == [ID_1]
    assert [r["id"] for r in _index_rows(odir)] == [f"{S8}-0001"]
    out = capsys.readouterr().out
    assert "held back        : 1 open exchange(s)" in out
    # The parseable line keeps its shape and counts only what the index lists.
    assert out.splitlines()[-1] == f"filter: 1 exchanges, 2 turns, 1 sessions -> {odir}"


def test_a_session_quiet_past_the_window_files_its_last_exchange(
        gm_home, tmp_path, write_jsonl, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    transcript = write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    _age(transcript, ft.OPEN_EXCHANGE_QUIET_SECONDS + 60)
    ft.main([str(tdir), str(odir)])                    # the real clock
    assert sorted(p.name for p in odir.glob("*.md")) == [ID_1, ID_2]
    out = capsys.readouterr().out
    assert "held back        : 0 open exchange(s)" in out
    assert out.splitlines()[-1] == f"filter: 2 exchanges, 4 turns, 1 sessions -> {odir}"


def test_a_short_last_exchange_is_still_the_open_one(gm_home, tmp_path, write_jsonl,
                                                     capsys):
    # The size floor is applied after the hold-back: a last exchange too short to
    # file is still the one that is growing, so the exchange before it is final
    # and is written.
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows()[:2] + [
        {"type": "user", "timestamp": f"{DAY}T05:10:00.000Z",
         "message": {"content": "A question just over the forty character floor."}},
    ])
    ft.main([str(tdir), str(odir)])
    assert [p.name for p in odir.glob("*.md")] == [ID_1]
    assert "held back        : 1 open exchange(s)" in capsys.readouterr().out


def test_a_held_back_exchange_keeps_its_earlier_file_and_index_row(
        gm_home, tmp_path, write_jsonl, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    transcript = write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))   # an earlier run filed 0002
    earlier_row = _index_rows(odir)[1]
    second = odir / ID_2
    _age(second, 3600)
    before_bytes, before_mtime = second.read_bytes(), second.stat().st_mtime_ns

    _append(transcript, _extra_reply())                    # the session is live again
    capsys.readouterr()
    ft.main([str(tdir), str(odir)])

    assert second.read_bytes() == before_bytes
    assert second.stat().st_mtime_ns == before_mtime
    assert EXTRA_REPLY not in second.read_text(encoding="utf-8")
    rows = _index_rows(odir)
    assert [r["id"] for r in rows] == [f"{S8}-0001", f"{S8}-0002"]
    assert rows[1] == earlier_row
    out = capsys.readouterr().out
    assert "held back        : 1 open exchange(s)" in out
    assert out.splitlines()[-1] == f"filter: 2 exchanges, 4 turns, 1 sessions -> {odir}"


def test_a_held_back_file_with_no_earlier_row_is_described_from_the_file(
        gm_home, tmp_path, write_jsonl):
    # The index can be missing (deleted, or a crash before it was written). The
    # file's own `turns:` count says how much of the exchange it holds.
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    transcript = write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    (odir / "_index.jsonl").unlink()
    _append(transcript, _extra_reply())
    ft.main([str(tdir), str(odir)])
    row = _index_rows(odir)[1]
    assert row == {"id": f"{S8}-0002", "session": SESSION, "date": DAY, "file": ID_2,
                   "turns": 2, "chars": len(USER_2) + len(CLAUDE_2),
                   "topic": " ".join(USER_2.split())[:120]}


def test_ids_stay_put_from_held_back_to_filed(gm_home, tmp_path, write_jsonl, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    transcript = write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)])                          # live: 0002 held back
    first = (odir / ID_1).read_bytes()
    assert not (odir / ID_2).exists()

    _append(transcript, _extra_reply())
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))      # quiet: 0002 filed
    assert sorted(p.name for p in odir.glob("*.md")) == [ID_1, ID_2]
    assert (odir / ID_1).read_bytes() == first
    body = (odir / ID_2).read_text(encoding="utf-8")
    assert f"id: {S8}-0002" in body
    assert USER_2 in body and CLAUDE_2 in body and EXTRA_REPLY in body
    assert "turns: 3" in body
    assert [r["id"] for r in _index_rows(odir)] == [f"{S8}-0001", f"{S8}-0002"]
    assert capsys.readouterr().out.splitlines()[-1] == \
        f"filter: 2 exchanges, 5 turns, 1 sessions -> {odir}"


def test_an_unchanged_exchange_file_is_not_rewritten(gm_home, tmp_path, write_jsonl,
                                                     capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    for p in odir.glob("*.md"):
        _age(p, 3600)
    before = {p.name: p.stat().st_mtime_ns for p in odir.glob("*.md")}
    capsys.readouterr()
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    assert {p.name: p.stat().st_mtime_ns for p in odir.glob("*.md")} == before
    assert "files changed    : 0" in capsys.readouterr().out


def test_a_changed_exchange_file_is_rewritten(gm_home, tmp_path, write_jsonl, capsys):
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    good = (odir / ID_1).read_bytes()
    (odir / ID_1).write_text("stale", encoding="utf-8")
    capsys.readouterr()
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    assert (odir / ID_1).read_bytes() == good
    assert "files changed    : 1" in capsys.readouterr().out
    assert not list(odir.glob(".*.tmp"))


def test_a_refused_replace_falls_back_to_writing_in_place(gm_home, tmp_path,
                                                          monkeypatch):
    # Windows refuses os.replace onto a file another process holds open.
    ft = importlib.import_module("filter_transcripts")
    target = tmp_path / "x.md"
    target.write_text("old", encoding="utf-8")

    def refuse(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(ft.os, "replace", refuse)
    assert ft.write_if_changed(str(target), "new") is True
    assert target.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".*.tmp"))


def test_an_unchanged_write_reports_nothing_written(tmp_path, gm_home):
    ft = importlib.import_module("filter_transcripts")
    target = tmp_path / "x.md"
    target.write_bytes("same — text".encode("utf-8"))
    assert ft.write_if_changed(str(target), "same — text") is False


def test_a_transcript_whose_mtime_cannot_be_read_counts_as_live(gm_home, tmp_path):
    # Holding back costs one run of delay; filing a growing exchange can jam
    # the chat graph. So the unknown case is the cautious one.
    ft = importlib.import_module("filter_transcripts")
    assert ft.session_is_live(str(tmp_path / "missing.jsonl"), time.time()) is True


def test_the_quiet_window_edge(gm_home, tmp_path, write_jsonl):
    ft = importlib.import_module("filter_transcripts")
    path = write_jsonl(tmp_path / "s.jsonl", _rows())
    mtime = os.path.getmtime(path)
    window = ft.OPEN_EXCHANGE_QUIET_SECONDS
    assert ft.session_is_live(str(path), mtime + window - 1) is True
    assert ft.session_is_live(str(path), mtime + window) is False


def test_a_held_back_file_with_no_turn_count_is_described_as_it_stands(
        gm_home, tmp_path, write_jsonl):
    # No earlier row and no readable `turns:` line: describe the exchange as the
    # transcript has it now. The file itself is still left alone.
    ft = importlib.import_module("filter_transcripts")
    tdir = tmp_path / "t"
    odir = tmp_path / "o"
    transcript = write_jsonl(tdir / f"{SESSION}.jsonl", _rows())
    ft.main([str(tdir), str(odir)], now=_quiet_now(ft))
    (odir / "_index.jsonl").unlink()
    (odir / ID_2).write_text("no frontmatter here\n", encoding="utf-8")
    _append(transcript, _extra_reply())
    ft.main([str(tdir), str(odir)])
    assert (odir / ID_2).read_text(encoding="utf-8") == "no frontmatter here\n"
    assert _index_rows(odir)[1]["turns"] == 3
