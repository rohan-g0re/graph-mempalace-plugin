"""kb_resolve - the wiring between the graphs and the stores.

The problem this solves
-----------------------
A Graphify node tells you WHAT is relevant. It does not tell you what was
actually said or written. Every production system solves this with a stored
back-pointer from the node to its source record:

    Graphiti   EntityEdge.episodes -> EpisodicNode.content
    GraphRAG   Entity.text_unit_ids -> TextUnit.text
    session-graph  devkg:extractedFrom -> message, sioc:content

We had the graphs and the stores but no pointer between them. This is it.

The architecture it implements
------------------------------
Two domains, each with a navigation layer and a ground-truth layer:

    code   Graph A  ->  file:line      ->  the repository    (always current)
    chat   Graph B  ->  exchange id    ->  the exchange file / MemPalace drawer

Code needs no database: the repo IS the store, and it never goes stale.
Chat has no readable store of its own, which is why MemPalace exists.

Resolution path
---------------
    question
      -> graphify query   (BFS over the graph; decides WHAT to look at)
      -> node.source_file (the stored back-pointer)
      -> _index.jsonl     (id -> file, for chat)
      -> verbatim text    (the exchange file, or the source file for code)

Usage
-----
    kb_resolve.py ask "<question>"        # chat graph, then verbatim exchanges
    kb_resolve.py ask "<question>" --code # code graph, then source files
    kb_resolve.py ask "<question>" --both
    kb_resolve.py get <exchange-id>       # dereference one id, e.g. b49fc7be-0007
    kb_resolve.py search "<text>"         # semantic search via MemPalace, ids back
    kb_resolve.py doctor                  # verify every link in the chain

Every command takes `--cwd <dir>` to work on a project other than the current
directory. `doctor --json` prints the checks as machine-readable JSON.

Paths, the palace wing and the tool binaries all come from gm_common, so this
resolver, the SessionStart hook, the gate and the compound chain cannot drift
apart.

One palace per project
----------------------
Every MemPalace command here is built by `gm_common.mempalace_argv`, which puts
`--palace <this project's palace>` in front of the subcommand. A search, the
knowledge graph and the cross-wing tunnels MemPalace digs after every mine are
all palace-wide, so a call that forgot `--palace` would read the shared default
palace and answer this project's question out of another project's memory. The
only call that deliberately looks outside is in `doctor`: when this project's
wing is missing it asks the plain `mempalace status` once, so it can say
"your wing is still in the shared 0.1.0 palace" instead of "your wing is gone".

Nothing here writes to a store. Reading the wing name may record the derived
name in ~/.graph-mempalace/wings.json; nothing else is written.
"""
from __future__ import annotations

import os
import sys

# gm_common first: it reconfigures stdout/stderr to UTF-8 (Windows consoles
# default to cp1252 and exchange text is full of arrows and em dashes).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gm_common  # noqa: E402
from gm_common import (  # noqa: E402
    exchanges_dir,
    find_tool,
    graph_a_path,
    graph_b_path,
    load_config,
    mempalace_argv,
    palace_path,
    wing_for,
)

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import textwrap  # noqa: E402


# ------------------------------------------------------------------- helpers

def _dialogue_dir(cwd: str) -> str:
    """Where the filtered exchanges and Graph B live for this project."""
    return str(exchanges_dir(cwd, load_config(cwd)))


def load_index(cwd: str) -> dict[str, dict]:
    """id -> row, from the filter's _index.jsonl. Empty dict when it is missing."""
    idx: dict[str, dict] = {}
    p = os.path.join(_dialogue_dir(cwd), "_index.jsonl")
    if not os.path.exists(p):
        return idx
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        idx[row["id"]] = row
    return idx


def run(cmd: list[str], timeout: int = 180) -> str:
    """Run a tool and return its stdout. Never raises; failures come back as text."""
    env = dict(os.environ)
    env.setdefault("GRAPHIFY_NO_AUTO_REFRESH", "1")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", env=env)
        return p.stdout or ""
    except Exception as e:
        return f"[kb_resolve] command failed: {e}\n"


# ---------------------------------------------------------------- id handling

EXCHANGE_ID = re.compile(r"\b([0-9a-f]{8}-\d{4})\b")


def ids_from_graph_output(text: str) -> list[str]:
    """Exchange ids referenced by graphify's node lines (src=<file>)."""
    out: list[str] = []
    for m in re.finditer(r"src=([^\s\]]+)", text):
        hit = EXCHANGE_ID.search(m.group(1))
        if hit and hit.group(1) not in out:
            out.append(hit.group(1))
    for m in EXCHANGE_ID.finditer(text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def files_from_graph_output(text: str) -> list[str]:
    """Repo-relative source files referenced by code-graph node lines."""
    out: list[str] = []
    for m in re.finditer(r"src=([^\s\]]+)", text):
        f = m.group(1)
        if EXCHANGE_ID.search(f):
            continue
        if f not in out:
            out.append(f)
    return out


# ------------------------------------------------------------------ commands

# The two fixes every failure here resolves to: a missing tool is a setup
# problem, an unbuilt palace or an unmined wing is a compound problem.
SETUP_FIX = "/graph-mempalace:kb-setup"
COMPOUND_FIX = "/graph-mempalace:kb-compound"


def cmd_get(exchange_id: str, cwd: str, chars: int = 4000) -> int:
    """Print one exchange in full from its stable id."""
    dd = _dialogue_dir(cwd)
    idx = load_index(cwd)
    row = idx.get(exchange_id)
    if not row:
        print(f"no exchange {exchange_id} in the index "
              f"({os.path.join(dd, '_index.jsonl')})")
        return 0
    path = os.path.join(dd, row["file"])
    print(f"=== {exchange_id}  {row['date']}  turns={row['turns']} ===")
    print(f"file: {path}")
    print(f"topic: {row['topic']}\n")
    if os.path.exists(path):
        body = open(path, encoding="utf-8", errors="replace").read()
        print(body[:chars])
        if len(body) > chars:
            print(f"\n... [{len(body)-chars} more chars; open the file for the rest]")
    else:
        print("(file missing - re-run filter_transcripts.py)")
    return 0


def cmd_search(text: str, cwd: str, results: int = 5) -> int:
    """Semantic search for when you do not know the id. Returns ids.

    Scoped to this project's palace and to its wing: a palace-wide search would
    reach other projects' drawers, and nothing here could tell them apart.
    """
    cfg = load_config(cwd)
    wing = wing_for(cwd, cfg, record=False)
    argv = mempalace_argv(cwd, cfg, "search", text, "--wing", wing,
                          "--results", str(results))
    if argv is None:
        print(f"mempalace not found - fix: {SETUP_FIX}")
        return 0
    out = run(argv)
    out = "\n".join(l for l in out.splitlines()
                    if "EmbedderIdentity" not in l and "_enforce_embedder" not in l)
    print(out.strip() or "(no results - is the palace mined?)")
    ids = ids_from_graph_output(out)
    if ids:
        print("\nexchange ids: " + ", ".join(ids))
        print("dereference with:  kb_resolve.py get <id>")
    return 0


def cmd_ask(question: str, cwd: str, want_code: bool = False, want_chat: bool = True,
            budget: int = 1200, snippet: int = 1200) -> int:
    """Route the question through a graph, then print the verbatim source behind it."""
    cfg = load_config(cwd)
    graphify = find_tool("graphify") or "graphify"
    if want_chat:
        dd = _dialogue_dir(cwd)
        gb = str(graph_b_path(cwd, cfg))
        print("=" * 72)
        print("CHAT  — Graph B decides what to look at, exchanges supply the words")
        print("=" * 72)
        if not os.path.exists(gb):
            print(f"no chat graph at {gb}")
        else:
            out = run([graphify, "query", question, "--budget", str(budget), "--graph", gb])
            out = "\n".join(l for l in out.splitlines() if not l.startswith("  warning: skill"))
            print(out.strip())
            idx = load_index(cwd)
            ids = [i for i in ids_from_graph_output(out) if i in idx]
            if ids:
                print("\n" + "-" * 72)
                print(f"VERBATIM SOURCE — {len(ids)} exchange(s) behind those nodes")
                print("-" * 72)
                for eid in ids[:4]:
                    row = idx[eid]
                    path = os.path.join(dd, row["file"])
                    print(f"\n### {eid}  ({row['date']})  {row['topic'][:80]}")
                    if os.path.exists(path):
                        body = open(path, encoding="utf-8", errors="replace").read()
                        body = body.split("---", 2)[-1].strip()
                        print(textwrap.shorten(body.replace("\n\n", "\n"),
                                               width=snippet, placeholder=" …"))
                    print(f"    full: kb_resolve.py get {eid}")
            else:
                print("\n(no exchange ids on those nodes — Graph B may predate "
                      "per-exchange filtering; rebuild it to get sharp pointers)")
    if want_code:
        ga = str(graph_a_path(cwd))
        print("\n" + "=" * 72)
        print("CODE  — Graph A decides what to look at, the repo is the source")
        print("=" * 72)
        if not os.path.exists(ga):
            print(f"no code graph at {ga}")
        else:
            out = run([graphify, "query", question, "--budget", str(budget), "--graph", ga])
            out = "\n".join(l for l in out.splitlines() if not l.startswith("  warning: skill"))
            print(out.strip())
            files = files_from_graph_output(out)[:8]
            if files:
                print("\n" + "-" * 72)
                print("READ THESE — the repo is current; the palace is not")
                print("-" * 72)
                for f in files:
                    print(f"  {f}")
    return 0


WING_LINE = re.compile(r"WING:\s+(\S+)")


def wings_from_status(status_output: str) -> list[str]:
    """The wing names in a `mempalace status` report."""
    return WING_LINE.findall(status_output or "")


def doctor_checks(cwd: str) -> tuple[list[dict], dict]:
    """Run every link check. Returns (checks, extras) without printing anything.

    Each check is {"check": label, "ok": bool, "detail": str}. `extras` carries
    the numbers the printed report uses for its NEXT hint.

    The palace checks are about THIS project's palace: whether it has been
    built, whether MemPalace can open it, and whether this project's wing is
    inside it. Only when the wing is missing is the shared 0.1.0 palace
    consulted, to tell "not moved yet" from "never mined".
    """
    cfg = load_config(cwd)
    checks: list[dict] = []

    def chk(label: str, cond: bool, detail: str = "") -> bool:
        checks.append({"check": label, "ok": bool(cond), "detail": detail})
        return bool(cond)

    mempalace = find_tool("mempalace")
    graphify = find_tool("graphify")
    chk("mempalace found", bool(mempalace), mempalace or f"not found - fix: {SETUP_FIX}")
    chk("graphify found", bool(graphify), graphify or f"not found - fix: {SETUP_FIX}")

    dd = _dialogue_dir(cwd)
    chk("dialogue dir exists", os.path.isdir(dd), dd)
    idx = load_index(cwd)
    chk("exchange index present", bool(idx), f"{len(idx)} exchanges")
    chk("dialogue .gitignore (stops graph self-ingestion)",
        os.path.exists(os.path.join(dd, ".gitignore")), os.path.join(dd, ".gitignore"))

    ga, gb = str(graph_a_path(cwd)), str(graph_b_path(cwd, cfg))
    chk("Graph A (code) exists", os.path.exists(ga), ga)
    chk("Graph B (chat) exists", os.path.exists(gb), gb)

    linked = 0
    if os.path.exists(gb):
        try:
            with open(gb, encoding="utf-8") as fh:
                g = json.load(fh)
            nodes = g.get("nodes", [])
            srcs = {str(n.get("source_file") or "") for n in nodes}
            linked = sum(1 for s in srcs if EXCHANGE_ID.search(s))
            chk("Graph B nodes carry exchange ids", linked > 0,
                f"{linked} of {len(srcs)} distinct sources")
            bad = [s for s in srcs if "graphify-out" in s]
            chk("Graph B free of self-ingestion", not bad, f"{len(bad)} bad sources")
        except Exception as e:
            chk("Graph B parses", False, str(e)[:80])

    # This project's own palace. MemPalace creates it on the first mine, so a
    # missing directory means "never compounded", not "broken".
    palace = palace_path(cwd, cfg)
    built = palace.is_dir()
    chk("palace", built,
        str(palace) if built else f"{palace} - not built yet - fix: {COMPOUND_FIX}")

    wings: list[str] = []
    if mempalace:
        wings = wings_from_status(run(mempalace_argv(cwd, cfg, "status")))
    chk("palace reachable", bool(wings), ", ".join(wings) or "no wings")

    wing = wing_for(cwd, cfg, record=False)
    if wing in wings:
        chk("dialogue wing present", True, wing)
    else:
        # 0.1.0 mined every project into one shared palace. A wing that is
        # there but not here has not been lost, it has not been moved yet, and
        # the next compound moves it - which is a different message to the user
        # than "nothing has ever been mined".
        detail = f"{wing} - fix: {COMPOUND_FIX}"
        if mempalace and wing in wings_from_status(run([mempalace, "status"])):
            detail = (f"{wing} - exists only in the shared 0.1.0 palace "
                      f"- fix: {COMPOUND_FIX} (moves it)")
        chk("dialogue wing present", False, detail)

    # Drawers whose source file is gitignored or gone are junk in every search
    # result; `sync --dry-run` counts them without changing anything. The check
    # is skipped (not failed) when the installed MemPalace has no sync report.
    if mempalace and wing in wings:
        stale = stale_drawers(run(mempalace_argv(cwd, cfg, "sync", "--dry-run",
                                                 "--wing", wing)))
        if stale is not None:
            gitignored, missing = stale
            total = gitignored + missing
            detail = f"{gitignored} gitignored, {missing} missing"
            if total:
                # The whole command, palace included: pasted without --palace it
                # would prune the shared palace instead of this project's.
                detail += (f" - fix: mempalace --palace {palace} "
                           f"sync --apply --wing {wing}")
            chk("no stale drawers", total == 0, detail)

    return checks, {"linked": linked, "exchanges": len(idx), "dialogue_dir": dd}


STALE_GITIGNORED = re.compile(r"^\s*Gitignored:\s+(\d+)", re.MULTILINE)
STALE_MISSING = re.compile(r"^\s*Missing:\s+(\d+)", re.MULTILINE)


def stale_drawers(sync_output: str) -> tuple[int, int] | None:
    """(gitignored, missing) drawer counts from a `mempalace sync` report.

    None when the output carries neither figure, which means the installed
    MemPalace did not produce a sync report, not that the palace is clean.
    """
    g = STALE_GITIGNORED.search(sync_output)
    m = STALE_MISSING.search(sync_output)
    if g is None and m is None:
        return None
    return (int(g.group(1)) if g else 0, int(m.group(1)) if m else 0)


def cmd_doctor(cwd: str, as_json: bool = False) -> int:
    """Verify every link in the chain, and say which one is broken."""
    checks, extras = doctor_checks(cwd)
    ok = all(c["ok"] for c in checks)
    if as_json:
        print(json.dumps(checks, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    print("=" * 72)
    print("kb doctor")
    print("=" * 72)
    for c in checks:
        detail = f" - {c['detail']}" if c["detail"] else ""
        print(f"  [{'OK ' if c['ok'] else 'FAIL'}] {c['check']}{detail}")
    print()
    if extras["linked"] == 0 and extras["exchanges"]:
        print("  NEXT: Graph B predates the per-exchange ids. Rebuild it:")
        print(f"    graphify {extras['dialogue_dir']} --backend claude-cli --max-concurrency 4")
    print("  verdict:", "all links resolve" if ok else "chain is broken above")
    return 0 if ok else 1


# ---------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    """The four commands, each with --cwd."""
    p = argparse.ArgumentParser(
        prog="kb_resolve.py",
        description="Resolve graph nodes to their verbatim source: exchanges for chat, "
                    "files for code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               '  kb_resolve.py ask "why two graphs"\n'
               '  kb_resolve.py ask "where is the gate" --code\n'
               "  kb_resolve.py get b49fc7be-0007\n"
               '  kb_resolve.py search "wing collision"\n'
               "  kb_resolve.py doctor --json\n",
    )
    sub = p.add_subparsers(dest="cmd", metavar="{ask,get,search,doctor}")

    def with_cwd(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--cwd", default=None, metavar="DIR",
                        help="project directory (default: the current directory)")
        return sp

    a = with_cwd(sub.add_parser("ask", help="route a question through a graph to its source"))
    a.add_argument("question", nargs="+", help="the question, quoted")
    a.add_argument("--code", action="store_true", help="use Graph A (code) instead of Graph B")
    a.add_argument("--both", action="store_true", help="use both graphs")
    a.add_argument("--budget", type=int, default=1200, help="graphify query token budget")
    a.add_argument("--snippet", type=int, default=1200, help="characters per verbatim snippet")

    g = with_cwd(sub.add_parser("get", help="print one exchange in full"))
    g.add_argument("exchange_id", help="stable id, e.g. b49fc7be-0007")
    g.add_argument("--chars", type=int, default=4000, help="characters to print")

    s = with_cwd(sub.add_parser("search", help="semantic search in the palace, ids back"))
    s.add_argument("text", nargs="+", help="the search text, quoted")
    s.add_argument("--results", type=int, default=5, help="how many hits")

    d = with_cwd(sub.add_parser("doctor", help="verify every link in the chain"))
    d.add_argument("--json", dest="as_json", action="store_true",
                   help="print the checks as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    """Dispatch one command. ask/get/search always exit 0; doctor exits 1 on any FAIL."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    cwd = os.path.abspath(args.cwd or os.getcwd())
    if args.cmd == "get":
        return cmd_get(args.exchange_id, cwd, chars=args.chars)
    if args.cmd == "search":
        return cmd_search(" ".join(args.text), cwd, results=args.results)
    if args.cmd == "ask":
        want_code = args.code or args.both
        want_chat = (not args.code) or args.both
        return cmd_ask(" ".join(args.question), cwd, want_code=want_code,
                       want_chat=want_chat, budget=args.budget, snippet=args.snippet)
    if args.cmd == "doctor":
        return cmd_doctor(cwd, as_json=args.as_json)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
