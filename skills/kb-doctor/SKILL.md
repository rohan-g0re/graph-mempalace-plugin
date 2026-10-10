---
name: kb-doctor
description: "Diagnoses the knowledge base — prerequisites, filtered exchanges, both graphs and every back-pointer — and gives the one printed fix beside each failing line. Handles 'kb doctor', 'check the knowledge base', 'is the memory working', 'diagnose mempalace', 'why does recall come back empty'."
disable-model-invocation: true
---

# Diagnose the knowledge base

1. Check the prerequisites.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_setup.py" --check
   ```

2. Check the chain end to end. This exits 1 when anything fails, which is
   expected output rather than an error to retry.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_resolve.py" doctor
   ```

3. For every row that reads MISSING or FAIL, repeat the row and put the fix the
   script printed next to it. A missing prerequisite is
   `/graph-mempalace:kb-setup`; an empty or stale graph is
   `/graph-mempalace:kb-compound`; for anything else pass on the printed line
   and name `~/.graph-mempalace/compound.log` as the next place to look.
   A `[NOTE]` line is an advisory and never a FAIL: `mcp on PATH` means a
   server is installed but not visible to Claude Code's MCP spawn, and its
   fix is to paste the printed `"env"` line into `~/.claude/settings.json`.
   `no stale drawers` failing means the palace holds drawers whose source
   file is gitignored or gone; the printed `mempalace sync --apply --wing`
   line deletes only those and leaves every other drawer alone.

4. When both commands come back clean, say so in one line and give the Graph A
   and Graph B node counts the doctor printed.

Complete when both commands have run and every failing line has a fix beside
it.
