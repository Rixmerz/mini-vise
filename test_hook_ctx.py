"""Run: python3 test_hook_ctx.py

Covers docs/context-handoff.md AC6 (SessionStart), AC8 (malformed input /
unreadable state never raises) and docs/multi-flow.md AC10 (SessionStart
additionalContext names every open flow), AC11 (corrupt/unparseable state ->
{} regression guard), AC13 (this file rewritten for the {"flows": {slug:
...}} shape, not the old single-slot one), and docs/tier-sweep.md C2
(PreCompact removed 0.7.0 — decide() still answers {} for a stale hooks.json
entry, never a systemMessage).
"""
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile


@contextlib.contextmanager
def tmpdir():
    """A throwaway directory, real path resolved — the idiom test_server.py
    uses in place of a hardcoded /tmp literal."""
    with tempfile.TemporaryDirectory() as d:
        yield os.path.realpath(d)


def hook(payload_str, state_file=None, content=None, set_env=True):
    if content is not None:
        pathlib.Path(state_file).write_text(content)
    env = {**os.environ}
    if set_env and state_file is not None:
        env["MINI_VISE_STATE"] = state_file
    p = subprocess.run(
        [sys.executable, "plugin/hook_ctx.py"],
        input=payload_str,
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, f"hook_ctx exited {p.returncode}: {p.stderr}"
    return json.loads(p.stdout)


def flow(node, lap=1, note=None, note_for=None, spec_path=None, evidence=None, dir_=None):
    return {"node": node, "lap": lap, "note": note, "note_for": note_for,
            "spec_path": spec_path, "evidence": evidence, "dir": dir_}


def one_open_flow():
    return json.dumps({"flows": {"main": flow("qa", spec_path="docs/x.md")}})


def two_open_flows(dir_a, dir_b):
    return json.dumps({"flows": {
        "a": flow("qa", spec_path="docs/a.md", dir_=dir_a),
        "b": flow("dev", lap=2, note="[from qa] fix x", note_for="dev", dir_=dir_b),
    }})


def test_session_start_no_state_file():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")  # never created
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f)
        assert r == {}, r


def test_session_start_open_pipeline_reports_node():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "resume"})
        r = hook(payload, f, one_open_flow())
        hso = r.get("hookSpecificOutput", {})
        assert hso.get("hookEventName") == "SessionStart", r
        assert "[flow: main]" in hso.get("additionalContext", ""), r
        assert "node: qa" in hso.get("additionalContext", ""), r
        assert "systemMessage" not in r, r
        assert "decision" not in r, r


def test_session_start_compact_source_reports():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
        r = hook(payload, f, one_open_flow())
        assert "node: qa" in r.get("hookSpecificOutput", {}).get("additionalContext", ""), r


def test_session_start_unknown_source_is_silent():
    # source not in {compact, resume, startup} => {} even with an open pipeline
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "clear"})
        r = hook(payload, f, one_open_flow())
        assert r == {}, r


def test_session_start_done_node_is_silent():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        done_state = json.dumps({"flows": {"main": flow("done", spec_path="docs/x.md", evidence="pytest -q\nok")}})
        r = hook(payload, f, done_state)
        assert r == {}, r


def test_ac10_session_start_names_every_open_flow():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, two_open_flows(dir_a, dir_b))
        ctx = r.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "[flow: a]" in ctx and "[flow: b]" in ctx, ctx
        assert "node: qa" in ctx and "node: dev" in ctx, ctx
        assert "fix x" in ctx, ctx


def test_ac10_session_start_omits_done_flow_from_open_list():
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        mixed = json.dumps({"flows": {
            "a": flow("done", dir_=dir_a),
            "b": flow("dev", note="[from qa] fix x", note_for="dev", dir_=dir_b),
        }})
        r = hook(payload, f, mixed)
        ctx = r.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "[flow: a]" not in ctx, ctx
        assert "[flow: b]" in ctx, ctx


def test_c1_precompact_returns_empty_even_with_open_flows():
    # C1: PreCompact is dropped from hooks.json (0.7.0); decide() keeps a
    # branch only so a stale install's hooks.json entry degrades silently
    # instead of emitting a systemMessage claiming to steer a summarizer
    # through a channel (`systemMessage`) that never reached it. Feeding two
    # open flows is the point: {} for the *right* reason, not because there
    # was nothing to report.
    with tempfile.TemporaryDirectory() as d, tmpdir() as dir_a, tmpdir() as dir_b:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "PreCompact"})
        r = hook(payload, f, two_open_flows(dir_a, dir_b))
        assert r == {}, r


def test_malformed_json_stdin_never_raises():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        r = hook("not json at all {{{", f, one_open_flow())
        assert r == {}, r


def test_empty_stdin_never_raises():
    # "" parses as {} per `json.loads(stdin or "{}")` — no hook_event_name, so
    # it isn't the SessionStart-source-filter branch; falls through to reading
    # state. Guarantee under test is exit 0 / no exception, not a specific body.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        r = hook("", f, one_open_flow())
        assert "decision" not in r, r


def test_non_dict_json_stdin_never_raises():
    # non-dict payload ([1,2,3]) is coerced to {} by `main()`, same fallthrough
    # as empty stdin. Guarantee under test is exit 0 / no exception.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        r = hook("[1, 2, 3]", f, one_open_flow())
        assert "decision" not in r, r


def test_ac11_corrupt_state_file_is_silent():
    # decide() parses raw JSON itself before calling server.read(), so corrupt
    # state is silent instead of asserting a node server.read()'s BLANK
    # fallback invented. Regression guard on the old single-slot AC11.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, "{not valid json")
        assert r == {}, r


def test_ac11_corrupt_state_non_dict_json_is_silent():
    # valid JSON, but not an object => no "flows"/"node" key => degrades to {}
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, "[1, 2, 3]")
        assert r == {}, r


def test_ac11_corrupt_state_wrong_node_type_is_silent():
    # "node" present but not a valid node name => server.read() drops the flow => {}
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, json.dumps({"flows": {"main": {"node": "not-a-real-node"}}}))
        assert r == {}, r


def test_ac11_corrupt_flows_value_not_a_dict_is_silent():
    # "flows" present but not an object -> server.read() returns {} (AC11 shape variant)
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, json.dumps({"flows": "not-a-dict"}))
        assert r == {}, r


def test_unreadable_state_path_never_raises():
    # MINI_VISE_STATE points at a directory, not a file: read_text() raises
    # IsADirectoryError, an OSError subclass — must still degrade to {}.
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "adir")
        os.mkdir(sub)
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, sub)
        assert r == {}, r


def test_ac12_migrated_0_5_0_state_reports_as_flow_main():
    # 0.5.0 single-slot file still loads and surfaces via SessionStart, keyed "main".
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        old_shape = json.dumps({"node": "qa", "lap": 1, "note": None, "note_for": None,
                                "spec_path": "docs/x.md", "evidence": None})
        r = hook(payload, f, old_shape)
        ctx = r.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "[flow: main]" in ctx and "node: qa" in ctx, r


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print("hook_ctx ok")


if __name__ == "__main__":
    main()
