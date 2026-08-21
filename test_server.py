"""Run: python3 test_server.py"""
import json, os, subprocess, sys, tempfile

def rpc(lines, env):
    p = subprocess.run([sys.executable, "server.py"], input="\n".join(json.dumps(l) for l in lines),
                       capture_output=True, text=True, env=env)
    return [json.loads(l) for l in p.stdout.splitlines()]

def main():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        c = lambda n: {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": n}}
        out = rpc([{"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
                   c("status"), c("advance"), c("advance"), c("advance"), c("advance"),
                   c("reset"), c("nope")], env)
        text = [r["result"].get("content", [{}])[0].get("text", "") for r in out[1:]]
        assert len(out[0]["result"]["tools"]) == 3, out[0]
        assert text[0].startswith("node: dev"), text
        assert text[1].startswith("node: qa"), text
        assert text[2].startswith("node: review"), text
        assert text[3].startswith("node: done"), text
        assert "already done" in text[4], text          # advance past done is a no-op
        assert text[5].startswith("node: dev"), text     # reset
        assert out[-1]["result"]["isError"] is True, out[-1]
        print("ok")

main()
