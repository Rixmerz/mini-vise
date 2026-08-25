"""Run: python3 test_server.py"""
import json, os, subprocess, sys, tempfile

def rpc(lines, env):
    p = subprocess.run([sys.executable, "plugin/server.py"], input="\n".join(json.dumps(l) for l in lines),
                       capture_output=True, text=True, env=env)
    return [json.loads(l) for l in p.stdout.splitlines()]

def main():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        seq, expect = [{"jsonrpc": "2.0", "id": 0, "method": "tools/list"}], []

        def step(check, tool, **args):
            seq.append({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool, "arguments": args}})
            expect.append(check)

        P = dict(verdict="pass")
        step(lambda t, e: t.startswith("node: spec (1/4)") and "no subagent" in t, "status")
        step(lambda t, e: e and "verdict" in t, "advance")                    # verdict required
        step(lambda t, e: e, "advance", verdict="maybe")                      # and must be valid
        step(lambda t, e: t.startswith("node: dev") and "delegate" in t, "advance",
             verdict="pass", spec_path="docs/x.md")
        step(lambda t, e: "spec: docs/x.md" in t, "status")
        step(lambda t, e: t.startswith("node: qa"), "advance", **P)
        step(lambda t, e: e and "evidence" in t, "advance", **P)                # evidence required at qa
        step(lambda t, e: t.startswith("node: review"), "advance", verdict="pass", evidence="pytest -q\n1 passed")
        step(lambda t, e: not e and "staying put" in t, "advance", verdict="fail")
        step(lambda t, e: t.startswith("node: review"), "status")             # fail did not move
        step(lambda t, e: e and "note" in t, "back", to="dev")                # note required
        step(lambda t, e: e, "back", to="nope", note="x")                     # real node required
        step(lambda t, e: t.startswith("node: spec (1/4) (lap 2)"), "back",
             to="spec", note="the empty-code case was never specified")       # review -> spec
        step(lambda t, e: "[from review] the empty-code case" in t, "status")
        step(lambda t, e: "[from review]" not in t, "advance", **P)           # a pass closes it
        step(lambda t, e: "[from review]" not in t, "status")                 # and it stays closed
        step(lambda t, e: t.startswith("node: qa"), "advance", **P)
        step(lambda t, e: t.startswith("node: review"), "advance", verdict="pass", evidence="pytest -q\n2 passed")
        step(lambda t, e: (t.startswith("node: done") and "lap 2" in t
                            and "qa evidence:\npytest -q\n2 passed" in t and "context can be drained now" in t),
             "advance", **P)
        step(lambda t, e: "already done" in t, "advance", **P)
        step(lambda t, e: t.startswith("node: spec") and "lap" not in t, "reset")
        step(lambda t, e: "already at the first node" not in t and e, "nope")

        out = rpc(seq, env)
        assert len(out[0]["result"]["tools"]) == 4, out[0]
        for n, (check, r) in enumerate(zip(expect, out[1:])):
            text = r["result"].get("content", [{}])[0].get("text", "")
            err = bool(r["result"].get("isError"))
            assert check(text, err), f"step {n}: err={err} {text!r}"
        print("ok")


main()


def hook(payload, state_file, content=None):
    import pathlib
    if content is not None:
        pathlib.Path(state_file).write_text(content)
    p = subprocess.run([sys.executable, "plugin/hook_stop.py"], input=json.dumps(payload),
                       capture_output=True, text=True,
                       env={**os.environ, "MINI_VISE_STATE": state_file})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_hook():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")

        assert hook({}, f) == {}, "no state file: nothing to gate"

        mid = json.dumps({"node": "qa", "lap": 2, "note": "[from review] fix the guard",
                          "note_for": "qa"})
        r = hook({}, f, mid)
        assert r["decision"] == "block", r
        assert "node: qa" in r["reason"] and "fix the guard" in r["reason"], r

        # the escape hatch: a second stop is allowed through, warned not blocked
        r = hook({"stop_hook_active": True}, f, mid)
        assert "decision" not in r and "systemMessage" in r, r

        assert hook({}, f, json.dumps({"node": "done", "lap": 1})) == {}, "done releases"
        assert "decision" not in hook({}, f, "{ not json"), "corrupt state must not trap"
        assert hook({}, f, json.dumps({"node": "bogus"})) == {}
        assert hook("not a dict", f, mid)["decision"] == "block", "junk payload still gates"
    print("hook ok")


test_hook()
