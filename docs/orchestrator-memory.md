# orchestrator-memory

Problem: the orchestrator is a mandatory hop on every `back` — a subagent
returns to its parent and cannot call another subagent, so `qa -> dev` direct
does not exist in the harness. That hop is already paid for on every lap. It
currently buys a relay.

It should buy the one thing no subagent can have. `dev`, `qa` and `reviewer`
each start with a clean context by design (`orchestration` SKILL §intro) — so
the orchestrator is the only actor that has seen lap 1 *and* lap 2 *and* lap 3.
Today it discards that:

- `server.py:479` — `back` does `s.update(note=...)`. Single field, overwritten.
- `server.py:450` — `advance` does `note=None`. Cleared.
- `.mini-vise.log` records the sequence (`ts, flow, tool, node, lap, verdict`)
  but not the finding text, and nothing reads it back.

Consequence: the `dev` spawned at lap 3 cannot know it already failed twice, or
why. It can re-propose exactly what was rejected at lap 1. Laps are the
dominant cost and latency term in a run — each one is dev + qa + review plus a
Stop-hook re-entry of the orchestrator, which is already ~2/3 of spend.

Scope: three groups, A-C, in that order. Version 0.9.0 — new state field,
changed `render()`, changed charters. No new tool.

**The line this change must not cross.** The orchestrator reasons about *the
pipeline*, never about *the code*. If it starts diagnosing, it becomes a
reviewer that never read the diff, whose opinion outranks the real reviewer's
because it holds the routing — and the separation of nodes is the only check
this system has (README: "nothing verifies that a node told the truth"). The
mechanical test, stated in the charter: **anything the orchestrator adds to a
brief is quoted verbatim from a previous node's report, never authored by it.**
Librarian, not author. Carrying history is mandatory; writing new claims about
the code is forbidden.

## A. Lap history in state

**A1. `history` on the flow record** (`plugin/server.py`, `BLANK`)
Add `"history": []`. Append-only list of every `back` raised on this flow.
Entry shape, six keys, no more:

`{"lap": <int>, "from": <node that found it>, "to": <node it was routed to>, "note": <text, verbatim>}`

`note` is stored without the `[from <node>] ` prefix `note` carries — `from` is
already its own key, and the prefix is a rendering concern.

`note`/`note_for` stay exactly as they are. They carry a different meaning —
*the finding currently open at this node* — and the `note_for == node` gate in
`render()` depends on it. Do not collapse the two; this is additive.

**A2. Append on `back`** (`plugin/server.py`, `back` handler)
After the existing `s.update(node=to, lap=s["lap"] + 1, ...)`, append an entry
whose `lap` is the *new* lap value. Every history entry therefore has a unique
lap, and the currently-open finding is always the entry whose `lap` equals
`s["lap"]` — which is what lets A4 render prior laps without duplicating it.

`advance` does not touch `history`. That is the entire point: a finding being
closed is exactly when it becomes worth remembering.

**A3. `reset` clears it, `flow_close` discards it with the flow**
`reset` already rebuilds the record from `BLANK` (`server.py:410`) and lands at
`spec`, whose job is to write a new proposal — carrying findings from a spec
that no longer exists would be stale. No code change needed at `reset` beyond
A1 landing in `BLANK`; state it as a guarded behavior.

**A3a. `BLANK`'s list must not be shared.** `read()` builds each flow as
`{**BLANK, **s, ...}` (`server.py:61`), so a mutable default in `BLANK` would
be the *same list object* across every flow and every read — an append on one
flow would surface on another. Copy it per flow.

## B. Rendering it where it is actually read

**B1. `render()` shows prior laps** (`plugin/server.py`, `render()`)
When `history` has entries with `lap < s["lap"]`, emit a block **before** the
existing `open finding to fix here:` block:

```
previous laps on this flow — already tried, do not repeat:
  lap 2 [review->dev] 429 returned but Retry-After missing (spec AC4)
  lap 3 [qa->dev] test_retry_after asserts 0, spec says >=1
```

At most the three most recent. If more were dropped, one further line:
`(N earlier laps not shown — full history in <STATE path>)`. Unbounded
rendering is not acceptable here: `render()` output is re-emitted on every
`status`, every Stop-hook fire, and every SessionStart.

Filter is `lap < s["lap"]`, not "everything except the last" — a finding closed
by `advance` leaves `note` cleared but its history entry behind, and that entry
must still render on the next lap.

**B2. This is why it goes in state and not in the orchestrator's head.**
Both hooks re-inject `render()` and nothing else. A compaction or a resumed
session drops whatever the orchestrator was holding; only what `render()` emits
survives. Memory that lives in context is memory that dies on the first
compaction — the same failure `PreCompact` was deleted for in 0.7.0.

## C. The orchestrator's charter

**C1. Carve-out in §2** (`plugin/skills/orchestration/SKILL.md`)
Today: *"that finding **is** the brief; do not invent your own."* The rule is
right against freelancing and wrong against history — as written it forbids
carrying anything forward. Reword to separate the two: **authoring is
forbidden, carrying is mandatory.** Add a brief bullet: prior laps from
`status`, verbatim, when `lap > 1`. Restate the librarian test from the header
in one line.

**C2. A fifth `advisor()` moment in §4** — contradiction between nodes.
`qa` asserts X, `reviewer` says X is wrong. Neither can see the other's report;
only the orchestrator holds both. That is not a `dev` bug and routing it to
`dev` produces a lap that cannot converge. Same rule as the existing two:
main-loop tool, subagents have none.

**C3. §5 reads convergence instead of counting** (`orchestration` SKILL §5)
The lap counter alone cannot tell two opposite diagnoses apart, and with A1 it
no longer has to:
- the **same** finding recurring across laps -> the brief is wrong, or the node
  cannot fix it from where it sits;
- a **different** finding each lap -> the spec is underspecified; that is a
  `back(to=spec)`, not another `dev` lap.

Keep the existing "four or more, stop and tell the user" floor.

**C4. Effort is documented but not enforced** (all three charters)
The README's measured recommendation ran with effort pinned at `medium`, but no
charter declares an `effort` field — subagent frontmatter supports one, and
without it all three inherit whatever the invoking session happens to be set
to. The documented configuration is therefore not reproducible. Add
`effort: medium` to `dev.md`, `qa.md`, `reviewer.md`: exactly what was measured,
no new claim. The orchestrator's own `medium` cannot be pinned from a plugin —
it is a session setting — so the README states it instead of promising it.

## Acceptance

1. A new flow's record has `history == []`, and two flows opened in one process
   do not share the list object: appending to one leaves the other empty.
2. A state file written before 0.9.0 (no `history` key) loads without error and
   reads back as `history == []`.
3. Each `back` appends exactly one entry with keys `lap`, `from`, `to`, `note`;
   `lap` equals the flow's lap *after* the increment; `note` is the caller's
   text with no `[from <node>] ` prefix.
4. `advance` with either verdict leaves `history` byte-identical.
5. `reset` empties `history`; `flow_close` removes it with the flow.
6. Two `back` calls produce two entries in call order; the second does not
   overwrite the first.
7. `render()` at lap 1, or with empty `history`, emits no previous-laps block.
8. `render()` at lap 3 shows the lap-2 entry in the previous-laps block and the
   lap-3 finding in `open finding to fix here:` — the lap-3 note appears once,
   not twice.
9. After a finding is closed by `advance`, its history entry still renders on a
   later lap.
10. With six history entries, `render()` shows the three most recent and one
    line naming the count not shown; total block is bounded regardless of
    history length.
11. Both hooks' text inherits the previous-laps block (they call `render()`).
12. `orchestration` SKILL §2 states that authoring is forbidden and carrying is
    mandatory, names prior laps as a brief input, and carries the
    quoted-verbatim test.
13. `orchestration` SKILL §4 lists the contradiction-between-nodes advisor
    moment, and still says subagents have no advisor.
14. `orchestration` SKILL §5 distinguishes a repeated finding from a differing
    one, and keeps the four-lap floor.
15. `dev.md`, `qa.md`, `reviewer.md` each declare `effort: medium`; `model`
    values are unchanged (`sonnet`, `sonnet`, `opus`).
16. `VERSION == "0.9.0"` and `plugin.json` / `marketplace.json` agree — the
    existing consistency test covers this once bumped.
17. `python3 test_server.py` and `python3 test_hook_ctx.py` both green.
18. README and CHANGELOG updated: lap history, the librarian rule, the pinned
    effort and what it does and does not claim, 0.9.0 entry.

## Not in scope

- **A new tool to read history.** `status` already renders it and the state
  file is JSON on disk. A tool for a field that is already in every `status`
  output earns nothing.
- **Note text in `.mini-vise.log`.** The log is a run record keyed to calls;
  history is flow state. Two shapes for one fact is how they drift apart.
- **Raising `dev`'s effort to `xhigh`.** The README's sweep measured `low` /
  `medium` / `high` only — `xhigh` and `max` exist and were never tested, and
  `xhigh` is Claude Code's own default for coding work, so the ceiling that was
  measured was not the real ceiling. That is a real experiment, not a config
  edit, and shipping an untested level as a default is the thing this repo does
  not do. Trigger: a sweep measuring **laps to `done`**, not cost per run —
  the existing data is per-run on tasks that mostly converged in one lap, which
  cannot answer the question that matters here.
- **Moving `dev`/`qa` off `sonnet`.** Concluded, documented in README: 2x cost,
  no correctness win. Unchanged by anything here.
- **Collapsing `note`/`note_for` into `history`.** Different meaning, live gate
  in `render()`, and a refactor nobody asked for.
- **Letting a subagent read the history itself.** It reaches them through the
  brief, which keeps the orchestrator accountable for what it passes on. A
  subagent that reads flow state is a subagent that can route itself.

Version: 0.9.0.
