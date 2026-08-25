# Changelog

## 0.6.1

Docs only. Adds this file, and folds the effort/model research from the
0.6.0 dogfooding session into the README as a stated recommendation — see
[Orchestrator effort, and whether dev/qa should run on opus](README.md#orchestrator-effort-and-whether-devqa-should-run-on-opus)
for the method and the evidence.

## 0.6.0 — run N flows in parallel, one directory each

`.mini-vise.json` keys the pipeline by a flow slug instead of holding a
single node/lap. `flow_start(slug, dir)` opens one against a working
directory and refuses if that directory is already held by another open
flow — a diff `review` reads has to trace to exactly one task, so two
code-touching flows sharing a tree is a hard error, not a warning.
`advance`, `back`, `reset` all take `flow` as a required argument, never
inferred; `status` renders every open flow when the slug is left out. Both
hooks iterate every open flow instead of asserting a single node.

Shipped over two review laps: the first pass's directory guard compared raw
strings and could be walked around with a relative path, a trailing slash,
or a flow migrated from the old single-slot state with no recorded
directory at all. Fixed with `realpath` on both sides and a defensive
default for the migration case.

## 0.5.0 — carry the spec path and qa evidence across a compaction

The state file held the node, the lap, and the open finding, but the path
to the spec and the evidence that `qa` actually ran its tests lived only in
the conversation — a compaction or a new session lost both. `advance` now
takes the spec path leaving `spec`, and at `qa` refuses to pass without the
command that ran and its real output. Reaching `done` prints a handoff
block naming what survives and where.

A new hook script, `hook_ctx.py`, covers the two moments context changes
underneath the session: `SessionStart` re-announces an open pipeline
through `additionalContext` — the channel the model actually reads, not
`systemMessage`, which a first lap of review caught landing in the wrong
one — and `PreCompact` tells the summarizer which lines to keep verbatim.
Neither can block a session; both stay silent on state they can't parse,
matching `hook_stop.py`'s existing refusal.

## 0.4.1 — wire style, packaging fixes, a reviewer blind spot

- Agent-to-agent text compresses: briefs, subagent reports, spec files, and
  `back()` notes drop articles, filler, and narration — they're read by
  other agents, not people. Commits, PRs, docs, and anything the user reads
  stay full prose. The rule lives in the three charters (always in
  context) and the longer version in `baseline`/`orchestration`.
- Fixed two packaging bugs that silently broke the marketplace install: a
  `git-subdir` source pointed at `.` was dropping every subdirectory, and
  declaring `hooks/hooks.json` explicitly conflicted with its auto-load.
- `reviewer` now flags diff hunks the spec doesn't explain — a run had
  shipped a leftover `ValueError` guard from an abandoned attempt, and
  nothing in the hunt list called an unexplained hunk a finding.
- `/mini-vise:run` is the one entry point (renamed from `/mini-vise`, which
  didn't resolve on its own).

## 0.4.0 — a spec node

A live run's reviewer said a question was "the author's call, not mine,"
and there was nowhere to put it — `back` only reached `dev`. `spec` is now
a node: a requirement nobody decided routes to a human instead of getting
guessed by a subagent. Added the `orchestration` skill — spec first via
OpenSpec when the repo has it, delegate rather than pre-solve, report the
verdict a subagent actually gave, route a finding to whoever owns it.

## 0.3.0 — the pipeline finishes itself

A `Stop` hook blocks the turn from ending while `node != done`, handing
back the same brief `status` would. Releases on `stop_hook_active`
re-entry, on corrupt state, and on anything unexpected — a hook that can
trap a session is worse than no hook.

## 0.2.0 — a verdict gate

`advance` requires a verdict; `fail` refuses to move the pipeline forward.
`back` requires a note, stored and echoed to the node that has to fix it.
`dev`/`qa` moved to sonnet, `reviewer` stayed on opus — an all-opus run had
billed $1.14 to implement fizzbuzz.

## 0.1.0 — three agents, three tools

First cut: `dev`, `qa`, `reviewer`, and the `status`/`advance`/`back` MCP
tools to walk them in sequence.
