# orchestrator-memory

Problem: laps are the dominant cost and latency term in a run. Each one is
dev + qa + review plus a Stop-hook re-entry of the orchestrator, which is
already ~2/3 of spend. Four things make laps more expensive than they need to
be, and all four share one shape.

**1. The pipeline forgets between laps.** The orchestrator is a mandatory hop
on every `back` — structural, not a design choice: a subagent returns to its
parent and cannot call another subagent, so `qa -> dev` direct does not exist
in the harness. That hop is paid on every lap and buys a relay. It is also the
only actor that has seen every lap, since `dev`, `qa` and `reviewer` each start
with a clean context by design. It discards what it alone holds:

- `server.py:494` — `back` does `s.update(note=...)`. Single field, overwritten.
- `server.py:465` — `advance` does `note=None`. Cleared.
- `.mini-vise.log` records the sequence but not the finding text, and nothing
  reads it back.

So the `dev` at lap 3 cannot know it already failed twice, or why, and can
re-propose exactly what was rejected at lap 1.

**2. Nothing at `dev` requires the code to run.** `advance` has two gates
today: `qa` needs `evidence` (`server.py:448`), and `dev` needs a changed tree
or `HEAD` (D3). D3 proves only that `dev` touched something — not that it
parses, imports, lints, or leaves the existing tests green. `qa` discovers
that, and discovering it there costs a full lap for something the repo's own
linter decides in seconds.

**3. A finding `dev` already had the evidence for still costs a `qa` spawn.**
The orchestrator reads dev's report, spawns `qa` anyway, and `qa` reports what
dev's own output already said.

**4. Nothing distinguishes a lap that should never have happened from a lap
that is the pipeline working.** Both are `back(to=..., note=...)` with
`lap += 1`. Indistinguishable in state and in the log — so "is any of this
helping?" is unanswerable, for gates, for effort, and for rules alike.

Scope: six groups, A-F. Version 0.9.0 — new state fields, changed `render()`,
a third `advance` gate, changed charters. No new tool.

**The one axis.** Every group below is the same rule applied in a different
place: **mechanical where a program can decide, judgement where it cannot, and
the two never wear each other's clothes.** D3 is already this — the README
calls it "the one honesty check that is mechanical rather than another agent".

**The line none of this may cross.** The orchestrator reasons about *the
pipeline*, never about *the code*. If it starts diagnosing it becomes a
reviewer that never read the diff, whose opinion outranks the real reviewer's
because it holds the routing — and the separation of nodes is the only check
this system has (README: "nothing verifies that a node told the truth"). The
mechanical test, stated in the charter: **anything the orchestrator adds to a
brief, or acts on, is quoted verbatim from a node's own report — never
authored by it.** Librarian, not author.

## A. Lap history in state

**A1. `history` on the flow record** (`plugin/server.py`, `BLANK`)
Add `"history": []`. Append-only, one entry per `back`. Five keys, no more:

`{"lap": <int>, "from": <node that found it>, "to": <node routed to>, "note": <text, verbatim>, "kind": <"mechanical"|"judgement">}`

`note` is stored without the `[from <node>] ` prefix that `note` carries —
`from` is already its own key, and the prefix is a rendering concern. `kind` is
group E.

`note`/`note_for` stay exactly as they are. They mean something different —
*the finding currently open at this node* — and `render()`'s
`note_for == node` gate depends on it. Additive, not a refactor.

**A2. Append on `back`** (`back` handler)
After the existing `s.update(node=to, lap=s["lap"] + 1, ...)`, append an entry
whose `lap` is the *new* value. Every entry therefore has a unique lap, and the
open finding is always the entry whose `lap == s["lap"]` — which is what lets
B1 render prior laps without duplicating it.

`advance` does not touch `history`. That is the point: a finding being closed
is exactly when it becomes worth remembering.

**A3. `reset` clears it; `flow_close` discards it with the flow.**
`reset` rebuilds from `BLANK` (`server.py:425`) and lands at `spec`, whose job
is a new proposal — findings against a spec that no longer exists are stale.
No code change beyond A1 landing in `BLANK`; state it as guarded behavior.

**A3a. `BLANK`'s list must not be shared.** `read()` builds each flow as
`{**BLANK, **s, ...}` (`server.py:61`), so a mutable default would be the *same
list object* across every flow and every read — an append on one would surface
on another. Copy it per flow.

## B. Rendering it where it is actually read

**B1. `render()` shows prior laps** (`render()`)
When `history` has entries with `lap < s["lap"]`, emit a block **before** the
existing `open finding to fix here:` block:

```
previous laps on this flow — already tried, do not repeat:
  lap 2 [review->dev, judgement] 429 returned but Retry-After missing (spec AC4)
  lap 3 [qa->dev, mechanical] ruff E501 x3 in ratelimit.py
```

At most the three most recent, then one line: `(N earlier laps not shown — full
history in <STATE path>)`. Unbounded rendering is not acceptable: `render()`
output is re-emitted on every `status`, every Stop-hook fire, and every
SessionStart.

Filter is `lap < s["lap"]`, not "all but the last" — a finding closed by
`advance` leaves `note` cleared but its history entry behind, and that entry
must still render on the next lap.

**B2. Why state and not the orchestrator's context.** Both hooks re-inject
`render()` and nothing else. A compaction or a resumed session drops whatever
the orchestrator held. Memory that lives in context dies on the first
compaction — the failure `PreCompact` was deleted for in 0.7.0.

## C. A `checks` gate at `dev`

**C1. `advance(verdict="pass")` at `dev` requires `checks`** (`advance` handler)
Same shape as the `qa` evidence gate at `server.py:448`: a `checks` argument,
rejected when absent or blank, with a message naming what it wants — the
command run and its real output, verbatim. Stored as flow field `checks`.

mini-vise cannot run the checks itself: it is language-agnostic and
stdlib-only, and cannot know how to lint an arbitrary repo. It does not have
to. `baseline` already tells `dev` to obey the toolchain the repo declares
(`ruff.toml`, `.eslintrc`, `pyproject.toml`, `go.mod`, `tsconfig`). The gate
only requires that `dev` **ran** it.

Like the `qa` gate, it does not judge the content — it refuses the empty. Same
accepted trade-off the repo already states: nothing verifies that a node told
the truth.

**C2. The line that keeps `dev` from becoming `qa`** (`plugin/agents/dev.md`)
The gate is about **checks that already existed**, never new tests.

- `dev` proves it did not break what was there.
- `qa` proves the new behaviour is correct.

Write that distinction into the charter explicitly. Without it the gate reads
as "dev writes tests" and two nodes collapse into one. `dev.md` keeps "do not
write tests and do not review your own work" — self-*review* stays forbidden;
self-*check* becomes mandatory. Say both, next to each other, or the charter
contradicts itself.

**C3. `checks` is shown to `qa`** (`render()`)
At node `qa`, render the stored `checks` the way `evidence` is rendered at
`review` (`server.py:94`). `qa` re-running what `dev` already ran is the
duplicated load this group exists to remove.

**C4. Migration.** A flow already sitting at `dev` when 0.9.0 lands has no
`checks`, and its next `advance` will be refused until the orchestrator
supplies one. That is correct and not a regression — same break the `qa`
evidence gate made when it was added. Note it in the CHANGELOG.

## D. The orchestrator may short-circuit on a node's own evidence

**D1. The rule** (`plugin/skills/orchestration/SKILL.md` §3)
When `dev`'s own reported `checks` show a failure, the orchestrator calls
`advance(verdict="fail")` and `back(to="dev", ...)` **without spawning `qa`**.
It is already in the path; spawning `qa` to be told what dev's output already
said is a wasted node.

No server change: `advance(verdict="fail")` at `dev` already stays put and
demands a `back`.

**D2. What it may act on, and nothing else.** Evidence the node itself
produced — a `verdict:` line, a failing command in `checks`. Never the
orchestrator's own reading of the diff. `dev` reports green and the
orchestrator has a hunch: it spawns `qa`. The hunch is not evidence, and acting
on it is the line in the header being crossed.

**D3. The bound — one short-circuit per entry to `dev`.**
Two consecutive dev laps with no `qa` between them are *worse* than one `qa`
spawn: `dev` gets no independent signal, only the orchestrator's opinion twice.
That is a dev/orchestrator ping-pong with no external check — the exact failure
the pipeline exists to prevent.

So: after one `dev -> dev` short-circuit, the next move out of `dev` must be
`advance` to `qa`. Derivable from `history` (consecutive tail entries with
`from == "dev"` and `to == "dev"`), and `render()` says so at `dev` when the
short-circuit is already spent.

Charter rule, not a server gate — the orchestrator can always be wrong about
which node owns a fix, and hard-blocking a legitimate `back` is worse than the
ping-pong it would prevent. `render()` making it visible is the enforcement.

## E. Classify the lap

**E1. `back` takes a `kind`** (`back` handler, TOOLS)
Required, one of `mechanical` or `judgement`. Stored on the history entry (A1)
and rendered (B1).

- **mechanical** — a gate should have caught it: a failing check, a broken
  import, a lint error, a test that was already red. Debt. Should trend to
  zero as gates improve.
- **judgement** — the work was genuinely contested: a design disagreement, a
  requirement nobody decided, a real bug found by reading. The pipeline working
  as designed. Should **not** trend to zero.

Required rather than defaulted: a default would be guessed, and a guessed
classification is worse than none — it reads as data.

**E2. It goes in the log too** (`log_call`)
Add `kind` and `note` to the JSONL line. This reverses `docs/orchestrator-
memory.md`'s own earlier draft, which ruled note text out of the log as "two
shapes for one fact". That was right for lap memory inside one flow and wrong
here: the log is already append-only and already survives `flow_close`, which
is exactly the shape a per-repo corpus needs, and `history` dies with the flow.

**E3. Why this is the measurement that was missing.** Without the split,
"is this helping?" is unanswerable — for the gates in C and D, for an effort
change, for any rule anyone ever adds. With it, the two series answer different
questions and only one of them is supposed to fall.

## F. The orchestrator's charter

**F1. Carve-out in §2** (`orchestration` SKILL.md)
Today: *"that finding **is** the brief; do not invent your own."* Right against
freelancing, wrong against history — as written it forbids carrying anything
forward. Reword to separate them: **authoring is forbidden, carrying is
mandatory.** Add prior laps from `status`, verbatim, as a brief input when
`lap > 1`. Restate the librarian test in one line.

**F2. A fifth `advisor()` moment in §4** — contradiction between nodes. `qa`
asserts X, `reviewer` says X is wrong. Neither can see the other's report; only
the orchestrator holds both. Not a `dev` bug, and routing it to `dev` produces
a lap that cannot converge. Main-loop tool, subagents still have none.

**F3. §5 reads convergence instead of counting.** With A1 the lap counter no
longer has to carry two opposite diagnoses:
- the **same** finding recurring -> the brief is wrong, or the node cannot fix
  it from where it sits;
- a **different** finding each lap -> the spec is underspecified; that is a
  `back(to=spec)`, not another `dev` lap.

Keep the existing "four or more, stop and tell the user" floor.

**F4. Effort is documented but not enforced** (all three charters)
The README's measured recommendation ran at effort `medium`, but no charter
declares an `effort` field — subagent frontmatter supports one, and without it
all three inherit whatever the invoking session is set to, so the documented
configuration is not reproducible. Add `effort: medium` to `dev.md`, `qa.md`,
`reviewer.md`: exactly what was measured, no new claim. The orchestrator's own
`medium` cannot be pinned from a plugin — it is a session setting — so the
README states it rather than promising it.

## Acceptance

1. A new flow has `history == []`, and two flows opened in one process do not
   share the list object: appending to one leaves the other empty.
2. A pre-0.9.0 state file (no `history`) loads without error, reads back as
   `history == []`.
3. Each `back` appends exactly one entry with keys `lap`, `from`, `to`, `note`,
   `kind`; `lap` equals the flow's lap *after* the increment; `note` carries no
   `[from <node>] ` prefix.
4. `advance` with either verdict leaves `history` byte-identical.
5. `reset` empties `history`; `flow_close` removes it with the flow.
6. Two `back` calls produce two entries in call order; the second does not
   overwrite the first.
7. `render()` at lap 1, or with empty `history`, emits no previous-laps block.
8. `render()` at lap 3 shows the lap-2 entry in the previous-laps block and the
   lap-3 finding under `open finding to fix here:` — the lap-3 note appears
   once, not twice.
9. A finding closed by `advance` still renders on a later lap.
10. With six history entries `render()` shows the three most recent plus one
    line naming the count not shown; the block is bounded regardless of length.
11. Both hooks' text inherits the previous-laps block.
12. `advance(verdict="pass")` at `dev` without `checks`, or with blank
    `checks`, raises; state unchanged; the message names the command-plus-output
    it wants.
13. The same call succeeds with non-blank `checks`, and stores it.
14. The `checks` gate does not run at `spec`, `qa` or `review`, nor for
    `verdict="fail"`.
15. D3's tree check and the `checks` gate both apply at `dev`, independently:
    an unchanged tree is refused even with `checks` supplied.
16. `render()` at node `qa` includes the stored `checks`; at other nodes it
    does not.
17. `back` without `kind`, or with a value outside
    `{mechanical, judgement}`, raises; state unchanged.
18. Every mutating log line carries `kind` and `note` (null for calls that have
    neither); `status` still appends nothing; a log-write failure still never
    fails the call.
19. `dev.md` states self-review is forbidden and self-check is mandatory, and
    scopes `checks` to pre-existing checks rather than new tests.
20. `orchestration` §2 states authoring forbidden / carrying mandatory, names
    prior laps as a brief input, and carries the quoted-verbatim test.
21. `orchestration` §3 states the short-circuit, that it acts only on a node's
    own reported evidence, and the one-per-entry bound.
22. `render()` at `dev` says when the short-circuit is already spent for this
    entry.
23. `orchestration` §4 lists the contradiction-between-nodes advisor moment and
    still says subagents have no advisor.
24. `orchestration` §5 distinguishes a repeated finding from a differing one,
    and keeps the four-lap floor.
25. `dev.md`, `qa.md`, `reviewer.md` each declare `effort: medium`; `model`
    values unchanged (`sonnet`, `sonnet`, `opus`).
26. `VERSION == "0.9.0"`, matching `plugin.json` and `marketplace.json` — the
    existing consistency test covers it once bumped.
27. `python3 test_server.py` and `python3 test_hook_ctx.py` both green, and
    both still green with `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` at
    `/dev/null` (0.8.1's guard).
28. README and CHANGELOG updated: lap history, the librarian rule, the `checks`
    gate and its migration break, the short-circuit and its bound, lap
    classification, the pinned effort and what it does and does not claim.

## Not in scope

- **A cross-repo finding corpus.** The real prize, and it needs two decisions
  this spec cannot make: *where* it lives (outside the working directory —
  `.mini-vise.json` and `.mini-vise.log` are per-directory and gitignored, so
  both die with the repo), and *what* it stores. A global corpus accrues
  finding text from every repo its owner works in, client code included, so it
  should hold the **shape** of a finding rather than verbatim code — which is
  also the more useful form, since a `file:line` does not generalise anywhere.
  E2 puts the per-repo half in place; the cross-repo half is its own change.
  Trigger: those two decisions made.
- **Promoting a corpus finding to a rule automatically.** Accrual is
  mechanical; promotion is judgement. An automatically-grown rules file only
  grows — after fifty sessions it is scar tissue competing for the same
  attention budget, and nobody measures whether any line still earns its place.
  E1's classification is what would eventually make that measurable.
- **Raising `dev`'s effort to `xhigh`.** The README's sweep measured `low` /
  `medium` / `high` only; `xhigh` and `max` exist and were never tested, and
  `xhigh` is Claude Code's own default for coding work — so the ceiling that
  was measured was not the real ceiling. An experiment, not a config edit, and
  it must measure **laps to `done`** rather than cost per run: the existing
  data is per-run on tasks that mostly converged in one lap.
- **Moving `dev`/`qa` off `sonnet`.** Concluded, documented in README: 2x cost,
  no correctness win.
- **mini-vise running the checks itself.** Language-agnostic and stdlib-only.
  Requiring evidence is the most it can do without learning every toolchain.
- **A server-side block on the short-circuit bound.** Charter rule plus
  `render()` visibility. Hard-blocking a legitimate `back` is worse than the
  ping-pong it prevents.
- **Collapsing `note`/`note_for` into `history`.** Different meaning, live gate
  in `render()`, and a refactor nobody asked for.
- **Letting a subagent read `history` itself.** It reaches them through the
  brief, which keeps the orchestrator accountable for what it passes on. A
  subagent that reads flow state is a subagent that can route itself.

Version: 0.9.0.
