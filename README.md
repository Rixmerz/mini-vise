# mini-vise

Three subagents, three tools. That's the whole plugin.

```
dev  ->  qa  ->  review  ->  done
```

## Agents

| Agent | Does | Doesn't |
|---|---|---|
| `dev` | writes the code | write tests, review itself |
| `qa` | writes and runs tests, reports real output | edit product code to go green |
| `reviewer` | adversarial review + debugging, read-only | fix anything |

## Tools

The MCP server exposes exactly enough to walk the pipeline:

- `status` — which node you're on and what's next
- `advance` — move to the next node (call it when the current agent reports done)
- `reset` — back to `dev`

State lives in `.mini-vise.json` in the working directory (override with
`MINI_VISE_STATE`). No database, no config.

## Install

```bash
git clone https://github.com/Rixmerz/mini-vise
claude plugin install ./mini-vise
```

Needs `python3`. Nothing else — the server is stdlib-only.

## Test

```bash
python3 test_server.py
```
