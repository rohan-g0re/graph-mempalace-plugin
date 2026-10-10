"""kb_setup - check, and optionally install, everything the knowledge base needs.

Ten things have to be true before the plugin works. This script says which of
them are true on this machine, and `--install` makes the rest true:

    uv                   installs and isolates the two tools
    Python >= 3.11       the scripts use tomllib
    mempalace            the chat store and its MCP server
    graphify             the graph builder and its MCP server
    graphify extras      pdf, sql and mcp, or whole file types vanish silently
    mcp servers          mempalace-mcp and graphify-mcp, the two stdio servers
    mcp launcher python  the interpreter .mcp.json runs scripts/mcp_launch.py with
    mempalace config     ~/.config/mempalace/config.json, written by `mempalace init`
    graphify patch       --strict-mcp-config, worth ~11 s per extraction call
    global git excludes   graphify-out/, so no repo ever commits a graph

GM_PYTHON
---------
The MemPalace MCP server is started through `scripts/mcp_launch.py`, and
`.mcp.json` runs that launcher with `${GM_PYTHON:-python}`. A bare `python` is
not good enough: on stock macOS there is no `python` at all, only `python3`, and
on Windows `python3` is usually the Microsoft Store stub, which exits instead of
running anything. So `--install` pins the variable instead of hoping: it writes
`env.GM_PYTHON` into `$CLAUDE_CONFIG_DIR/settings.json` (`~/.claude/settings.json`
by default) with the absolute path of the interpreter running this script, which
is by definition a Python the plugin already works with. Claude Code expands
`${GM_PYTHON:-python}` from its own environment and the `env` block of
settings.json is part of that environment, so the pin reaches the spawned server.
MCP servers are started when a session starts, so the pin takes effect in the
NEXT session - every `--install` that writes it says so.

Usage
-----
    python kb_setup.py                 # the check table (default)
    python kb_setup.py --check
    python kb_setup.py --install       # fix only what is missing, then re-check
    python kb_setup.py --check --json  # {"ok":…, "checks":[…], "advisories":[…]}

`--install` never installs uv itself: uv is how everything else arrives, so if
it is absent the script prints the official one-liner for this OS and stops.
Subprocess output is streamed through so you see progress, and with `--json`
that progress goes to stderr so stdout stays one JSON document.

Exit codes: 0 when every check is OK, 1 when any is MISSING.
"""
from __future__ import annotations

import os
import sys

# gm_common first: UTF-8 streams, and the same tool discovery the hooks use.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gm_common  # noqa: E402
from gm_common import HOME, find_tool  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent

MEMPALACE_CONFIG = HOME / ".config" / "mempalace" / "config.json"
DEFAULT_GIT_EXCLUDES = HOME / ".config" / "git" / "ignore"
EXCLUDE_LINE = "graphify-out/"

# The interpreter `.mcp.json` hands scripts/mcp_launch.py, and the variable that
# pins it. See the GM_PYTHON section of the module docstring.
GM_PYTHON_ENV = "GM_PYTHON"
LAUNCHER_CHECK = "mcp launcher python"
MIN_PYTHON = (3, 11)
VERSION_PROBE = "import sys;print(sys.version_info[:2])"
LAUNCHER_FIX = "run --install (writes GM_PYTHON to settings.json)"
RESTART_LINE = "restart the session so the MCP server picks it up"

GRAPHIFY_SPEC = "graphifyy[pdf,sql,mcp]"  # double y; `graphify` is a different package
GRAPHIFY_TOOL = "graphifyy"

# The two stdio servers behind `.mcp.json`, each with the command that brings
# it: mempalace-mcp ships with mempalace, graphify-mcp with graphify's mcp extra.
MCP_SERVERS = (
    ("mempalace-mcp", "uv tool install mempalace"),
    ("graphify-mcp", f'uv tool install "{GRAPHIFY_SPEC}"'),
)

# Servers whose own name has to be findable on Claude Code's PATH. Only
# graphify-mcp is: `.mcp.json` spawns it as `${GM_GRAPHIFY_MCP_BIN:-graphify-mcp}`.
# mempalace-mcp is started by scripts/mcp_launch.py, which resolves the binary
# itself (GM_MEMPALACE_MCP_BIN, else find_tool), so PATH is not in its way.
PATH_SENSITIVE_SERVERS = ("graphify-mcp",)

UV_INSTALL_WINDOWS = 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
UV_INSTALL_POSIX = "curl -LsSf https://astral.sh/uv/install.sh | sh"

SETUP_SKILL = "/graph-mempalace:kb-setup"


def fwd(path) -> str:
    """One path with forward slashes: what JSON needs and Windows still accepts."""
    return str(path).replace("\\", "/")


def env_key_for(tool: str) -> str:
    """The GM_*_BIN variable `find_tool` and `.mcp.json` read for one tool.

    The tool's own name, upper-cased, every non-alphanumeric character an
    underscore: mempalace-mcp -> GM_MEMPALACE_MCP_BIN. The same key gm_common
    builds, so setting it once fixes both the MCP spawn and the scripts.
    """
    return "GM_" + re.sub(r"[^A-Za-z0-9]", "_", tool).upper() + "_BIN"


def uv_install_hint() -> str:
    """The official uv installer for this operating system."""
    return UV_INSTALL_WINDOWS if os.name == "nt" else UV_INSTALL_POSIX


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
@dataclass
class Check:
    """One prerequisite: its name, whether it holds, what was found, how to fix it."""

    name: str
    ok: bool
    detail: str = ""
    fix: str = ""

    def as_dict(self) -> dict:
        return {"check": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


@dataclass
class Report:
    """Every check plus the advisory lines, which never fail the run."""

    checks: list[Check] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def get(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    def missing(self, name: str) -> bool:
        c = self.get(name)
        return c is not None and not c.ok

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": [c.as_dict() for c in self.checks],
                "advisories": list(self.advisories)}


def uv_tool_dir(uv: str | None) -> Path | None:
    """Where uv keeps its tool environments. Falls back to the documented default."""
    if uv:
        # gm_common.uv_output passes --color never and strips escape codes:
        # Claude Code's shells set FORCE_COLOR, which makes uv colour the path.
        out = gm_common.uv_output([uv, "tool", "dir"], timeout=20)
        if out:
            return Path(out)
    if os.name == "nt":
        guess = HOME / "AppData" / "Roaming" / "uv" / "tools"
    else:
        guess = HOME / ".local" / "share" / "uv" / "tools"
    return guess if guess.exists() else None


def tool_python(uv: str | None, tool: str = GRAPHIFY_TOOL) -> Path | None:
    """The interpreter inside one uv tool environment, or None when it is absent.

    Windows puts it at <tool-dir>/<tool>/Scripts/python.exe, every other OS at
    <tool-dir>/<tool>/bin/python. This is the only Python that can import
    graphify, which both the extras check and the patch need.
    """
    tdir = uv_tool_dir(uv)
    if not tdir:
        return None
    cand = (tdir / tool / "Scripts" / "python.exe") if os.name == "nt" \
        else (tdir / tool / "bin" / "python")
    return cand if cand.exists() else None


def git_excludes_path() -> Path:
    """The global git excludes file: core.excludesFile if set, else ~/.config/git/ignore."""
    git = shutil.which("git")
    if git:
        try:
            out = subprocess.run([git, "config", "--global", "core.excludesFile"],
                                 capture_output=True, text=True, timeout=20)
            value = (out.stdout or "").strip()
            if out.returncode == 0 and value:
                return Path(os.path.expanduser(value))
        except Exception:
            pass
    return DEFAULT_GIT_EXCLUDES


def excludes_has_rule(path: Path) -> bool:
    """True when the excludes file already ignores graphify-out/."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.strip().rstrip("/") == EXCLUDE_LINE.rstrip("/")
               for line in text.splitlines())


def graphify_pkg_dir(tpy: Path | None) -> Path | None:
    """graphify's package directory, asked of the interpreter that can import it."""
    if not tpy:
        return None
    try:
        out = subprocess.run([str(tpy), "-c",
                              "import graphify,os;print(os.path.dirname(graphify.__file__))"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return None


def mcp_servers_check() -> Check:
    """Are both stdio servers installed?

    `.mcp.json` spawns graphify-mcp by name and mempalace-mcp through
    scripts/mcp_launch.py, which resolves the binary by absolute path itself,
    so for mempalace-mcp only the install matters and PATH does not. Either
    way each server arrives with its package, so a missing one means a missing
    or partial install and the fix is that package's install command. Resolved
    with `find_tool`, which looks where the servers actually land (a GM_*_BIN
    override, ~/.local/bin, the uv tool environments) and not only on PATH.
    """
    found: list[str] = []
    missing: list[str] = []
    fixes: list[str] = []
    for name, fix in MCP_SERVERS:
        path = find_tool(name)
        if path:
            found.append(f"{name} at {path}")
        else:
            missing.append(name)
            if fix not in fixes:
                fixes.append(fix)
    if not missing:
        return Check("mcp servers", True, "; ".join(found))
    detail = "not found: " + ", ".join(missing)
    if found:
        detail += "; " + "; ".join(found)
    return Check("mcp servers", False, detail, fix="; ".join(fixes))


def mcp_path_advisories() -> list[str]:
    """Servers that are installed but invisible to Claude Code's MCP spawn.

    `.mcp.json` spawns graphify-mcp as `${GM_GRAPHIFY_MCP_BIN:-graphify-mcp}`,
    so that bare name has to be on the PATH Claude Code itself was started with
    - which a GUI-launched editor on macOS does not have. `find_tool` looks in
    more places than PATH, so when it finds the server that `which` cannot, the
    server is installed and the spawn will still fail. Never a failure here:
    the fix is one line in ~/.claude/settings.json, printed verbatim so it can
    be pasted.

    mempalace-mcp is not checked, because PATH cannot break it: it is started
    by scripts/mcp_launch.py, which resolves the binary by absolute path
    (GM_MEMPALACE_MCP_BIN, else find_tool) before spawning it.
    """
    lines: list[str] = []
    for name, _fix in MCP_SERVERS:
        if name not in PATH_SENSITIVE_SERVERS:
            continue
        if shutil.which(name):
            continue
        path = find_tool(name)
        if not path:
            continue  # absent altogether; the `mcp servers` check says so
        lines.append(
            f"mcp on PATH: {name} is installed at {fwd(path)} but not on PATH "
            f"for Claude Code's MCP spawn; add to ~/.claude/settings.json: "
            f'"env": {{"{env_key_for(name)}": "{fwd(path)}"}}')
    return lines


# ---------------------------------------------------------------------------
# the interpreter behind the MemPalace launcher
# ---------------------------------------------------------------------------
def settings_path() -> Path:
    """The settings.json whose `env` block Claude Code expands `${GM_PYTHON}` from."""
    return gm_common.CLAUDE_CONFIG_DIR / "settings.json"


def launcher_interpreter() -> str | None:
    """What `${GM_PYTHON:-python}` resolves to today, or None when nothing does."""
    pinned = (os.environ.get(GM_PYTHON_ENV) or "").strip()
    return pinned or shutil.which("python")


def _probe_version(interp: str) -> tuple[tuple[int, int] | None, str]:
    """Ask one interpreter for `sys.version_info[:2]`. Returns (version, error)."""
    kwargs: dict = {}
    if os.name == "nt":  # a console window must never flash up during a check
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        out = subprocess.run([interp, "-c", VERSION_PROBE],
                             capture_output=True, text=True, timeout=20, **kwargs)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()
        return None, (tail[-1] if tail else f"exit {out.returncode}")
    nums = re.findall(r"\d+", out.stdout or "")
    if len(nums) < 2:
        return None, "printed no version"
    return (int(nums[0]), int(nums[1])), ""


def launcher_python_check() -> Check:
    """Can `.mcp.json` start scripts/mcp_launch.py at all?

    `.mcp.json` runs the launcher as `${GM_PYTHON:-python}`, so this check
    probes exactly that: GM_PYTHON when it is set, otherwise the `python` on
    PATH. The fallback is the whole reason the variable exists - stock macOS
    has no `python`, and on Windows the inherited `python3`/`python` can be the
    Store stub - so a MISSING row here means the MemPalace MCP server will not
    start until `--install` pins GM_PYTHON.
    """
    interp = launcher_interpreter()
    if not interp:
        return Check(LAUNCHER_CHECK, False,
                     f"not found: {GM_PYTHON_ENV} is not set and no `python` is on PATH",
                     fix=LAUNCHER_FIX)
    version, error = _probe_version(interp)
    if version is None:
        return Check(LAUNCHER_CHECK, False, f"{fwd(interp)} did not run ({error})",
                     fix=LAUNCHER_FIX)
    shown = "%d.%d" % version
    if version < MIN_PYTHON:
        return Check(LAUNCHER_CHECK, False,
                     f"{fwd(interp)} is {shown} - too old "
                     f"(need {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
                     fix=LAUNCHER_FIX)
    return Check(LAUNCHER_CHECK, True, f"{fwd(interp)} is {shown}")


def gm_python_snippet() -> str:
    """The settings.json fragment that pins GM_PYTHON, ready to paste."""
    return '"env": {"%s": "%s"}' % (GM_PYTHON_ENV, fwd(sys.executable))


def _write_json_newline(path: Path, obj) -> None:
    """Write `obj` as JSON (indent 2, trailing newline) through a temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_gm_python_setting(sink=None) -> str:
    """Pin GM_PYTHON in settings.json to the interpreter running this script.

    Reads and rewrites `$CLAUDE_CONFIG_DIR/settings.json` (`~/.claude/settings.json`
    by default), keeping every other key, and returns what it did:

        "env"         GM_PYTHON is already in the environment; nothing to write
        "present"     settings.json already pins it; another value is the user's
        "written"     env.GM_PYTHON now names this interpreter
        "unparsable"  the file is not a JSON object, so it was left untouched
        "unreadable"  the file is there and could not be read
        "failed"      the write itself failed

    Someone else's settings file is not ours to lose, so anything other than a
    JSON object is left exactly as it is and the one line to paste is printed
    instead. A write never takes effect in the running session: MCP servers are
    started at session start, so the line says to restart.
    """
    sink = sink or sys.stdout
    if (os.environ.get(GM_PYTHON_ENV) or "").strip():
        return "env"

    path = settings_path()
    data: dict = {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        print(f"\n-> {path} exists and could not be read ({exc}); "
              f"it was not changed. Add this yourself: {gm_python_snippet()}", file=sink)
        return "unreadable"
    if raw is not None and raw.strip():
        try:
            loaded = json.loads(raw)
        except ValueError:
            loaded = None
        if not isinstance(loaded, dict):
            print(f"\n-> {path} is not a JSON object, so it was left alone. "
                  f"Add this to it yourself: {gm_python_snippet()}", file=sink)
            return "unparsable"
        data = loaded

    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    if str(env.get(GM_PYTHON_ENV) or "").strip():
        return "present"

    env[GM_PYTHON_ENV] = fwd(sys.executable)
    data["env"] = env
    try:
        _write_json_newline(path, data)
    except Exception as exc:
        print(f"\n-> could not write {path} ({exc}); add this yourself: "
              f"{gm_python_snippet()}", file=sink)
        return "failed"
    print(f"\n-> wrote {GM_PYTHON_ENV}={fwd(sys.executable)} to {path} - {RESTART_LINE}",
          file=sink)
    return "written"


def collect(project_dir: str) -> Report:
    """Run every check. Reads only; writes nothing anywhere."""
    rep = Report()

    uv = find_tool("uv")
    rep.checks.append(Check(
        "uv", bool(uv), uv or "not found",
        fix=uv_install_hint()))

    ver = sys.version_info
    rep.checks.append(Check(
        "python >= 3.11", ver >= (3, 11),
        f"{sys.version.split()[0]} at {sys.executable}",
        fix="install Python 3.11 or newer, or let uv supply one: uv python install 3.12"))

    mempalace = find_tool("mempalace")
    rep.checks.append(Check(
        "mempalace", bool(mempalace), mempalace or "not found",
        fix="uv tool install mempalace"))

    graphify = find_tool("graphify")
    rep.checks.append(Check(
        "graphify", bool(graphify), graphify or "not found",
        fix=f'uv tool install "{GRAPHIFY_SPEC}"'))

    tpy = tool_python(uv)
    extras_ok = False
    extras_detail = f"{GRAPHIFY_TOOL} tool Python not found"
    if tpy:
        try:
            out = subprocess.run([str(tpy), "-c", "import mcp, tree_sitter_sql"],
                                 capture_output=True, text=True, timeout=60)
            extras_ok = out.returncode == 0
            if extras_ok:
                extras_detail = f"mcp + tree_sitter_sql import in {tpy}"
            else:
                lines = (out.stderr or "").strip().splitlines()
                extras_detail = lines[-1] if lines else "import failed"
        except Exception as exc:
            extras_detail = f"could not run {tpy}: {exc}"
    rep.checks.append(Check(
        "graphify extras (pdf, sql, mcp)", extras_ok, extras_detail,
        fix=f'uv tool install --force "{GRAPHIFY_SPEC}" then re-run the patch step'))

    rep.checks.append(mcp_servers_check())
    rep.checks.append(launcher_python_check())
    rep.advisories.extend(mcp_path_advisories())

    # Which palace this project uses, so the one question that matters after the
    # one-palace-per-project change - "where did my memory go?" - has an answer
    # in the table. Never a failure: MemPalace creates the directory on the
    # first mine, so it is absent on a project nothing has ingested yet.
    cfg = gm_common.load_config(project_dir)
    palace = gm_common.palace_path(project_dir, cfg)
    rep.advisories.append(f"palace: {palace}")

    rep.checks.append(Check(
        "mempalace config", MEMPALACE_CONFIG.exists(), str(MEMPALACE_CONFIG),
        fix=f'mempalace --palace "{palace}" init {project_dir} --yes --no-llm'))

    pkg = graphify_pkg_dir(tpy)
    llm = (pkg / "llm.py") if pkg else None
    patched = False
    patch_detail = "graphify package not found; install graphify first"
    if llm and llm.exists():
        try:
            patched = "--strict-mcp-config" in llm.read_text(encoding="utf-8", errors="replace")
            patch_detail = f"{llm}" if patched else f"{llm} has no --strict-mcp-config"
        except OSError as exc:
            patch_detail = f"could not read {llm}: {exc}"
    patch_cmd = f'"{tpy}" "{SCRIPTS_DIR / "patch_graphify.py"}"' if tpy else \
        "run patch_graphify.py with the graphifyy tool Python"
    rep.checks.append(Check(
        "graphify patch (--strict-mcp-config)", patched, patch_detail, fix=patch_cmd))

    gi = git_excludes_path()
    rep.checks.append(Check(
        "global git excludes (graphify-out/)", excludes_has_rule(gi), str(gi),
        fix=f"append {EXCLUDE_LINE} to {gi}"))

    if platform.machine().upper() in ("ARM64", "AARCH64"):
        rep.advisories.append(
            "Windows on ARM: the tool Pythons must stay x86_64, because onnxruntime "
            "has no win_arm64 wheel and ChromaDB needs it. uv does this by default."
            if os.name == "nt" else
            "ARM machine: keep the tool Pythons on the architecture uv picks by default.")

    return rep


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def print_table(rep: Report, out=None) -> None:
    """Print the check table, with the fix under every MISSING line."""
    out = out or sys.stdout
    width = max((len(c.name) for c in rep.checks), default=10)
    print("=" * 72, file=out)
    print("graph-mempalace prerequisites", file=out)
    print("=" * 72, file=out)
    for c in rep.checks:
        status = "OK     " if c.ok else "MISSING"
        print(f"  [{status}] {c.name.ljust(width)}  {c.detail}", file=out)
        if not c.ok and c.fix:
            print(f"            {' ' * width}  fix: {c.fix}", file=out)
    for line in rep.advisories:
        print(f"  [NOTE   ] {line}", file=out)
    print(file=out)
    print("  verdict:", "ready" if rep.ok else "missing pieces above", file=out)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
def stream(argv: list[str], to_stderr: bool, label: str) -> int:
    """Run one install command with its output passed through. Returns its exit code."""
    sink = sys.stderr if to_stderr else sys.stdout
    print(f"\n-> {label}", file=sink)
    print(f"   {' '.join(argv)}", file=sink)
    sink.flush()
    try:
        out = subprocess.run(argv, stdout=sink, stderr=subprocess.STDOUT)
        return out.returncode
    except Exception as exc:
        print(f"   failed to start: {exc}", file=sink)
        return 1


def do_install(rep: Report, project_dir: str, to_stderr: bool) -> None:
    """Fix only the checks that are MISSING, in dependency order."""
    sink = sys.stderr if to_stderr else sys.stdout
    uv = find_tool("uv")
    if not uv:
        print("uv is missing, and uv is how everything else installs. Install it with:",
              file=sink)
        print(f"  {uv_install_hint()}", file=sink)
        print(f"then run {SETUP_SKILL} again.", file=sink)
        return

    installed_graphify = False
    if rep.missing("mempalace"):
        stream([uv, "tool", "install", "mempalace"], to_stderr, "install mempalace")
    if rep.missing("graphify"):
        stream([uv, "tool", "install", GRAPHIFY_SPEC], to_stderr, "install graphify")
        installed_graphify = True
    elif rep.missing("graphify extras (pdf, sql, mcp)"):
        stream([uv, "tool", "install", "--force", GRAPHIFY_SPEC], to_stderr,
               "reinstall graphify with the pdf, sql and mcp extras")
        installed_graphify = True

    # The interpreter pin is independent of the two tools, but it is only worth
    # writing once they are there: it exists so .mcp.json can start the MemPalace
    # launcher, and the launcher has nothing to start until mempalace-mcp exists.
    ensure_gm_python_setting(sink)

    if rep.missing("mempalace config"):
        # `mempalace init` is KEPT, and pointed at this project's palace.
        #
        # `mempalace mine` does create a palace, so init is not what brings the
        # directory into existence any more. It is kept for the other two things
        # it alone does: it writes ~/.config/mempalace/config.json, which is the
        # `mempalace config` check and nothing else writes, and it registers this
        # project's entities so the miner can compute tunnels at mine time.
        # It goes through `mempalace_argv`, so it runs against THIS project's
        # palace: verified in mempalace 3.10.0, `cmd_init` honours the global
        # `--palace` flag by exporting MEMPALACE_PALACE_PATH before it reads the
        # config, and it does not write palace_path into config.json - so one
        # project's init cannot redirect another project's default palace.
        cfg = gm_common.load_config(project_dir)
        argv = gm_common.mempalace_argv(project_dir, cfg,
                                        "init", project_dir, "--yes", "--no-llm")
        if argv:
            stream(argv, to_stderr,
                   "write the MemPalace config and detect this project's entities")
        else:
            print("  mempalace still not found; skipping its init", file=sink)

    # A fresh install wipes the patch, so patch whenever graphify was just installed.
    if installed_graphify or rep.missing("graphify patch (--strict-mcp-config)"):
        tpy = tool_python(find_tool("uv"))
        if tpy:
            stream([str(tpy), str(SCRIPTS_DIR / "patch_graphify.py")], to_stderr,
                   "patch graphify's claude-cli call")
        else:
            print("  graphifyy tool Python not found; skipping the patch", file=sink)

    if rep.missing("global git excludes (graphify-out/)"):
        gi = git_excludes_path()
        print(f"\n-> add {EXCLUDE_LINE} to {gi}", file=sink)
        try:
            gi.parent.mkdir(parents=True, exist_ok=True)
            existing = ""
            if gi.exists():
                existing = gi.read_text(encoding="utf-8", errors="replace")
            prefix = "" if (not existing or existing.endswith("\n")) else "\n"
            with open(gi, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(f"{prefix}{EXCLUDE_LINE}\n")
            print("   done", file=sink)
        except OSError as exc:
            print(f"   could not write {gi}: {exc}", file=sink)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Check, or install then check. 0 when everything is OK, 1 otherwise."""
    ap = argparse.ArgumentParser(
        prog="kb_setup.py",
        description="Check, and optionally install, the knowledge base prerequisites.")
    ap.add_argument("--check", action="store_true",
                    help="print the check table and change nothing (the default)")
    ap.add_argument("--install", action="store_true",
                    help="install or fix only the missing pieces, then check again")
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="print the result as JSON instead of a table")
    ap.add_argument("--cwd", default=None, metavar="DIR",
                    help="the project to initialise MemPalace for (default: current directory)")
    args = ap.parse_args(argv)

    project_dir = os.path.abspath(args.cwd or os.getcwd())
    rep = collect(project_dir)

    if args.install:
        if not args.as_json:
            print_table(rep)
        do_install(rep, project_dir, to_stderr=args.as_json)
        rep = collect(project_dir)

    if args.as_json:
        print(json.dumps(rep.as_dict(), indent=2, ensure_ascii=False))
    else:
        print_table(rep)
        if args.install:
            print("  restart your Claude Code session so the MCP servers connect")

    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
