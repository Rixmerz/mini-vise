#!/usr/bin/env python3
"""Stop hook: don't let the session end while any flow is mid-pipeline.

Claude Code sends this hook a JSON payload when the turn is about to end. If
any flow is at a node other than `spec`, we answer `decision: block` and hand
back the same text `status` returns for those flows — current node, lap, and
any open finding — so the model re-enters with the brief instead of stopping.

Flows parked at `spec` are not blocked: that node's only completion condition
is a human approving the proposal, and there is nothing for the model to do
while it waits. Blocking there either burns a turn or pressures the model into
approving its own proposal — the exact failure `spec` exists to prevent. Those
flows are surfaced through `systemMessage` instead, addressed to the person
whose approval is pending. If every open flow is parked at `spec`, the hook
does not block at all.

The escape hatch matters as much as the gate: the payload's `stop_hook_active`
means "you already blocked this once". Blocking again on re-entry is how a
session becomes impossible to end (claude-code-harness measured 12 consecutive
fires before they fixed the same bug). One nudge total, not one per open flow.
"""
from __future__ import annotations

import json
import sys

import server


def decide(payload: dict) -> dict:
    try:
        raw = server.STATE.read_text()
    except OSError:
        return {}  # no pipeline here — nothing to hold open
    try:
        json.loads(raw)
    except (ValueError, TypeError):
        # ponytail: corrupt state releases the session. A gate that cannot read
        # its own state must not be able to trap someone in it.
        return {"systemMessage": f"[mini-vise] unreadable state at {server.STATE}; not gating."}
    open_flows = server.open_flows(server.read())
    if not open_flows:
        return {}  # every flow done, or none exist
    blocking = {slug: s for slug, s in open_flows.items() if s["node"] != server.HUMAN}
    parked = {slug: s for slug, s in open_flows.items() if s["node"] == server.HUMAN}
    if not blocking:
        text = "\n\n".join(server.render(slug, s) for slug, s in sorted(parked.items()))
        return {"systemMessage": f"[mini-vise] {len(parked)} flow(s) parked at spec, waiting on user approval.\n{text}"}
    text = "\n\n".join(server.render(slug, s) for slug, s in sorted(blocking.items()))
    if payload.get("stop_hook_active"):
        return {"systemMessage": f"[mini-vise] stopping with open flows.\n{text}"}
    return {"decision": "block", "reason": f"[mini-vise] {len(blocking)} flow(s) not finished.\n{text}"}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
        out = decide(payload)
    except Exception:  # noqa: BLE001 - a hook that raises takes the session with it
        out = {}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
