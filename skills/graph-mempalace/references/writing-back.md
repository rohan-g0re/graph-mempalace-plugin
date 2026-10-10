# Writing back

Compounding files the conversation itself, so a decision that was spoken aloud
is already on its way into the palace. Write back by hand for the thing a
transcript does not hold: a conclusion that only exists once several exchanges
are put together, or a fact that has just changed.

Four MemPalace MCP tools cover it.

| tool | use when |
|---|---|
| `mempalace_add_drawer` | a durable outcome worth reading again later: what was decided, what it replaced, why |
| `mempalace_kg_add` | a new fact, as a subject-relation-object triple |
| `mempalace_kg_supersede` | a single-valued fact changed, and the old value should stay queryable |
| `mempalace_kg_invalidate` | a fact ended and nothing took its place |

The plugin's server is namespaced, so the full tool names are
`mcp__plugin_graph-mempalace_mempalace__mempalace_add_drawer` and so on for the
other three.

## Supersede against invalidate

This is the IETF *Obsoletes* against *Updates* distinction. Supersede says "read
the new one instead", and the old value stays in the timeline, so a later
question about what the value used to be still has an answer. Invalidate says
"that is simply over", with no successor.

Pick deliberately. The temporal knowledge graph is the part of MemPalace that
earns its keep, and it only works if the two are kept apart.

## Supersession is an explicit call

There is no automatic contradiction detection. Deduplication blocks identical
triples and nothing more, so two facts that disagree will both sit in the graph
until someone supersedes one. That is the right default — the alternative is a
model deciding which of the user's decisions is dead — and it means the call is
yours to make at the moment the fact changes.

## Where the writes land

Drawers for this project go in its own wing. `kb-doctor` prints the wing name in
use; `palace.wing` in `~/.graph-mempalace/config.toml`, or in
`<repo>/.graph-mempalace.toml`, pins it to an existing one.

## Keep compression off

Leave `mempalace compress` alone. It is lossy: measured retrieval accuracy falls
about twelve points, from 96.6% to 84.2%, and the claim of zero information loss
is not supported by the code. Storage is cheap next to a recalled decision that
turns out to be wrong.
