---
name: Bug report
about: Something does not work the way the README says it does
title: ""
labels: bug
---

## What happened

<!-- What you did, and what happened instead of what you expected. -->

## Environment

- **OS and version:**
- **Claude Code version** (`claude --version`):
- **Plugin version** (from `/plugin` or `.claude-plugin/plugin.json`):

## Doctor output

Run `/graph-mempalace:kb-doctor` and paste the whole thing. It checks the
prerequisites and every link in the chain, and names the one that is broken.

```
paste here
```

## Log

The last 30 lines of `~/.graph-mempalace/compound.log`:

```bash
# macOS / Linux / Git Bash
tail -n 30 ~/.graph-mempalace/compound.log

# PowerShell
Get-Content "$HOME/.graph-mempalace/compound.log" -Tail 30
```

```
paste here
```

## Anything else

<!--
Your config, if you changed it: ~/.graph-mempalace/config.toml and any
<repo>/.graph-mempalace.toml.

Please check what you paste. The log records paths, counts and timings rather than
conversation text, but a path can still be revealing. Redact what you would not
publish.
-->
