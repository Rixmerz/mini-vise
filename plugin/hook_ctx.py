#!/usr/bin/env python3
"""SessionStart hook: re-surface every open flow around context loss.

Claude Code fires SessionStart on compact/resume/startup, which risks losing
spec_path, the current node, and qa evidence for every open flow — they live
only in chat unless we push them back in here, via `additionalContext`, the
channel the model actually reads. The hook never blocks: SessionStart has no
decision to block with.

PreCompact used to be wired here too, but its only payload field is
`custom_instructions` and its only outbound channel is `systemMessage` ("a
message to the user", per Claude Code's own hook docs) — neither can inject
into the summarizer's context, so it never did what this hook claimed.
`SessionStart` with `source: "compact"` already re-announces every open flow
after a compaction runs, which is the problem PreCompact was for. Dropped in
0.7.0; `decide()` still answers `{}` for a PreCompact payload in case a stale
`hooks.json` still calls it.
"""
from __future__ import annotations

import json
import sys

import server


def decide(payload: dict) -> dict:
    event = payload.get("hook_event_name")
    if event == "PreCompact":
        # stale hooks.json entry from before 0.7.0 — must not claim to steer
        # a summarizer through a channel that doesn't reach it
        return {}
    if event == "SessionStart" and payload.get("source") not in ("compact", "resume", "startup"):
        return {}
    open_flows = server.open_flows(server.read())
    if not open_flows:
        return {}  # no state file, every flow done, corrupt state, or none exist
    text = "\n\n".join(server.render(slug, s) for slug, s in sorted(open_flows.items()))
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
