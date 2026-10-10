---
name: graph-mempalace
description: "Answers questions about this project's past work and code from its own knowledge base, quoting the verbatim exchange or the file behind every claim. Use whenever the question touches earlier sessions or settled decisions: 'did we', 'what did we decide', 'why is this the way it is', 'last time', 'have we tried', 'where does this live in the code'."
---

# graph-mempalace

This project keeps a knowledge base of its own past: every past conversation
filed verbatim in MemPalace, plus a graph of the current code. Two graphs route;
the exchange or the file answers.

| domain | what routes | what answers |
|---|---|---|
| chat | Graph B, over the filtered dialogue | MemPalace drawers, keyed by exchange id |
| code | Graph A, over the repository | the repository itself, `file:line` |

## Consult it when

The question is about past work, a decision, why something is shaped the way it
is, whether something was already tried, or what happened last time. Also when
the question is where something lives in the code.

## Steps

1. Query the chat history:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" ask "<question>"
   ```

   Done when the output shows a `VERBATIM SOURCE` block, or states that the
   nodes carry no exchange ids.

2. For a question about code location or structure, query the code graph. Use
   `--both` when the question spans a decision and the code that came out of it:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" ask "<question>" --code
   ```

   Done when you have the file list and have read the files that matter.

3. Pull the full text of any exchange you will quote:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" get b49fc7be-0007
   ```

   Done when the sentences you plan to use are on screen verbatim.

4. Answer from the quoted exchange or from the file you read. A node's
   description is an LLM summary, so treat it as a pointer to look at. Cite the
   exchange ids you used, written like `b49fc7be-0008`. Nodes marked
   `verification=unverified` are claims rather than facts, so name them as
   claims. When nothing comes back, say the knowledge base has nothing on it and
   answer from the code or ask the user.

   Done when every statement about the past traces to a cited exchange id or a
   file you read.

Compounding is automatic. `/graph-mempalace:kb-compound` runs it now.

## Depth

- `references/answering.md` — reading `ask` output, the `EXTRACTED`/`INFERRED`/`AMBIGUOUS` edge tags, truncation and widening the budget, falling back to `search`.
- `references/writing-back.md` — filing a durable outcome or a new fact with the MemPalace MCP tools, and choosing between supersede and invalidate.
- `references/troubleshooting.md` — missing prerequisites, an empty knowledge base, a held lock, a duplicate wing, answers that look like another project's, MCP servers that will not connect.
