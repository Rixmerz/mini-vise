#!/usr/bin/env python3
"""SessionStart + PreCompact hooks: re-surface pipeline state around context loss.

Claude Code fires SessionStart on compact/resume/startup and PreCompact right
before a compaction runs. Both risk losing spec_path, the current node, and qa
evidence — they live only in chat unless we push them back in here. Neither
hook blocks: SessionStart has no decision to block with, and PreCompact must
never stop a compaction, only tell the summarizer what to keep verbatim.
"""
from __future__ import annotations

import json
import sys

import server


def decide(payload: dict) -> dict:
    event = payload.get("hook_event_name")
    if event == "SessionStart" and payload.get("source") not in ("compact", "resume", "startup"):
        return {}
    try:
        raw = server.STATE.read_text()
    except OSError:
        return {}  # no pipeline here — nothing to surface
    try:
        node = json.loads(raw)["node"]
    except (ValueError, KeyError, TypeError):
        return {}  # corrupt state — don't assert a node that doesn't exist
    if node not in server.NODES:
        return {}  # done, or something we don't recognise
    text = server.render(server.read())
    if event == "PreCompact":
        return {
            "systemMessage": (
                "[mini-vise] compacting — keep these verbatim: spec path, current node, "
                "open finding, qa evidence.\n" + text
            )
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "[mini-vise] pipeline open.\n" + text,
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
