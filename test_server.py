"""Run: python3 test_server.py

Covers docs/multi-flow.md AC1-8, 12, 14 — the flows-keyed-by-slug schema,
flow_start's dir collision guard, per-flow independence of advance/back/reset,
required `flow` arg, and 0.5.0 single-slot migration.
"""
import json, os, subprocess, sys, tempfile


def rpc(lines, env, cwd=None):
    p = subprocess.run([sys.executable, os.path.join(os.getcwd(), "plugin/server.py")],
                       input="\n".join(json.dumps(l) for l in lines),
                       capture_output=True, text=True, env=env, cwd=cwd)
    return [json.loads(l) for l in p.stdout.splitlines()]


def run(seq, env, cwd=None):
    out = rpc([{"jsonrpc": "2.0", "id": 0, "method": "tools/list"}] + [
        {"jsonrpc": "2.0", "id": i + 1, "method": "tools/call", "params": {"name": n, "arguments": a}}
        for i, (n, a) in enumerate(seq)
    ], env, cwd=cwd)
    tools, results = out[0], out[1:]
    texts = [r["result"]["content"][0]["text"] for r in results]
    errs = [bool(r["result"].get("isError")) for r in results]
    return tools, texts, errs


def state(env):
    return json.loads(open(env["MINI_VISE_STATE"]).read())


def test_tool_list_has_five_tools_flow_required():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        tools, _, _ = run([], env)
        names = sorted(t["name"] for t in tools["result"]["tools"])
        assert names == ["advance", "back", "flow_start", "reset", "status"], names
        by_name = {t["name"]: t for t in tools["result"]["tools"]}
        assert by_name["advance"]["inputSchema"]["required"] == ["flow", "verdict"]
        assert by_name["back"]["inputSchema"]["required"] == ["flow", "to", "note"]
        assert by_name["reset"]["inputSchema"]["required"] == ["flow"]
        assert "required" not in by_name["status"]["inputSchema"] or "flow" not in \
            by_name["status"]["inputSchema"].get("required", [])


def test_ac1_two_flows_different_dir_independent():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/dir-a")),
            ("flow_start", dict(slug="b", dir="/tmp/dir-b")),
        ], env)
        assert errs == [False, False], (texts, errs)
        assert texts[0].startswith("[flow: a] node: spec")
        assert texts[1].startswith("[flow: b] node: spec")
        s = state(env)
        assert set(s["flows"]) == {"a", "b"}
        assert s["flows"]["a"]["dir"] == "/tmp/dir-a"
        assert s["flows"]["b"]["dir"] == "/tmp/dir-b"


def test_ac2_flow_start_same_dir_as_open_flow_errors_and_does_not_create():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/shared")),
            ("flow_start", dict(slug="b", dir="/tmp/shared")),
        ], env)
        assert errs == [False, True], (texts, errs)
        assert "already in use by open flow='a'" in texts[1], texts[1]
        s = state(env)
        assert set(s["flows"]) == {"a"}, "b must not have been created"


def test_ac3_flow_start_same_dir_after_other_flow_done_succeeds():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        P = dict(verdict="pass")
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/shared")),
            ("advance", dict(flow="a", **P)),                                    # spec->dev
            ("advance", dict(flow="a", **P)),                                    # dev->qa
            ("advance", dict(flow="a", verdict="pass", evidence="pytest\nok")),  # qa->review
            ("advance", dict(flow="a", **P)),                                    # review->done
            ("flow_start", dict(slug="b", dir="/tmp/shared")),                   # a is done now
        ], env)
        assert errs == [False, False, False, False, False, False], (texts, errs)
        assert texts[4].startswith("[flow: a] node: done"), texts[4]
        assert texts[-1].startswith("[flow: b] node: spec")


def test_ac4_advance_moves_only_target_flow():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("flow_start", dict(slug="b", dir="/tmp/b")),
            ("advance", dict(flow="a", verdict="pass")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        s = state(env)
        assert s["flows"]["a"]["node"] == "dev"
        assert s["flows"]["b"]["node"] == "spec", "b must be untouched"
        assert s["flows"]["b"]["lap"] == 1


def test_ac5_evidence_gate_at_qa_is_per_flow():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("flow_start", dict(slug="b", dir="/tmp/b")),
            ("advance", dict(flow="a", verdict="pass")),   # a: spec->dev
            ("advance", dict(flow="a", verdict="pass")),   # a: dev->qa
            ("advance", dict(flow="a", verdict="pass")),   # a: qa, missing evidence -> error
            ("advance", dict(flow="b", verdict="pass")),   # b: spec->dev, unaffected by a's block
        ], env)
        assert errs == [False, False, False, False, True, False], (texts, errs)
        assert "evidence" in texts[4]
        s = state(env)
        assert s["flows"]["a"]["node"] == "qa", "a must not have advanced past the missing-evidence gate"
        assert s["flows"]["b"]["node"] == "dev"


def test_ac6_back_raises_lap_of_only_target_flow():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("flow_start", dict(slug="b", dir="/tmp/b")),
            ("back", dict(flow="a", to="dev", note="fix the guard")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        assert texts[2].startswith("[flow: a] node: dev (2/4) (lap 2)"), texts[2]
        s = state(env)
        assert s["flows"]["a"]["lap"] == 2
        assert s["flows"]["a"]["node"] == "dev"
        assert s["flows"]["b"]["lap"] == 1, "b's lap must be untouched"
        assert s["flows"]["b"]["node"] == "spec"


def test_ac7_advance_back_reset_missing_or_unknown_flow_errors_state_unchanged():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir="/tmp/x"))], env)
        snap = state(env)
        _, texts, errs = run([
            ("advance", dict(verdict="pass")),                    # flow omitted
            ("advance", dict(flow="ghost", verdict="pass")),      # flow unknown
            ("back", dict(to="dev", note="x")),                   # flow omitted
            ("back", dict(flow="ghost", to="dev", note="x")),     # flow unknown
            ("reset", dict()),                                    # flow omitted
            ("reset", dict(flow="ghost")),                        # flow unknown
        ], env)
        assert errs == [True, True, True, True, True, True], (texts, errs)
        for t in texts:
            assert "valid slugs: a" in t, t
        assert state(env) == snap, "no advance/back/reset error may mutate state"


def test_ac8_status_no_flow_renders_all_open_flows_each_with_own_finding():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("flow_start", dict(slug="b", dir="/tmp/b")),
            ("advance", dict(flow="a", verdict="pass")),                    # a: spec->dev
            ("back", dict(flow="a", to="dev", note="finding on a only")),
            ("status", dict()),
        ], env)
        assert errs == [False, False, False, False, False], (texts, errs)
        out = texts[-1]
        assert "[flow: a]" in out and "[flow: b]" in out
        assert "finding on a only" in out
        # b has no open finding
        b_section = out.split("[flow: b]")[1]
        assert "open finding to fix here" not in b_section


def test_status_unknown_flow_errors_not_silent():
    """dev's open question: status(flow=<unknown>) errors via unknown_flow.
    Spec only mandates the error for advance/back/reset (AC7); status's
    optional `flow` isn't covered either way. Pinning current behavior."""
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir="/tmp/a"))], env)
        _, texts, errs = run([("status", dict(flow="ghost"))], env)
        assert errs == [True], (texts, errs)
        assert "valid slugs: a" in texts[0], texts[0]


def test_ac12_0_5_0_single_slot_state_migrates_to_main_no_error():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        old = {"node": "qa", "lap": 1, "note": None, "note_for": None,
               "spec_path": "docs/x.md", "evidence": None}
        with open(f, "w") as fh:
            json.dump(old, fh)
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("status", dict())], env)
        assert errs == [False], (texts, errs)
        assert texts[0].startswith("[flow: main] node: qa"), texts[0]
        # not rewritten to disk until the next write() — crash mid-migration leaves original intact
        assert json.loads(open(f).read()) == old, "read() must not rewrite the file"


def test_ac14_two_flows_full_pipeline_parallel_no_crossed_evidence():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/pipeline-a")),
            ("flow_start", dict(slug="b", dir="/tmp/pipeline-b")),
            ("advance", dict(flow="a", verdict="pass", spec_path="docs/a.md")),
            ("advance", dict(flow="b", verdict="pass", spec_path="docs/b.md")),
            ("advance", dict(flow="a", verdict="pass")),
            ("advance", dict(flow="b", verdict="pass")),
            ("advance", dict(flow="a", verdict="pass", evidence="pytest a\n1 passed")),
            ("advance", dict(flow="b", verdict="pass", evidence="pytest b\n1 passed")),
            ("advance", dict(flow="a", verdict="pass")),
            ("advance", dict(flow="b", verdict="pass")),
        ], env)
        assert errs == [False] * 10, (texts, errs)
        s = state(env)
        a, b = s["flows"]["a"], s["flows"]["b"]
        assert a["node"] == "done" and b["node"] == "done"
        assert a["spec_path"] == "docs/a.md" and b["spec_path"] == "docs/b.md"
        assert a["evidence"] == "pytest a\n1 passed"
        assert b["evidence"] == "pytest b\n1 passed"
        assert "pytest a" in texts[-2] and "pytest a" not in texts[-1]
        assert "pytest b" in texts[-1] and "pytest b" not in texts[-2]


def hook(payload, state_file, content=None):
    import pathlib
    if content is not None:
        pathlib.Path(state_file).write_text(content)
    p = subprocess.run([sys.executable, "plugin/hook_stop.py"], input=json.dumps(payload),
                       capture_output=True, text=True,
                       env={**os.environ, "MINI_VISE_STATE": state_file})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_hook_stop_single_flow_shape():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")

        assert hook({}, f) == {}, "no state file: nothing to gate"

        mid = json.dumps({"flows": {"main": {
            "node": "qa", "lap": 2, "note": "[from review] fix the guard",
            "note_for": "qa", "spec_path": None, "evidence": None, "dir": None,
        }}})
        r = hook({}, f, mid)
        assert r["decision"] == "block", r
        assert "[flow: main]" in r["reason"] and "node: qa" in r["reason"] and "fix the guard" in r["reason"], r

        # the escape hatch: a second stop is allowed through, warned not blocked
        r = hook({"stop_hook_active": True}, f, mid)
        assert "decision" not in r and "systemMessage" in r, r

        done = json.dumps({"flows": {"main": {
            "node": "done", "lap": 1, "note": None, "note_for": None,
            "spec_path": None, "evidence": None, "dir": None,
        }}})
        assert hook({}, f, done) == {}, "done releases"
        assert "decision" not in hook({}, f, "{ not json"), "corrupt state must not trap"
        assert hook({}, f, json.dumps({"flows": {"main": {"node": "bogus"}}})) == {}
        assert hook("not a dict", f, mid)["decision"] == "block", "junk payload still gates"


def test_ac9_hook_stop_two_flows_blocks_until_both_done_one_nudge_not_per_flow():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")

        def flow(node, lap=1, note=None, note_for=None, dir_=None):
            return {"node": node, "lap": lap, "note": note, "note_for": note_for,
                    "spec_path": None, "evidence": None, "dir": dir_}

        two_open = json.dumps({"flows": {
            "a": flow("qa", dir_="/tmp/a"),
            "b": flow("dev", lap=2, note="[from qa] fix x", note_for="dev", dir_="/tmp/b"),
        }})
        r = hook({}, f, two_open)
        assert r["decision"] == "block", r
        assert "2 flow(s) not finished" in r["reason"], r
        assert "[flow: a]" in r["reason"] and "[flow: b]" in r["reason"], r
        assert "fix x" in r["reason"], r

        one_done_one_open = json.dumps({"flows": {
            "a": flow("done", dir_="/tmp/a"),
            "b": flow("dev", lap=2, note="[from qa] fix x", note_for="dev", dir_="/tmp/b"),
        }})
        r = hook({}, f, one_done_one_open)
        assert r["decision"] == "block", r
        assert "1 flow(s) not finished" in r["reason"], r
        assert "[flow: a]" not in r["reason"], "done flow must not appear in the block text"
        assert "[flow: b]" in r["reason"], r

        both_done = json.dumps({"flows": {"a": flow("done", dir_="/tmp/a"), "b": flow("done", dir_="/tmp/b")}})
        assert hook({}, f, both_done) == {}, "silent only once both are done"

        # one nudge total per stop_hook_active, not one per open flow
        r = hook({"stop_hook_active": True}, f, two_open)
        assert "decision" not in r, r
        assert r["systemMessage"].count("[mini-vise] stopping with open flows.") == 1, r


def test_advance_fail_stays_put_per_flow_other_flow_unaffected():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("flow_start", dict(slug="b", dir="/tmp/b")),
            ("advance", dict(flow="a", verdict="fail")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        assert not errs[2] and "staying put" in texts[2], texts[2]
        s = state(env)
        assert s["flows"]["a"]["node"] == "spec", "fail must not move the node"
        assert s["flows"]["a"]["lap"] == 1
        assert s["flows"]["b"]["node"] == "spec", "b untouched by a's fail"


def test_advance_verdict_validation_errors():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir="/tmp/a"))], env)
        _, texts, errs = run([
            ("advance", dict(flow="a")),               # verdict missing
            ("advance", dict(flow="a", verdict="maybe")),  # verdict invalid
        ], env)
        assert errs == [True, True], (texts, errs)
        assert "verdict" in texts[0] and "verdict" in texts[1]


def test_advance_on_done_flow_reports_already_done():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        P = dict(verdict="pass")
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("advance", dict(flow="a", **P)),
            ("advance", dict(flow="a", **P)),
            ("advance", dict(flow="a", verdict="pass", evidence="pytest\nok")),
            ("advance", dict(flow="a", **P)),   # -> done
            ("advance", dict(flow="a", **P)),   # already done
        ], env)
        assert errs[-1] is False
        assert "already done" in texts[-1], texts[-1]


def test_back_validation_errors():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir="/tmp/a"))], env)
        _, texts, errs = run([
            ("back", dict(flow="a", to="dev", note="")),        # note required
            ("back", dict(flow="a", to="nope", note="x")),      # to must be a real node
        ], env)
        assert errs == [True, True], (texts, errs)
        assert "note" in texts[0]


def test_pass_clears_open_finding_stays_cleared():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("advance", dict(flow="a", verdict="pass")),                       # spec->dev
            ("back", dict(flow="a", to="dev", note="the empty-code case")),
            ("status", dict(flow="a")),
            ("advance", dict(flow="a", verdict="pass")),                       # a pass closes it
            ("status", dict(flow="a")),
        ], env)
        assert errs == [False] * 6, (texts, errs)
        assert "the empty-code case" in texts[3]
        assert "[from dev]" not in texts[4] and "open finding" not in texts[4]
        assert "open finding" not in texts[5], "must stay closed"


def test_reset_happy_path_is_per_flow_and_preserves_dir_for_the_collision_guard():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir="/tmp/a")),
            ("flow_start", dict(slug="b", dir="/tmp/b")),
            ("advance", dict(flow="a", verdict="pass")),
            ("back", dict(flow="a", to="dev", note="x")),
            ("reset", dict(flow="a")),
            ("flow_start", dict(slug="c", dir="/tmp/a")),   # a still open post-reset -> dir still blocked
        ], env)
        assert errs == [False, False, False, False, False, True], (texts, errs)
        assert texts[4].startswith("[flow: a] node: spec") and "lap" not in texts[4]
        assert "already in use by open flow='a'" in texts[5], texts[5]
        s = state(env)
        assert s["flows"]["a"] == {**BLANK_FOR_TEST, "dir": "/tmp/a"}
        assert s["flows"]["b"]["node"] == "spec", "b untouched by a's reset"


BLANK_FOR_TEST = {"node": "spec", "lap": 1, "note": None, "note_for": None,
                   "spec_path": None, "evidence": None}


def test_flow_start_dir_collision_relative_dot_vs_absolute_cwd():
    # lap 2 fix: os.path.realpath() on both sides. "." resolves to the
    # server's cwd — must collide with the absolute form of that same cwd.
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        with tempfile.TemporaryDirectory() as work:
            work = os.path.realpath(work)
            _, texts, errs = run([("flow_start", dict(slug="a", dir=work))], env, cwd=work)
            assert errs == [False], (texts, errs)
            _, texts2, errs2 = run([("flow_start", dict(slug="b", dir="."))], env, cwd=work)
            assert errs2 == [True], (texts2, errs2)
            assert f"already in use by open flow='a'" in texts2[0], texts2[0]
            s = state(env)
            assert set(s["flows"]) == {"a"}, "b must not have been created"


def test_flow_start_dir_collision_trailing_slash():
    # lap 2 fix: realpath normalizes a trailing slash — "/tmp/x" and "/tmp/x/"
    # must be treated as the same dir.
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        with tempfile.TemporaryDirectory() as work:
            work = os.path.realpath(work)
            _, texts, errs = run([("flow_start", dict(slug="a", dir=work))], env)
            assert errs == [False], (texts, errs)
            _, texts2, errs2 = run([("flow_start", dict(slug="b", dir=work + "/"))], env)
            assert errs2 == [True], (texts2, errs2)
            assert "already in use by open flow='a'" in texts2[0], texts2[0]
            s = state(env)
            assert set(s["flows"]) == {"a"}, "b must not have been created"


def test_flow_start_blocked_by_legacy_migrated_flow_with_no_recorded_dir():
    # lap 2 fix: a 0.5.0-migrated flow has dir=None (BLANK default) and we
    # cannot prove it doesn't collide with a new flow's dir, so flow_start
    # must block defensively rather than silently permit a possible collision.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        old_shape = {"node": "qa", "lap": 1, "note": None, "note_for": None,
                     "spec_path": "docs/x.md", "evidence": None}  # no "dir" key -> BLANK fills None
        with open(f, "w") as fh:
            json.dump(old_shape, fh)
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("flow_start", dict(slug="b", dir="/tmp/anything"))], env)
        assert errs == [True], (texts, errs)
        assert "already in use by open flow='main'" in texts[0], texts[0]
        # a failed flow_start must not write() — disk stays in the pre-migration
        # single-slot shape, untouched (same crash-safety guarantee as AC12)
        assert json.loads(open(f).read()) == old_shape, "state file must be untouched by a failed flow_start"


def test_flow_start_not_blocked_by_legacy_flow_once_it_is_done():
    # the defensive dir=None block only applies while the migrated flow is open
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        old_shape = {"node": "done", "lap": 1, "note": None, "note_for": None,
                     "spec_path": "docs/x.md", "evidence": "pytest -q\nok"}
        with open(f, "w") as fh:
            json.dump(old_shape, fh)
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("flow_start", dict(slug="b", dir="/tmp/anything"))], env)
        assert errs == [False], (texts, errs)
        assert texts[0].startswith("[flow: b] node: spec")


def test_unknown_tool_name_errors():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir="/tmp/a"))], env)
        _, texts, errs = run([("nope", dict(flow="a"))], env)
        assert errs == [True], (texts, errs)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print("test_server ok")


if __name__ == "__main__":
    main()
