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
        out = rpc([{"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
                   c("status"), c("advance"), c("advance"), c("advance"), c("advance"),
                   c("back"), c("back", to="dev"), c("advance"), c("back", to="nope"),
                   c("reset"), c("back"), c("nope")], env)
        text = [r["result"].get("content", [{}])[0].get("text", "") for r in out[1:]]
        assert len(out[0]["result"]["tools"]) == 4, out[0]
        assert text[0].startswith("node: dev"), text
        assert text[1].startswith("node: qa"), text
        assert text[2].startswith("node: review"), text
        assert text[3].startswith("node: done"), text
        assert "already done" in text[4], text            # advance past done is a no-op
        assert text[5].startswith("node: review (3/3) (lap 2)"), text   # back from done
        assert text[6].startswith("node: dev (1/3) (lap 3)"), text      # back --to jumps
        assert text[7].startswith("node: qa (2/3) (lap 3)"), text       # lap survives advance
        assert out[9]["result"]["isError"] is True, out[9]              # bad node name
        assert text[9].startswith("node: dev") and "lap" not in text[9], text  # reset clears lap
        assert "already at the first node" in text[10], text
        assert out[-1]["result"]["isError"] is True, out[-1]
        print("ok")

main()
