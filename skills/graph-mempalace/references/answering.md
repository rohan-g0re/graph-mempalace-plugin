# Reading an `ask` answer

## The shape of the output

`ask` prints up to two halves. Each half has a navigation part and a
ground-truth part, in that order.

The chat half starts with a `CHAT` banner. Under it come the Graph B nodes the
query matched: a label, a community, a one-line description and the edges
between them. Then a rule, then:

```
VERBATIM SOURCE — 3 exchange(s) behind those nodes
### b49fc7be-0007  (2026-10-02)  why the wing name has to be derivable
    <a shortened slice of the exchange>
    full: kb_resolve.py get b49fc7be-0007
```

Each `###` line is one exchange id, its date and its topic. The indented text is
a shortened slice, kept small on purpose. Run `get <id>` for the whole thing
before quoting from it.

The code half starts with a `CODE` banner, the Graph A nodes, then a
`READ THESE` list of repository paths. Read the files. The repository is current
at all times, so a path from Graph A is a live answer in a way a drawer is not.

## What the node tags mean

Every edge carries one tag:

| tag | meaning |
|---|---|
| `EXTRACTED` | the extractor found the relation stated in the source |
| `INFERRED` | the extractor concluded it; the source does not say it outright |
| `AMBIGUOUS` | the extractor saw a relation but could not pin the direction or the kind |

Weigh `EXTRACTED` first, and go to the exchange for anything tagged `INFERRED`
or `AMBIGUOUS`. A node may also carry `verification=unverified`, meaning the
extractor found no evidence for the node itself. Those are claims. Name them as
claims, or drop them.

One known artefact: Graph B sometimes mints a node against a filename that was
merely quoted inside a conversation, as if the file had been read. Check the
exchange before treating such a node as a fact about the repository.

## Truncation, and widening the view

Two caps are in play.

The graph half runs through `graphify query` with a token budget, so a long node
list arrives trimmed. When the nodes look cut off mid-thought, go to the graph
directly with a larger budget:

```bash
graphify query "<question>" --budget 4000 --graph graphify-out/graph.json
```

Graph A lives at `graphify-out/graph.json` inside the repository. Graph B lives
under `~/.graph-mempalace/projects/<slug>/exchanges/graphify-out/graph.json`.
`kb-doctor` prints both paths.

The exchange half shortens each snippet. `get <id>` prints the exchange up to a
few thousand characters and then names the file that holds the rest, so open
that file when the part you need sits past the cut.

## When `ask` comes back thin

Three cases, three moves.

The `VERBATIM SOURCE` block is missing or says the nodes carry no exchange ids:
Graph B was built before the per-exchange filter existed. Rebuild it with
`/graph-mempalace:kb-compound`.

The graph matched nothing useful, but the subject is surely in the history: go
past the graph and search the drawers directly, then dereference what comes back.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" search "<exact words you expect>"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" get <id-from-search>
```

`search` is hybrid cosine plus BM25 over the drawers, so it finds an exchange
whose wording you remember even when no node points at it. It covers this
project's own palace, so an empty result means this project's history holds
nothing on the question rather than that the words sit in another project's
memory.

Both the graph and the search come back empty: say the knowledge base has
nothing on the question. That is a real answer, and it is the one that keeps the
knowledge base trustworthy.
