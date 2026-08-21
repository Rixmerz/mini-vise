---
name: implementing
description: How to implement a change well — scope discipline, reading before writing, and what not to touch. Use when writing or editing product code, in any language, at the dev node of the pipeline.
---

# implementing

> Sits on top of `baseline`. That file's precedence rule settles conflicts.

## Before writing

1. **Read the target and its callers.** Grep for every call site. A change you
   make in one place is a contract change everywhere it is used.
2. **Find the existing pattern.** This repo already handles errors, config,
   logging, and I/O some way. Use that way.
3. **Name the smallest edit that satisfies the task.** Write it down. If your
   plan touches files the task never mentioned, that is scope creep — stop and
   say so instead.

## While writing

- Change behavior in one place, not three. Duplicated logic is a bug waiting
  for one copy to be updated.
- Guard clauses over nesting. Return early.
- Make illegal states unrepresentable where the language lets you; validate
  where it does not.
- A shortcut you take on purpose gets a comment naming its ceiling and the
  upgrade path — `# global lock; per-key locks if throughput matters`. Silent
  shortcuts read as ignorance.
- Public functions get a one-line doc saying what they do and what they raise.
  Private helpers usually need nothing.

## Boundaries — not your node

- **Do not write tests.** `qa` owns that node and writes better tests without
  your assumptions baked in.
- **Do not review your own work** or declare it correct. `reviewer` owns that.
- **Do not refactor adjacent code** because you were in the neighborhood.
  Report it as a finding instead.
- **Do not commit** unless asked.

## Report

File by file: what changed and why. Then, explicitly:
- what you deliberately left out,
- any assumption you had to make,
- anything you noticed that is broken but out of scope.
