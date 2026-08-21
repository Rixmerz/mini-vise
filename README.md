# mini-vise

Three subagents, three tools. That's the whole plugin.

```
        +---------- back ----------+
        v                          |
      dev  ->  qa  ->  review  ->  done
```

Forward is `advance`. When a node finds a problem an earlier node has to fix —
a failing test at `qa`, a blocking finding at `review` — `back` returns there
and the lap counter goes up. Nothing reaches `done` for good until a lap
gets through without a `back`.

## Agents

| Agent | Does | Doesn't | Skills | Model |
|---|---|---|---|---|
| `dev` | writes the code | write tests, review itself | `baseline`, `implementing` | sonnet |
| `qa` | writes and runs tests, reports real output | edit product code to go green | `baseline`, `testing` | sonnet |
| `reviewer` | adversarial review + debugging, read-only | fix anything | `baseline`, `reviewing` | opus |

`baseline` is the shared precedence rule and the language-agnostic standards.
The role skill on top carries what that node actually has to get right —
scope discipline, what makes a test worth writing, how to rank a finding
without inventing a severity score.

`reviewer` runs on the bigger model and the other two don't. Applying a guard
and writing asserts is mechanical; noticing that a green test pins a bug is
not.

## Tools

The MCP server exposes exactly enough to walk the pipeline:

- `status` — which node you're on, which lap, and any open finding sent here to fix
- `advance` — takes the node's `verdict`. `pass` moves on; `fail` **stays put**
- `back` — route the fix to the node that owns it, carrying a `note` of what to fix
- `reset` — back to `dev`, lap and findings cleared

Both arguments are required on purpose. `advance` will not move without a
verdict, so walking past a "do not ship" has to be a deliberate lie rather than
an oversight. `back` will not move without a note, so the finding lives in the
state file instead of in whoever happened to be reading the review — it survives
a compaction, a new session, and a different model picking the pipeline up.

`fail` does not route on its own. It stops and makes you call `back(to=...)`,
because the node that owns a fix is a judgement: a code finding at `review`
belongs to `dev`, not to the node just behind it.

What the tools still don't do is check whether a node told the truth. `qa` can
report a pass on a suite that pins a bug green — that happened here, and what
caught it was `reviewer`, not a tool.

State lives in `.mini-vise.json` in the working directory (override with
`MINI_VISE_STATE`). No database, no config.

## Install

```bash
claude plugin marketplace add Rixmerz/mini-vise
claude plugin install mini-vise@mini-vise
```

Needs `python3`. Nothing else — the server is stdlib-only.

## Test

```bash
python3 test_server.py
```
