---
name: kb-setup
description: "Checks and installs the knowledge base prerequisites — uv, mempalace, graphify, the graphify patch, the MemPalace config and the git excludes — then tells the user to restart the session. Handles 'kb setup', 'set up memory', 'install the knowledge base', 'set up mempalace', 'prerequisites missing', and a knowledge base that will not start."
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

   The script prints what it is about to do and then does it. Done when the
   command exits and its output is on screen.

3. Run the check again. For any row still MISSING, pass on the fix printed
   beside it — `uv` itself is installed by the user from the one-liner the
   script prints for their platform, after which step 2 can run again. Done when
   every row reads OK.

4. Tell the user to restart the session so the hooks and the two MCP servers
   load; a running session keeps the servers it started with. Done when every
   row reads OK and that message has been given.
