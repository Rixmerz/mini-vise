---
name: testing
description: How to write tests that catch real bugs and report results honestly — coverage of failure paths, real output, no green-washing. Use when writing or running tests, in any language, at the qa node of the pipeline.
---

# testing

> Sits on top of `baseline`. That file's precedence rule settles conflicts.

## Find the harness first

Look for the runner this repo already uses — a test directory, a `Makefile`
target, a `scripts.test` entry, a CI workflow. Match its structure, its
naming, and its fixtures. Never introduce a second test framework alongside an
existing one.

If there is genuinely no harness, the smallest thing that runs is right:
one test file, plain asserts, no fixtures, no config.

## What to test

Test the behavior that would break silently. In rough priority:

1. **The boundary the change actually moved.** If a guard was added at 15,
   test 14, 15, 16 — and 0 if it is reachable.
2. **Error paths.** The branch that raises, the input that is rejected, the
   dependency that fails. Untested error handling is decoration.
3. **The contract at the entry point**, not each private helper. Testing
   internals pins the implementation and blocks refactors.
4. **The regression itself.** For a bug fix, one test that fails against the
   old code. If it would have passed before, it does not test the fix.

Skip: getters, one-line pass-throughs, generated code, and a happy-path test
per function written to raise a coverage number.

## Structure

Arrange, act, assert — in that order, visually separated. One behavior per
test. The name says the condition and the expected result, so a failure is
readable without opening the file. No logic in a test: no loops that hide
which case failed, no branching on the result.

## Honesty — this is the whole job

- **Run them. Paste the real output**, including the command.
- **Never edit product code to make a test pass.** A failing test is a
  finding, and it belongs in your report. Handing it to `reviewer` is the
  correct outcome, not a failure on your part.
- **Never weaken an assertion** to go green — no widened tolerance, no
  removed case, no `skip` added quietly.
- A flake is a result too. Say it flaked and how often.

## Report

The command, its verbatim output, what each new test pins, and every failure
you could not explain.
