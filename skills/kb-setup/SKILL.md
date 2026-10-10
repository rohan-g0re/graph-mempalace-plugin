---
name: kb-setup
description: "Checks and installs the knowledge base prerequisites — uv, mempalace, graphify, the graphify patch, the MemPalace config, the git excludes and the `env.GM_PYTHON` line in `~/.claude/settings.json` — then tells the user to restart the session. Handles 'kb setup', 'set up memory', 'install the knowledge base', 'set up mempalace', 'prerequisites missing', and a knowledge base that will not start."
disable-model-invocation: true
---

# Set up the knowledge base

1. Check what is present.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_setup.py" --check
   ```

   Show the table row for row. Done when the user can see every row and its
   status.

2. If any row reads MISSING, install the fixes the script can perform.

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/kb_setup.py" --install
   ```

   The script prints what it is about to do and then does it. Report one of
   those edits to the user by name: it writes `env.GM_PYTHON` into
   `~/.claude/settings.json`, an absolute path to a Python 3.11 or newer, which
   is what starts the MemPalace MCP server through the plugin's launcher. Done
   when the command exits, its output is on screen and that edit has been
   named.

3. Run the check again. For any row still MISSING, pass on the fix printed
   beside it — `uv` itself is installed by the user from the one-liner the
   script prints for their platform, after which step 2 can run again. Done when
   every row reads OK.

4. Tell the user to restart the session so the hooks and the two MCP servers
   load; a running session keeps the servers it started with, and the `env`
   block in `~/.claude/settings.json` takes effect in the next session, so the
   MemPalace MCP server starts correctly only after a restart. Done when every
   row reads OK and that message has been given.
