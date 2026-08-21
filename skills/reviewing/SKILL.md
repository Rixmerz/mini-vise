---
name: reviewing
description: How to review a finished change adversarially and diagnose a bug evidence-first — what to hunt for, how to rank findings by attacker or user preconditions, and how to reach a verdict. Use when reviewing a diff or debugging a failure, in any language, at the review node of the pipeline.
---

# reviewing

> Sits on top of `baseline`. That file's precedence rule settles conflicts.

Read-only. You report; you never fix. A fix you apply is a change nobody
reviewed.

## Read in this order

1. **The diff**, whole, before forming an opinion.
2. **What the diff touches** — the functions it changed, and their callers.
   Grep every call site. Most regressions live outside the diff.
3. **The tests**, last. Green tests prove the tests passed, not that the change
   is correct. Ask what a passing suite would still let through.

## Hunt list

- **Regressions in callers** — a changed return type, a new raise, a shifted
  default, an argument that silently reorders.
- **Silent breakage** — a swallowed error, a fallback that hides a failure, a
  retry that masks a real outage, a `None`/`nil`/`undefined` that flows on.
- **Boundary handling** — unvalidated input from user, network, file, or env;
  off-by-one at the edge the change moved; empty collection, zero, negative.
- **Concurrency and state** — shared mutable state, a check-then-act race, a
  lock not held across the whole invariant.
- **Security** — injection into a query, shell, or template; authz checked in
  one path and not its sibling; a secret in source, log, or error text. Cite
  the CWE when you name one.
- **Over-engineering** — an abstraction with one caller, a dependency that
  replaced ten lines, config for a constant, dead flexibility.

## Debugging — evidence before cause

When something is actually broken:

1. **Reproduce it.** A bug you cannot trigger is a hypothesis.
2. **Narrow it** — bisect the input, the commit, or the layer until the
   smallest failing case is in front of you.
3. **Attribute the layer** before naming a line. Is it the caller's input, this
   function's logic, or the dependency's behavior?
4. **Then read the code** and explain the mechanism, step by step.

A plausible story is not a diagnosis. If you did not observe it, say
"unverified" next to it.

## Ranking — preconditions, never a score

Do not produce a CVSS number or any invented severity score. You cannot know
the deployment, the exposure, or the data classification, and a fabricated
number carries more authority than its evidence. Rank on what an attacker or
an unlucky user needs in order to hit it:

1. **Nothing** — reachable with no auth, no special input, ordinary use.
2. **A normal account or a specific but ordinary input.**
3. **Privileged access, an unusual configuration, or a race won.**
4. **Conditions that do not occur in this deployment** — note it, rank it last.

## Verdict

Every finding as `file:line — what breaks, under what input`, severest first.
Separate **blocking** from **non-blocking**; do not smuggle a preference into
the blocking list.

Then one line: **ship** or **do not ship**. A clean review is a real verdict —
say "ship, nothing blocking" plainly rather than inventing a finding to look
thorough.
