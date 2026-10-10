"""kb_resolve: id extraction, dereferencing an exchange, and the doctor checks."""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

EID = "b49fc7be-0001"
MD_NAME = f"2026-03-04-{EID}.md"
TOPIC = "why there are two graphs"
EXCHANGE = f"""---
id: {EID}
session: b49fc7be-1111-2222-3333-444444444444
date: 2026-03-04
turns: 2
topic: "{TOPIC}"
---

# Exchange {EID}

## USER — 2026-03-04 05:06:07

Why are there two graphs instead of one?

## CLAUDE — 2026-03-04 05:06:20

Because code and chat have different ground truth: the repo is always current.
"""


@pytest.fixture
def kb(gm_home, project, empty_path, tmp_path, monkeypatch, make_tool):
    """A complete, healthy knowledge-base tree plus hooks for fake tools."""
    gm = importlib.import_module("gm_common")
    dd = gm.exchanges_dir(project)
    dd.mkdir(parents=True)
    (dd / MD_NAME).write_text(EXCHANGE, encoding="utf-8", newline="\n")
    (dd / "_index.jsonl").write_text(
        json.dumps({"id": EID, "session": "b49fc7be-1111", "date": "2026-03-04",
                    "file": MD_NAME, "turns": 2, "chars": 140, "topic": TOPIC}) + "\n",
        encoding="utf-8", newline="\n")
    (dd / ".gitignore").write_text("graphify-out/\n.graphify_*\n_index.jsonl\n",
                                   encoding="utf-8", newline="\n")

    def write_graph(path, sources):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"nodes": [{"id": i, "label": f"n{i}",
                                               "source_file": s}
                                              for i, s in enumerate(sources)]}),
                        encoding="utf-8")
        return path

    write_graph(gm.graph_a_path(project), ["scripts/gm_common.py"])
    write_graph(gm.graph_b_path(project), [MD_NAME])

    bins = tmp_path / "bins"
    kbmod = importlib.import_module("kb_resolve")

    def install(name, **kwargs):
        path = make_tool(bins, name, **kwargs)
        monkeypatch.setenv("GM_%s_BIN" % name.upper(), str(path))
        gm._FIND_TOOL_CACHE.clear()
        return path

    return SimpleNamespace(gm=gm, mod=kbmod, project=project, dd=dd,
                           install=install, write_graph=write_graph,
                           wing="proj_dialogue")


def healthy_tools(kb, graphify_stdout=""):
    kb.install("mempalace", stdout=f"PALACE\nWING:  {kb.wing}\n")
    kb.install("graphify", stdout=graphify_stdout)


# ------------------------------------------------------------- id extraction

def test_ids_come_off_the_src_pointer(kb):
    text = ("  - gate decides when to compound [src=2026-09-16-b49fc7be-0008.md]\n"
            "  - the lock is machine wide [src=2026-09-17-b49fc7be-0012.md]\n")
    assert kb.mod.ids_from_graph_output(text) == ["b49fc7be-0008", "b49fc7be-0012"]


def test_repeated_ids_are_reported_once(kb):
    text = ("[src=2026-09-16-b49fc7be-0008.md] [src=2026-09-16-b49fc7be-0008.md]\n"
            "see b49fc7be-0008 above\n")
    assert kb.mod.ids_from_graph_output(text) == ["b49fc7be-0008"]


def test_a_bare_id_in_prose_is_found_too(kb):
    assert kb.mod.ids_from_graph_output("as decided in b49fc7be-0007") == \
        ["b49fc7be-0007"]


def test_non_ids_are_not_mistaken_for_ids(kb):
    assert kb.mod.ids_from_graph_output("src=scripts/gm_common.py\nversion 12345678") == []


def test_code_files_come_off_the_same_pointer(kb):
    text = ("  - find_tool [src=scripts/gm_common.py]\n"
            "  - the gate [src=scripts/compound_gate.py]\n"
            "  - an exchange [src=2026-09-16-b49fc7be-0008.md]\n")
    assert kb.mod.files_from_graph_output(text) == ["scripts/gm_common.py",
                                                    "scripts/compound_gate.py"]


# ------------------------------------------------------------------------ get

def test_get_prints_the_exchange_behind_an_id(kb, capsys):
    assert kb.mod.cmd_get(EID, str(kb.project)) == 0
    out = capsys.readouterr().out
    assert f"=== {EID}  2026-03-04  turns=2 ===" in out
    assert str(kb.dd / MD_NAME) in out
    assert f"topic: {TOPIC}" in out
    assert "Why are there two graphs instead of one?" in out


def test_get_truncates_a_long_exchange_and_says_so(kb, capsys):
    kb.mod.cmd_get(EID, str(kb.project), chars=40)
    out = capsys.readouterr().out
    assert "more chars; open the file for the rest" in out


def test_get_of_an_unknown_id_says_so_and_still_exits_zero(kb, capsys):
    assert kb.mod.cmd_get("00000000-9999", str(kb.project)) == 0
    assert "no exchange 00000000-9999 in the index" in capsys.readouterr().out


def test_get_reports_a_file_the_index_still_lists(kb, capsys):
    (kb.dd / MD_NAME).unlink()
    assert kb.mod.cmd_get(EID, str(kb.project)) == 0
    assert "(file missing - re-run filter_transcripts.py)" in capsys.readouterr().out


def test_main_dispatches_get(kb, capsys):
    assert kb.mod.main(["get", EID, "--cwd", str(kb.project)]) == 0
    assert f"=== {EID}" in capsys.readouterr().out


# --------------------------------------------------------------------- doctor

def test_doctor_passes_on_a_healthy_tree(kb, capsys):
    healthy_tools(kb)
    assert kb.mod.cmd_doctor(str(kb.project)) == 0
    out = capsys.readouterr().out
    assert "[FAIL]" not in out
    assert "all links resolve" in out
    assert "Graph B nodes carry exchange ids" in out


def test_doctor_fails_and_exits_one_without_the_index(kb, capsys):
    healthy_tools(kb)
    (kb.dd / "_index.jsonl").unlink()
    assert kb.mod.cmd_doctor(str(kb.project)) == 1
    out = capsys.readouterr().out
    assert "[FAIL] exchange index present" in out
    assert "chain is broken above" in out


def test_doctor_fails_when_the_tools_are_absent(kb, capsys):
    assert kb.mod.cmd_doctor(str(kb.project)) == 1
    out = capsys.readouterr().out
    assert "[FAIL] mempalace found" in out
    assert "/graph-mempalace:kb-setup" in out


def test_doctor_notices_a_graph_that_ingested_itself(kb, capsys):
    healthy_tools(kb)
    kb.write_graph(kb.gm.graph_b_path(kb.project),
                   [MD_NAME, "graphify-out/graph.json"])
    assert kb.mod.cmd_doctor(str(kb.project)) == 1
    assert "[FAIL] Graph B free of self-ingestion" in capsys.readouterr().out


def test_doctor_suggests_a_rebuild_when_no_node_carries_an_id(kb, capsys):
    healthy_tools(kb)
    kb.write_graph(kb.gm.graph_b_path(kb.project), ["some-old-session-file.md"])
    assert kb.mod.cmd_doctor(str(kb.project)) == 1
    out = capsys.readouterr().out
    assert "[FAIL] Graph B nodes carry exchange ids" in out
    assert "NEXT: Graph B predates the per-exchange ids" in out


def test_doctor_reports_an_unparsable_graph_b(kb, capsys):
    healthy_tools(kb)
    kb.gm.graph_b_path(kb.project).write_text("{not json", encoding="utf-8")
    assert kb.mod.cmd_doctor(str(kb.project)) == 1
    assert "[FAIL] Graph B parses" in capsys.readouterr().out


def test_doctor_fails_when_the_wing_is_not_in_the_palace(kb, capsys):
    kb.install("mempalace", stdout="PALACE\nWING:  someone_elses_dialogue\n")
    kb.install("graphify")
    assert kb.mod.cmd_doctor(str(kb.project)) == 1
    out = capsys.readouterr().out
    assert "[FAIL] dialogue wing present" in out
    assert "[OK ] palace reachable" in out


def test_doctor_json_is_a_list_of_checks(kb, capsys):
    healthy_tools(kb)
    assert kb.mod.main(["doctor", "--cwd", str(kb.project), "--json"]) == 0
    checks = json.loads(capsys.readouterr().out)
    assert isinstance(checks, list) and checks
    for check in checks:
        assert set(check) == {"check", "ok", "detail"}
        assert isinstance(check["ok"], bool)
    assert all(c["ok"] for c in checks)


def test_doctor_json_exits_one_when_a_link_is_broken(kb, capsys):
    (kb.dd / "_index.jsonl").unlink()
    assert kb.mod.main(["doctor", "--cwd", str(kb.project), "--json"]) == 1
    checks = json.loads(capsys.readouterr().out)
    assert any(not c["ok"] for c in checks)


# ------------------------------------------------------------------------ ask

def test_ask_says_so_when_there_is_no_chat_graph(kb, capsys):
    healthy_tools(kb)
    kb.gm.graph_b_path(kb.project).unlink()
    assert kb.mod.cmd_ask("why two graphs", str(kb.project)) == 0
    out = capsys.readouterr().out
    assert f"no chat graph at {kb.gm.graph_b_path(kb.project)}" in out


def test_ask_code_says_so_when_there_is_no_code_graph(kb, capsys):
    healthy_tools(kb)
    kb.gm.graph_a_path(kb.project).unlink()
    assert kb.mod.cmd_ask("where is the gate", str(kb.project),
                          want_code=True, want_chat=False) == 0
    out = capsys.readouterr().out
    assert f"no code graph at {kb.gm.graph_a_path(kb.project)}" in out
    assert "CHAT" not in out


def test_ask_prints_the_verbatim_exchange_behind_a_node(kb, capsys):
    healthy_tools(kb, graphify_stdout=f"  - two graphs, two stores [src={MD_NAME}]\n")
    assert kb.mod.main(["ask", "why", "two", "graphs", "--cwd", str(kb.project)]) == 0
    out = capsys.readouterr().out
    assert "VERBATIM SOURCE — 1 exchange(s) behind those nodes" in out
    assert "Why are there two graphs instead of one?" in out
    assert f"full: kb_resolve.py get {EID}" in out


def test_ask_warns_when_no_node_carries_an_id(kb, capsys):
    healthy_tools(kb, graphify_stdout="  - a node with no pointer\n")
    kb.mod.cmd_ask("why two graphs", str(kb.project))
    assert "no exchange ids on those nodes" in capsys.readouterr().out


def test_ask_both_prints_both_sections(kb, capsys):
    healthy_tools(kb, graphify_stdout="  - something [src=scripts/gm_common.py]\n")
    assert kb.mod.main(["ask", "anything", "--both", "--cwd", str(kb.project)]) == 0
    out = capsys.readouterr().out
    assert "CHAT" in out and "CODE" in out
    assert "READ THESE" in out
    assert "scripts/gm_common.py" in out


# --------------------------------------------------------------------- search

def test_search_says_when_the_palace_has_nothing(kb, capsys):
    kb.install("mempalace", stdout="")
    assert kb.mod.cmd_search("wing collision", str(kb.project)) == 0
    assert "(no results - is the palace mined?)" in capsys.readouterr().out


def test_search_reports_the_ids_it_found(kb, capsys):
    kb.install("mempalace",
               stdout=f"1. drawer about graphs [src={MD_NAME}]\nEmbedderIdentity noise\n")
    assert kb.mod.main(["search", "graphs", "--cwd", str(kb.project)]) == 0
    out = capsys.readouterr().out
    assert f"exchange ids: {EID}" in out
    assert "dereference with:  kb_resolve.py get <id>" in out
    assert "EmbedderIdentity" not in out


# ----------------------------------------------------------------------- main

def test_no_command_prints_the_help(kb, capsys):
    assert kb.mod.main([]) == 0
    out = capsys.readouterr().out
    assert "{ask,get,search,doctor}" in out


def test_load_index_is_empty_without_a_filter_run(kb):
    (kb.dd / "_index.jsonl").unlink()
    assert kb.mod.load_index(str(kb.project)) == {}


def test_run_never_raises_on_a_missing_tool(kb):
    out = kb.mod.run([str(kb.project / "no-such-tool")])
    assert "command failed" in out
