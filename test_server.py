"""Run: python3 test_server.py"""
import json, os, subprocess, sys, tempfile

def rpc(lines, env):
    p = subprocess.run([sys.executable, "server.py"], input="\n".join(json.dumps(l) for l in lines),
                       capture_output=True, text=True, env=env)
    return [json.loads(l) for l in p.stdout.splitlines()]

def main():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}

        def c(n, **a):
            return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": n, "arguments": a}}

        P = dict(verdict="pass")
        out = rpc([{"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
                   c("status"),                                    # 0
                   c("advance"),                                   # 1 no verdict
                   c("advance", verdict="maybe"),                  # 2 bad verdict
                   c("advance", **P), c("advance", **P),           # 3,4 -> qa, review
                   c("advance", verdict="fail"),                   # 5 stays put
                   c("status"),                                    # 6 still review
                   c("back", to="dev"),                            # 7 note required
                   c("back", to="nope", note="x"),                 # 8 bad node
                   c("back", to="dev", note="falsy code skips the guard"),  # 9
                   c("status"),                                    # 10 note echoed
                   c("advance", **P),                              # 11 -> qa, note cleared
                   c("status"),                                    # 12
                   c("advance", **P), c("advance", **P),           # 13,14 -> done
                   c("advance", **P),                              # 15 no-op
                   c("reset"),                                     # 16
                   c("nope")], env)                                # 17
        r = out[1:]
        t = [x["result"].get("content", [{}])[0].get("text", "") for x in r]
        err = [x["result"].get("isError") for x in r]

        assert len(out[0]["result"]["tools"]) == 4, out[0]
        assert t[0].startswith("node: dev"), t[0]
        assert err[1] and "verdict" in t[1], t[1]         # advance without a verdict is refused
        assert err[2], t[2]                               # and with a bogus one
        assert t[4].startswith("node: review"), t[4]
        assert not err[5] and "staying put" in t[5], t[5] # fail does NOT move
        assert t[6].startswith("node: review"), t[6]      # ...confirmed
        assert err[7] and "note" in t[7], t[7]            # back demands a note
        assert err[8], t[8]                               # and a real node
        assert t[9].startswith("node: dev (1/3) (lap 2)"), t[9]
        assert "[from review] falsy code skips the guard" in t[10], t[10]  # note persists
        assert "[from review]" not in t[11], t[11]        # a pass closes the finding
        assert "[from review]" not in t[12], t[12]        # ...and it stays closed
        assert t[14].startswith("node: done") and "lap 2" in t[14], t[14]
        assert "already done" in t[15], t[15]
        assert t[16].startswith("node: dev") and "lap" not in t[16], t[16]
        assert err[17], t[17]
        print("ok")

main()


def hook(payload, state_file, content=None):
    import pathlib
    if content is not None:
        pathlib.Path(state_file).write_text(content)
    p = subprocess.run([sys.executable, "hook_stop.py"], input=json.dumps(payload),
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
