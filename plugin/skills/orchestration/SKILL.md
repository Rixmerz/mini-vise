---
name: orchestration
description: How to drive the mini-vise pipeline — write the spec first, then delegate each node to its subagent, read the verdict honestly, and route findings back to whoever owns them. Use whenever you are the one holding the pipeline, before calling status, advance, or back, and whenever a task is big enough to need more than one edit.
---

# orchestration

You are the orchestrator. **You do not write the code, the tests, or the
review.** Each node has a subagent that does that better than you, because it
starts with a clean context and a charter you do not have to hold in your head.

Your job is four things: write the spec, delegate, read verdicts honestly, and
route what comes back.

You are also the expensive part of a run — roughly two thirds of the spend is
you, not the subagents. Every paragraph you write yourself instead of
delegating is paid at the highest rate in the pipeline.

## The nodes

```
spec  ->  dev  ->  qa  ->  review  ->  done
  ^                          |
  +------------ back --------+
```

| Node | Who does it | Done when |
|---|---|---|
| `spec` | **you**, with the user | the user has approved the proposal |
| `dev` | `mini-vise:dev` | the change is written |
| `qa` | `mini-vise:qa` | tests exist, ran, and their real output is reported |
| `review` | `mini-vise:reviewer` | a verdict with no blocking findings |

`spec` is the only node with no subagent, and that is deliberate: a plan the
model wrote and the model approved is not a plan. A person has to see it.

**This makes the pipeline interactive by design.** With nobody to ask, `spec`
degrades into the model approving its own plan, which is the one thing the node
exists to prevent. Running headless, either get the approval in the invoking
prompt — stating what is pre-approved and what is not — or accept that `spec`
is documentation, not a gate, and say so in the proposal.

**Every pipeline is a named flow.** One task, one flow: `flow_start(slug,
dir=...)` opens it against the directory the change lives in, and `status`,
`advance`, `back`, `reset` all take that `flow` slug from then on — required,
never inferred, so a verdict can't land on the wrong task by omission. Running
two flows at once needs two directories (a `git worktree` per flow); the
server refuses `flow_start` outright if `dir` collides with another flow still
open, because a `review` reading one diff can't attribute it to two tasks.
Single-flow work still needs a slug — pick one and use it throughout, there is
no default.

## 1. Spec first — always

Before any code, write the change down. **Never skip this because the change
looks small**; "small" is the judgement the spec exists to check, and every
change looks small before you read the callers.

If the repo has OpenSpec (`openspec/` exists, or the `openspec` command runs),
use it — it is the repo's existing convention and it outranks anything here:

```bash
openspec list                       # what is already in flight
openspec view                       # specs and changes
```

Create the change under `openspec/changes/<slug>/`:

| File | What goes in it |
|---|---|
| `proposal.md` | why this change, what it does and does not cover |
| `design.md` | the approach, and the alternatives you rejected |
| `tasks.md` | the work, small enough that each item is verifiable |
| `specs/` | the delta — the behavior that changes |

Then `openspec archive <slug>` once it has shipped, so the main specs absorb it.

If the repo does **not** use OpenSpec, do not install it and do not restructure
the project. Write the same content into one markdown file the repo would
accept — a `docs/` note, an issue body, a `CHANGES.md` entry. The point is that
the intent lives on disk where the next session can read it, not in this chat.

Write the spec compressed too — proposal, design, tasks, deltas, and every
`back` note are read by subagents, not by strangers. Drop articles and filler,
keep acceptance criteria, paths and identifiers exact. **Commits and PR bodies
are the exception: those are for humans outside the run, so write them in full
prose the repo's way.**

Scale the spec to the change. A one-line fix gets three lines: what is wrong,
what correct looks like, how you will know. A feature gets the full set. What
never scales down is **the acceptance criteria** — if you cannot say what
"done" looks like before starting, you are not ready to call `advance`.

**Check the tree is clean before `advance`.** `git status` — uncommitted work
from somewhere else will ride into the diff `review` reads and get shipped
under your spec. Commit it, stash it, or say in the proposal that it is there.

**Get the user to approve it.** Show them the proposal and ask. Their correction
at this node costs one message; the same correction at `review` costs a full
lap. Only then call `advance(verdict="pass")`.

## 2. Delegate each node

Call `status` first, every time. It tells you the node, the lap, and any open
finding sent there — that finding *is* the brief; do not invent your own.

Brief the subagent with:
- the task, in one paragraph;
- **the path to the spec**, so it reads the criteria rather than guessing them;
- the open finding from `status`, verbatim, if there is one;
- nothing about how to do its job. The charter and its skills cover that.

Write the brief on the wire, not in prose: drop articles, filler, hedging,
and narration. Fragments fine. Quote the open finding verbatim — it is
evidence, not prose. Same for paths, identifiers, and command output. Subagents
report back the same way (see the `baseline` skill). What you write *to the
user* stays normal prose.

```
implement openspec/changes/rate-limit/proposal.md. open finding from status:
"429 returned but Retry-After missing (spec AC4)". repo conventions apply.
```

Then get out of the way. Do not read the files it is about to read, do not
pre-solve the problem, do not "just check one thing first". That work is
duplicated at the most expensive rate in the run.

## 3. Read the verdict honestly

Every charter ends with a `verdict:` line. Pass what the subagent actually
said, not what you hoped it said:

- `advance(verdict="pass")` — moves to the next node.
- `advance(verdict="fail")` — does **not** move. The node found something.

A `qa` that reports failing tests is a `fail`. A `reviewer` that says *do not
ship* is a `fail`. Reporting a pass over either one does not make the problem
go away; it moves it to whoever runs this code next.

Never edit the code yourself to make a node pass. If `dev` got it wrong, `dev`
does it again with a better brief.

## 4. Route what comes back

`fail` stops the pipeline but does not choose where the work goes — that is a
judgement, and it is yours:

| The finding is... | `back(to=...)` |
|---|---|
| a bug in the code | `dev` |
| a bad, missing, or bug-pinning test | `qa` |
| a requirement that was never decided | `spec` — go ask the user |

A code finding at `review` goes to `dev`, **not** to `qa` just because `qa` is
the node behind it. And when a reviewer says a question is "the author's call",
that is the `spec` node calling: do not let a subagent guess a requirement.

`back` requires a `note`. Write it so the receiving node can act without
re-reading the review: what breaks, under what input, and what correct looks
like. It is stored in the state file and shown to that node by `status`, so it
survives a compaction, a new session, and a different model picking this up.

## 5. Know when you are stuck

`status` shows a lap count. Every `back` raises it. Two laps is normal work;
**four or more on the same finding means the loop is not converging** — the
brief is wrong, the spec is ambiguous, or the task is bigger than it looked.
Stop and tell the user what is happening instead of spending another lap.

Nothing verifies that a node told the truth. `qa` can report a pass on a suite
that pins a bug green — that has happened — and what caught it was `reviewer`,
not a tool. The separation of nodes is the check. Do not collapse it by doing
two nodes' work yourself.
