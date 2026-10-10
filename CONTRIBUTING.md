# Contributing

Thanks for looking. This is a small plugin with a written contract, so most of
contributing is reading two files first.

## Read these first

- `docs/DESIGN.md` is the contract. Architecture and the memory-system rules were
  decided before this repository existed. If a change would contradict DESIGN, the
  change is to DESIGN first, in its own pull request.
- `AGENTS.md` is the authoring standard — how to write a script, a skill, a hook
  entry and a docs page, with the reason attached to each rule. Every rule in it is
  a checklist item a reviewer will check.

## Run the checks

Two commands. Both must pass before you open a pull request.

```bash
claude plugin validate . --strict --json
python -m pytest -q
```

`claude plugin validate` is offline. It reads the manifests, the skills and the
hooks, and needs no login (verified locally 2026-10-10 on Claude Code 2.1.273).
`--strict` turns warnings into failures, which is the point: an unknown manifest
key otherwise loads silently and does nothing.

Two more greps from `AGENTS.md`, both of which should print nothing:

```bash
grep -rnE '\]\((\./)?(references|scripts)/' skills/
grep -rn 'CLAUDE_PLUGIN_ROOT' skills/ | grep -v '"\${CLAUDE_PLUGIN_ROOT}'
```

The first finds markdown links to bundled files, which are read as paths relative
to the working directory and fail. The second finds unquoted uses of the plugin
root, which break on any home directory with a space in it.

### What the tests assume

The suite is offline. No network, no real `mempalace`, no real `graphify`. Fake
executables go on a temporary PATH and `HOME` is redirected, so a test run never
touches your palace or your config. Keep it that way: a test that needs the real
tools is a test that will not run in CI.

## Pull requests

- **One change per pull request.** A bug fix and a refactor in the same diff are
  two reviews pretending to be one.
- **No version bumps in a feature pull request.** `version` in `plugin.json` is a
  release action, not a change action. Users see an update the moment it changes.
- **No changelog entry in a feature pull request** either, for the same reason. The
  release commit writes it.
- **Script changes come with tests.** Every decision branch in `scripts/` gets a
  test, with fake tools on a temporary PATH and a temporary `HOME`. Portability is
  a test, not an intention.
- **Behavioural claims in docs are tested or dated.** If a sentence describes what
  Claude Code does, either a test proves it or the sentence carries the date and
  version it was checked on.
- **Say what you verified.** In the description, list the platforms you ran on. CI
  covers Linux, Windows and macOS on Python 3.11 and 3.12, but it cannot tell you
  whether a hook felt slow.

## Releases

A release is one commit and one tag.

1. Bump `version` in `.claude-plugin/plugin.json`.
2. Move the `Unreleased` entries in `CHANGELOG.md` under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading, and update the link definitions at the
   bottom.
3. Commit both together. One commit, so the version and its changelog can never
   disagree.
4. Cut the tag:

```bash
claude plugin tag
```

That writes a `graph-mempalace--vX.Y.Z` tag. Push it. Users on the marketplace see
the update because `version` changed.

## Reporting a bug

Open a GitHub issue. The bug template asks for your OS, `claude --version`, the
plugin version, the output of `/graph-mempalace:kb-doctor` and the last 30 lines of
`~/.graph-mempalace/compound.log`. Those five things answer most questions before
anyone has to guess.

Security issues go through `SECURITY.md`.
