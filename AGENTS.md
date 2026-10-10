# Authoring standard

Rules for anyone (person or agent) changing this repository. Each rule carries its
reason. `docs/DESIGN.md` is the contract; this file is how to write to it.

## Scripts (`scripts/*.py`, `scripts/*.sh`)

- [ ] Python 3.11+, standard library only. Reason: hooks run on every turn on
      machines we do not control; a dependency is a failure mode.
- [ ] Every path, name and config read goes through `gm_common`. Reason: the gate,
      the chain, the resolver and the SessionStart hook must agree by construction.
- [ ] Hook scripts never raise and always exit 0. Wrap `main()` in a `try/except`
      that logs to `~/.graph-mempalace/compound.log`. Reason: a hook that fails
      blocks or spams the user's session.
- [ ] Hook stdout is model-facing. Print only what the model should read. Debug
      goes to the log file. Reason: stdout is injected into context.
- [ ] Reconfigure stdout/stderr to UTF-8 at the top of every script. Reason:
      Windows consoles default to cp1252 and exchange text contains arrows.
- [ ] Subprocesses get `argv` lists, never shell strings. Tool paths come from
      `find_tool`. Reason: spaces in home directories; PATH not inherited by hooks.
- [ ] Long work is spawned detached (`spawn_detached`) with stdout to the log.
      Reason: hooks have timeouts; the session must never wait.
- [ ] Shell launchers are POSIX `sh`, LF line endings, invoked as
      `bash "${CLAUDE_PLUGIN_ROOT}/scripts/x.sh"`, never relying on an exec bit or a
      shebang. Reason: NTFS has no exec bit; CRLF breaks `#!`.
- [ ] Unit tests for every decision branch, with fake tools on a temp PATH and a
      temp HOME. Reason: portability is a test, not an intention.

## Skills (`skills/<name>/SKILL.md`)

- [ ] `name` equals the directory name, kebab-case, no colons. Reason: validator
      and Windows paths.
- [ ] `description` is one tight sentence of what + when, quoted if it contains a
      colon, with the literal phrases a user would type. Reason: every session
      pays the description; triggering depends on the phrases.
- [ ] User-invoked skills set `disable-model-invocation: true`. Reason: they
      orchestrate; they should not fire on their own.
- [ ] Body: imperative steps, each ending in a completion criterion. Scripts do
      the work; the skill runs them and presents the result. Reason: deterministic
      work in a script costs a fraction of the tokens and never drifts.
- [ ] Reference bundled scripts as `"${CLAUDE_PLUGIN_ROOT}/scripts/x.py"` inside the
      bash block. Reason: that is the one place the variable is substituted; the
      Bash tool's environment does not carry it.
- [ ] Supporting material goes in `references/*.md` beside `SKILL.md`, referenced
      by a backtick path. No markdown links to files. Reason: links are read as
      cwd-relative paths and fail.
- [ ] Positive instructions. Say what to do, not what to avoid. Reason: a
      prohibition drags the forbidden behaviour into context.
- [ ] Delete sentences the model already obeys by default. Reason: no-ops cost
      load and say nothing.

## Hooks and manifests

- [ ] `hooks/hooks.json` is wrapped in a top-level `"hooks"` key. Reason: the
      unwrapped event map fails to load, silently, as a file.
- [ ] Shell-form commands quote the variable: `bash "${CLAUDE_PLUGIN_ROOT}/..."`.
      Reason: spaces in paths; the validator warns otherwise.
- [ ] Every manifest path starts with `./`. Reason: validation fails otherwise.
- [ ] `homepage` must be a valid URL. Reason: it is the one metadata field that
      stops the plugin loading.
- [ ] Run `claude plugin validate . --strict --json` after touching any manifest,
      skill or hook. Reason: unknown keys load silently otherwise.
- [ ] Do not bump `version` in a feature change. Releases do it, with a CHANGELOG
      entry, in one commit. Reason: pin semantics; users update when it changes.

## Docs

- [ ] Plain language. Short words. One idea per sentence. Reason: readers include
      people who are not native English speakers and agents with a token budget.
- [ ] Every claim about behaviour is either tested, or carries the date and
      version it was verified on. Reason: harness behaviour changes.
- [ ] README is organised by the reader's situation (install, first session,
      asking, configuring, fixing), not by component. Reason: people find things
      by their problem.
- [ ] Install commands appear in exactly one place in the README and are copied,
      not retyped, anywhere else. Reason: drift.
- [ ] No author names inside skills or docs pages. Reason: content outlives people.

## Quick checks

```bash
claude plugin validate . --strict --json
python -m pytest -q
grep -rnE '\]\((\./)?(references|scripts)/' skills/      # should print nothing
grep -rn 'CLAUDE_PLUGIN_ROOT' skills/ | grep -v '"\${CLAUDE_PLUGIN_ROOT}' # unquoted uses: none
```
