# tier-sweep

Problem: 0.5.0 and 0.6.0 shipped fast and left edges unwired. `docs/multi-flow.md`
AC14 (two flows in parallel, end to end) was never run, and §d has no AC at all —
so a spec delta went unimplemented and `review` passed the change with nothing to
check it against. Plus: no LICENSE, no CI, no run record.

Scope: five groups, A→E, in that order. Version 0.7.0 — new tool, removed hook,
new artifact.

## A. Drift from the last two features

**A1. `render()` omits `dir`** (`plugin/server.py`, `render()`)
Add `dir: <path>` line when `s["dir"]` set, after the `[flow: ...]` header line.
Both hooks call `render()`, so after a compaction with 2 open flows the model
currently gets no way to tell which tree either flow touches — breaks 0.6.0
across the exact boundary 0.5.0 exists to cover.

**A2. `/mini-vise:run` predates multi-flow** (`plugin/commands/run.md`)
Step 1 says "Call `status` first". Since 0.6.0 `advance`/`back`/`reset` require
`flow`, and none exists until `flow_start`. Rewrite step 1: call `status`, then
`flow_start(slug, dir)` if no flow covers this task. Keep every other step.

**A3. Report example is a `dev` example in all three charters**
(`plugin/agents/qa.md`, `plugin/agents/reviewer.md`)
`qa.md` says "the command you ran, its output verbatim" then shows an example
with neither. `reviewer.md` says "`file:line — what breaks, under what input`"
then shows the same dev example. Replace each with an example matching that
charter's own instruction, same compressed style, same `verdict:` ending.
`dev.md` keeps its example — it is correct there.

**A4. Version lives in 3 places and has diverged** (`plugin/server.py:305`)
`serverInfo.version` hardcoded `"0.2.0"`; plugin is 0.6.1. Add
`VERSION = "0.7.0"` constant in `server.py`, use it in `serverInfo`. Bump
`plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` to
0.7.0. Add a test asserting all three agree — no release script.

**A5. `reset` tool description is wrong** (`plugin/server.py`, TOOLS)
Says "back to the first node (dev)"; `NODES[0]` is `spec` and reset lands there.
Observed live. Fix the string to say `spec`. Reset's *behavior* is unchanged —
it clears `spec_path` on purpose, because it drops you at the node whose job is
to write a new one.

**A6. The Stop hook fights the `spec` node** (`plugin/hook_stop.py`)
Observed live driving this spec: the orchestrator asked the user to approve the
proposal, the turn ended, and the hook answered `decision: block` because the
flow was not at `done`. But `spec` is the one node whose completion condition is
*a human replying* — the pipeline was parked correctly, not drifting.

The hook exists to stop the model wandering off mid-pipeline. At `spec` there is
nothing for the model to do, so blocking there either burns a turn or pressures
it into approving its own proposal, which is the single failure the node was
built to prevent.

Fix: `decide()` blocks only on open flows whose node is not `spec`. Flows parked
at `spec` are reported through `systemMessage` instead — user-facing is the right
channel here, unlike C1, because the message is addressed to the person whose
approval is being waited on. If every open flow is at `spec`, do not block.

Both routes into `spec` want this: the pipeline starts there, and `back(to=spec)`
means "go ask the user". `stop_hook_active` handling is unchanged.

## B. Specced in multi-flow.md §d, never shipped

**B1. Strike the subagent-naming delta** (`docs/multi-flow.md` §d)
`name: "<node>-<slug>"` at spawn is not expressible: the `Agent` tool takes
`description`, `isolation`, `model`, `prompt`, `subagent_type` — no `name`.
Replace the bullet with a one-line note saying it was struck and why. Do not
file it as pending.

**B2. `flow: <slug>` on the way back** (three charters)
With 2 flows open a bare `verdict: pass` does not say which flow it belongs to —
the ambiguity `[flow: <slug>]` fixed on the outbound path, still open inbound.
Add `flow: <slug>` as a non-compressible first line of the report block in
`dev.md`, `qa.md`, `reviewer.md`, alongside `verdict:`. Update each example.

**B3. Advisor rule** (`plugin/skills/orchestration/SKILL.md`)
Declared "not in scope" at `docs/multi-flow.md:94`, never written. Two moments,
both orchestrator-only: before showing a spec to the user, and before routing a
`back` (is this a code bug or an undecided requirement). Subagents have no
advisor — it is a main-loop tool, do not tell them to use it.

## C. The PreCompact hook cannot do what it claims

**C1. Remove PreCompact** (`plugin/hooks/hooks.json`, `plugin/hook_ctx.py`)
`hook_ctx.decide()` returns `systemMessage` for PreCompact. Claude Code's own
hook docs: `systemMessage` = "Display a message to the user (all hooks)";
`additionalContext` = "Text injected into model context". Same bug review lap 1
caught for SessionStart in 0.5.0, fixed there, left here. And it is not a
channel swap: PreCompact's payload field is `custom_instructions`, PostCompact's
is `compact_summary` — neither carries `additionalContext`.

`SessionStart` with `source: "compact"` already works and is already wired —
verified live this session (another plugin's SessionStart:compact hook fired and
its context landed). So PreCompact is redundant at best.

Drop the `PreCompact` block from `hooks.json`; drop the PreCompact branch from
`hook_ctx.decide()`; update its module docstring. Keep SessionStart untouched,
including its non-blocking behavior on corrupt state.

**C2. Tests follow** (`test_hook_ctx.py`)
Delete PreCompact cases. Add one asserting `decide()` on a PreCompact payload
returns `{}` — a stale hooks.json entry in someone's install must not produce a
`systemMessage` claiming to steer a summarizer.

## D. Features

Noted at spec time: the brainstorm recommended picking one of D1–D3. All three
were requested. They are independent; D3 carries the most new failure surface.

**D1. `flow_close(flow)`** (`plugin/server.py`, new tool)
Multi-flow invites "open three, work one, come back tomorrow" and gives no way
to put one down: SessionStart re-injects every open flow, Stop nudges on each,
slugs are never reusable, `status` grows without bound. Removes the flow entry
entirely, freeing both slug and `dir`. Works on any flow, open or done — closing
an open one is explicit intent; say in the response what node it was on so a
mistake is visible. Unknown slug → the same `unknown_flow()` error everything
else raises.

**D2. Run log** (`plugin/server.py`, `.gitignore`)
One JSONL line appended per successful **state-mutating** call — `flow_start`,
`advance`, `back`, `reset`, `flow_close`. `status` is a read and is not logged;
logging it would make most rows null-flow noise.

`{"ts": <unix float>, "flow": <slug>, "tool": <name>, "node": <node after the call>, "lap": <int>, "verdict": <pass|fail|null>}`

Path: `STATE.with_suffix(".log")`, so `MINI_VISE_STATE` moves both together.
Wrapped so a logging failure can never fail a tool call — the log is a record,
not a dependency. Add `.mini-vise.log` to `.gitignore` (which today lists only
`.mini-vise.json`).

**D3. `dev` cannot pass without having changed the tree** (`plugin/server.py`)
The one honesty check that is mechanical rather than another agent.

A bare "is the tree dirty" check does not work here: spec-first means a spec
file is already untracked before `dev` starts, so `git status --porcelain` is
non-empty regardless of what dev did. It needs a baseline.

Record a hash of `git status --porcelain` output **on entering `dev`** — both
paths: `advance(verdict="pass")` leaving `spec`, and `back(to="dev")`. Store as
flow field `tree`. On `advance(flow, verdict="pass")` at node `dev`, recompute
and compare: identical → ValueError, state unchanged, message naming the dir.
Re-snapshotting on `back` means lap 2 must change something relative to when it
was sent back, not merely differ from the spec baseline.

Degrades silent-open — records/compares nothing and never blocks — when `dir`
is None (migrated flow), `git` is not on PATH, `dir` is not a repo, `tree` was
never recorded, or the subprocess errors or times out. Only node `dev`, only
`verdict="pass"`.

Known gap, accepted: a dev that creates a file and deletes it again produces an
unchanged hash and is blocked. Net-zero work being refused is the right call.

No other git use — no `base_ref`, no diff hashing, no worktree creation.
Amended by `docs/snapshots.md` H1/I: `git rev-parse HEAD` (so a `dev` that
commits its work is not wedged) and the snapshot commits at
`refs/mini-vise/snapshots/<slug>` are now in scope. Nothing else — still no
`base_ref`, no diff hashing, no worktree creation.

## E. Packaging

**E1. LICENSE** — MIT, copyright Rixmerz. Absent today; blocking for anyone
installing a marketplace plugin inside a company.

**E2. CI** (`.github/workflows/test.yml`) — on push and PR: checkout, setup
python3, run `python3 test_server.py` and `python3 test_hook_ctx.py`. No matrix,
no coverage, no lint gate. Both suites already run stdlib-only.

## F. Process rule

**F1.** One line in `plugin/skills/orchestration/SKILL.md` §1: every delta in a
spec gets an acceptance criterion, or it does not ship. A section describing a
change with no criterion is the one that gets lost — `review` has nothing to
check it against. Evidence: §d of `docs/multi-flow.md`.

## Acceptance

1. `render()` output contains a `dir:` line when the flow has a dir, and omits
   it when `dir` is None. Both hooks' text inherits it.
2. `plugin/commands/run.md` names `flow_start` before any `advance`/`back`.
3. `qa.md`'s example shows a command and its output; `reviewer.md`'s shows
   `file:line — what breaks`; neither is the `dev` example.
4. `server.py` `VERSION == "0.7.0"`, `serverInfo.version` uses it, and a test
   fails if `plugin.json`, `marketplace.json`, and `VERSION` disagree.
5. `reset` lands on `spec`, keeps `dir`, and clears `spec_path`, `evidence`,
   `note`, `note_for`, `lap` (regression guard on existing behavior).
6. `reset`'s tool description says `spec`, not `dev`.
7. `docs/multi-flow.md` §d no longer lists the subagent-naming delta as pending.
8. All three charters emit `flow: <slug>` as the report's first line, and each
   example shows it.
9. `orchestration` SKILL.md states the two advisor moments, and does not tell
   subagents to use an advisor.
10. `hooks.json` has no `PreCompact` entry; `hook_ctx.decide()` on a PreCompact
    payload returns `{}`; SessionStart behavior byte-identical to before,
    including `{}` on corrupt state.
11. `flow_close(flow)` removes the entry: the slug is reusable by `flow_start`,
    the dir is no longer held, and `status` no longer renders it. Unknown slug →
    error listing valid slugs, state unchanged.
12. `flow_close` on an open flow succeeds and its response names the node the
    flow was on.
13. Every successful mutating call appends exactly one line to
    `STATE.with_suffix(".log")`, parseable as JSON with the six named keys.
    `status` appends nothing.
14. A tool call still succeeds when the log path is unwritable (e.g. its parent
    is read-only) — no raise, no `isError`.
15. `.gitignore` lists `.mini-vise.log`.
16. `tree` is recorded on entering `dev` by both paths: `advance` leaving `spec`,
    and `back(to="dev")`.
17. `advance(verdict="pass")` at `dev` with a tree identical to the recorded
    baseline raises, and state is unchanged.
18. The same call advances to `qa` when the tree differs from the baseline.
19. It also advances, with no raise, when `dir` is None, `dir` is not a git
    repo, `git` is unavailable, or `tree` was never recorded.
20. The check does not run at `spec`, `qa`, or `review`, nor for
    `verdict="fail"`.
21. `LICENSE` exists at repo root, MIT, naming Rixmerz.
22. `.github/workflows/test.yml` runs both suites on push and PR.
23. `orchestration` SKILL.md carries the every-delta-gets-an-AC rule.
24. `python3 test_server.py` and `python3 test_hook_ctx.py` both green.
25. README and CHANGELOG updated: `flow_close` in Tools, PreCompact removal and
    its reason, run log, the `dev` tree check, the `spec` Stop-hook carve-out,
    0.7.0 entry.
26. (A6) Stop hook with every open flow at `spec` → no `decision` key; a
    `systemMessage` naming those flows instead.
27. (A6) Stop hook with one flow at `spec` and one at `qa` → blocks, and the
    block text names the `qa` flow. Existing `stop_hook_active` behavior
    unchanged: one nudge, then release.

## Not in scope

- **Verifying `docs/multi-flow.md` AC14** (two flows in parallel, end to end).
  Still unverified, and not runnable here: the installed plugin is 0.4.1, so
  `flow_start` is not callable in this session. Trigger: push, reinstall, then
  two `git worktree` dirs. This spec fixes what that run *would* have surfaced;
  it does not substitute for the run.
- Preserving `spec_path` across `reset`. Reset lands on `spec`, whose job is to
  write a new one — keeping the old pointer would show a stale spec at the node
  replacing it. A "redo dev, keep the approved spec" operation is a different
  tool; not this change.
- `PostCompact`. `SessionStart:compact` already covers re-injection; a third
  mechanism for a solved problem is what C1 is deleting.
- Any further git use in `server.py` beyond D3's `status --porcelain`.
- Parking/pausing a flow as a distinct state. `flow_close` + `flow_start` is the
  lazy version; add a `paused` node only if reopening proves to need history.
- Re-running the model/effort experiments. Concluded, documented in README.
- Fixing `reviewer` severity calibration on out-of-alphabet input. Tested twice,
  did not validate, deliberately dropped.

Version: 0.7.0.
