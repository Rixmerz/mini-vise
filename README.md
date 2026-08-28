# mini-vise

![mini-vise mascot](docs/images/mascot.png)

Three subagents, three tools. That's the whole plugin.

```
   spec  ->  dev  ->  qa  ->  review  ->  done
     ^                          |
     +----------- back ---------+
```

`spec` is the only node with no subagent. You write the change proposal — via
OpenSpec if the repo has it — and a person approves it before any code exists.
A plan the model wrote and the model approved is not a plan.

Forward is `advance`. When a node finds a problem an earlier node has to fix —
a failing test at `qa`, a blocking finding at `review` — `back` returns there
and the lap counter goes up. Nothing reaches `done` for good until a lap
gets through without a `back`.

## Agents

| Agent | Does | Doesn't | Skills | Model | Effort |
|---|---|---|---|---|---|
| — | *`spec` has no agent: the orchestrator writes it, a human approves it* | | `orchestration` | — | — |
| `dev` | writes the code, runs the repo's existing checks | write tests, review itself | `baseline`, `implementing` | sonnet | medium |
| `qa` | writes and runs tests, reports real output | edit product code to go green | `baseline`, `testing` | sonnet | medium |
| `reviewer` | adversarial review + debugging, read-only | fix anything | `baseline`, `reviewing` | opus | medium |

`effort` is pinned because the numbers below were measured at `medium`, and a
charter that declares no effort inherits whatever the invoking session is set
to — which makes the recommendation unreproducible. `xhigh` and `max` exist and
were never measured; the sweep that would settle them has to count laps to
`done`, not cost per run.

`baseline` is the shared precedence rule and the language-agnostic standards.
The role skill on top carries what that node actually has to get right —
scope discipline, what makes a test worth writing, how to rank a finding
without inventing a severity score.

`reviewer` runs on the bigger model and the other two don't. Applying a guard
and writing asserts is mechanical; noticing that a green test pins a bug is
not.

## Orchestrator effort, and whether dev/qa should run on opus

Two questions worth real answers instead of a guess: what reasoning effort
should the *orchestrator* run at, and should `dev`/`qa` move off sonnet the
way `reviewer` already has. Tested both, headless, in isolated throwaway
repos — `claude -p ... --model sonnet --effort <level> --output-format json
--dangerously-skip-permissions`, one `tmux` session per run so they ran
concurrently. Every result was checked against a test oracle written
independently of the pipeline, not against what the pipeline claimed —
`subagent_stats.by_type` in the JSON result also confirms delegation
actually happened rather than the orchestrator quietly doing the work
itself.

**Orchestrator effort — `low` / `medium` / `high`, same trivial task, three
runs.** All three delegated correctly and shipped identical, correct code.
The difference was cost and whether the orchestrator used its own
`advisor()` self-check before calling a task done:

| effort | cost | wall time | orchestrator output tokens | called `advisor()` |
|---|---|---|---|---|
| high | $1.05 | 145s | 3399 | yes |
| medium | $0.92 | 116s | 2161 | yes |
| low | $0.73 | 77s | 171 | **no** |

**Recommendation: `medium`.** It's 12% cheaper than `high` with identical
output, and unlike `low` it still ran the self-check before declaring the
task done. `low` is real money saved but the one behavioral difference it
showed — skipping a safety step — is exactly the kind of corner-cutting
that's invisible on an easy task and only shows up when it matters.

**`dev`/`qa` model — sonnet (current) vs opus, three harder tasks, two reps
each, effort pinned at `medium`.** Each task had a real, unstated edge case:
merging touching intervals, validating a duration string, redacting emails
from free text. 11 of 12 runs shipped correct, verified code regardless of
model (the 12th hit a 10-minute timeout after a legitimate `back()` lap, not
a wrong answer). Cost scaled roughly 2× — $0.82/run average on sonnet,
$2.09/run on opus, and the only timeout was an opus run.

The one real difference: feeding every implementation the string `'٥'`
(Arabic-indic digit five, never mentioned in any spec) as input showed both
sonnet-authored duration parsers silently accepted it and returned `5` —
`reviewer` (opus in both configs) didn't catch it either time. The
opus-authored parser rejected it correctly — but only because its *own*
first draft had introduced a related bug (`re.IGNORECASE` without
`re.ASCII`) that its own review lap caught and dev fixed. Read plainly: dev
being opus didn't produce fewer mistakes, it produced a mistake shaped in a
way the same reviewer happened to catch. Two samples per cell — a pattern,
not a proof.

**Recommendation: keep `dev`/`qa` on sonnet.** No correctness win was found
to justify 2× the cost. The actual gap — `reviewer` missing an
out-of-ASCII-alphabet input — is a `reviewer` charter problem, not a `dev`
model problem, and costs nothing to fix directly: tell `reviewer` to probe
parsers and validators with non-ASCII input, rather than paying double on
every task hoping a different model happens to trip over the same class of
bug.

## Tools

The MCP server exposes exactly enough to walk the pipeline:

- `flow_start` — opens a named pipeline against a working directory. Two open
  flows can't share a directory: a diff `review` reads has to trace to one flow.
- `status` — which node a flow is on, which lap, and any open finding sent here
  to fix. Omit `flow` to see every open flow at once.
- `advance` — takes a `flow` and the node's `verdict`. `pass` moves on; `fail`
  **stays put**. A pass at `qa` needs `evidence`; a pass at `dev` needs
  `checks`
- `back` — takes a `flow`, routes the fix to the node that owns it, carrying a
  `note` of what to fix and a `kind` saying which sort of lap it is
- `reset` — takes a `flow`, back to `spec`, lap and findings cleared
- `flow_close` — takes a `flow`, removes it entirely; the slug and its `dir`
  are free for a new `flow_start`. Works on any flow, open or done.

`flow` is required on every one of these — never inferred, never defaulted —
so applying one flow's verdict to another can't happen by omission. `advance`
also will not move without a verdict, so walking past a "do not ship" has to be
a deliberate lie rather than an oversight. `back` will not move without a note,
so the finding lives in the state file instead of in whoever happened to be
reading the review — it survives a compaction, a new session, and a different
model picking the pipeline up.

`fail` does not route on its own. It stops and makes you call `back(to=...)`,
because the node that owns a fix is a judgement: a code finding at `review`
belongs to `dev`, not to the node just behind it.

## What a lap costs, and the four things that make it cost more

A lap is `dev` + `qa` + `review` plus a Stop-hook re-entry of the orchestrator,
which is already about two thirds of a run's spend. Everything in this section
is the same rule applied in four places: **mechanical where a program can
decide, judgement where it cannot, and the two never wear each other's
clothes.** The `dev` tree check below was already this.

**The pipeline used to forget between laps.** `back` overwrote a single `note`
and `advance` cleared it, so the `dev` at lap 3 could re-propose exactly what
lap 1 rejected. Every `back` now appends to a `history` on the flow, and
`status` renders a `previous laps on this flow — already tried, do not repeat:`
block ahead of the open finding — the three most recent, then a count of the
rest, because that text is re-emitted on every `status`, every Stop-hook fire
and every SessionStart. It lives in state rather than in the orchestrator's head
because both hooks re-inject `status` output and nothing else: memory held in
context dies on the first compaction.

**`dev` has to prove the code runs.** `advance(verdict="pass")` at `dev` needs
`checks` — the command run and its real output, the same shape `qa` has needed
for `evidence` since 0.6.0. mini-vise does not run them: it is language-agnostic
and stdlib-only and cannot lint an arbitrary repo, so it requires the evidence
instead, and `baseline` already points `dev` at whatever toolchain the repo
declares. The line that keeps the nodes apart is in `dev`'s charter:
self-*review* stays forbidden, self-*check* becomes mandatory. `dev` proves it
did not break what was there; `qa` proves the new behaviour is correct.

**A finding `dev` already had the evidence for no longer costs a `qa` spawn.**
When dev's own `checks` show a failure the orchestrator sends it straight back,
bounded two ways: only on evidence a node itself produced — never the
orchestrator's reading of a diff it did not open — and once per entry to `dev`,
because two dev laps with no `qa` between them give `dev` the orchestrator's
opinion twice and no independent signal.

**Every `back` says which sort of lap it is.** `kind="mechanical"` means a gate
should have caught it — a failing check, a broken import, a lint error. That is
debt, and it should trend to zero as the gates improve. `kind="judgement"` means
the work was genuinely contested. That is the pipeline working, and it should
not. Both land in `.mini-vise.log` alongside the finding text, so the record
outlives the flow that produced it — `history` is cleared by `reset` and deleted
by `flow_close`, the log is append-only and survives both. Without the split,
"is any of this helping?" has no answer: for these gates, for an effort change,
or for any rule anyone ever adds.

## The loop closes itself

One `Stop` hook. When the turn is about to end and a flow is open at a node
other than `spec`, it answers `decision: block` and hands back what `status`
would say — node, lap, and the open finding. The session cannot end
mid-pipeline, so the model re-enters with the brief instead of drifting off.

Flows parked at `spec` don't block: that node's only completion condition is
a human approving the proposal, and there is nothing for the model to do
while it waits — blocking there either burns a turn or pressures the model
into approving its own proposal, the exact failure `spec` exists to prevent.
Those flows surface through `systemMessage` instead, addressed to the person
whose approval is pending. If every open flow is parked at `spec`, the hook
doesn't block at all.

The escape hatch is the other half, and it is not optional. The hook payload's
`stop_hook_active` means "you already blocked this once"; on re-entry the hook
warns and lets go. Blocking twice is how a session becomes impossible to end —
[claude-code-harness](https://github.com/Chachamaru127/claude-code-harness)
measured 12 consecutive fires before fixing exactly this. One nudge, then out
of the way. Corrupt state also releases the session: a gate that cannot read
its own state must not be able to trap you in it.

This costs tokens. Re-entering the loop adds orchestrator turns, and the
orchestrator is already about two thirds of a run's spend. It buys a pipeline
that finishes.

What the tools still don't do is check whether a node told the truth. `qa` can
report a pass on a suite that pins a bug green — that happened here, and what
caught it was `reviewer`, not a tool.

`dev` is the one exception: `advance(verdict="pass")` at `dev` is refused if
the working tree and `HEAD` both look exactly as they did on entry —
`git status --porcelain` plus `git rev-parse HEAD`, before and after,
compared. `HEAD` is in the hash so a `dev` that commits its work still shows
a change and is not wedged; a `dev` that did nothing at all is still caught.
It degrades open, never blocks, when `dir` is unset, not a git repo, `git`
isn't on PATH, or the baseline was never recorded — a mechanical honesty
check, not a substitute for `qa`/`review`.

`SessionStart` re-announces every open flow after a compaction or a resume.
A `PreCompact` hook used to duplicate this; it's gone as of 0.7.0 — its only
outbound channel is `systemMessage` ("a message to the user"), which can't
inject into what a summarizer keeps, so it never did what it claimed to.

Every successful `flow_start`/`advance`/`back`/`reset`/`flow_close` call
appends one line to `.mini-vise.log` (`status` doesn't) — carrying the finding
text and its `kind` where there is one — a run record, not a
dependency: a write failure there never fails the tool call.

The same five calls also snapshot the flow's `dir`: a commit at
`refs/mini-vise/snapshots/<slug>`, one ref per flow, built through a
throwaway index so it never touches the real working tree, index, or `HEAD`.
`flow_close` snapshots before the entry is deleted, since closing an open
flow discards its finding. It's content only — `git add -A` honours
`.gitignore`, so `.mini-vise.json` and `.mini-vise.log` are never in the
snapshot, and restoring one never brings the flow state back. `status` shows
the ref once a snapshot exists. Same degradation rule as everything else
here: not a repo, `git` unavailable, `dir` unset or missing, or a repo with
no commits yet (no parent for the snapshot commit) — skip silently, never
fail the call.

To recover a snapshot: `git restore --source=refs/mini-vise/snapshots/<slug> .`
No wrapper tool — `git restore` and `git reflog show refs/mini-vise/snapshots/<slug>`
(to see every snapshot taken, not just the latest) already do it.

State lives in `.mini-vise.json` in the working directory (override with
`MINI_VISE_STATE`, which moves the log with it), one entry per flow. No
database, no config.

## Use it

```
/mini-vise:run <what to build or fix>
```

One entry point. It writes the spec, gets you to approve it, then walks the
pipeline — delegating each node and routing findings back — until `done`.

## Install

```bash
claude plugin marketplace add Rixmerz/claude-plugins
claude plugin install mini-vise@rixmerz
```

Needs `python3`. Nothing else — the server is stdlib-only.

## Test

```bash
python3 test_server.py
```

## Layout

The plugin bundle is `plugin/`; the repo root holds the tests and this file.
The nesting is not decoration — a marketplace `git-subdir` source needs a real
subdirectory. Pointed at `"."` it clones the root files and silently drops
every subdirectory, which installs "successfully" and leaves you a plugin with
no agents, skills, or hooks.
