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

| Agent | Does | Doesn't | Skills |
|---|---|---|---|
| `dev` | writes the code | write tests, review itself | `baseline`, `implementing` |
| `qa` | writes and runs tests, reports real output | edit product code to go green | `baseline`, `testing` |
| `reviewer` | adversarial review + debugging, read-only | fix anything | `baseline`, `reviewing` |

`baseline` is the shared precedence rule and the language-agnostic standards.
The role skill on top carries what that node actually has to get right —
scope discipline, what makes a test worth writing, how to rank a finding
without inventing a severity score.

## Tools

The MCP server exposes exactly enough to walk the pipeline:

- `status` — which node you're on, which lap, and what's next
- `advance` — move to the next node (call it when the current agent reports done)
- `back` — return to an earlier node to fix what this one found; defaults to the
  previous node, takes `to` to jump further (`review` sends a code fix straight
  to `dev`)
- `reset` — back to `dev`, lap counter cleared

The tools move the pointer and nothing else. They do not judge whether a node
really finished — that stays the orchestrator's call, on the agent's report.

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
