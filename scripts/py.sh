#!/bin/sh
# py.sh - find a Python 3.11 or newer, then run a bundled script with it.
#
#   bash py.sh /path/to/script.py [args...]
#
# Hooks fire on every turn, so this has to be cheap, and it has to work on
# machines where the obvious lookup fails:
#
#   * macOS has no `python`, only `python3`.
#   * Windows ships a `python3` stub that advertises the Store and exits 9009.
#   * Git Bash under Claude Code can inherit a WINDOWS-format PATH
#     (semicolons, backslashes, drive letters). `command -v python` then finds
#     nothing at all even though Python is installed. Absolute candidates are
#     the only reliable answer, which is why most of this file is a list of
#     places interpreters actually live.
#   * An interpreter's path can contain SPACES, as a per-user install under
#     C:/Users/John Smith/AppData/Local/Programs/Python/Python312/ does. So the
#     command is kept in two parts: $GM_PY is one word and is always quoted,
#     $GM_PYARG holds extra arguments (`-3` for the Windows launcher, the whole
#     `run --no-project ...` line for uv) and is deliberately left unquoted.
#   * /usr/bin is sometimes missing from that same PATH, so this script uses
#     shell builtins only. No mkdir, no tr, no sed. The cache file is written
#     by the interpreter that just passed the probe.
#
# Search order, first candidate that reports version >= 3.11 wins:
#
#   1. $GM_PYTHON                                     explicit override
#   2. ${GM_HOME:-$HOME}/.graph-mempalace/python_path  what won last time
#   3. PATH names: python, py -3, python3 on Windows; python3, python elsewhere
#   4. uv:  ~/.local/bin/uv[.exe], then uv on PATH
#           -> uv run --no-project --python >=3.11 python
#   5. absolute interpreters: /c/Windows/py.exe -3, the Python installer's
#      per-user directory, uv's downloaded builds, /usr/bin, /usr/local/bin,
#      /opt/homebrew/bin
#
# The winner is cached as TWO LINES: the interpreter on the first, its extra
# arguments (empty line when there are none) on the second. Two lines, rather
# than one string, is what lets a path with spaces survive the round trip. A
# one-line cache written by an older version still reads correctly - the second
# `read` just leaves the arguments empty - so a bare `python` or an absolute
# path is used as it is, and only a one-line cache that carried arguments (or
# an interpreter that has since moved) fails the probe and is re-probed.
#
# When nothing works this prints ONE line to stderr and exits 0: a hook must
# never block a session, whatever is wrong with the machine.
#
# POSIX sh. LF line endings. Never relies on an exec bit or a shebang.

GM_STATE="${GM_HOME:-$HOME}/.graph-mempalace"
GM_CACHE="$GM_STATE/python_path"
GM_PROBE='import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'

if [ "$#" -eq 0 ]; then
    printf '%s\n' 'graph-mempalace: usage: bash py.sh <script.py> [args...]' >&2
    exit 1
fi

GM_SCRIPT="$1"
shift

# After `/plugin update` an already-open session still points at the PREVIOUS
# ${CLAUDE_PLUGIN_ROOT} (the version is a path segment), whose scripts/ no
# longer exists. Python exits 2 on a missing file, and exit 2 from a Stop hook
# BLOCKS the turn, so the launcher reports the real problem and exits 0. Checked
# before the interpreter probe: there is nothing to run either way.
if [ ! -f "$GM_SCRIPT" ]; then
    printf 'graph-mempalace: %s missing (plugin updated?); restart the session\n' "$GM_SCRIPT" >&2
    exit 0
fi

# Accept a candidate only when it really is Python 3.11+. Every byte of output
# is discarded: the Windows Store stub prints an advert, and a hook's stdout is
# injected into the session.
gm_try() {
    [ -n "${1:-}" ] || return 1
    "$@" -c "$GM_PROBE" >/dev/null 2>&1
}

# The same probe for a candidate that is already split the way it will be run:
# $1 is the interpreter (quoted, so spaces in it are safe), $2 is the extra
# arguments as one string (unquoted, so it word-splits - that is the point).
gm_try_pair() {
    [ -n "${1:-}" ] || return 1
    # shellcheck disable=SC2086
    "$1" ${2:-} -c "$GM_PROBE" >/dev/null 2>&1
}

# Split "py -3" into GM_SPLIT_CMD=py and GM_SPLIT_ARG=-3, for the candidates
# that carry their own arguments. No arguments leaves GM_SPLIT_ARG empty.
gm_split() {
    GM_SPLIT_CMD="${1%% *}"
    GM_SPLIT_ARG="${1#* }"
    if [ "$GM_SPLIT_ARG" = "$1" ]; then
        GM_SPLIT_ARG=""
    fi
}

# The interpreter that just passed the probe writes its own cache entry: mkdir
# may not be on this PATH, but Python is, by definition, right here.
gm_cache_write() {
    # shellcheck disable=SC2086
    "$GM_PY" $GM_PYARG -c 'import os, sys
path, py, arg = sys.argv[1], sys.argv[2], sys.argv[3]
parent = os.path.dirname(path)
if parent:
    os.makedirs(parent, exist_ok=True)
with open(path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(py + "\n" + arg + "\n")
' "$GM_CACHE" "$GM_PY" "$GM_PYARG" >/dev/null 2>&1 || true
}

gm_is_windows() {
    case "${OSTYPE:-}" in
        msys* | cygwin* | win32*) return 0 ;;
    esac
    for gm_u in /usr/bin/uname /bin/uname uname; do
        case "$("$gm_u" -s 2>/dev/null)" in
            MINGW* | MSYS* | CYGWIN*) return 0 ;;
            ?*) return 1 ;;
        esac
    done
    return 1
}

# $LOCALAPPDATA is a Windows path; cygpath converts it, but cygpath is not
# always present. $HOME/AppData/Local is the same directory in practice.
gm_localappdata() {
    if [ -n "${LOCALAPPDATA:-}" ] && command -v cygpath >/dev/null 2>&1; then
        gm_conv="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null)"
        if [ -n "$gm_conv" ]; then
            printf '%s\n' "$gm_conv"
            return 0
        fi
    fi
    printf '%s\n' "$HOME/AppData/Local"
}

GM_PY=""
GM_PYARG=""
GM_FROM_CACHE=""

# 1. explicit override. A bare path wins first, so an override that contains
#    spaces works; only then is it re-read as "command arguments" for the
#    GM_PYTHON="py -3" shape.
if [ -n "${GM_PYTHON:-}" ]; then
    if gm_try_pair "$GM_PYTHON" ""; then
        GM_PY="$GM_PYTHON"
    else
        gm_split "$GM_PYTHON"
        if [ -n "$GM_SPLIT_ARG" ] && gm_try_pair "$GM_SPLIT_CMD" "$GM_SPLIT_ARG"; then
            GM_PY="$GM_SPLIT_CMD"
            GM_PYARG="$GM_SPLIT_ARG"
        fi
    fi
fi

# 2. what won last time. Two reads, so an interpreter path with spaces comes
#    back as one word. `read` returns non-zero at EOF but still assigns, so its
#    status is deliberately ignored; a one-line cache leaves GM_C_ARG empty.
if [ -z "$GM_PY" ] && [ -f "$GM_CACHE" ]; then
    GM_C_PY=""
    GM_C_ARG=""
    { read -r GM_C_PY; read -r GM_C_ARG; } < "$GM_CACHE" 2>/dev/null || true
    if gm_try_pair "$GM_C_PY" "$GM_C_ARG"; then
        GM_PY="$GM_C_PY"
        GM_PYARG="$GM_C_ARG"
        GM_FROM_CACHE=1
    fi
fi

# 3. names on PATH. Windows first tries `python`: there `python3` is usually
#    the Store stub, and `py -3` costs a launcher hop.
if [ -z "$GM_PY" ]; then
    if gm_is_windows; then
        GM_NAMES="python|py -3|python3"
    else
        GM_NAMES="python3|python"
    fi
    while [ -n "$GM_NAMES" ]; do
        GM_ONE="${GM_NAMES%%|*}"
        case "$GM_NAMES" in
            *\|*) GM_NAMES="${GM_NAMES#*|}" ;;
            *) GM_NAMES="" ;;
        esac
        gm_split "$GM_ONE"
        if gm_try_pair "$GM_SPLIT_CMD" "$GM_SPLIT_ARG"; then
            # Cache the absolute path, not the bare name: a later hook whose
            # PATH is empty or Windows-format still finds the same interpreter
            # instead of falling through to uv on every turn.
            GM_ABS="$(command -v "$GM_SPLIT_CMD" 2>/dev/null)"
            case "$GM_ABS" in
                /*) GM_PY="$GM_ABS" ;;
                *) GM_PY="$GM_SPLIT_CMD" ;;
            esac
            GM_PYARG="$GM_SPLIT_ARG"
            break
        fi
    done
fi

# 4. uv, by absolute path first. It downloads a private 3.11+ on demand, so it
#    works on a machine with no system Python at all. Costs ~700 ms per call.
if [ -z "$GM_PY" ]; then
    for gm_uv in "$HOME/.local/bin/uv" "$HOME/.local/bin/uv.exe"; do
        [ -f "$gm_uv" ] || continue
        if gm_try "$gm_uv" run --no-project --python ">=3.11" python; then
            GM_PY="$gm_uv"
            GM_PYARG="run --no-project --python >=3.11 python"
            break
        fi
    done
fi
if [ -z "$GM_PY" ] && command -v uv >/dev/null 2>&1; then
    if gm_try uv run --no-project --python ">=3.11" python; then
        GM_PY="uv"
        GM_PYARG="run --no-project --python >=3.11 python"
    fi
fi

# 5a. the Windows launcher, at its fixed location
if [ -z "$GM_PY" ] && [ -f /c/Windows/py.exe ]; then
    if gm_try /c/Windows/py.exe -3; then
        GM_PY="/c/Windows/py.exe"
        GM_PYARG="-3"
    fi
fi

# 5b. absolute interpreters, including the ones uv downloads. Unmatched globs
#     stay literal, which the -f test filters out.
if [ -z "$GM_PY" ]; then
    GM_LA="$(gm_localappdata)"
    for gm_p in \
        "$GM_LA"/Programs/Python/Python3*/python.exe \
        "$HOME"/AppData/Roaming/uv/python/*/python.exe \
        "$HOME"/.local/share/uv/python/*/bin/python3 \
        /usr/bin/python3 \
        /usr/local/bin/python3 \
        /opt/homebrew/bin/python3; do
        [ -f "$gm_p" ] || continue
        if gm_try "$gm_p"; then
            GM_PY="$gm_p"
            GM_PYARG=""   # set in step with GM_PY, like every other rung
            break
        fi
    done
fi

if [ -z "$GM_PY" ]; then
    printf '%s\n' 'graph-mempalace: no Python >= 3.11 found; run /graph-mempalace:kb-setup' >&2
    exit 0
fi

if [ -z "$GM_FROM_CACHE" ]; then
    gm_cache_write
fi

# shellcheck disable=SC2086
exec "$GM_PY" $GM_PYARG "$GM_SCRIPT" "$@"
