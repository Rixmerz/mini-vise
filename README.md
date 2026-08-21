# mini-vise

Three subagents, three tools. That's the whole plugin.

```
dev  ->  qa  ->  review  ->  done
```

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

- `status` — which node you're on and what's next
- `advance` — move to the next node (call it when the current agent reports done)
- `reset` — back to `dev`

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
