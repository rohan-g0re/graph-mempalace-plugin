---
name: kb-compound
description: "Compounds this project into the knowledge base now — filters the transcripts into exchanges, mines them into MemPalace, refreshes the code graph and rebuilds the chat graph — then reports the counts and runs the doctor. Handles 'kb compound', 'compound now', 'update the knowledge base', 'ingest this conversation', 'rebuild the graphs'."
disable-model-invocation: true
---

# Compound now

1. Run the chain in the foreground. Two flags may follow `--wait`, and only
   these two: `--no-graph-b` skips the chat graph rebuild, which is the slow
   half, and `--no-graph-a` skips the code graph. Take them from `$ARGUMENTS`
   when the user asked for them, append them to the end of the line below, and
   drop everything else `$ARGUMENTS` holds, including any question or sentence —
   this command takes flags, not a topic. With no flags asked for, the line ends
   at `--wait`, exactly as written.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/compound_run.py" --project "$PWD" --wait
   ```

   If the output says a compound is already running, report that one is in
   flight and stop here. Done when the chain has printed its summary, or the
   already-running line has been reported.

2. Verify every link in the chain.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" doctor
   ```

   Done when the doctor's verdict line is on screen.

3. Report in one short block: exchanges filed, drawers added, Graph A and
   Graph B node counts, how many files still produced no nodes, the duration,
   and the doctor's verdict. Done when those numbers and the verdict are
   reported, or the already-running line was reported instead.
