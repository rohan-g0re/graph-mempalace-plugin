"""filter_transcripts: exchange grouping, stable ids, drops, index, summary line."""
from __future__ import annotations

import importlib
import json

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
    rc = ft.main([str(tdir), str(odir)])
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
    ft.main([str(tdir), str(odir)])
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
    ft.main([str(tdir), str(odir)])
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
    assert ft.main([str(tdir), str(odir)]) == 0
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
