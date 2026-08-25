# multi-flow

Problem: one state file, one pipeline. Two features in flight need two
orchestrator sessions running blind of each other, or one session serializing
work that could run in parallel.

## Schema

`.mini-vise.json` becomes `{"flows": {"<slug>": <old per-flow shape>}}`. Each
flow keeps node/lap/note/note_for/spec_path/evidence/dir independently.

Back-compat: a file with a top-level `node` (0.5.0 shape) is read once and
migrated in-memory to `{"flows": {"main": <that object>}}` — not rewritten to
disk until the next `write()`, so a crash mid-migration leaves the original
file intact.

## Deltas

**a. `flow_start(slug, dir=None)`** (`plugin/server.py`, new tool)
- Creates `slug` at `BLANK` if absent; error if it already exists (use
  `status` to inspect, `reset(flow=...)` to restart).
- `dir` defaults to cwd. **Refuses** (ValueError) if `dir` matches an *open*
  flow's `dir` (any flow not at `node="done"`) — two code-touching flows in
  one working tree make `git diff` unattributable to a reviewer, so this is a
  hard block, not a warning. Different `dir` (e.g. a `git worktree add`) or a
  `dir` shared only with a `done` flow is fine.

**b. `flow` becomes a required argument** — `advance`, `back`, `reset` all
take it, unconditionally (not "when >1 flow exists": JSON Schema can't
express that condition and it'd be forgotten exactly when it matters). Error
naming valid slugs if `flow` is missing or unknown. `status` takes an
*optional* `flow`; omitted, it renders every flow, each with its own open
finding.

Every `advance`/`back`/`status` response starts with `[flow: <slug>]` so
applying one flow's verdict to another shows up in the transcript instead of
being silent.

**c. Both hooks operate over all flows, not `s["node"]`.**
(`plugin/hook_stop.py`, `plugin/hook_ctx.py`)
- Stop: blocks while **any** flow's node is not `done`; the block text lists
  every open flow (slug + one-line render). Releases only once all are done.
  `stop_hook_active` behavior unchanged — one nudge total per stop, not one
  per flow.
- SessionStart: `additionalContext` names every open flow.
- PreCompact: `systemMessage` lists what to keep verbatim per open flow.
- Corrupt/unparseable state: both stay non-blocking — never `decision`, never
  a node-specific claim. `hook_ctx.py` returns `{}`; `hook_stop.py` returns
  `{"systemMessage": "...not gating."}`, which is its own pre-existing,
  correct behavior, not a regression. The invariant carried over from
  context-handoff's AC11 is "does not gate on unreadable state", not
  byte-identical output between the two hooks.

**d. Attribution outside `server.py`** (orchestration-only, no server change)
- Every subagent spawned for a flow gets `name: "<node>-<slug>"` (e.g.
  `dev-authz`), so `SendMessage(to: "dev-authz")` resumes the right one by
  name across a compaction, without recalling an agentId.
- The three charters' wire-style report block gets a new non-compressible
  first line: `flow: <slug>`, alongside `verdict:`.

## Acceptance

1. `flow_start("a")` then `flow_start("b")`, different `dir` → both open,
   independent state.
2. `flow_start("b", dir=<a's dir>)` while `a` is open → ValueError, `b` not
   created.
3. `flow_start("b", dir=<a's dir>)` after `a` reaches `done` → succeeds.
4. `advance(flow="a", verdict="pass")` moves only `a`; `b`'s node/lap
   untouched.
5. Evidence gate at `qa` fires per-flow — missing evidence on `a` doesn't
   block `b`.
6. `back(flow="a", ...)` raises only `a`'s lap.
7. `advance`/`back` with no `flow` or an unknown slug → error listing valid
   slugs; state unchanged.
8. `status()` with two open flows renders both, each with its own open
   finding if any.
9. Stop hook: blocks while either flow is open; the block text names both;
   only silent once both are `done`; still one nudge per `stop_hook_active`,
   not per flow.
10. SessionStart `additionalContext` names every open flow.
11. Corrupt/unparseable state → both hooks stay non-blocking (never
    `decision`); `hook_ctx.py` returns `{}`, `hook_stop.py` keeps its own
    pre-existing `systemMessage` behavior (regression guard on context-
    handoff's AC11, not a claim the two hooks match byte-for-byte).
12. A 0.5.0 single-slot state file loads as `{"flows": {"main": ...}}}` with
    no error.
13. `test_hook_ctx.py`'s existing 15 tests are rewritten for the flows shape,
    not left asserting the old single-slot one.
14. Two flows through the full pipeline in parallel (different `dir`s) each
    reach `done` with correct, non-crossed evidence and spec_path.

## Not in scope

- The advisor-at-two-moments rule (spec review, spec-vs-bug routing) — a
  separate edit to the `orchestration` skill, not bundled with a change that
  already touches `server.py`, both hooks, three charters, two test files.
- Any git operation from `server.py` (worktree creation, branch checkout).
  `dir` is caller-supplied; the server only checks it, never creates it.
- Cross-flow dependency ordering (flow B waiting on flow A). Flows are
  independent by construction; sequencing them is the orchestrator's call.

Version: 0.6.0 (breaking state schema, new tool).
