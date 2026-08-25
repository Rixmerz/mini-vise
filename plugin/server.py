#!/usr/bin/env python3
"""mini-vise MCP server: a 3-node pipeline and the tools to walk it.

ponytail: raw JSON-RPC over stdio instead of the mcp SDK — zero install,
and the protocol surface we need is three methods. Swap to the SDK if we
ever need resources, prompts, or notifications.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "0.8.0"

NODES = ["spec", "dev", "qa", "review"]
HUMAN = "spec"  # the node with no subagent — the orchestrator writes it, a person approves it
DONE = "done"

STATE = Path(os.environ.get("MINI_VISE_STATE") or Path.cwd() / ".mini-vise.json")
LOG = STATE.with_suffix(".log")

BLANK = {
    "node": NODES[0], "lap": 1, "note": None, "note_for": None,
    "spec_path": None, "evidence": None, "dir": None, "tree": None,
}


def read() -> dict:
    """All flows, keyed by slug. Corrupt/unparseable state -> {}.

    Back-compat: a 0.5.0 single-slot file (top-level "node") is migrated
    in-memory to {"main": <that flow>} — not written back until the next
    write(), so a crash mid-migration leaves the original file intact.
    """
    try:
        raw = json.loads(STATE.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if "flows" not in raw and raw.get("node") in [*NODES, DONE]:
        raw = {"flows": {"main": raw}}
    flows = raw.get("flows")
    if not isinstance(flows, dict):
        return {}
    out = {}
    for slug, s in flows.items():
        # skip, don't preserve: a malformed flow entry is dropped from this read,
        # and the next write() persists that drop — same trade-off the old
        # single-slot BLANK fallback made, now scoped to one flow instead of
        # the whole file
        if not isinstance(s, dict) or s.get("node") not in [*NODES, DONE]:
            continue
        out[slug] = {**BLANK, **s, "lap": int(s.get("lap", 1))}
    return out


def write(flows: dict) -> None:
    STATE.write_text(json.dumps({"flows": flows}))


def open_flows(flows: dict) -> dict:
    return {slug: s for slug, s in flows.items() if s["node"] != DONE}


def render(slug: str, s: dict) -> str:
    node, lap = s["node"], s["lap"]
    tail = f" (lap {lap})" if lap > 1 else ""
    if node == DONE:
        out = [f"[flow: {slug}] node: done{tail} — pipeline finished. Call reset(flow={slug!r}) to start over."]
        if s.get("dir"):
            out.append(f"dir: {s['dir']}")
        return "\n".join(out)
    i = NODES.index(node)
    nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
    who = (
        "write the change proposal yourself and get the user to approve it — "
        "there is no subagent for this node, that is the point"
        if node == HUMAN
        else f"delegate to the `{node}` subagent"
    )
    out = [f"[flow: {slug}] node: {node} ({i + 1}/{len(NODES)}){tail} — {who}."]
    if s.get("dir"):
        out.append(f"dir: {s['dir']}")
    if s.get("spec_path"):
        out.append(f"spec: {s['spec_path']}")
    if node == "review" and s.get("evidence"):
        out.append(f"qa evidence:\n{s['evidence']}")
    if s.get("note") and s.get("note_for") == node:
        out.append(f"open finding to fix here:\n{s['note']}")
    out.append(
        f"next: {nxt}. Call advance(flow={slug!r}, ...) with the node's verdict — pass moves "
        f"on, fail stays put so you can send it back."
    )
    return "\n".join(out)


def unknown_flow(flow, flows: dict) -> str:
    valid = ", ".join(sorted(flows)) or "(none open — call flow_start first)"
    return f"flow={flow!r} is missing or unknown — valid slugs: {valid}"


def tree_hash(d) -> str | None:
    """Hash of `git status --porcelain` plus `git rev-parse HEAD` in `d`, or
    None if it can't be computed.

    HEAD is included (H1) so a `dev` that commits its work moves the hash even
    though the tree itself goes clean — otherwise a commit is indistinguishable
    from doing nothing and `advance` wedges.

    None covers every degraded case on purpose (D3): no dir, git not on PATH,
    `d` not a repo, or either subprocess errors or times out. The caller treats
    None as "nothing to compare" rather than raising.
    """
    if not d:
        return None
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=d,
            capture_output=True, text=True, timeout=10,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=d,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None
    head_out = head.stdout if head.returncode == 0 else ""
    return hashlib.sha256((status.stdout + head_out).encode()).hexdigest()


def log_call(tool: str, flow: str, s: dict, verdict: str | None) -> None:
    """Append one JSONL line for a successful mutating call. Never raises —
    the log is a record, not a dependency (D2)."""
    entry = {
        "ts": time.time(), "flow": flow, "tool": tool,
        "node": s["node"], "lap": s["lap"], "verdict": verdict,
    }
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def snapshot_ref_name(flow: str) -> str:
    return f"refs/mini-vise/snapshots/{flow}"


def snapshot(tool: str, flow: str, s: dict) -> None:
    """Best-effort git snapshot of `s['dir']`'s working-tree content into
    `refs/mini-vise/snapshots/<flow>` (I1). Runs after every successful
    mutating call. Never raises and never touches the real index or HEAD —
    it builds the commit through a throwaway `GIT_INDEX_FILE`, exactly the
    plumbing in docs/snapshots.md §I1, so `git status --porcelain`, `git diff
    --cached` and HEAD are byte-identical before and after (I3, verified by
    hand). A snapshot failure must never fail the call, same contract as
    log_call()."""
    d = s.get("dir")
    if not d:
        return
    # a throwaway dir + a filename inside it that doesn't exist yet — the
    # `mktemp -u` semantics docs/snapshots.md §I1 calls for (name only; a
    # real empty file is an invalid index), without tempfile.mktemp()'s
    # known race
    idx_dir = tempfile.mkdtemp()
    idx = os.path.join(idx_dir, "index")
    env = {**os.environ, "GIT_INDEX_FILE": idx}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=d,
            capture_output=True, text=True, timeout=10,
        )
        if head.returncode != 0:
            return  # not a repo, or no commits yet — commit-tree would have no parent
        add = subprocess.run(
            ["git", "add", "-A"], cwd=d, env=env,
            capture_output=True, text=True, timeout=10,
        )
        if add.returncode != 0:
            return
        tree = subprocess.run(
            ["git", "write-tree"], cwd=d, env=env,
            capture_output=True, text=True, timeout=10,
        )
        if tree.returncode != 0:
            return
        msg = f"mini-vise snapshot: flow={flow} tool={tool} node={s['node']} lap={s['lap']}"
        commit = subprocess.run(
            ["git", "commit-tree", tree.stdout.strip(), "-p", head.stdout.strip(), "-m", msg],
            cwd=d, capture_output=True, text=True, timeout=10,
        )
        if commit.returncode != 0:
            return
        subprocess.run(
            ["git", "update-ref", "--create-reflog", "-m", msg, snapshot_ref_name(flow), commit.stdout.strip()],
            cwd=d, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return
    finally:
        shutil.rmtree(idx_dir, ignore_errors=True)


def snapshot_ref(flow: str, s: dict) -> str | None:
    """The flow's snapshot ref if at least one snapshot exists there, else
    None. Degrades silently like everything else here (I3/I4)."""
    d = s.get("dir")
    if not d:
        return None
    ref = snapshot_ref_name(flow)
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref], cwd=d,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return ref


def render_with_snapshot(slug: str, s: dict) -> str:
    """`render()` plus the snapshot ref line for `status` (I4)."""
    out = render(slug, s)
    ref = snapshot_ref(slug, s)
    return f"{out}\nsnapshot: {ref}" if ref else out


TOOLS = [
    {
        "name": "flow_start",
        "description": (
            "Open a new flow so a second, independent pipeline can run alongside others. "
            "Fails if the slug already exists — use `status` to inspect it, "
            "`reset(flow=...)` to restart it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Short name for this flow, e.g. 'authz'."},
                "dir": {
                    "type": "string",
                    "description": (
                        "Working tree this flow touches. Defaults to cwd. Must not match an "
                        "open (non-done) flow's dir — two code-touching flows in one working "
                        "tree make a diff unattributable to a reviewer."
                    ),
                },
            },
            "required": ["slug"],
        },
    },
    {
        "name": "status",
        "description": (
            "Where a flow stands: current node, which lap, and any open finding an earlier "
            "node sent here to fix. Read this before briefing a subagent. Omit `flow` to "
            "render every open and done flow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"flow": {"type": "string", "description": "Slug to inspect. Omit for all flows."}},
        },
    },
    {
        "name": "advance",
        "description": (
            "Record the current node's verdict for a flow. verdict='pass' moves to the next "
            "node (dev -> qa -> review -> done). verdict='fail' does NOT move — the node found "
            "a problem, so call `back` to route it to whoever owns the fix. Report the "
            "verdict the subagent actually gave; do not pass a node that reported failing "
            "tests or a blocking finding."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "flow": {"type": "string", "description": "Slug from flow_start."},
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
            "required": ["flow", "verdict"],
        },
    },
    {
        "name": "back",
        "description": (
            "Send a flow back to the node that owns the fix, carrying what has to be fixed. "
            "Use after a fail: a failing test at qa, a blocking finding at review. `to` is "
            "required — pick the node that owns it, which for a code finding at review is "
            "dev, not qa. The note is stored and shown to that node in `status`, so the "
            "finding survives a compaction or a fresh session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "flow": {"type": "string", "description": "Slug from flow_start."},
                "to": {"type": "string", "enum": NODES, "description": "Node that owns the fix."},
                "note": {
                    "type": "string",
                    "description": "What that node has to fix, specific enough to act on without re-reading the review.",
                },
            },
            "required": ["flow", "to", "note"],
        },
    },
    {
        "name": "reset",
        "description": "Send a flow back to the first node (spec), clearing the lap count and any open finding.",
        "inputSchema": {
            "type": "object",
            "properties": {"flow": {"type": "string", "description": "Slug from flow_start."}},
            "required": ["flow"],
        },
    },
    {
        "name": "flow_close",
        "description": (
            "Remove a flow entirely, freeing both its slug and its dir for reuse. Works on any "
            "flow, open or done — closing an open one is explicit intent. The response names "
            "the node it was on so a mistake is visible."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"flow": {"type": "string", "description": "Slug from flow_start."}},
            "required": ["flow"],
        },
    },
]


def call(name: str, args: dict) -> str:
    flows = read()

    if name == "flow_start":
        slug = args.get("slug")
        if not slug or not isinstance(slug, str):
            raise ValueError("flow_start needs a slug")
        if slug in flows:
            raise ValueError(f"flow={slug!r} already exists — use status(flow={slug!r}) to inspect it")
        d = os.path.realpath(args.get("dir") or os.getcwd())
        for other_slug, s in open_flows(flows).items():
            other_dir = s.get("dir")
            # a flow migrated in-memory from 0.5.0 state has no recorded dir
            # (BLANK default None) — we can't prove it doesn't collide, so
            # block defensively rather than risk the exact unattributable
            # diff this guard exists to prevent
            if other_dir is None or os.path.realpath(other_dir) == d:
                raise ValueError(
                    f"dir={d!r} is already in use by open flow={other_slug!r} — two code-touching "
                    f"flows in one working tree make a diff unattributable to a reviewer. Use a "
                    f"different dir (e.g. a git worktree), or wait until {other_slug!r} is done."
                )
        flows[slug] = {**BLANK, "dir": d}
        write(flows)
        log_call("flow_start", slug, flows[slug], None)
        snapshot("flow_start", slug, flows[slug])
        return render(slug, flows[slug])

    if name == "status":
        flow = args.get("flow")
        if flow is not None:
            if flow not in flows:
                raise ValueError(unknown_flow(flow, flows))
            return render_with_snapshot(flow, flows[flow])
        if not flows:
            return "no open flows — call flow_start(slug, dir) to begin."
        return "\n\n".join(render_with_snapshot(slug, flows[slug]) for slug in sorted(flows))

    if name not in ("advance", "back", "reset", "flow_close"):
        raise ValueError(f"unknown tool: {name}")

    flow = args.get("flow")
    if flow not in flows:
        raise ValueError(unknown_flow(flow, flows))
    s = flows[flow]
    node = s["node"]

    if name == "flow_close":
        # snapshot before deleting the entry — closing an open flow discards
        # its finding, so this is the one most worth keeping (I1)
        snapshot("flow_close", flow, s)
        del flows[flow]
        write(flows)
        log_call("flow_close", flow, {"node": node, "lap": s["lap"]}, None)
        return f"[flow: {flow}] closed — was at node: {node}. Slug and dir are free for a new flow_start."

    if name == "reset":
        flows[flow] = dict(BLANK, dir=s.get("dir"))
        write(flows)
        log_call("reset", flow, flows[flow], None)
        snapshot("reset", flow, flows[flow])
        return render(flow, flows[flow])

    if name == "advance":
        verdict = args.get("verdict")
        if verdict not in ("pass", "fail"):
            raise ValueError("advance needs verdict='pass' or verdict='fail'")
        if node == DONE:
            log_call("advance", flow, s, verdict)
            snapshot("advance", flow, s)
            return f"[flow: {flow}] already done — call reset(flow={flow!r}) to start over."
        if verdict == "fail":
            log_call("advance", flow, s, verdict)
            snapshot("advance", flow, s)
            return (
                f"[flow: {flow}] {node} failed — staying put.\n"
                f"Call back(flow={flow!r}, to=..., note=...) with the node that owns the fix. "
                f"A code finding at review goes to dev, not qa."
            )
        evidence = args.get("evidence")
        if node == "qa" and not (evidence and evidence.strip()):
            raise ValueError("advance needs evidence at node 'qa' — command run + its real output, verbatim")
        if node == "dev":
            # D3: the one honesty check that is mechanical rather than another
            # agent. Degrades silent-open (never blocks) when either side is
            # unknowable — see tree_hash. Known gap, accepted: a dev that
            # creates a file and deletes it again is refused too.
            baseline, current = s.get("tree"), tree_hash(s.get("dir"))
            if baseline is not None and current is not None and current == baseline:
                raise ValueError(
                    f"advance blocked at dev: {s.get('dir')} — tree and HEAD are both "
                    f"unchanged since entering dev. A pass without a touched tree or a "
                    f"commit is refused, not shipped."
                )
        i = NODES.index(node)
        nxt = NODES[i + 1] if i + 1 < len(NODES) else DONE
        # the finding this node was sent back to fix is closed by its own pass
        s.update(node=nxt, note=None, note_for=None)
        if node == "spec" and args.get("spec_path"):
            s["spec_path"] = args["spec_path"]
        if node == "qa":
            s["evidence"] = evidence
        if nxt == "dev":  # entering dev — record the baseline D3 compares against
            s["tree"] = tree_hash(s.get("dir"))
        write(flows)
        log_call("advance", flow, s, verdict)
        snapshot("advance", flow, s)
        out = render(flow, s)
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
        if to == "dev":  # re-entering dev — re-record the baseline D3 compares against
            s["tree"] = tree_hash(s.get("dir"))
        write(flows)
        log_call("back", flow, s, None)
        snapshot("back", flow, s)
        return render(flow, s)


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mini-vise", "version": VERSION},
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
