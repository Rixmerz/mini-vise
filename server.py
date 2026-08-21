#!/usr/bin/env python3
"""mini-vise MCP server: a 3-node pipeline and the tools to walk it.

ponytail: raw JSON-RPC over stdio instead of the mcp SDK — zero install,
and the protocol surface we need is three methods. Swap to the SDK if we
ever need resources, prompts, or notifications.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NODES = ["dev", "qa", "review"]
DONE = "done"

STATE = Path(os.environ.get("MINI_VISE_STATE") or Path.cwd() / ".mini-vise.json")


def read() -> tuple[str, int]:
    try:
        s = json.loads(STATE.read_text())
        return s["node"], int(s.get("lap", 1))
    except (OSError, ValueError, KeyError, TypeError):
        return NODES[0], 1


def write(node: str, lap: int) -> None:
    STATE.write_text(json.dumps({"node": node, "lap": lap}))


def render(node: str, lap: int) -> str:
    tail = f" (lap {lap})" if lap > 1 else ""
    if node == DONE:
        return f"node: done{tail} — pipeline finished. Call reset to start over."
    i = NODES.index(node)
    nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
    return (
        f"node: {node} ({i + 1}/{len(NODES)}){tail} — delegate to the `{node}` subagent.\n"
        f"next: {nxt}. Call advance when {node} reports done, "
        f"or back if it found something an earlier node has to fix."
    )


TOOLS = [
    {
        "name": "status",
        "description": "Where the mini-vise pipeline stands: current node and what comes next.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "advance",
        "description": "Move to the next node (dev -> qa -> review -> done). Call only after the current node's subagent reports done.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "back",
        "description": (
            "Send the pipeline back to an earlier node because the current one found a problem — "
            "a failing test at qa, a blocking finding at review. Defaults to the previous node; "
            "pass `to` to jump further back (e.g. review sends a code fix straight to dev). "
            "Brief that node with what has to be fixed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "enum": NODES, "description": "Node to return to. Defaults to the previous one."}
            },
        },
    },
    {
        "name": "reset",
        "description": "Send the pipeline back to the first node (dev).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call(name: str, args: dict) -> str:
    node, lap = read()
    if name == "status":
        return render(node, lap)
    if name == "reset":
        write(NODES[0], 1)
        return render(NODES[0], 1)
    if name == "advance":
        if node == DONE:
            return "already done — call reset to start over."
        i = NODES.index(node)
        nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
        write(nxt, lap)
        return render(nxt, lap)
    if name == "back":
        to = args.get("to")
        if to is not None and to not in NODES:
            raise ValueError(f"no such node: {to!r} — pick one of {', '.join(NODES)}")
        if to is None:
            i = len(NODES) - 1 if node == DONE else NODES.index(node) - 1
            if i < 0:
                return render(node, lap) + "\n(already at the first node — nothing to go back to.)"
            to = NODES[i]
        # ponytail: a lap is any backward move, so the count is "times something
        # was sent back", not laps of the whole pipeline. Close enough to spot a
        # ping-pong; make it exact if anyone ever needs it to be.
        write(to, lap + 1)
        return render(to, lap + 1) + f"\nBrief `{to}` with exactly what {node} found."
    raise ValueError(f"unknown tool: {name}")


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mini-vise", "version": "0.1.0"},
        })
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            text, err = call(name, args), False
        except ValueError as exc:
            text, err = str(exc), True
        return ok({"content": [{"type": "text", "text": text}], "isError": err})
    if rid is None:
        return None  # notification
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except ValueError:
            continue
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
