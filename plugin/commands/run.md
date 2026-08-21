---
description: Run a task through the mini-vise pipeline — spec, then dev, qa and review by subagent
argument-hint: <what to build or fix>
---

Use the `orchestration` skill and drive the mini-vise pipeline to `done` for:

**$ARGUMENTS**

Non-negotiable, in this order:

1. Call `status` first.
2. Write the change proposal at the `spec` node — OpenSpec if this repo has it —
   and get the user to approve it before any code exists.
3. Delegate every other node to its subagent (`mini-vise:dev`, `mini-vise:qa`,
   `mini-vise:reviewer`). Do not do a node's work yourself.
4. `advance` with the verdict the subagent actually gave. On a `fail`, `back` to
   whoever owns the finding, with a note they can act on.
5. Keep going until `status` says `done` with no blocking findings.
