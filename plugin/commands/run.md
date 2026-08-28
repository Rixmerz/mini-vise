---
description: Run a task through the mini-vise pipeline — spec, then dev, qa and review by subagent
argument-hint: <what to build or fix>
---

Use the `orchestration` skill and drive the mini-vise pipeline to `done` for:

**$ARGUMENTS**

## First, check the task is worth a pipeline

A lap is `dev` + `qa` + `review` plus a re-entry of the orchestrator. Four
laps on a task that did not need them is the most expensive way to be right.
**Say so and stop** if the task is any of these:

| Shape | Why not | Do instead |
|---|---|---|
| docs, packaging, config, manifest edits only | no behavior to regress; three nodes verify an artifact nobody ran | edit it, run it once, done |
| exploratory — "try X and see" | the spec node has nothing to write down yet | explore first, pipeline the result if there is one |
| the risk is in **what to build**, not in building it | every node validates conformance to a spec nobody attacked | argue the spec with the user, then decide |
| one file, one obvious edit | the spec costs more than the change | just do it |

The pipeline earns its cost on **mechanical work with real blast radius** —
migrations, wide refactors, anything touching auth, money, or data loss —
where the spec is the easy part and the execution is the hard one. There, a
clean context and an adversarial charter produce attention the main loop
cannot.

If none of the disqualifying shapes fit, continue.

Non-negotiable, in this order:

1. Call `status`, then `flow_start(slug, dir)` if no flow already covers this
   task.
2. Write the change proposal at the `spec` node — OpenSpec if this repo has it —
   and get the user to approve it before any code exists.
3. Delegate every other node to its subagent (`mini-vise:dev`, `mini-vise:qa`,
   `mini-vise:reviewer`), passing the flow slug in the brief. Do not do a
   node's work yourself.
4. Run the product yourself before `done` — see the `orchestration` skill §4.
   No node can do this for you.
5. `advance` with the verdict the subagent actually gave. On a `fail`, `back` to
   whoever owns the finding, with a note they can act on.
6. Keep going until `status` says `done` with no blocking findings.
