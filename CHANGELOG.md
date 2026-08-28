# Changelog

## 0.8.1 — snapshots never ran where they were needed most

CI has been red since it was added in 0.7.0 — all four runs, `main` included.
The failing assertion looked cosmetic and was not.

`snapshot()` ran `git commit-tree` with no committer identity, so it inherited
whatever the machine had configured globally. Where nothing was configured git
exits *Author identity unknown*, `commit-tree` returns non-zero, and
`snapshot()` returns silently — correct per its own degrade-open contract, and
the wrong outcome: **no snapshot was ever written on a machine without a global
git identity.** CI containers, fresh checkouts, Docker images. 0.8.0 added
snapshots because a `qa` run destroyed three files of uncommitted work; the
throwaway environments least likely to have a git identity are exactly the ones
where that safety net matters most.

The fix is an explicit `-c user.email` / `-c user.name` on that one call.
`git add -A`, `git write-tree` and `git update-ref --create-reflog` all work
without an identity — verified, so they are untouched. The snapshot commits are
now attributed to `mini-vise <snapshot@mini-vise.local>`, which is also the
truer attribution: they are machine-written commits in a private ref namespace,
not the user's.

The test helper already stated the rule the product code was missing —
*"explicit -c identity, not the environment's global config — a snapshot test
must not depend on git being pre-configured on whatever machine runs it."* It
was applied to the helper and not to the code the helper exercises, which is
why every snapshot test passed locally and none of them could catch this.

New regression guard: `test_snap_ac18_snapshot_works_with_no_global_git_identity`
runs the snapshot path with `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pointed at
`/dev/null` and asserts both that the ref exists and that the commit carries the
fixed identity. Without it the suite stays green on any configured machine and
the same bug returns unnoticed.

## 0.8.0 — snapshots, and the D3 wedge

Follow-on to 0.7.0's tier sweep, which closed with two non-blocking findings
and a review-found wedge:

- **`dev` committing its own work no longer wedges the flow.** The tree hash
  D3 compares now includes `git rev-parse HEAD` alongside `git status
  --porcelain`, so a `dev` that commits moves the hash even though the tree
  itself goes clean. The block message no longer asserts "no change" as fact —
  it names tree and `HEAD` as both unchanged.
- **Every successful `flow_start`/`advance`/`back`/`reset`/`flow_close` now
  snapshots the flow's `dir`** to a commit at
  `refs/mini-vise/snapshots/<slug>` — one ref per flow, built through a
  throwaway index so the real working tree, index, and `HEAD` are never
  touched. `flow_close` snapshots before the entry is deleted, since closing
  an open flow discards its finding. Content only: `.mini-vise.json` and
  `.mini-vise.log` stay out via `.gitignore`, so a restore never brings the
  flow state back. `status` names the ref once a snapshot exists. Degrades
  silently on every failure, same contract as the run log: not a repo, `git`
  unavailable, `dir` unset or missing, a repo with no commits yet. Recovery is
  `git restore --source=refs/mini-vise/snapshots/<slug> .`, documented in the
  README; no new tool.
- `baseline/SKILL.md`'s report example was missing the `flow: <slug>` line
  every charter requires, and the never-compress list didn't name it either —
  fixed.

## 0.7.0 — tier sweep: wired-back edges, a run log, packaging

Rendering, wire style, and the version string had drifted from 0.6.0's
multi-flow work; this pass wires them back and closes what §d of
`docs/multi-flow.md` specced but never shipped:

- `render()` now includes a `dir:` line for a flow that has one — both hooks'
  text inherits it, so a compaction with two open flows can tell them apart
  by tree, not just by slug.
- `/mini-vise:run` now calls `flow_start` before any `advance`/`back`, matching
  0.6.0's required `flow` argument.
- `qa`/`reviewer` charters got their own report examples instead of `dev`'s.
- All three charters' report block now leads with `flow: <slug>`, alongside
  `verdict:` — the ambiguity `[flow: <slug>]` fixed on the outbound path, now
  fixed inbound too.
- `reset`'s tool description said "back to dev"; it lands on `spec`, and now
  says so.
- The `Stop` hook no longer blocks a flow parked at `spec` — that node's only
  completion condition is a human approving the proposal, and blocking there
  either burns a turn or pressures the model into approving its own plan. A
  `systemMessage` names those flows instead; the hook still blocks on any
  other open node.
- `server.py`'s version was hardcoded `0.2.0` against a `0.6.1` plugin. A
  single `VERSION` constant now backs `serverInfo` and matches
  `plugin.json`/`marketplace.json`.
- **`PreCompact` is gone.** Its only payload field is `custom_instructions`
  and its only outbound channel is `systemMessage`, which Claude Code
  documents as user-facing, not model context — it never did what it
  claimed. `SessionStart` with `source: "compact"` already re-announces every
  open flow after a compaction and is already wired.
- **`flow_close(flow)`** — new tool. Removes a flow entirely, freeing its
  slug and `dir` for reuse. Works on any flow, open or done; the response
  names the node it was closed on.
- **A run log.** Every successful `flow_start`/`advance`/`back`/`reset`/
  `flow_close` call appends one JSONL line to `.mini-vise.log` (`status`
  doesn't). Wrapped so a logging failure can never fail a tool call.
- **`dev` can no longer pass without touching the tree.** `advance` records a
  hash of `git status --porcelain` on entering `dev` and refuses
  `verdict="pass"` there if the tree is byte-identical to that baseline —
  the mechanical half of the honesty check, next to `qa`/`review`'s human
  one. Degrades open (never blocks) when `dir` is unset, not a repo, `git`
  isn't on PATH, or no baseline was recorded.
- `LICENSE` (MIT) and a GitHub Actions workflow running both test suites on
  push and PR — both were absent.
- `orchestration` now states: every delta in a spec gets an acceptance
  criterion, or it does not ship. `docs/multi-flow.md` §d described a change
  with none, and it went unimplemented.

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
