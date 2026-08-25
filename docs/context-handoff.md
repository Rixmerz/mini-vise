# context handoff — spec_path, evidence, two hooks

Problem: state file holds node/lap/note only. Spec path + qa evidence live in
chat, so a compaction or fresh session loses them. No signal marks when a task
is finished and context can be drained.

## Deltas

**a. state fields** (`plugin/server.py`)
- `spec_path`: set by `advance` when leaving `spec`. New optional arg
  `spec_path` on `advance`; ignored at other nodes. Rendered by `status` as
  `spec: <path>` whenever set.
- `evidence`: required arg on `advance(verdict="pass")` **at node `qa` only** —
  command run + its real output, verbatim. Missing/blank at qa => ValueError
  naming what is needed. Stored; `status` renders it at `review` as
  `qa evidence:\n<text>`. Cleared by `reset`.
- `BLANK` gains both keys as `None`. `read()` back-fills them for old state
  files (missing key => None, no crash).

**b. SessionStart hook** (`plugin/hook_ctx.py`, new)
- Fires on `source` in {`compact`, `resume`, `startup`}. Node not in NODES
  (done/absent/unreadable) => `{}`, silent.
- Otherwise emits `{"systemMessage": "[mini-vise] pipeline open.\n" + render(read())}`.

**c. PreCompact hook** (same file, branch on `hook_event_name`)
- Node not in NODES => `{}`.
- Otherwise `{"systemMessage": ...}` telling the summarizer to keep verbatim:
  spec path, current node, open finding, qa evidence. Never blocks.

**d. done handoff** (`plugin/server.py`)
- `advance` moving to `DONE` returns render + a handoff block: spec path,
  qa evidence, open finding if any, then `context can be drained now — state
  in <STATE path> restores this`.

Both hooks registered in `plugin/hooks/hooks.json` next to the existing Stop
entry, same `python3 "${CLAUDE_PLUGIN_ROOT}/hook_ctx.py"` shape.

## Acceptance

1. `advance(spec_path="docs/x.md")` at `spec` => later `status` shows `spec: docs/x.md`.
2. `advance(verdict="pass")` at `qa` without evidence => error, node unchanged.
3. With evidence => moves to `review`; `status` there shows the evidence verbatim.
4. `reset` clears spec_path + evidence.
5. State file written by 0.4.1 (no new keys) loads without error.
6. hook_ctx SessionStart on open pipeline => systemMessage containing the node;
   on `done` / no state file => `{}`.
7. hook_ctx PreCompact on open pipeline => systemMessage, never `decision`.
8. Malformed stdin / unreadable state => `{}`, exit 0. Hook never raises.
9. `advance` to done => output contains spec path, evidence, drain line.
10. Existing `test_server.py` still passes.

## Not in scope

- Blocking Write/Edit by node (delegation works today; hooks cannot tell
  subagent from orchestrator).
- Verifying evidence is true. It makes an omission explicit, nothing more.
