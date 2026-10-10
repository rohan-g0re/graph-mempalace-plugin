"""gm_common - the one place graph-mempalace decides paths, names, config and state.

Every other script imports this so the SessionStart hook, the Stop gate, the
compound chain and the resolver agree by construction on:

  * where Claude Code keeps this project's transcripts
  * where the filtered exchanges and Graph B live
  * which MemPalace wing the project's exchanges are filed in
  * which configuration values are in force
  * where the lock, the watermark and the log are

Standard library only. Python 3.11+ (tomllib). Reads and writes only under
~/.graph-mempalace/ except where a function's docstring says otherwise.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = "graph-mempalace"

# ---------------------------------------------------------------------------
# console encoding - Windows consoles default to cp1252; exchange text is UTF-8
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - never let encoding setup fail a hook
            pass

# ---------------------------------------------------------------------------
# state locations (all under one directory the user can find and delete)
# ---------------------------------------------------------------------------
HOME = Path(os.environ.get("GM_HOME") or Path.home())
STATE_DIR = HOME / ".graph-mempalace"
CONFIG_PATH = STATE_DIR / "config.toml"
LOG_PATH = STATE_DIR / "compound.log"
LOCK_PATH = STATE_DIR / "compound.lock"
WATERMARK_PATH = STATE_DIR / "watermark.json"
WINGS_PATH = STATE_DIR / "wings.json"
PYTHON_CACHE_PATH = STATE_DIR / "python_path"
TOOLS_CACHE_PATH = STATE_DIR / "tools.json"
PROJECTS_DIR = STATE_DIR / "projects"
# Claude Code keeps transcripts under its config dir, which CLAUDE_CONFIG_DIR can move.
CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude"))
CLAUDE_PROJECTS_DIR = CLAUDE_CONFIG_DIR / "projects"

SCRIPTS_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, dict[str, object]] = {
    "compound": {
        "threshold_tokens": 100_000,
        "require_settled": True,
        "on_first_sight": True,
        "on_precompact": True,
        "lock_stale_hours": 3.0,  # float: fractions are allowed, 0 disables the stale check
    },
    "graph_a": {
        "mode": "code-only",  # "code-only" | "full"
        "cluster": False,
    },
    "graph_b": {
        "model": "sonnet",
        "concurrency": 2,
        "token_budget": 15_000,
        "passes": 2,
        "cluster": True,
    },
    "palace": {
        "wing": "",
        "exchanges_dir": "",
    },
    "session": {
        "inject_instructions": True,
        "show_status": True,
    },
}

PROJECT_CONFIG_NAME = ".graph-mempalace.toml"


_WARNED: set[str] = set()


def _warn(msg: str) -> None:
    """Configuration problems go to stderr (never stdout, which is model-facing).

    Each distinct message is printed once per process, so a typo-ridden config
    cannot spam a hook's stderr on every turn.
    """
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    print(f"{APP}: {msg}", file=sys.stderr)


def _read_toml(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        _warn(f"Python {sys.version.split()[0]} has no tomllib; config at {path} ignored. Use Python 3.11+.")
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # malformed TOML must not take a hook down
        _warn(f"could not parse {path}: {exc}; using defaults")
        return {}


def _merge(base: dict, override: dict, origin: str) -> None:
    """Merge override into base in place, keeping types; warn on unknown keys and bad types."""
    for section, values in override.items():
        if section not in base:
            _warn(f"{origin}: unknown section [{section}] ignored")
            continue
        if not isinstance(values, dict):
            _warn(f"{origin}: [{section}] must be a table; ignored")
            continue
        for key, val in values.items():
            if key not in base[section]:
                _warn(f"{origin}: unknown key {section}.{key} ignored")
                continue
            want = type(base[section][key])
            ok = isinstance(val, want) and not (want is int and isinstance(val, bool))
            if want is int and isinstance(val, float) and val.is_integer():
                val, ok = int(val), True
            if want is float and isinstance(val, int) and not isinstance(val, bool):
                val, ok = float(val), True  # TOML `3` for a float key such as lock_stale_hours
            if not ok:
                _warn(f"{origin}: {section}.{key} should be {want.__name__}; keeping default")
                continue
            base[section][key] = val


WING_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Sections a repository's own .graph-mempalace.toml may NOT set. A cloned repo
# must never be able to choose where this plugin writes or which wing it mines
# into; those belong to the user's own ~/.graph-mempalace/config.toml.
USER_ONLY_SECTIONS = ("palace",)


def project_config_path(project_dir: str | os.PathLike | None = None) -> Path:
    """The user-side, per-project override: ~/.graph-mempalace/projects/<slug>/config.toml.

    It lives outside the repository, so it may set everything, including
    `[palace]`. This is where a project adopts an existing wing or exchanges
    folder (for example when migrating from the manual setup).
    """
    return project_state_dir(project_dir) / "config.toml"


def load_config(project_dir: str | os.PathLike | None = None) -> dict:
    """Layered config, later layers win:

        defaults
        <- ~/.graph-mempalace/config.toml                    (user, machine-wide)
        <- ~/.graph-mempalace/projects/<slug>/config.toml    (user, this project)
        <- <project>/.graph-mempalace.toml                   (repo; may NOT set [palace])

    Reads three files at most. Never raises. Returns a fresh dict each call. The
    repository file may not set `[palace]` (write locations and wing names); a
    repository you cloned must not decide where the plugin writes.
    """
    cfg = {s: dict(v) for s, v in DEFAULTS.items()}
    _merge(cfg, _read_toml(CONFIG_PATH), str(CONFIG_PATH))
    if project_dir is not None:
        up = project_config_path(project_dir)
        if up.exists():
            _merge(cfg, _read_toml(up), str(up))
        p = Path(project_dir) / PROJECT_CONFIG_NAME
        if p.exists():
            proj = _read_toml(p)
            for section in USER_ONLY_SECTIONS:
                if section in proj:
                    _warn(f"{p}: [{section}] is read from {CONFIG_PATH} only; ignored here")
                    proj.pop(section, None)
            _merge(cfg, proj, str(p))
    if cfg["graph_a"]["mode"] not in ("code-only", "full"):
        _warn("graph_a.mode must be 'code-only' or 'full'; keeping default")
        cfg["graph_a"]["mode"] = "code-only"
    wing = str(cfg["palace"]["wing"]).strip()
    if wing and not WING_RE.match(wing):
        _warn("palace.wing must match [A-Za-z0-9._-]{1,64}; deriving the wing instead")
        cfg["palace"]["wing"] = ""
    xdir = str(cfg["palace"]["exchanges_dir"]).strip()
    if xdir:
        expanded = Path(os.path.expanduser(xdir))
        if not expanded.is_absolute():
            _warn("palace.exchanges_dir must be an absolute path; using the default")
            cfg["palace"]["exchanges_dir"] = ""
        else:
            cfg["palace"]["exchanges_dir"] = str(expanded)
    return cfg


# ---------------------------------------------------------------------------
# project identity
# ---------------------------------------------------------------------------
def project_token(project_dir: str | os.PathLike | None = None) -> str:
    """Lower-case directory name reduced to [a-z0-9._-]; 'project' if nothing survives."""
    base = Path(project_dir or os.getcwd()).resolve().name.lower()
    tok = "".join(c for c in base if c.isalnum() or c in "._-")
    return tok or "project"


def slug_for(project_dir: str | os.PathLike | None = None) -> str:
    """Claude Code's folder name for a project under ~/.claude/projects.

    One dash per non-alphanumeric character of the absolute path; on Windows the
    drive letter is lower-cased. C:/x/y -> c--x-y ; /Users/x -> -Users-x
    """
    p = str(Path(project_dir or os.getcwd()).resolve())
    if p[1:2] == ":":
        p = p[0].lower() + p[1:]
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def transcripts_dir(project_dir: str | os.PathLike | None = None) -> Path:
    """Where Claude Code writes this project's session .jsonl files."""
    return CLAUDE_PROJECTS_DIR / slug_for(project_dir)


def project_state_dir(project_dir: str | os.PathLike | None = None) -> Path:
    return PROJECTS_DIR / slug_for(project_dir)


def exchanges_dir(project_dir: str | os.PathLike | None = None, cfg: dict | None = None) -> Path:
    """Filtered exchanges + Graph B. palace.exchanges_dir overrides the default."""
    cfg = cfg or load_config(project_dir)
    override = str(cfg["palace"]["exchanges_dir"]).strip()
    if override:
        return Path(os.path.expanduser(override))
    return project_state_dir(project_dir) / "exchanges"


def _hash6(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


def wing_for(project_dir: str | os.PathLike | None = None, cfg: dict | None = None,
             record: bool = True) -> str:
    """MemPalace wing for this project's exchanges.

    palace.wing overrides. Otherwise <token>_dialogue, unless ~/.graph-mempalace/
    wings.json shows that name already belongs to a different project, in which
    case <token>_<hash6>_dialogue. With record=True (the writers: the compound
    chain) the chosen name is recorded in wings.json; read-only callers such as
    the resolver pass record=False and never write.
    """
    cfg = cfg or load_config(project_dir)
    override = str(cfg["palace"]["wing"]).strip()
    if override:
        return override
    slug = slug_for(project_dir)
    token = project_token(project_dir)
    wings = read_json(WINGS_PATH, {})
    plain = f"{token}_dialogue"
    owner = wings.get(plain)
    if owner in (None, slug):
        if owner is None and record:
            wings[plain] = slug
            write_json_atomic(WINGS_PATH, wings)
        return plain
    hashed = f"{token}_{_hash6(slug)}_dialogue"
    if wings.get(hashed) != slug and record:
        wings[hashed] = slug
        write_json_atomic(WINGS_PATH, wings)
    return hashed


def graph_a_path(project_dir: str | os.PathLike | None = None) -> Path:
    return Path(project_dir or os.getcwd()).resolve() / "graphify-out" / "graph.json"


def graph_b_path(project_dir: str | os.PathLike | None = None, cfg: dict | None = None) -> Path:
    return exchanges_dir(project_dir, cfg) / "graphify-out" / "graph.json"


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
_UV_TOOL_BIN_CACHE: list = []  # [] = not asked yet; [None] or [Path]
_FIND_TOOL_CACHE: dict[str, str | None] = {}


def _uv_bin() -> str | None:
    """The uv executable: ~/.local/bin first (hooks may lack PATH), then PATH."""
    for cand in (HOME / ".local" / "bin" / "uv.exe", HOME / ".local" / "bin" / "uv"):
        if cand.exists():
            return str(cand)
    return shutil.which("uv")


def _uv_tool_bin() -> Path | None:
    """`uv tool dir --bin`, asked at most once per process (it is a subprocess)."""
    if _UV_TOOL_BIN_CACHE:
        return _UV_TOOL_BIN_CACHE[0]
    result: Path | None = None
    uv = _uv_bin()
    if uv:
        try:
            out = uv_output([uv, "tool", "dir", "--bin"])
            if out:
                result = Path(out)
        except Exception:
            result = None
    _UV_TOOL_BIN_CACHE.append(result)
    return result


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def uv_output(argv: list[str], timeout: float = 10) -> str:
    """Run a uv query and return its stdout stripped of colour codes and whitespace.

    uv colours its output whenever FORCE_COLOR or CLICOLOR_FORCE is set, which
    Claude Code's tool shells do, so a captured path can arrive wrapped in
    escape sequences. Pass --color never, set NO_COLOR, and strip anyway.
    Returns "" on any failure.
    """
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env.pop("FORCE_COLOR", None)
    env.pop("CLICOLOR_FORCE", None)
    argv = [argv[0], "--color", "never", *argv[1:]]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return _ANSI_RE.sub("", out.stdout or "").strip()


def _uv_tool_env_for(name: str) -> str | None:
    """Which uv tool environment ships an executable: mempalace* -> mempalace, graphify* -> graphifyy."""
    if name.startswith("mempalace"):
        return "mempalace"
    if name.startswith("graphify"):
        return "graphifyy"
    return None


def _tool_cache_get(name: str) -> str | None:
    """A path remembered from an earlier run, if it still exists."""
    cached = read_json(TOOLS_CACHE_PATH, {})
    p = cached.get(name) if isinstance(cached, dict) else None
    return p if isinstance(p, str) and Path(p).exists() else None


def _tool_cache_put(name: str, path: str) -> None:
    try:
        cached = read_json(TOOLS_CACHE_PATH, {})
        if not isinstance(cached, dict):
            cached = {}
        if cached.get(name) != path:
            cached[name] = path
            write_json_atomic(TOOLS_CACHE_PATH, cached)
    except Exception:
        pass  # a cache that cannot be written is only a slower cache


def find_tool(name: str) -> str | None:
    """Absolute path of a CLI tool (mempalace, graphify, mempalace-mcp, ...).

    Order: $GM_<NAME>_BIN; the path remembered in ~/.graph-mempalace/tools.json
    from a previous run (if it still exists); ~/.local/bin/<name>[.exe]; the uv
    tool environment's own Scripts/ or bin/ directory; `uv tool dir --bin`
    (a subprocess, asked at most once per process); PATH. Hooks do not inherit
    an interactive PATH, so explicit locations come first. Returns None when
    nothing is found. The subprocess rung is what the file cache exists to skip
    on the SessionStart path.
    """
    if name in _FIND_TOOL_CACHE:
        return _FIND_TOOL_CACHE[name]
    found: str | None = None
    env_key = "GM_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_BIN"
    env = os.environ.get(env_key)
    if env and Path(env).exists():
        found = env
    if not found:
        found = _tool_cache_get(name)
    if not found:
        exts = (".exe", "") if os.name == "nt" else ("",)
        candidates = [HOME / ".local" / "bin"]
        tool_env = _uv_tool_env_for(name)
        if tool_env:
            candidates.append(HOME / "AppData" / "Roaming" / "uv" / "tools" / tool_env / "Scripts")
            candidates.append(HOME / ".local" / "share" / "uv" / "tools" / tool_env / "bin")
        for d in candidates:
            for ext in exts:
                p = d / f"{name}{ext}"
                if p.exists():
                    found = str(p)
                    break
            if found:
                break
        if not found:
            uvbin = _uv_tool_bin()
            if uvbin and uvbin not in candidates:
                for ext in exts:
                    p = uvbin / f"{name}{ext}"
                    if p.exists():
                        found = str(p)
                        break
        if not found:
            found = shutil.which(name)
        if found and not env:
            _tool_cache_put(name, found)
    _FIND_TOOL_CACHE[name] = found
    return found


def tool_env(cfg: dict | None = None) -> dict:
    """Environment for graphify runs: claude-cli backend tuned, no skill auto-refresh."""
    cfg = cfg or load_config()
    env = dict(os.environ)
    env.setdefault("GRAPHIFY_NO_AUTO_REFRESH", "1")
    env.setdefault("GRAPHIFY_CLAUDE_CLI_PARALLEL", "1")
    env.setdefault("GRAPHIFY_CLAUDE_CLI_MODEL", str(cfg["graph_b"]["model"]))
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------
def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, obj) -> None:
    """Write via a temp file and os.replace so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_log(line: str) -> None:
    """One timestamped line to ~/.graph-mempalace/compound.log. Never raises."""
    try:
        ensure_state_dir()
        with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"{now_stamp()} {line.rstrip()}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# lock - one compound at a time, machine-wide
# ---------------------------------------------------------------------------
def lock_held(stale_hours: float = 3.0) -> bool:
    """True while a fresh lock exists. A stale lock (older than stale_hours) is removed."""
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
    except OSError:
        return False
    if age > stale_hours * 3600:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
        return False
    return True


def lock_acquire(stale_hours: float = 3.0) -> bool:
    """Take the lock if free. Returns False if another compound holds it.

    The holder's pid is written into the file so that only the holder can
    release it, and the holder refreshes the file's mtime between steps
    (`lock_touch`) so a long but live compound is never mistaken for a stale one.
    """
    ensure_state_dir()
    if lock_held(stale_hours):
        return False
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"{now_stamp()} pid={os.getpid()}\n")
    return True


def lock_owner_pid() -> int | None:
    """The pid recorded in the lock file, or None."""
    try:
        text = LOCK_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"pid=(\d+)", text)
    return int(m.group(1)) if m else None


def lock_touch() -> None:
    """Heartbeat: refresh the lock's mtime so `lock_held` keeps treating it as live."""
    try:
        os.utime(LOCK_PATH, None)
    except OSError:
        pass


def lock_release(force: bool = False) -> None:
    """Release the lock - only if this process owns it, unless force=True.

    Without the owner check, a chain whose lock was removed as stale and then
    re-taken by a second chain would delete the second chain's lock on exit.
    """
    if not force:
        owner = lock_owner_pid()
        if owner is not None and owner != os.getpid():
            return
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# detached processes
# ---------------------------------------------------------------------------
def spawn_detached(argv: list[str], log_path: Path | None = None, cwd: str | None = None,
                   env: dict | None = None) -> int | None:
    """Start argv so it outlives the hook that launched it. Returns the pid or None.

    Windows: DETACHED_PROCESS | CREATE_NO_WINDOW. POSIX: a new session. stdout and
    stderr go to log_path (append) so nothing reaches the hook's stdout.
    """
    log_path = log_path or LOG_PATH
    ensure_state_dir()
    try:
        logf = open(log_path, "a", encoding="utf-8", errors="replace")
    except OSError:
        logf = subprocess.DEVNULL
    kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                        cwd=cwd, env=env, close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kwargs)
        return proc.pid
    except Exception as exc:
        append_log(f"spawn failed: {exc}: {argv}")
        return None
    finally:
        if logf is not subprocess.DEVNULL:
            try:
                logf.close()  # the child holds its own handle
            except Exception:
                pass


def python_exe() -> str:
    """The interpreter running this script - what detached children should use too."""
    return sys.executable or "python"
