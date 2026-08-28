"""Run: python3 test_server.py

Covers docs/multi-flow.md AC1-8, 12, 14 — the flows-keyed-by-slug schema,
flow_start's dir collision guard, per-flow independence of advance/back/reset,
required `flow` arg, and 0.5.0 single-slot migration. Plus docs/tier-sweep.md
AC4 (version consistency), AC11-12 (flow_close), AC13-15 (run log), AC16-20
(D3 dev-tree gate), AC26-27 (Stop hook spec carve-out).
"""
import contextlib, hashlib, json, os, pathlib, subprocess, sys, tempfile


@contextlib.contextmanager
def tmpdir():
    """A throwaway directory, real path resolved — AC3's idiom in place of a
    hardcoded /tmp literal (those only ever coincidentally worked where the
    system temp dir's realpath equals its own literal path)."""
    with tempfile.TemporaryDirectory() as d:
        yield os.path.realpath(d)


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


def test_tool_list_has_six_tools_flow_required():
    # tier-sweep D1 adds flow_close as the 5th mutating tool (6th tool
    # overall) — was "five tools", spec-called-out break (dev's handoff).
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        tools, _, _ = run([], env)
        names = sorted(t["name"] for t in tools["result"]["tools"])
        assert names == ["advance", "back", "flow_close", "flow_start", "reset", "status"], names
        by_name = {t["name"]: t for t in tools["result"]["tools"]}
        assert by_name["advance"]["inputSchema"]["required"] == ["flow", "verdict"]
        assert "checks" in by_name["advance"]["inputSchema"]["properties"]
        assert by_name["back"]["inputSchema"]["required"] == ["flow", "to", "note", "kind"]
        assert by_name["reset"]["inputSchema"]["required"] == ["flow"]
        assert by_name["flow_close"]["inputSchema"]["required"] == ["flow"]
        assert "required" not in by_name["status"]["inputSchema"] or "flow" not in \
            by_name["status"]["inputSchema"].get("required", [])


def test_ac1_two_flows_different_dir_independent():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
        ], env)
        assert errs == [False, False], (texts, errs)
        assert texts[0].startswith("[flow: a] node: spec")
        assert texts[1].startswith("[flow: b] node: spec")
        s = state(env)
        assert set(s["flows"]) == {"a", "b"}
        assert s["flows"]["a"]["dir"] == dir_a
        assert s["flows"]["b"]["dir"] == dir_b


def test_ac2_flow_start_same_dir_as_open_flow_errors_and_does_not_create():
    with tempfile.TemporaryDirectory() as d, tmpdir() as shared:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=shared)),
            ("flow_start", dict(slug="b", dir=shared)),
        ], env)
        assert errs == [False, True], (texts, errs)
        assert "already in use by open flow='a'" in texts[1], texts[1]
        s = state(env)
        assert set(s["flows"]) == {"a"}, "b must not have been created"


def test_ac3_flow_start_same_dir_after_other_flow_done_succeeds():
    # shared dir must not be a git repo — otherwise D3's dev-tree gate at the
    # dev->qa advance below would fire on an unrelated assumption (tier-sweep
    # reviewer finding 3). tempfile.TemporaryDirectory guarantees that, a
    # hardcoded /tmp path did not.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as shared:
        shared = os.path.realpath(shared)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        P = dict(verdict="pass", checks="ruff check: All checks passed")
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=shared)),
            ("advance", dict(flow="a", **P)),                                    # spec->dev
            ("advance", dict(flow="a", **P)),                                    # dev->qa
            ("advance", dict(flow="a", verdict="pass", evidence="pytest\nok", checks="ruff check: All checks passed")),  # qa->review
            ("advance", dict(flow="a", **P)),                                    # review->done
            ("flow_start", dict(slug="b", dir=shared)),                          # a is done now
        ], env)
        assert errs == [False, False, False, False, False, False], (texts, errs)
        assert texts[4].startswith("[flow: a] node: done"), texts[4]
        assert texts[-1].startswith("[flow: b] node: spec")


def test_ac4_advance_moves_only_target_flow():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        s = state(env)
        assert s["flows"]["a"]["node"] == "dev"
        assert s["flows"]["b"]["node"] == "spec", "b must be untouched"
        assert s["flows"]["b"]["lap"] == 1


def test_ac5_evidence_gate_at_qa_is_per_flow():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),   # a: spec->dev
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),   # a: dev->qa
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),   # a: qa, missing evidence -> error
            ("advance", dict(flow="b", verdict="pass", checks="ruff check: All checks passed")),   # b: spec->dev, unaffected by a's block
        ], env)
        assert errs == [False, False, False, False, True, False], (texts, errs)
        assert "evidence" in texts[4]
        s = state(env)
        assert s["flows"]["a"]["node"] == "qa", "a must not have advanced past the missing-evidence gate"
        assert s["flows"]["b"]["node"] == "dev"


def test_ac6_back_raises_lap_of_only_target_flow():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
            ("back", dict(flow="a", to="dev", note="fix the guard", kind="judgement")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        assert texts[2].startswith("[flow: a] node: dev (2/4) (lap 2)"), texts[2]
        s = state(env)
        assert s["flows"]["a"]["lap"] == 2
        assert s["flows"]["a"]["node"] == "dev"
        assert s["flows"]["b"]["lap"] == 1, "b's lap must be untouched"
        assert s["flows"]["b"]["node"] == "spec"


def test_ac7_advance_back_reset_missing_or_unknown_flow_errors_state_unchanged():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=dir_a))], env)
        snap = state(env)
        _, texts, errs = run([
            ("advance", dict(verdict="pass", checks="ruff check: All checks passed")),                    # flow omitted
            ("advance", dict(flow="ghost", verdict="pass", checks="ruff check: All checks passed")),      # flow unknown
            ("back", dict(to="dev", note="x", kind="judgement")),                   # flow omitted
            ("back", dict(flow="ghost", to="dev", note="x", kind="judgement")),     # flow unknown
            ("reset", dict()),                                    # flow omitted
            ("reset", dict(flow="ghost")),                        # flow unknown
        ], env)
        assert errs == [True, True, True, True, True, True], (texts, errs)
        for t in texts:
            assert "valid slugs: a" in t, t
        assert state(env) == snap, "no advance/back/reset error may mutate state"


def test_ac8_status_no_flow_renders_all_open_flows_each_with_own_finding():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),                    # a: spec->dev
            ("back", dict(flow="a", to="dev", note="finding on a only", kind="judgement")),
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
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=dir_a))], env)
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
    # dirs must not be git repos, same reason as test_ac3 above.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as pa, \
            tempfile.TemporaryDirectory() as pb:
        pa, pb = os.path.realpath(pa), os.path.realpath(pb)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=pa)),
            ("flow_start", dict(slug="b", dir=pb)),
            ("advance", dict(flow="a", verdict="pass", spec_path="docs/a.md", checks="ruff check: All checks passed")),
            ("advance", dict(flow="b", verdict="pass", spec_path="docs/b.md", checks="ruff check: All checks passed")),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),
            ("advance", dict(flow="b", verdict="pass", checks="ruff check: All checks passed")),
            ("advance", dict(flow="a", verdict="pass", evidence="pytest a\n1 passed", checks="ruff check: All checks passed")),
            ("advance", dict(flow="b", verdict="pass", evidence="pytest b\n1 passed", checks="ruff check: All checks passed")),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),
            ("advance", dict(flow="b", verdict="pass", checks="ruff check: All checks passed")),
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
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        f = os.path.join(d, "s.json")

        def flow(node, lap=1, note=None, note_for=None, dir_=None):
            return {"node": node, "lap": lap, "note": note, "note_for": note_for,
                    "spec_path": None, "evidence": None, "dir": dir_}

        two_open = json.dumps({"flows": {
            "a": flow("qa", dir_=dir_a),
            "b": flow("dev", lap=2, note="[from qa] fix x", note_for="dev", dir_=dir_b),
        }})
        r = hook({}, f, two_open)
        assert r["decision"] == "block", r
        assert "2 flow(s) not finished" in r["reason"], r
        assert "[flow: a]" in r["reason"] and "[flow: b]" in r["reason"], r
        assert "fix x" in r["reason"], r

        one_done_one_open = json.dumps({"flows": {
            "a": flow("done", dir_=dir_a),
            "b": flow("dev", lap=2, note="[from qa] fix x", note_for="dev", dir_=dir_b),
        }})
        r = hook({}, f, one_done_one_open)
        assert r["decision"] == "block", r
        assert "1 flow(s) not finished" in r["reason"], r
        assert "[flow: a]" not in r["reason"], "done flow must not appear in the block text"
        assert "[flow: b]" in r["reason"], r

        both_done = json.dumps({"flows": {"a": flow("done", dir_=dir_a), "b": flow("done", dir_=dir_b)}})
        assert hook({}, f, both_done) == {}, "silent only once both are done"

        # one nudge total per stop_hook_active, not one per open flow
        r = hook({"stop_hook_active": True}, f, two_open)
        assert "decision" not in r, r
        assert r["systemMessage"].count("[mini-vise] stopping with open flows.") == 1, r


def test_advance_fail_stays_put_per_flow_other_flow_unaffected():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
            ("advance", dict(flow="a", verdict="fail")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        assert not errs[2] and "staying put" in texts[2], texts[2]
        s = state(env)
        assert s["flows"]["a"]["node"] == "spec", "fail must not move the node"
        assert s["flows"]["a"]["lap"] == 1
        assert s["flows"]["b"]["node"] == "spec", "b untouched by a's fail"


def test_advance_verdict_validation_errors():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=dir_a))], env)
        _, texts, errs = run([
            ("advance", dict(flow="a")),               # verdict missing
            ("advance", dict(flow="a", verdict="maybe")),  # verdict invalid
        ], env)
        assert errs == [True, True], (texts, errs)
        assert "verdict" in texts[0] and "verdict" in texts[1]


def test_advance_on_done_flow_reports_already_done():
    # dir must not be a git repo, same reason as test_ac3 above.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as flow_dir:
        flow_dir = os.path.realpath(flow_dir)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        P = dict(verdict="pass", checks="ruff check: All checks passed")
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=flow_dir)),
            ("advance", dict(flow="a", **P)),
            ("advance", dict(flow="a", **P)),
            ("advance", dict(flow="a", verdict="pass", evidence="pytest\nok", checks="ruff check: All checks passed")),
            ("advance", dict(flow="a", **P)),   # -> done
            ("advance", dict(flow="a", **P)),   # already done
        ], env)
        assert errs[-1] is False
        assert "already done" in texts[-1], texts[-1]


def test_back_validation_errors():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=dir_a))], env)
        _, texts, errs = run([
            ("back", dict(flow="a", to="dev", note="", kind="judgement")),        # note required
            ("back", dict(flow="a", to="nope", note="x", kind="judgement")),      # to must be a real node
        ], env)
        assert errs == [True, True], (texts, errs)
        assert "note" in texts[0]


def test_pass_clears_open_finding_stays_cleared():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),                       # spec->dev
            ("back", dict(flow="a", to="dev", note="the empty-code case", kind="judgement")),
            ("status", dict(flow="a")),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),                       # a pass closes it
            ("status", dict(flow="a")),
        ], env)
        assert errs == [False] * 6, (texts, errs)
        assert "the empty-code case" in texts[3]
        assert "[from dev]" not in texts[4] and "open finding" not in texts[4]
        assert "open finding" not in texts[5], "must stay closed"


def test_reset_happy_path_is_per_flow_and_preserves_dir_for_the_collision_guard():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=dir_a)),
            ("flow_start", dict(slug="b", dir=dir_b)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),
            ("back", dict(flow="a", to="dev", note="x", kind="judgement")),
            ("reset", dict(flow="a")),
            ("flow_start", dict(slug="c", dir=dir_a)),   # a still open post-reset -> dir still blocked
        ], env)
        assert errs == [False, False, False, False, False, True], (texts, errs)
        assert texts[4].startswith("[flow: a] node: spec") and "lap" not in texts[4]
        assert "already in use by open flow='a'" in texts[5], texts[5]
        s = state(env)
        assert s["flows"]["a"] == {**BLANK_FOR_TEST, "dir": dir_a}
        assert s["flows"]["b"]["node"] == "spec", "b untouched by a's reset"


BLANK_FOR_TEST = {"node": "spec", "lap": 1, "note": None, "note_for": None,
                   "spec_path": None, "evidence": None, "tree": None,
                   "checks": None, "history": []}


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
    # lap 2 fix: realpath normalizes a trailing slash — a dir and that same
    # dir with a trailing slash appended must be treated as the same dir.
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
    with tempfile.TemporaryDirectory() as d, tmpdir() as anywhere:
        f = os.path.join(d, "s.json")
        old_shape = {"node": "qa", "lap": 1, "note": None, "note_for": None,
                     "spec_path": "docs/x.md", "evidence": None}  # no "dir" key -> BLANK fills None
        with open(f, "w") as fh:
            json.dump(old_shape, fh)
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("flow_start", dict(slug="b", dir=anywhere))], env)
        assert errs == [True], (texts, errs)
        assert "already in use by open flow='main'" in texts[0], texts[0]
        # a failed flow_start must not write() — disk stays in the pre-migration
        # single-slot shape, untouched (same crash-safety guarantee as AC12)
        assert json.loads(open(f).read()) == old_shape, "state file must be untouched by a failed flow_start"


def test_flow_start_not_blocked_by_legacy_flow_once_it_is_done():
    # the defensive dir=None block only applies while the migrated flow is open
    with tempfile.TemporaryDirectory() as d, tmpdir() as anywhere:
        f = os.path.join(d, "s.json")
        old_shape = {"node": "done", "lap": 1, "note": None, "note_for": None,
                     "spec_path": "docs/x.md", "evidence": "pytest -q\nok"}
        with open(f, "w") as fh:
            json.dump(old_shape, fh)
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("flow_start", dict(slug="b", dir=anywhere))], env)
        assert errs == [False], (texts, errs)
        assert texts[0].startswith("[flow: b] node: spec")


def test_unknown_tool_name_errors():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=dir_a))], env)
        _, texts, errs = run([("nope", dict(flow="a"))], env)
        assert errs == [True], (texts, errs)


def git_init(d):
    subprocess.run(["git", "init", "-q"], cwd=d, check=True, capture_output=True)


def git_status_hash(d):
    r = subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True)
    return hashlib.sha256(r.stdout.encode()).hexdigest()


def git_commit_all(d, msg):
    # explicit -c identity, not the environment's global config — a snapshot
    # test must not depend on git being pre-configured on whatever machine runs it.
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=qa@example.com", "-c", "user.name=qa", "commit", "-q", "-m", msg],
        cwd=d, check=True, capture_output=True,
    )


def git_init_with_commit(d):
    """A repo with real history — needed to exercise H1 (HEAD moving) and the
    snapshot feature (commit-tree needs a parent), unlike the bare `git_init`
    above whose repos deliberately have no commits."""
    git_init(d)
    pathlib.Path(d, "README").write_text("init")
    git_commit_all(d, "initial")


def snapshot_ref_exists(d, slug):
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/mini-vise/snapshots/{slug}"],
        cwd=d, capture_output=True, text=True,
    )
    return r.returncode == 0


def snapshot_ref_rev(d, slug):
    r = subprocess.run(
        ["git", "rev-parse", f"refs/mini-vise/snapshots/{slug}"],
        cwd=d, capture_output=True, text=True,
    )
    return r.stdout.strip()


def reflog_count(d, ref):
    r = subprocess.run(["git", "reflog", "show", ref], cwd=d, capture_output=True, text=True)
    return len([l for l in r.stdout.splitlines() if l.strip()])


def write_state(path, flows):
    pathlib.Path(path).write_text(json.dumps({"flows": flows}))


def raw_flow(node, lap=1, note=None, note_for=None, spec_path=None, evidence=None, dir_=None,
             tree=None, checks=None, history=None):
    return {"node": node, "lap": lap, "note": note, "note_for": note_for,
            "spec_path": spec_path, "evidence": evidence, "dir": dir_, "tree": tree,
            "checks": checks, "history": list(history or [])}


def hist(lap, frm, to, note, kind="judgement"):
    return {"lap": lap, "from": frm, "to": to, "note": note, "kind": kind}


CHECKS = "ruff check: All checks passed"
DEV_PASS = dict(verdict="pass", checks=CHECKS)


def test_ac1_render_shows_dir_line_when_set_omits_when_none():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_x:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([("flow_start", dict(slug="a", dir=dir_x))], env)
        assert errs == [False], (texts, errs)
        assert f"dir: {dir_x}" in texts[0], texts[0]

        f = os.path.join(d, "legacy.json")
        old_shape = {"node": "qa", "lap": 1, "note": None, "note_for": None,
                     "spec_path": "docs/x.md", "evidence": None}  # no dir -> BLANK fills None
        with open(f, "w") as fh:
            json.dump(old_shape, fh)
        env2 = {**os.environ, "MINI_VISE_STATE": f}
        _, texts2, errs2 = run([("status", dict())], env2)
        assert errs2 == [False], (texts2, errs2)
        assert "dir:" not in texts2[0], texts2[0]


def test_ac4_version_consistent_across_server_plugin_json_marketplace_json():
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        out = rpc([{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}], env)
        server_version = out[0]["result"]["serverInfo"]["version"]
        plugin_json = json.loads(open("plugin/.claude-plugin/plugin.json").read())
        marketplace_json = json.loads(open(".claude-plugin/marketplace.json").read())
        mp_version = marketplace_json["plugins"][0]["version"]
        assert server_version == plugin_json["version"] == mp_version, \
            (server_version, plugin_json["version"], mp_version)


def test_ac11_flow_close_removes_entry_frees_slug_and_dir():
    with tempfile.TemporaryDirectory() as d, tmpdir() as close_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=close_a)),
            ("flow_close", dict(flow="a")),
            ("status", dict()),
            ("flow_start", dict(slug="a", dir=close_a)),  # slug and dir both reusable
        ], env)
        assert errs == [False, False, False, False], (texts, errs)
        assert "was at node: spec" in texts[1], texts[1]
        assert texts[2] == "no open flows — call flow_start(slug, dir) to begin.", texts[2]
        assert texts[3].startswith("[flow: a] node: spec")
        s = state(env)
        assert set(s["flows"]) == {"a"}


def test_ac11_flow_close_unknown_slug_errors_state_unchanged():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=dir_a))], env)
        snap = state(env)
        _, texts, errs = run([("flow_close", dict(flow="ghost"))], env)
        assert errs == [True], (texts, errs)
        assert "valid slugs: a" in texts[0], texts[0]
        assert state(env) == snap, "a failed flow_close must not mutate state"


def test_ac12_flow_close_on_open_flow_names_the_node():
    with tempfile.TemporaryDirectory() as d, tmpdir() as close_b:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=close_b)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev
            ("flow_close", dict(flow="a")),
        ], env)
        assert errs == [False, False, False], (texts, errs)
        assert "was at node: dev" in texts[2], texts[2]


def test_ac13_mutating_calls_log_one_jsonl_line_status_logs_none():
    with tempfile.TemporaryDirectory() as d, tmpdir() as log_a:
        state_path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": state_path}
        log_path = pathlib.Path(state_path).with_suffix(".log")
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=log_a)),
            ("status", dict()),  # a read — must append nothing
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev
            ("back", dict(flow="a", to="dev", note="x", kind="judgement")),
            ("reset", dict(flow="a")),
            ("flow_close", dict(flow="a")),
        ], env)
        assert errs == [False] * 6, (texts, errs)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 5, lines  # status contributes none
        entries = [json.loads(l) for l in lines]
        for e in entries:
            assert set(e) == {"ts", "flow", "tool", "node", "lap", "verdict", "kind", "note"}, e
            assert e["flow"] == "a"
        assert [e["tool"] for e in entries] == ["flow_start", "advance", "back", "reset", "flow_close"]
        assert entries[1]["verdict"] == "pass"  # the advance line


def test_ac14_log_write_failure_never_fails_the_call():
    # AC14's own example is "parent read-only", but this sandbox likely runs
    # as root, where chmod can't block a write. Occupying the log path with a
    # directory forces the same OSError (IsADirectoryError) deterministically.
    with tempfile.TemporaryDirectory() as d, tmpdir() as log_fail:
        state_path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": state_path}
        log_path = pathlib.Path(state_path).with_suffix(".log")
        log_path.mkdir()
        _, texts, errs = run([("flow_start", dict(slug="a", dir=log_fail))], env)
        assert errs == [False], (texts, errs)
        assert texts[0].startswith("[flow: a] node: spec")
        assert state(env)["flows"]["a"]["node"] == "spec"
        assert log_path.is_dir(), "log path left untouched, no raise reached it"


def test_ac15_gitignore_lists_mini_vise_log():
    lines = open(".gitignore").read().splitlines()
    assert ".mini-vise.log" in lines, lines


def test_ac16_tree_recorded_entering_dev_via_advance_leaving_spec():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev
        ], env)
        assert errs == [False, False], (texts, errs)
        s = state(env)
        assert s["flows"]["a"]["node"] == "dev"
        assert s["flows"]["a"]["tree"] == git_status_hash(repo)


def test_ac16_tree_re_recorded_entering_dev_via_back():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev, baseline1
        ], env)
        baseline1 = state(env)["flows"]["a"]["tree"]
        pathlib.Path(repo, "new.txt").write_text("x")  # tree now differs from baseline1
        _, texts, errs = run([
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),      # dev -> qa (tree differs, allowed)
            ("back", dict(flow="a", to="dev", note="fix it", kind="judgement")),  # re-enter dev, re-record
        ], env)
        assert errs == [False, False], (texts, errs)
        baseline2 = state(env)["flows"]["a"]["tree"]
        assert baseline2 == git_status_hash(repo)
        assert baseline2 != baseline1, "back(to=dev) must re-snapshot, not reuse the old baseline"


def test_ac17_advance_pass_at_dev_blocked_when_tree_unchanged_state_unchanged():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev, baseline recorded
        ], env)
        snap = state(env)
        log_path = pathlib.Path(env["MINI_VISE_STATE"]).with_suffix(".log")
        lines_before = log_path.read_text().splitlines()
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)  # no tree change
        assert errs == [True], (texts, errs)
        assert repo in texts[0], texts[0]
        # AC6: the block message must not assert "no change" as fact — it
        # names the two things it actually compared, tree and HEAD.
        assert "no change" not in texts[0], texts[0]
        assert "tree" in texts[0] and "HEAD" in texts[0], texts[0]
        assert "unchanged since entering dev" in texts[0], texts[0]
        assert state(env) == snap, "a blocked advance must not mutate state"
        assert log_path.read_text().splitlines() == lines_before, "a raise must precede log_call"


def test_ac18_advance_pass_at_dev_allowed_when_tree_differs():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev
        ], env)
        pathlib.Path(repo, "touched.txt").write_text("x")
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "qa"


def test_ac19_dev_check_skips_when_dir_is_none():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        write_state(f, {"a": raw_flow("dev", dir_=None, tree=None)})
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "qa"


def test_ac19_dev_check_skips_when_dir_is_not_a_git_repo():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as notrepo:
        notrepo = os.path.realpath(notrepo)  # no git init — not a repo
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=notrepo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev; tree_hash -> None
        ], env)
        assert state(env)["flows"]["a"]["tree"] is None
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "qa"


def test_ac19_dev_check_skips_when_git_unavailable():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo, \
            tempfile.TemporaryDirectory() as no_git_path:
        repo = os.path.realpath(repo)
        git_init(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev, git present -> real baseline
        ], env)
        assert state(env)["flows"]["a"]["tree"] is not None
        degraded_env = {**env, "PATH": no_git_path}  # empty dir on PATH -> git not found
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], degraded_env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "qa"


def test_ac19_dev_check_skips_when_tree_was_never_recorded():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        pathlib.Path(repo, "dirty.txt").write_text("x")  # real, git-visible change
        f = os.path.join(d, "s.json")
        write_state(f, {"a": raw_flow("dev", dir_=repo, tree=None)})
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "qa"


def test_ac20_dev_tree_check_ignored_on_fail_verdict():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev, tree unchanged since
        ], env)
        _, texts, errs = run([("advance", dict(flow="a", verdict="fail"))], env)
        assert errs == [False], (texts, errs)
        assert "staying put" in texts[0], texts[0]
        assert state(env)["flows"]["a"]["node"] == "dev"


def test_ac20_dev_tree_check_ignored_at_spec():
    # tree field deliberately set equal to the dir's current hash — if the
    # check ran at spec too, this would block; it must not.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        f = os.path.join(d, "s.json")
        write_state(f, {"a": raw_flow("spec", dir_=repo, tree=git_status_hash(repo))})
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "dev"


def test_ac20_dev_tree_check_ignored_at_qa_and_review():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        h = git_status_hash(repo)
        f = os.path.join(d, "s.json")
        write_state(f, {"a": raw_flow("qa", dir_=repo, tree=h)})
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", evidence="pytest\nok", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "review"

        f2 = os.path.join(d, "s2.json")
        write_state(f2, {"a": raw_flow("review", dir_=repo, tree=h, evidence="pytest\nok")})
        env2 = {**os.environ, "MINI_VISE_STATE": f2}
        _, texts2, errs2 = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env2)
        assert errs2 == [False], (texts2, errs2)
        assert state(env2)["flows"]["a"]["node"] == "done"


# docs/snapshots.md AC4-AC7 (H1 — tree hash must count a commit as change)


def test_snap_ac4_advance_pass_at_dev_succeeds_after_commit_with_clean_tree():
    # the recovery path H1 exists for: dev writes code, commits it, tree goes
    # clean again — must not wedge, because HEAD moved even though the
    # working tree porcelain didn't.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev, baseline = clean tree + initial HEAD
        ], env)
        pathlib.Path(repo, "new.txt").write_text("x")
        git_commit_all(repo, "dev work")  # tree clean again, but HEAD moved
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "qa"


def test_snap_ac5_advance_blocked_when_truly_nothing_changed_with_commit_history():
    # AC17's variant used a repo with zero commits (HEAD always unresolvable,
    # so the hash never actually exercises the HEAD half). This one has real
    # history on both sides of the comparison, and is still blocked when dev
    # neither touches the tree nor commits.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # spec -> dev
        ], env)
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)  # nothing touched
        assert errs == [True], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "dev"


# AC6 (block message wording) is pinned in test_ac17_advance_pass_at_dev_blocked_when_tree_unchanged_state_unchanged above.
# AC7 (H1 degrades open) is pinned in the existing test_ac19_dev_check_skips_* series above — dir=None, non-repo,
# git unavailable, and tree never recorded all still advance without raising; unaffected by H1's HEAD addition.


# docs/snapshots.md AC8-AC17 (the snapshot feature, I1-I4)


def test_snap_ac8_every_mutating_call_snapshots_flow_close_snapshots_before_delete():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}

        run([("flow_start", dict(slug="a", dir=repo))], env)
        assert snapshot_ref_exists(repo, "a"), "flow_start must snapshot"
        rev1 = snapshot_ref_rev(repo, "a")

        run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)  # spec -> dev
        rev2 = snapshot_ref_rev(repo, "a")
        assert rev2 != rev1, "advance must snapshot"

        run([("back", dict(flow="a", to="dev", note="x", kind="judgement"))], env)
        rev3 = snapshot_ref_rev(repo, "a")
        assert rev3 != rev2, "back must snapshot"

        run([("reset", dict(flow="a"))], env)
        rev4 = snapshot_ref_rev(repo, "a")
        assert rev4 != rev3, "reset must snapshot"

        _, texts, errs = run([("flow_close", dict(flow="a"))], env)
        assert errs == [False], (texts, errs)
        assert snapshot_ref_exists(repo, "a"), "flow_close's snapshot must survive the entry being deleted"
        rev5 = snapshot_ref_rev(repo, "a")
        assert rev5 != rev4, "flow_close must snapshot before deleting"


def test_snap_ac9_status_creates_no_snapshot():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=repo))], env)
        before = snapshot_ref_rev(repo, "a")
        before_reflog = reflog_count(repo, "refs/mini-vise/snapshots/a")
        run([("status", dict(flow="a")), ("status", dict())], env)
        assert snapshot_ref_rev(repo, "a") == before, "status must not move the snapshot ref"
        assert reflog_count(repo, "refs/mini-vise/snapshots/a") == before_reflog, \
            "status must not add a reflog entry"


def test_snap_ac10_snapshot_leaves_status_diff_cached_and_head_byte_identical():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        pathlib.Path(repo, "untracked.txt").write_text("hi")  # dirty tree going into the snapshot
        status_before = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                        capture_output=True, text=True).stdout
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                      capture_output=True, text=True).stdout
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=repo))], env)  # triggers a snapshot
        assert snapshot_ref_exists(repo, "a")
        status_after = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                       capture_output=True, text=True).stdout
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                     capture_output=True, text=True).stdout
        diff_cached = subprocess.run(["git", "diff", "--cached"], cwd=repo,
                                      capture_output=True, text=True).stdout
        assert status_after == status_before, (status_before, status_after)
        assert head_after == head_before, (head_before, head_after)
        assert diff_cached == "", diff_cached


def test_snap_ac11_snapshot_tree_has_untracked_content_but_not_gitignored_state():
    with tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)
        # the flow's own state files must be gitignored in its dir, or the
        # snapshot legitimately includes them and this reads as a phantom bug
        pathlib.Path(repo, ".gitignore").write_text(".mini-vise.json\n.mini-vise.log\n")
        git_commit_all(repo, "initial")
        pathlib.Path(repo, "untracked.txt").write_text("keep me")
        # state lives inside the flow's own dir, same as the real default (cwd)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(repo, ".mini-vise.json")}
        run([("flow_start", dict(slug="a", dir=repo))], env)
        assert snapshot_ref_exists(repo, "a")
        files = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "refs/mini-vise/snapshots/a"],
            cwd=repo, capture_output=True, text=True,
        ).stdout.splitlines()
        assert "untracked.txt" in files, files
        assert ".gitignore" in files, files
        assert ".mini-vise.json" not in files, files
        assert ".mini-vise.log" not in files, files


def test_snap_ac12_two_snapshots_leave_two_reflog_entries():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=repo))], env)             # snapshot #1
        run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)          # snapshot #2
        assert reflog_count(repo, "refs/mini-vise/snapshots/a") == 2


def test_snap_ac13_two_flows_sharing_a_repo_via_worktrees_use_independent_refs():
    # the real risk I2 guards against: two worktrees of the *same* repo share
    # one ref namespace (refs, unlike HEAD/index, are not per-worktree), so a
    # snapshot ref named by dir instead of by slug would let one flow's
    # snapshot clobber the other's.
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as main_repo, \
            tempfile.TemporaryDirectory() as parent:
        main_repo = os.path.realpath(main_repo)
        git_init_with_commit(main_repo)
        wt_dir = os.path.realpath(os.path.join(parent, "wt"))
        subprocess.run(["git", "worktree", "add", wt_dir], cwd=main_repo, check=True, capture_output=True)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}

        run([("flow_start", dict(slug="a", dir=main_repo))], env)
        pathlib.Path(wt_dir, "only-in-b.txt").write_text("b")
        run([("flow_start", dict(slug="b", dir=wt_dir))], env)

        rev_a = snapshot_ref_rev(main_repo, "a")
        rev_b = snapshot_ref_rev(main_repo, "b")  # same repo db, seen from either worktree
        assert rev_a and rev_b and rev_a != rev_b, (rev_a, rev_b)
        files_b = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "refs/mini-vise/snapshots/b"],
            cwd=main_repo, capture_output=True, text=True,
        ).stdout
        assert "only-in-b.txt" in files_b, files_b

        run([("flow_close", dict(flow="a"))], env)  # re-snapshots a (I1)
        assert snapshot_ref_rev(main_repo, "a") != rev_a, "flow_close must re-snapshot a"
        assert snapshot_ref_rev(main_repo, "b") == rev_b, "closing flow a must not touch flow b's ref"


def test_snap_ac14_snapshot_failure_dir_none_no_error():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        write_state(f, {"a": raw_flow("dev", dir_=None)})
        env = {**os.environ, "MINI_VISE_STATE": f}
        _, texts, errs = run([("advance", dict(flow="a", verdict="fail"))], env)
        assert errs == [False], (texts, errs)


def test_snap_ac14_snapshot_failure_non_repo_dir_no_error():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as notrepo:
        notrepo = os.path.realpath(notrepo)  # no git init
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([("flow_start", dict(slug="a", dir=notrepo))], env)
        assert errs == [False], (texts, errs)
        assert not snapshot_ref_exists(notrepo, "a")


def test_snap_ac14_snapshot_failure_git_unavailable_no_error():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo, \
            tempfile.TemporaryDirectory() as no_git_path:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        degraded_env = {**env, "PATH": no_git_path}  # empty dir on PATH -> git not found
        _, texts, errs = run([("flow_start", dict(slug="a", dir=repo))], degraded_env)
        assert errs == [False], (texts, errs)
        assert not snapshot_ref_exists(repo, "a")


def test_snap_ac14_snapshot_failure_nonexistent_dir_no_error():
    with tempfile.TemporaryDirectory() as d:
        nonexistent = os.path.join(d, "does-not-exist")
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([("flow_start", dict(slug="a", dir=nonexistent))], env)
        assert errs == [False], (texts, errs)


def test_snap_ac14_snapshot_failure_no_commits_yet_no_error():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init(repo)  # no commit -> commit-tree would have no parent
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([("flow_start", dict(slug="a", dir=repo))], env)
        assert errs == [False], (texts, errs)
        assert not snapshot_ref_exists(repo, "a")


def test_snap_ac15_snapshot_does_not_disturb_h1_hash_still_blocked_after_snapshots():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([
            ("flow_start", dict(slug="a", dir=repo)),              # snapshot
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),           # spec -> dev, baseline recorded, snapshot
        ], env)
        # a fail verdict at dev stays put but still snapshots (I1) — this is
        # the snapshot AC15 must not let leak into the D3 comparison
        _, texts, errs = run([("advance", dict(flow="a", verdict="fail"))], env)  # snapshot, no state move
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["node"] == "dev"
        _, texts2, errs2 = run([("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed"))], env)  # nothing really changed
        assert errs2 == [True], (texts2, errs2)
        assert "unchanged since entering dev" in texts2[0], texts2[0]


def test_snap_ac16_status_names_snapshot_ref_once_it_exists_not_before():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, errs = run([
            ("flow_start", dict(slug="a", dir=repo)),  # not yet a repo -> snapshot silently fails
            ("status", dict(flow="a")),
        ], env)
        assert errs == [False, False], (texts, errs)
        assert "snapshot:" not in texts[-1], texts[-1]

        git_init_with_commit(repo)
        _, texts2, errs2 = run([
            ("advance", dict(flow="a", verdict="pass", checks="ruff check: All checks passed")),  # now a repo -> snapshot succeeds
            ("status", dict(flow="a")),
        ], env)
        assert errs2 == [False, False], (texts2, errs2)
        assert "snapshot: refs/mini-vise/snapshots/a" in texts2[-1], texts2[-1]


def test_snap_ac17_readme_documents_ref_layout_and_restore_command():
    text = pathlib.Path("README.md").read_text()
    assert "refs/mini-vise/snapshots/<slug>" in text, "README missing the snapshot ref layout"
    assert "git restore --source=refs/mini-vise/snapshots/<slug> ." in text, \
        "README missing the git restore recovery line"


def test_ac18_tier_sweep_amended_for_head_and_snapshot_git_scope():
    text = pathlib.Path("docs/tier-sweep.md").read_text()
    assert "Amended by `docs/snapshots.md`" in text, \
        "tier-sweep.md:154 no longer reads as amended for the H1/snapshot git-use expansion"
    assert "git rev-parse HEAD" in text
    assert "refs/mini-vise/snapshots/<slug>" in text


def test_ac2_baseline_skill_example_and_never_compress_list_keep_flow_line():
    # docs/snapshots.md G1/AC2: baseline's report example must open with
    # `flow: <slug>` and its never-compress list must name `flow:` — same
    # guard shape as the AC8 charter tests below, scoped to the shared skill
    # all three charters load.
    text = pathlib.Path("plugin/skills/baseline/SKILL.md").read_text()
    blocks = text.split("```")
    example_bodies = blocks[1::2]
    assert any(b.lstrip().startswith("flow: ") for b in example_bodies), \
        "baseline/SKILL.md: no example block opens with a `flow: ` line"
    never_compress = text.split("Never compress:", 1)[1].split("\n\n", 1)[0]
    assert "`flow:`" in never_compress, \
        "baseline/SKILL.md: never-compress list drops the `flow:` line"


def test_ac3_no_test_file_hardcodes_a_tmp_path():
    # built from parts so this assertion's own source line doesn't match its
    # own pattern and self-trigger
    pattern = '"' + '/tmp'
    out = subprocess.run(["grep", "-n", pattern, "test_server.py", "test_hook_ctx.py"],
                          capture_output=True, text=True)
    assert out.returncode == 1, out.stdout  # grep exit 1 = no match found
    assert out.stdout == "", out.stdout


def test_ac26_stop_hook_all_open_flows_at_spec_no_decision_names_flows():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        f = os.path.join(d, "s.json")
        two_at_spec = json.dumps({"flows": {
            "a": raw_flow("spec", dir_=dir_a),
            "b": raw_flow("spec", dir_=dir_b),
        }})
        r = hook({}, f, two_at_spec)
        assert "decision" not in r, r
        msg = r.get("systemMessage", "")
        assert "[flow: a]" in msg and "[flow: b]" in msg, msg
        assert "parked at spec" in msg, msg


def test_ac27_stop_hook_mixed_spec_and_qa_blocks_names_qa_flow_only():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        f = os.path.join(d, "s.json")
        mixed = json.dumps({"flows": {
            "a": raw_flow("spec", dir_=dir_a),
            "b": raw_flow("qa", dir_=dir_b),
        }})
        r = hook({}, f, mixed)
        assert r["decision"] == "block", r
        assert "[flow: b]" in r["reason"] and "node: qa" in r["reason"], r
        assert "[flow: a]" not in r["reason"], "the spec-parked flow must not appear in the block text"

        # existing stop_hook_active carve-out unchanged: one nudge, then release
        r = hook({"stop_hook_active": True}, f, mixed)
        assert "decision" not in r and "systemMessage" in r, r


CHARTERS = ["plugin/agents/dev.md", "plugin/agents/qa.md", "plugin/agents/reviewer.md"]


def test_ac8_charters_demand_flow_slug_first_line_and_show_it():
    # tier-sweep B2 + lap-2 regression guard: each charter must both state the
    # `flow: <slug>` requirement AND demonstrate it as the first line of its
    # own example block. Losing either half is the exact gap review caught —
    # the demand present but nothing telling the orchestrator to supply it.
    for path in CHARTERS:
        text = pathlib.Path(path).read_text()
        assert "`flow: <slug>`" in text, f"{path}: missing the flow-slug requirement"
        assert "as the first line" in text, f"{path}: requirement not pinned to first line"
        blocks = text.split("```")
        # every fenced block is bounded by a pair of ``` markers -> odd
        # indices (1, 3, 5, ...) are block bodies.
        example_bodies = blocks[1::2]
        assert any(b.lstrip().startswith("flow: ") for b in example_bodies), \
            f"{path}: no example block opens with a `flow: ` line"


def test_ac8_orchestrator_docs_instruct_passing_flow_slug():
    # the other half of the same regression guard: nothing upstream told the
    # orchestrator to hand the slug to a subagent, so charter compliance was
    # unreachable. Pins orchestration/SKILL.md sec2's brief checklist and
    # /mini-vise:run step 3, both wired in dev's lap-2 change.
    skill = pathlib.Path("plugin/skills/orchestration/SKILL.md").read_text()
    assert "## 2. Delegate each node" in skill, "sec2 header moved or renamed"
    sec2 = skill.split("## 2. Delegate each node", 1)[1].split("\n## ", 1)[0]
    assert "flow slug" in sec2, "sec2 brief checklist drops the flow-slug bullet"
    blocks = sec2.split("```")
    example_bodies = blocks[1::2]
    assert any(b.lstrip().startswith("flow: rate-limit.") for b in example_bodies), \
        "sec2 example no longer opens with a `flow: <slug>.` prefix"

    run_md = pathlib.Path("plugin/commands/run.md").read_text()
    assert "3. Delegate" in run_md, "run.md step 3 renumbered or reworded away from delegation"
    step3 = run_md.split("3. Delegate", 1)[1].split("\n4.", 1)[0]
    assert "flow slug" in step3, \
        "run.md step 3 no longer says to pass the flow slug when delegating"


def test_snap_ac18_snapshot_works_with_no_global_git_identity():
    """Regression guard. `commit-tree` is the one call in snapshot() that needs a
    committer identity; without an explicit one it inherits the machine's global
    config, so every snapshot silently no-opped on any box that has none — CI
    containers, fresh checkouts, Docker images. Every other snapshot test passes
    on a configured machine and so cannot catch this coming back.

    GIT_CONFIG_GLOBAL/SYSTEM=/dev/null hides the ambient config from the server
    subprocess only; the helpers below commit with an explicit -c identity of
    their own and are unaffected."""
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as repo:
        repo = os.path.realpath(repo)
        git_init_with_commit(repo)
        env = {
            **os.environ,
            "MINI_VISE_STATE": os.path.join(d, "s.json"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        run([("flow_start", dict(slug="a", dir=repo))], env)
        assert snapshot_ref_exists(repo, "a"), \
            "snapshot did not commit with no global git identity — commit-tree needs an explicit -c identity"
        # and the commit really is attributed to the fixed identity, not to whatever
        # the machine happened to have configured
        who = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>", f"refs/mini-vise/snapshots/a"],
            cwd=repo, capture_output=True, text=True,
        ).stdout.strip()
        assert who == "mini-vise <snapshot@mini-vise.local>", who


# --- 0.9.0: lap history, the checks gate, the short-circuit, classification ---

def test_om_ac1_history_starts_empty_and_is_per_flow():
    """A3a. `read()` builds each flow as {**BLANK, **s}, which copies the
    *reference* — a mutable default in BLANK would be one list object behind
    every flow and every read, and an append on one would surface on another."""
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        with tempfile.TemporaryDirectory() as wa, tempfile.TemporaryDirectory() as wb:
            _, texts, errs = run([
                ("flow_start", dict(slug="a", dir=wa)),
                ("flow_start", dict(slug="b", dir=wb)),
            ], env)
            assert errs == [False, False], (texts, errs)
            s = state(env)
            assert s["flows"]["a"]["history"] == []
            assert s["flows"]["b"]["history"] == []
            # append on `a` only
            run([("advance", dict(flow="a", verdict="pass")),
                 ("advance", dict(flow="a", **DEV_PASS)),
                 ("back", dict(flow="a", to="dev", note="only a", kind="mechanical"))], env)
            s = state(env)
            assert len(s["flows"]["a"]["history"]) == 1, s["flows"]["a"]["history"]
            assert s["flows"]["b"]["history"] == [], "b must not see a's append"


def test_om_ac2_pre_090_state_without_history_loads_as_empty():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": path}
        legacy = {"node": "dev", "lap": 2, "note": "[from qa] x", "note_for": "dev",
                  "spec_path": None, "evidence": None, "dir": None, "tree": None}
        write_state(path, {"a": legacy})
        _, texts, errs = run([("status", dict(flow="a"))], env)
        assert errs == [False], (texts, errs)
        run([("advance", dict(flow="a", **DEV_PASS))], env)
        assert state(env)["flows"]["a"]["history"] == []


def test_om_ac3_back_appends_one_entry_lap_is_post_increment_note_unprefixed():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass")),
             ("advance", dict(flow="a", **DEV_PASS)),
             ("back", dict(flow="a", to="dev", note="429 missing Retry-After", kind="judgement"))], env)
        f = state(env)["flows"]["a"]
        assert len(f["history"]) == 1, f["history"]
        e = f["history"][0]
        assert set(e) == {"lap", "from", "to", "note", "kind"}, e
        assert e["lap"] == f["lap"] == 2, (e, f["lap"])
        assert e["from"] == "qa" and e["to"] == "dev", e
        assert e["note"] == "429 missing Retry-After", e["note"]
        assert "[from" not in e["note"], "history stores the note unprefixed"
        assert f["note"] == "[from qa] 429 missing Retry-After", "note keeps its prefix"


def test_om_ac4_advance_never_touches_history():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass")),
             ("advance", dict(flow="a", **DEV_PASS)),
             ("back", dict(flow="a", to="dev", note="n", kind="mechanical"))], env)
        before = json.dumps(state(env)["flows"]["a"]["history"])
        pathlib.Path(w, "touch.txt").write_text("x")
        run([("advance", dict(flow="a", **DEV_PASS)),      # dev->qa
             ("advance", dict(flow="a", verdict="fail"))], env)
        assert json.dumps(state(env)["flows"]["a"]["history"]) == before


def test_om_ac5_reset_empties_history_flow_close_removes_it():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass")),
             ("advance", dict(flow="a", **DEV_PASS)),
             ("back", dict(flow="a", to="dev", note="n", kind="mechanical"))], env)
        assert len(state(env)["flows"]["a"]["history"]) == 1
        run([("reset", dict(flow="a"))], env)
        assert state(env)["flows"]["a"]["history"] == [], "reset lands at spec — old findings are stale"
        run([("flow_close", dict(flow="a"))], env)
        assert "a" not in state(env)["flows"]


def test_om_ac6_two_backs_append_in_order_second_does_not_overwrite():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass")),
             ("advance", dict(flow="a", **DEV_PASS)),
             ("back", dict(flow="a", to="dev", note="first", kind="mechanical"))], env)
        pathlib.Path(w, "t.txt").write_text("x")
        run([("advance", dict(flow="a", **DEV_PASS)),
             ("back", dict(flow="a", to="dev", note="second", kind="judgement"))], env)
        h = state(env)["flows"]["a"]["history"]
        assert [e["note"] for e in h] == ["first", "second"], h
        assert [e["lap"] for e in h] == [2, 3], h
        assert [e["kind"] for e in h] == ["mechanical", "judgement"], h


def test_om_ac7_no_previous_laps_block_at_lap_1_or_empty_history():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        _, texts, _ = run([("flow_start", dict(slug="a", dir=w)),
                           ("status", dict(flow="a"))], env)
        assert "previous laps" not in texts[1], texts[1]


def test_om_ac8_ac9_prior_laps_render_once_and_survive_a_close():
    """AC8: the open finding shows under `open finding`, not twice.
    AC9: an entry closed by `advance` still renders on a later lap."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": path}
        write_state(path, {"a": raw_flow("dev", lap=3, note="[from review] third", note_for="dev",
                                         history=[hist(2, "qa", "dev", "second finding"),
                                                  hist(3, "review", "dev", "third")])})
        _, texts, _ = run([("status", dict(flow="a"))], env)
        t = texts[0]
        assert "previous laps on this flow" in t, t
        assert "second finding" in t, t
        assert t.count("third") == 1, ("lap-3 note must not appear in both blocks", t)
        assert "open finding to fix here" in t, t
        # AC9: same history, finding already closed (note cleared) — lap 2 still shows
        write_state(path, {"b": raw_flow("qa", lap=3, history=[hist(2, "qa", "dev", "second finding")])})
        _, texts2, _ = run([("status", dict(flow="b"))], env)
        assert "second finding" in texts2[0], texts2[0]
        assert "open finding to fix here" not in texts2[0], texts2[0]


def test_om_ac10_previous_laps_block_is_bounded():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": path}
        h = [hist(i, "qa", "dev", f"finding {i}") for i in range(2, 8)]   # six entries
        write_state(path, {"a": raw_flow("dev", lap=9, history=h)})
        _, texts, _ = run([("status", dict(flow="a"))], env)
        t = texts[0]
        shown = [i for i in range(2, 8) if f"finding {i}" in t]
        assert shown == [5, 6, 7], ("only the three most recent", shown, t)
        assert "3 earlier laps not shown" in t, t


def test_om_ac11_hooks_inherit_the_previous_laps_block():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        write_state(path, {"a": raw_flow("dev", lap=3, note="[from qa] now", note_for="dev",
                                         history=[hist(2, "qa", "dev", "earlier finding")])})
        for script, payload in (("hook_stop.py", {"stop_hook_active": False}),
                                ("hook_ctx.py", {"hook_event_name": "SessionStart", "source": "startup"})):
            r = subprocess.run([sys.executable, os.path.join(os.getcwd(), "plugin", script)],
                               input=json.dumps(payload), capture_output=True, text=True,
                               env={**os.environ, "MINI_VISE_STATE": path})
            assert "earlier finding" in r.stdout, (script, r.stdout, r.stderr)


def test_om_ac12_advance_pass_at_dev_without_checks_raises_state_unchanged():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass"))], env)          # spec->dev
        pathlib.Path(w, "code.py").write_text("x = 1\n")
        before = json.dumps(state(env))
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass")),
                              ("advance", dict(flow="a", verdict="pass", checks="   "))], env)
        assert errs == [True, True], (texts, errs)
        for t in texts:
            assert "checks" in t and "node 'dev'" in t, t
            assert "verbatim" in t, t
        assert json.dumps(state(env)) == before, "a refused advance must not move the flow"


def test_om_ac13_ac16_checks_stored_and_shown_to_qa_only():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass"))], env)
        pathlib.Path(w, "code.py").write_text("x = 1\n")
        _, texts, errs = run([("advance", dict(flow="a", verdict="pass", checks="ruff: 0 errors"))], env)
        assert errs == [False], (texts, errs)
        assert state(env)["flows"]["a"]["checks"] == "ruff: 0 errors"
        assert "dev checks:" in texts[0] and "ruff: 0 errors" in texts[0], texts[0]
        # at review the checks block is gone; qa evidence takes over
        _, t2, _ = run([("advance", dict(flow="a", verdict="pass", evidence="pytest\n1 passed"))], env)
        assert "dev checks:" not in t2[0], t2[0]


def test_om_ac14_checks_gate_only_at_dev_and_only_on_pass():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        # spec passes with no checks
        _, texts, errs = run([("flow_start", dict(slug="a", dir=w)),
                              ("advance", dict(flow="a", verdict="pass"))], env)
        assert errs == [False, False], (texts, errs)
        # dev fail needs no checks either
        _, texts2, errs2 = run([("advance", dict(flow="a", verdict="fail"))], env)
        assert errs2 == [False], (texts2, errs2)
        # qa and review pass with no checks
        pathlib.Path(w, "c.py").write_text("x = 1\n")
        _, texts3, errs3 = run([("advance", dict(flow="a", **DEV_PASS)),
                                ("advance", dict(flow="a", verdict="pass", evidence="pytest\nok")),
                                ("advance", dict(flow="a", verdict="pass"))], env)
        assert errs3 == [False, False, False], (texts3, errs3)


def test_om_ac15_tree_check_and_checks_gate_are_independent():
    """An unchanged tree is refused even when checks are supplied — D3 did not
    become optional by adding a second gate beside it."""
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        with tempfile.TemporaryDirectory() as repo:
            repo = os.path.realpath(repo)
            git_init_with_commit(repo)
            run([("flow_start", dict(slug="a", dir=repo)),
                 ("advance", dict(flow="a", verdict="pass"))], env)   # entering dev records tree
            _, texts, errs = run([("advance", dict(flow="a", **DEV_PASS))], env)
            assert errs == [True], (texts, errs)
            assert "unchanged since entering dev" in texts[0], texts[0]


def test_om_ac17_back_requires_a_valid_kind_state_unchanged():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        env = {**os.environ, "MINI_VISE_STATE": os.path.join(d, "s.json")}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass")),
             ("advance", dict(flow="a", **DEV_PASS))], env)
        before = json.dumps(state(env))
        _, texts, errs = run([("back", dict(flow="a", to="dev", note="n")),
                              ("back", dict(flow="a", to="dev", note="n", kind="oops"))], env)
        assert errs == [True, True], (texts, errs)
        for t in texts:
            assert "mechanical" in t and "judgement" in t, t
        assert json.dumps(state(env)) == before


def test_om_ac18_log_carries_kind_and_note():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as w:
        path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": path}
        run([("flow_start", dict(slug="a", dir=w)),
             ("advance", dict(flow="a", verdict="pass")),
             ("advance", dict(flow="a", **DEV_PASS)),
             ("back", dict(flow="a", to="dev", note="the finding text", kind="mechanical"))], env)
        entries = [json.loads(l) for l in pathlib.Path(path).with_suffix(".log").read_text().splitlines()]
        by_tool = {e["tool"]: e for e in entries}
        assert by_tool["back"]["kind"] == "mechanical", by_tool["back"]
        assert by_tool["back"]["note"] == "the finding text", by_tool["back"]
        assert by_tool["flow_start"]["kind"] is None and by_tool["flow_start"]["note"] is None


def test_om_ac21_ac22_short_circuit_bound_is_stated_and_rendered():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.json")
        env = {**os.environ, "MINI_VISE_STATE": path}
        # not spent: the last lap came from qa
        write_state(path, {"a": raw_flow("dev", lap=2, history=[hist(2, "qa", "dev", "x")])})
        _, t1, _ = run([("status", dict(flow="a"))], env)
        assert "short-circuit spent" not in t1[0], t1[0]
        # spent: the last lap was dev -> dev, so qa has seen nothing
        write_state(path, {"b": raw_flow("dev", lap=2, history=[hist(2, "dev", "dev", "x")])})
        _, t2, _ = run([("status", dict(flow="b"))], env)
        assert "short-circuit spent" in t2[0], t2[0]
        assert "advance to qa" in t2[0], t2[0]


def test_om_ac19_dev_charter_separates_self_check_from_self_review():
    t = pathlib.Path("plugin/agents/dev.md").read_text()
    assert "do not review your own work" in t
    assert "Self-review is forbidden" in t and "Self-check is mandatory" in t, \
        "the gate reads as 'dev writes tests' unless both halves are stated together"
    assert "not break what was there" in t
    for f in ("dev", "qa", "reviewer"):
        assert "effort: medium" in pathlib.Path(f"plugin/agents/{f}.md").read_text(), f


def test_om_ac20_to_ac24_orchestration_skill_carries_the_new_rules():
    t = pathlib.Path("plugin/skills/orchestration/SKILL.md").read_text()
    # AC20 — authoring vs carrying, and the librarian test
    assert "Authoring is forbidden. Carrying is mandatory." in t
    assert "quoted verbatim" in t and "Librarian, not author" in t
    assert "previous laps from `status`" in t
    # AC21 — the short-circuit, its evidence limit and its bound
    assert "without\nspawning `qa`" in t or "without spawning `qa`" in t.replace("\n", " ")
    assert "Only on evidence the node itself produced" in t
    assert "Once per entry to `dev`" in t
    # AC23 — fifth advisor moment, subagents still have none
    assert "when two nodes contradict each other" in t
    assert "subagents still have no advisor" in t
    # AC24 — convergence read, four-lap floor kept
    assert "The same finding recurring" in t and "A different finding each lap" in t
    assert "four or more" in t


def test_om_ac28_readme_and_changelog_document_090():
    rd, cl = pathlib.Path("README.md").read_text(), pathlib.Path("CHANGELOG.md").read_text()
    assert "0.9.0" in cl, "CHANGELOG needs the 0.9.0 entry"
    for probe in ("previous laps", "checks", "mechanical", "judgement"):
        assert probe in rd, f"README does not document {probe!r}"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print("test_server ok")


if __name__ == "__main__":
    main()
