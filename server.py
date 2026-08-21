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


def read() -> str:
    try:
        return json.loads(STATE.read_text())["node"]
    except (OSError, ValueError, KeyError):
        return NODES[0]


def write(node: str) -> None:
    STATE.write_text(json.dumps({"node": node}))


def render(node: str) -> str:
    if node == DONE:
        return "node: done — pipeline finished. Call reset to start over."
    i = NODES.index(node)
    nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
    return (
        f"node: {node} ({i + 1}/{len(NODES)}) — delegate to the `{node}` subagent.\n"
        f"next: {nxt}. Call advance when {node} reports done."
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
        "name": "reset",
        "description": "Send the pipeline back to the first node (dev).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call(name: str) -> str:
    node = read()
    if name == "status":
        return render(node)
    if name == "reset":
        write(NODES[0])
        return render(NODES[0])
    if name == "advance":
        if node == DONE:
            return "already done — call reset to start over."
        i = NODES.index(node)
        nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
        write(nxt)
        return render(nxt)
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
        name = req.get("params", {}).get("name", "")
        try:
            text, err = call(name), False
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
