#!/usr/bin/env python3
"""SessionStart + PreCompact hooks: re-surface every open flow around context loss.

Claude Code fires SessionStart on compact/resume/startup and PreCompact right
before a compaction runs. Both risk losing spec_path, the current node, and qa
evidence for every open flow — they live only in chat unless we push them
back in here. Neither hook blocks: SessionStart has no decision to block
with, and PreCompact must never stop a compaction, only tell the summarizer
what to keep verbatim.
"""
from __future__ import annotations

import json
import sys

import server


def decide(payload: dict) -> dict:
    event = payload.get("hook_event_name")
    if event == "SessionStart" and payload.get("source") not in ("compact", "resume", "startup"):
        return {}
    open_flows = server.open_flows(server.read())
    if not open_flows:
        return {}  # no state file, every flow done, corrupt state, or none exist
    text = "\n\n".join(server.render(slug, s) for slug, s in sorted(open_flows.items()))
    if event == "PreCompact":
        return {
            "systemMessage": (
                "[mini-vise] compacting — keep these verbatim per open flow: spec path, "
                "current node, open finding, qa evidence.\n" + text
            )
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "[mini-vise] open flows.\n" + text,
        }
    }


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
