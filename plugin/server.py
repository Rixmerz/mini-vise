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

NODES = ["spec", "dev", "qa", "review"]
HUMAN = "spec"  # the node with no subagent — the orchestrator writes it, a person approves it
DONE = "done"

STATE = Path(os.environ.get("MINI_VISE_STATE") or Path.cwd() / ".mini-vise.json")

BLANK = {"node": NODES[0], "lap": 1, "note": None, "note_for": None, "spec_path": None, "evidence": None}


def read() -> dict:
    try:
        s = json.loads(STATE.read_text())
        if not isinstance(s, dict) or s.get("node") not in [*NODES, DONE]:
            return dict(BLANK)
        return {**BLANK, **s, "lap": int(s.get("lap", 1))}
    except (OSError, ValueError, TypeError):
        return dict(BLANK)


def write(s: dict) -> None:
    STATE.write_text(json.dumps(s))


def render(s: dict) -> str:
    node, lap = s["node"], s["lap"]
    tail = f" (lap {lap})" if lap > 1 else ""
    if node == DONE:
        return f"node: done{tail} — pipeline finished. Call reset to start over."
    i = NODES.index(node)
    nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
    who = (
        "write the change proposal yourself and get the user to approve it — "
        "there is no subagent for this node, that is the point"
        if node == HUMAN
        else f"delegate to the `{node}` subagent"
    )
    out = [f"node: {node} ({i + 1}/{len(NODES)}){tail} — {who}."]
    if s.get("spec_path"):
        out.append(f"spec: {s['spec_path']}")
    if node == "review" and s.get("evidence"):
        out.append(f"qa evidence:\n{s['evidence']}")
    if s.get("note") and s.get("note_for") == node:
        out.append(f"open finding to fix here:\n{s['note']}")
    out.append(
        f"next: {nxt}. Call advance with the node's verdict — pass moves on, "
        f"fail stays put so you can send it back."
    )
    return "\n".join(out)


TOOLS = [
    {
        "name": "status",
        "description": (
            "Where the mini-vise pipeline stands: current node, which lap, and any open "
            "finding an earlier node sent here to fix. Read this before briefing a subagent."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "advance",
        "description": (
            "Record the current node's verdict. verdict='pass' moves to the next node "
            "(dev -> qa -> review -> done). verdict='fail' does NOT move — the node found "
            "a problem, so call `back` to route it to whoever owns the fix. Report the "
            "verdict the subagent actually gave; do not pass a node that reported failing "
            "tests or a blocking finding."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["pass", "fail"],
                    "description": "What the node's subagent actually reported.",
                },
                "spec_path": {
                    "type": "string",
                    "description": "Path to the spec — set when leaving the `spec` node, ignored elsewhere.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Command run + its real output, verbatim. Required for verdict='pass' at node `qa`.",
                },
            },
            "required": ["verdict"],
        },
    },
    {
        "name": "back",
        "description": (
            "Send the pipeline back to the node that owns the fix, carrying what has to be "
            "fixed. Use after a fail: a failing test at qa, a blocking finding at review. "
            "`to` is required — pick the node that owns it, which for a code finding at "
            "review is dev, not qa. The note is stored and shown to that node in `status`, "
            "so the finding survives a compaction or a fresh session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "enum": NODES, "description": "Node that owns the fix."},
                "note": {
                    "type": "string",
                    "description": "What that node has to fix, specific enough to act on without re-reading the review.",
                },
            },
            "required": ["to", "note"],
        },
    },
    {
        "name": "reset",
        "description": "Send the pipeline back to the first node (dev), clearing the lap count and any open finding.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call(name: str, args: dict) -> str:
    s = read()
    node = s["node"]
    if name == "status":
        return render(s)
    if name == "reset":
        write(dict(BLANK))
        return render(BLANK)
    if name == "advance":
        verdict = args.get("verdict")
        if verdict not in ("pass", "fail"):
            raise ValueError("advance needs verdict='pass' or verdict='fail'")
        if node == DONE:
            return "already done — call reset to start over."
        if verdict == "fail":
            return (
                f"{node} failed — staying put.\n"
                f"Call back(to=..., note=...) with the node that owns the fix. "
                f"A code finding at review goes to dev, not qa."
            )
        evidence = args.get("evidence")
        if node == "qa" and not (evidence and evidence.strip()):
            raise ValueError("advance needs evidence at node 'qa' — command run + its real output, verbatim")
        i = NODES.index(node)
        nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
        # the finding this node was sent back to fix is closed by its own pass
        s.update(node=nxt, note=None, note_for=None)
        if node == "spec" and args.get("spec_path"):
            s["spec_path"] = args["spec_path"]
        if node == "qa":
            s["evidence"] = evidence
        write(s)
        out = render(s)
        if nxt == DONE:
            handoff = ["\n"]
            if s.get("spec_path"):
                handoff.append(f"spec: {s['spec_path']}")
            handoff.append(f"qa evidence:\n{s.get('evidence')}")
            handoff.append(f"context can be drained now — state in {STATE} restores this")
            out += "\n".join(handoff)
        return out
    if name == "back":
        to, note = args.get("to"), (args.get("note") or "").strip()
        if to not in NODES:
            raise ValueError(f"back needs to=one of {', '.join(NODES)} — got {to!r}")
        if not note:
            raise ValueError("back needs a note saying what that node has to fix")
        # ponytail: a lap is any backward move, so the count is "times something
        # was sent back", not laps of the whole pipeline. Close enough to spot a
        # ping-pong; make it exact if anyone ever needs it to be.
        s.update(node=to, lap=s["lap"] + 1, note=f"[from {node}] {note}", note_for=to)
        write(s)
        return render(s)
    raise ValueError(f"unknown tool: {name}")


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mini-vise", "version": "0.2.0"},
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
