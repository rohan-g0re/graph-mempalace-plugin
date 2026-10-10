---
name: kb-ask
description: "Answers a question about this project's past from its knowledge base, quoting the verbatim exchanges behind the graph nodes and citing their exchange ids. Handles 'kb ask', 'what did we decide about', 'did we ever', 'why did we', 'when did we change', 'search my past conversations'."
disable-model-invocation: true
---

# Ask the knowledge base

1. Query it. Add `--code` when the question is about where code lives or how it
   is structured, and `--both` when it could be either. The question is spliced
   into a shell command, so write it as plain words on one line — letters,
   digits, spaces and plain punctuation — and keep the double quotes around
   `"$ARGUMENTS"` exactly as they are.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" ask "$ARGUMENTS"
   ```

2. Read the `VERBATIM SOURCE` block; the node list above it only says what to
   look at. Pull the full text of anything you will quote, and treat this step
   as done once those sentences are on screen verbatim.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" get <exchange-id>
   ```

3. If that block is empty or reports no exchange ids on the nodes, search the
   drawers directly and dereference what comes back with `get`.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" search "$ARGUMENTS"
   ```

4. Answer from the quoted text alone, with the relevant sentences quoted and
   each exchange id cited, written like `b49fc7be-0008`. Done when the answer
   cites at least one exchange id, or says plainly that the knowledge base has
   nothing stored on the question.
