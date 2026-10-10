"""py.sh, through a real bash. Skipped when bash is absent."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PY_SH = str(SCRIPTS / "py.sh").replace("\\", "/")

PROBE = """import sys
print("PYVER %d.%d" % (sys.version_info[0], sys.version_info[1]))
print("PYEXE %s" % sys.executable)
print("ARGS %s" % " ".join(sys.argv[1:]))
"""


def _find_bash():
    """A POSIX bash. On Windows, Git Bash - never the WSL stub in System32."""
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe",
                          r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.isfile(candidate):
                return candidate
        found = shutil.which("bash")
        if found and "system32" not in found.lower():
            return found
        return None
    return shutil.which("bash")


BASH = _find_bash()

pytestmark = pytest.mark.skipif(BASH is None, reason="no POSIX bash on this machine")


def fwd(path) -> str:
    return str(path).replace("\\", "/")


def run_sh(script, args, env_extra, cwd=None, stdin=""):
    env = dict(os.environ)
    env.pop("GM_PYTHON", None)
    env.update(env_extra)
    return subprocess.run([BASH, script, *args], capture_output=True, text=True,
                          env=env, cwd=cwd, input=stdin, timeout=120)


@pytest.fixture
def home(tmp_path):
    """A temporary GM_HOME in the slash style both bash and Python accept."""
    h = tmp_path / "gmhome"
    h.mkdir()
    return h


@pytest.fixture
def probe(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text(PROBE, encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------- py.sh

def test_py_sh_runs_a_script_with_python_311_or_newer(home, probe):
    proc = run_sh(PY_SH, [fwd(probe), "one", "two"], {"GM_HOME": fwd(home)})
    assert proc.returncode == 0, proc.stderr
    lines = dict(line.split(" ", 1) for line in proc.stdout.splitlines() if " " in line)
    major, minor = (int(n) for n in lines["PYVER"].split("."))
    assert (major, minor) >= (3, 11)
    assert lines["ARGS"] == "one two"


def test_py_sh_caches_the_interpreter_as_two_lines(home, probe):
    cache = home / ".graph-mempalace" / "python_path"
    assert not cache.exists()
    assert run_sh(PY_SH, [fwd(probe)], {"GM_HOME": fwd(home)}).returncode == 0
    assert cache.is_file()
    # line 1 the interpreter (which may contain spaces), line 2 its extra
    # arguments, empty when there are none
    lines = cache.read_text(encoding="utf-8").split("\n")
    assert lines[0].strip()
    assert len(lines) >= 2
    assert b"\r\n" not in cache.read_bytes()


def test_py_sh_reports_a_script_that_is_gone_without_blocking(home, tmp_path):
    proc = run_sh(PY_SH, [fwd(tmp_path / "vanished.py")], {"GM_HOME": fwd(home)})
    assert proc.returncode == 0          # exit 2 from a Stop hook blocks the turn
    assert "missing (plugin updated?); restart the session" in proc.stderr
    assert proc.stdout == ""


def test_py_sh_honours_gm_python(home, probe):
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"GM_HOME": fwd(home), "GM_PYTHON": fwd(sys.executable)})
    assert proc.returncode == 0, proc.stderr
    used = [ln.split(" ", 1)[1] for ln in proc.stdout.splitlines()
            if ln.startswith("PYEXE ")][0]
    assert os.path.normcase(os.path.realpath(used)) == \
        os.path.normcase(os.path.realpath(sys.executable))


def test_py_sh_runs_an_interpreter_whose_path_contains_a_space(home, probe,
                                                               tmp_path,
                                                               make_sh_script):
    """$GM_PY is quoted, so a per-user install under "John Smith" still runs."""
    wrapper = make_sh_script(tmp_path / "Program Files" / "py wrapper",
                             'exec "%s" "$@"\n' % sys.executable)
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"GM_HOME": fwd(home), "GM_PYTHON": fwd(wrapper)})
    assert proc.returncode == 0, proc.stderr
    assert "PYVER" in proc.stdout
    cache = home / ".graph-mempalace" / "python_path"
    assert cache.read_text(encoding="utf-8").splitlines()[0] == fwd(wrapper)


def test_py_sh_reuses_the_cached_interpreter(home, probe):
    cache = home / ".graph-mempalace" / "python_path"
    cache.parent.mkdir(parents=True)
    cache.write_text(fwd(sys.executable) + "\n\n", encoding="utf-8", newline="\n")
    before = cache.read_bytes()
    nowhere = home / "nowhere"
    nowhere.mkdir()
    proc = run_sh(PY_SH, [fwd(probe)],
                  {"GM_HOME": fwd(home), "PATH": fwd(nowhere)})
    assert proc.returncode == 0, proc.stderr
    used = [ln.split(" ", 1)[1] for ln in proc.stdout.splitlines()
            if ln.startswith("PYEXE ")][0]
    assert os.path.normcase(os.path.realpath(used)) == \
        os.path.normcase(os.path.realpath(sys.executable))
    assert cache.read_bytes() == before  # a cache hit is not rewritten


def test_py_sh_rewrites_a_one_line_cache_from_an_older_version(home, probe):
    cache = home / ".graph-mempalace" / "python_path"
    cache.parent.mkdir(parents=True)
    cache.write_text("no-such-interpreter\n", encoding="utf-8", newline="\n")
    proc = run_sh(PY_SH, [fwd(probe)], {"GM_HOME": fwd(home)})
    assert proc.returncode == 0, proc.stderr
    assert "no-such-interpreter" not in cache.read_text(encoding="utf-8")


def test_py_sh_without_arguments_explains_itself(home):
    proc = run_sh(PY_SH, [], {"GM_HOME": fwd(home)})
    assert proc.returncode == 1
    assert "usage: bash py.sh" in proc.stderr
    assert proc.stdout == ""
