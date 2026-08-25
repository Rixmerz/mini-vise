# mini-vise

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

| Agent | Does | Doesn't | Skills | Model |
|---|---|---|---|---|
| — | *`spec` has no agent: the orchestrator writes it, a human approves it* | | `orchestration` | — |
| `dev` | writes the code | write tests, review itself | `baseline`, `implementing` | sonnet |
| `qa` | writes and runs tests, reports real output | edit product code to go green | `baseline`, `testing` | sonnet |
| `reviewer` | adversarial review + debugging, read-only | fix anything | `baseline`, `reviewing` | opus |

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
  **stays put**
- `back` — takes a `flow`, routes the fix to the node that owns it, carrying a
  `note` of what to fix
- `reset` — takes a `flow`, back to `dev`, lap and findings cleared

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

## The loop closes itself

One `Stop` hook. When the turn is about to end and the pipeline is not at
`done`, it answers `decision: block` and hands back what `status` would say —
node, lap, and the open finding. The session cannot end mid-pipeline, so the
model re-enters with the brief instead of drifting off.

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

State lives in `.mini-vise.json` in the working directory (override with
`MINI_VISE_STATE`), one entry per flow. No database, no config.

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
