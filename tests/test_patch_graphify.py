"""patch_graphify: patch, re-patch, check-only, and the missing-anchor refusal."""
from __future__ import annotations

import importlib
import importlib.util

import pytest

ANCHOR = '        "--no-session-persistence",\n'

FAKE_LLM = '''"""A minimal stand-in for graphify's llm.py."""
import sys


def claude_argv(prompt: str) -> list[str]:
    return [
        "claude",
        "-p",
        prompt,
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
'''


@pytest.fixture
def pg():
    return importlib.import_module("patch_graphify")


@pytest.fixture
def llm(tmp_path):
    path = tmp_path / "graphify" / "llm.py"
    path.parent.mkdir(parents=True)
    path.write_text(FAKE_LLM, encoding="utf-8", newline="\n")
    assert FAKE_LLM.count(ANCHOR) == 1
    return path


def test_patching_inserts_the_flag_after_the_anchor(pg, llm, capsys):
    assert pg.main(["--llm-path", str(llm)]) == 0
    assert f"patched: {llm}" in capsys.readouterr().out
    text = llm.read_text(encoding="utf-8")
    assert "--strict-mcp-config" in text
    assert "GRAPHIFY_KEEP_MCP" in text
    lines = text.splitlines()
    i = lines.index('        "--no-session-persistence",')
    assert "graph-mempalace" in lines[i + 1]
    assert b"\r\n" not in llm.read_bytes()


def test_patching_adds_the_import_it_needs(pg, llm):
    assert "import os" not in llm.read_text(encoding="utf-8")
    pg.main(["--llm-path", str(llm)])
    text = llm.read_text(encoding="utf-8")
    assert "import os\n" in text
    # inserted into the existing import block, not before the docstring
    assert text.startswith('"""A minimal stand-in')


def test_an_existing_import_os_is_not_duplicated(pg, tmp_path):
    path = tmp_path / "llm.py"
    path.write_text("import os\nimport sys\n\n\ndef f():\n    return [\n" + ANCHOR
                    + "    ]\n", encoding="utf-8", newline="\n")
    assert pg.main(["--llm-path", str(path)]) == 0
    assert path.read_text(encoding="utf-8").count("import os") == 1


def test_a_second_run_changes_nothing(pg, llm, capsys):
    pg.main(["--llm-path", str(llm)])
    capsys.readouterr()
    before = llm.read_bytes()
    assert pg.main(["--llm-path", str(llm)]) == 0
    assert f"already patched: {llm}" in capsys.readouterr().out
    assert llm.read_bytes() == before


def test_check_reports_unpatched_and_writes_nothing(pg, llm, capsys):
    before = llm.read_bytes()
    assert pg.main(["--llm-path", str(llm), "--check"]) == 0
    assert f"unpatched: {llm}" in capsys.readouterr().out
    assert llm.read_bytes() == before


def test_check_reports_an_already_patched_file(pg, llm, capsys):
    pg.main(["--llm-path", str(llm)])
    capsys.readouterr()
    assert pg.main(["--llm-path", str(llm), "--check"]) == 0
    assert "already patched" in capsys.readouterr().out


def test_a_missing_anchor_is_refused(pg, tmp_path, capsys):
    path = tmp_path / "llm.py"
    path.write_text("import sys\n\n\ndef f():\n    return []\n", encoding="utf-8")
    before = path.read_bytes()
    assert pg.main(["--llm-path", str(path)]) == 1
    err = capsys.readouterr().err
    assert "anchor not unique" in err and "found 0 occurrences" in err
    assert path.read_bytes() == before


def test_a_duplicated_anchor_is_refused(pg, tmp_path, capsys):
    path = tmp_path / "llm.py"
    path.write_text("def f():\n    return [\n" + ANCHOR + "    ]\n\n"
                    "def g():\n    return [\n" + ANCHOR + "    ]\n",
                    encoding="utf-8", newline="\n")
    assert pg.main(["--llm-path", str(path)]) == 1
    assert "found 2 occurrences" in capsys.readouterr().err


def test_a_path_that_is_not_a_file_is_refused(pg, tmp_path, capsys):
    assert pg.main(["--llm-path", str(tmp_path / "nowhere" / "llm.py")]) == 1
    assert "not a file" in capsys.readouterr().err


@pytest.mark.skipif(importlib.util.find_spec("graphify") is not None,
                    reason="graphify is importable from this interpreter")
def test_without_graphify_it_says_which_interpreter_to_use(pg, capsys):
    assert pg.main([]) == 1
    err = capsys.readouterr().err
    assert "cannot import graphify from this interpreter" in err
    assert "--llm-path" in err


def test_ensure_import_os_handles_a_file_with_no_imports(pg):
    assert pg.ensure_import_os("x = 1\n") == "import os\nx = 1\n"


@pytest.mark.parametrize("header", [
    "import os\n",
    "import os, sys\n",
    "import sys, os\n",
    "from os import environ\n",
    "import os  # comment\n",
])
def test_ensure_import_os_detects_an_existing_import(pg, header):
    assert pg.ensure_import_os(header + "x = 1\n") == header + "x = 1\n"
