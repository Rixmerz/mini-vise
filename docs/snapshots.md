# snapshots

Follow-on to `docs/tier-sweep.md`, which reached `done` at lap 3 with two
non-blocking findings open. This change closes both, fixes the D3 wedge review
found, and adds the snapshot feature that lap 2's data loss argued for.

Version 0.8.0 — new behavior in `server.py`, second git use.

## Why now

Lap 2 destroyed three files: `qa` mutation-tested some docs, then cleaned up
with `git checkout` on edits that were never committed. Git restored HEAD.
Unrecoverable. Cost two laps, and was caught only because the orchestrator
re-ran the suites instead of trusting a `verdict: pass`.

The pipeline itself maintains the condition that makes this destructive: the
`implementing` charter says don't commit unless asked, and D3's tree gate wants
a dirty tree, so work stays uncommitted across every node.

## G. Close the two open review findings

**G1. `baseline`'s example contradicts the charters** (`plugin/skills/baseline/SKILL.md:59-65`)
The report example shows `verdict: pass` with no `flow:` line, and the
never-compress list omits `flow:`. All three charters load `baseline`, so a
subagent reads "`flow: <slug>` first line, non-compressible" in its charter and
sees a counter-example in the shared skill. This is A3's failure mode — example
beats prose — in the file A3 did not cover, and it can defeat AC8.
Add `flow: <slug>` to the example's first line and to the never-compress list.

**G2. New tests hardcode `/tmp`** (`test_server.py`, incl. :494
`assert "dir: /tmp/x" in texts[0]`)
Same class as the finding `qa` fixed for the three pre-existing tests: holds
only where `realpath("/tmp") == "/tmp"`. Use `tempfile.TemporaryDirectory()` +
`os.path.realpath`, the idiom already in the suite. `qa` owns this.

## H. D3 wedge

**H1. Tree hash must count a commit as change** (`plugin/server.py`)
Review reproduced it: `dev` writes code, commits it, `git status --porcelain`
goes clean, the hash matches the baseline recorded on entering `dev`, and
`advance` is refused with "shows no change since entering dev" — which is false.
No legal exit: `back(to="dev")` and `reset` both re-snapshot the same clean
tree, so only `flow_close` escapes.

Fix: hash `git status --porcelain` **plus `git rev-parse HEAD`**. A commit moves
HEAD, so the hash differs and the flow advances. A `dev` that did nothing leaves
both unchanged and is still caught. Same degradation rules as today — any git
failure means record nothing, block nothing.

Correct the error text: it must say the tree and HEAD are both unchanged since
entering `dev`, not assert "no change" as fact.

This amends `docs/tier-sweep.md:154` ("no other git use"), which was written
before the wedge was known. `git rev-parse HEAD` is now in scope; nothing else.

## I. Snapshots

**I1. Snapshot at every mutating transition** (`plugin/server.py`)
On each successful `flow_start`, `advance`, `back`, `reset`, `flow_close` —
the same five calls `log_call()` already covers, and after the state write.

Plumbing only. It must not touch the working tree, the index, or HEAD:

```
IDX=$(mktemp -u)                                    # name only; mktemp creates a
GIT_INDEX_FILE=$IDX git add -A                      # real file, invalid as an index
tree=$(GIT_INDEX_FILE=$IDX git write-tree)
commit=$(git commit-tree $tree -p $(git rev-parse HEAD) -m "<msg>")
git update-ref --create-reflog refs/mini-vise/snapshots/<slug> $commit
rm -f $IDX
```

Verified by hand before speccing: after this runs, `git status --short` is
byte-identical, nothing is staged, HEAD is unmoved. So it does **not** disturb
H1's hash — the two features do not interact.

`--create-reflog` is required, not decoration: reflogs are automatic only for
`refs/heads`, `refs/remotes`, `refs/notes` and `HEAD`. Without it each snapshot
silently overwrites the last and you keep exactly one.

Run in the flow's `dir`. Message names flow, tool, node and lap.

For `flow_close`, snapshot **before** the entry is deleted — that is the one
most worth keeping, since closing an open flow discards its finding — and name
the ref by the slug being closed.

**Content only, not pipeline state.** `git add -A` honours `.gitignore`, so
`.mini-vise.json` and `.mini-vise.log` are *not* in the snapshot. Restoring
brings back files, never the flow.

**I2. One ref per flow** — `refs/mini-vise/snapshots/<slug>`, so parallel flows
in separate worktrees keep separate histories and a slug's history survives
`flow_close`.

**I3. Never load-bearing** — not a repo, `git` absent, `dir` is None, a repo
with **no commits yet** (`rev-parse HEAD` fails, so `commit-tree` has no parent),
a `dir` that does not exist, non-zero exit, timeout: skip silently. A snapshot failure must never fail a tool call or
change a verdict, exactly like `log_call()`.

**I4. Discoverable** — `status` output ends with the snapshot ref name for that
flow when at least one snapshot exists. Recovery is `git restore --source=<ref> .`;
document that line in the README, do not build a restore tool.

## Acceptance

1. `baseline/SKILL.md`'s report example opens with `flow: <slug>`, and its
   never-compress list names `flow:`.
2. A test fails if `baseline`'s example or never-compress list loses `flow:` —
   same guard shape as the AC8 charter tests.
3. No test in `test_server.py` or `test_hook_ctx.py` hardcodes `/tmp`;
   `grep -n '"/tmp' test_server.py test_hook_ctx.py` returns nothing.
4. Tree hash changes when HEAD moves with a clean tree: in a temp repo, enter
   `dev`, write a file, `git add -A && git commit`, then `advance(verdict="pass")`
   at `dev` succeeds and reaches `qa`. This pins the **recovery** path — the
   `implementing` charter still says do not commit unless asked; H1 exists so
   that a `dev` which does anyway is not wedged, not to bless it.
5. A `dev` that changes nothing at all is still blocked — tree and HEAD both
   unchanged.
6. The block message does not claim "no change"; it names tree and HEAD.
7. H1 degrades open exactly as before: `dir` None, non-repo, `git` unavailable,
   no baseline recorded → advances, no raise.
8. Every successful `flow_start`/`advance`/`back`/`reset`/`flow_close` in a git
   repo with at least one commit creates a commit at
   `refs/mini-vise/snapshots/<slug>`; `flow_close` snapshots before deleting.
9. `status` creates no snapshot.
10. After a snapshot, `git status --porcelain` is byte-identical to before,
    `git diff --cached` is empty, and `HEAD` is unchanged. Scoped to those three
    — new blob objects under `.git/objects` are expected and are not a failure.
11. The snapshot commit's tree holds working-tree **content**: files untracked
    at HEAD are included, gitignored ones are not — so `.mini-vise.json` and
    `.mini-vise.log` are absent and a restore does not bring back flow state.
12. Two snapshots on one flow leave two reflog entries at that flow's ref.
13. Two flows with different slugs write to different refs and do not overwrite
    each other.
14. A snapshot failure never fails the call: non-repo `dir`, `git` stripped from
    `PATH`, `dir=None`, a nonexistent `dir`, and a repo with no commits yet all
    return a normal result with no `isError`.
15. Taking a snapshot does not change H1's hash — an `advance` at `dev` blocked
    for an unchanged tree is still blocked after snapshots have run.
16. `status` names the flow's snapshot ref once a snapshot exists, and does not
    when none does.
17. README documents the ref layout and the `git restore --source=<ref> .`
    recovery line.
18. `docs/tier-sweep.md:154` amended: `git rev-parse HEAD` and the snapshot
    plumbing are in scope; the line no longer reads as a blanket ban.
19. Both suites green.
20. CHANGELOG 0.8.0 entry; `VERSION`, `plugin.json`, `marketplace.json` all
    0.8.0 and still agreeing per the existing consistency test.

## Not in scope

- A restore tool or a `snapshot_list` MCP tool. `git restore --source=<ref> .`
  and `git reflog` already do it; a wrapper is surface with no new capability.
- Retention or pruning. Reflog expiry defaults handle it; revisit if a repo
  actually bloats.
- Snapshotting on `Write`/`Edit` via a PostToolUse hook. Per-transition is the
  granularity that matches the pipeline and costs five call sites, not hundreds
  of firings.
- Verifying `docs/multi-flow.md` AC14 — still unrun, still needs push +
  reinstall + two worktrees.
- Reviewer severity calibration on out-of-alphabet input. Dropped twice already.

Version: 0.8.0.
