"""Stop graphify's extraction calls from loading the calling project's MCP servers.

Graphify drives extraction through `claude -p`. Every spawn otherwise re-loads
the MCP servers of whatever project you are sitting in and fires its
SessionStart hooks: 19.1 s -> 8.3 s on a trivial prompt once stopped, so roughly
11 s of pure overhead per chunk. Extraction needs no MCP tools, so we add
`--strict-mcp-config` to that one argv.

The patch edits graphify's own `llm.py` in place. It is idempotent: a second run
prints `already patched` and changes nothing. `uv tool install --force` wipes it
silently, so run it again after any graphify install or upgrade.

Run it with the graphifyy tool's OWN Python, because that is the interpreter
that can `import graphify`:

    Windows        <HOME>/AppData/Roaming/uv/tools/graphifyy/Scripts/python.exe
    macOS / Linux  <HOME>/.local/share/uv/tools/graphifyy/bin/python

    <tool-python> patch_graphify.py            # patch
    <tool-python> patch_graphify.py --check    # report only, write nothing
    python patch_graphify.py --llm-path <path> # any interpreter, explicit file

Exit codes: 0 patched, already patched, or checked. 1 when the file cannot be
found or the anchor line is not there exactly once.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Windows consoles default to cp1252. Done here rather than through gm_common
# because this script runs under graphify's own interpreter, not the plugin's,
# and it reads no plugin paths or config.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

MARKER = "--strict-mcp-config"

ANCHOR = '        "--no-session-persistence",\n'

INSERT = (
    "        # graph-mempalace: do not load the calling project's MCP servers for extraction.\n"
    "        # Opt out with GRAPHIFY_KEEP_MCP=1. `uv tool install --force` removes this patch.\n"
    '        *([] if os.environ.get("GRAPHIFY_KEEP_MCP", "").strip() == "1"\n'
    '          else ["--strict-mcp-config"]),\n'
)

# A module-level `import os`, `import os, sys` or `from os import ...`.
# Anchored at column 0, with no leading whitespace allowed: an indented
# `import os` inside a function does not bind the name at module level, and
# matching one made the patch skip the import the inserted code needs.
IMPORT_OS = re.compile(
    r"^(?:import\s+os\s*(?:#.*)?$|import\s+os\s*,|import\s+[\w.,\s]*\bos\b\s*(?:,|$)"
    r"|from\s+os\s+import\b)",
    re.M,
)
# The insertion point is a module-level import block too. Matching an indented
# import - the `import x` inside a `try:` - would put `import os` at column 0
# in the middle of that block and break the file.
IMPORT_LINE = re.compile(r"^(?:import\s|from\s)")


def locate_llm(explicit: str | None) -> str | None:
    """Absolute path of graphify's llm.py: --llm-path when given, else via import."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    try:
        import graphify  # type: ignore
    except Exception as exc:
        print(f"cannot import graphify from this interpreter ({sys.executable}): {exc}",
              file=sys.stderr)
        print("run this with the graphifyy tool's own Python, or pass "
              "--llm-path <path-to-graphify/llm.py>", file=sys.stderr)
        return None
    pkg = os.path.dirname(os.path.abspath(graphify.__file__ or ""))
    return os.path.join(pkg, "llm.py")


def ensure_import_os(text: str) -> str:
    """Return the text with `import os` added after the first import block if it lacks one."""
    if IMPORT_OS.search(text):
        return text
    lines = text.splitlines(keepends=True)
    first = next((i for i, line in enumerate(lines) if IMPORT_LINE.match(line)), None)
    if first is None:
        return "import os\n" + text
    end = first
    while end < len(lines) and IMPORT_LINE.match(lines[end]):
        end += 1
    lines.insert(end, "import os\n")
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Patch, or report on, graphify's llm.py. Exit codes are in the module docstring."""
    ap = argparse.ArgumentParser(
        prog="patch_graphify.py",
        description="Add --strict-mcp-config to graphify's claude-cli extraction call.")
    ap.add_argument("--llm-path", default=None, metavar="PATH",
                    help="graphify's llm.py (default: found by importing graphify)")
    ap.add_argument("--check", action="store_true",
                    help="report patched or unpatched and write nothing")
    args = ap.parse_args(argv)

    path = locate_llm(args.llm_path)
    if not path:
        return 1
    if not os.path.isfile(path):
        print(f"not a file: {path}", file=sys.stderr)
        return 1

    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    if MARKER in text:
        print(f"already patched: {path}")
        return 0

    if args.check:
        print(f"unpatched: {path}")
        return 0

    found = text.count(ANCHOR)
    if found != 1:
        print(f"anchor not unique in {path}: found {found} occurrences of the line "
              '\'        "--no-session-persistence",\' and need exactly 1; patch by hand',
              file=sys.stderr)
        return 1

    patched = ensure_import_os(text.replace(ANCHOR, ANCHOR + INSERT))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(patched)
    print(f"patched: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
