"""Run: python3 test_hook_ctx.py

Covers docs/context-handoff.md AC6 (SessionStart), AC7 (PreCompact), AC8
(malformed input / unreadable state never raises).
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile


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


def open_state():
    return json.dumps({"node": "qa", "lap": 1, "note": None, "note_for": None,
                        "spec_path": "docs/x.md", "evidence": None})


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
        r = hook(payload, f, open_state())
        hso = r.get("hookSpecificOutput", {})
        assert hso.get("hookEventName") == "SessionStart", r
        assert "node: qa" in hso.get("additionalContext", ""), r
        assert "systemMessage" not in r, r
        assert "decision" not in r, r


def test_session_start_compact_source_reports():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
        r = hook(payload, f, open_state())
        assert "node: qa" in r.get("hookSpecificOutput", {}).get("additionalContext", ""), r


def test_session_start_unknown_source_is_silent():
    # source not in {compact, resume, startup} => {} even with an open pipeline
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "clear"})
        r = hook(payload, f, open_state())
        assert r == {}, r


def test_session_start_done_node_is_silent():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        done_state = json.dumps({"node": "done", "lap": 1, "note": None, "note_for": None,
                                  "spec_path": "docs/x.md", "evidence": "pytest -q\nok"})
        r = hook(payload, f, done_state)
        assert r == {}, r


def test_precompact_open_pipeline_never_uses_decision():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "PreCompact"})
        r = hook(payload, f, open_state())
        assert "systemMessage" in r, r
        assert "decision" not in r, r
        assert "node: qa" in r["systemMessage"], r


def test_precompact_no_state_is_silent():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")  # never created
        payload = json.dumps({"hook_event_name": "PreCompact"})
        r = hook(payload, f)
        assert r == {}, r


def test_precompact_done_node_is_silent():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "PreCompact"})
        done_state = json.dumps({"node": "done", "lap": 1, "note": None, "note_for": None,
                                  "spec_path": None, "evidence": None})
        r = hook(payload, f, done_state)
        assert r == {}, r


def test_malformed_json_stdin_never_raises():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        r = hook("not json at all {{{", f, open_state())
        assert r == {}, r


def test_empty_stdin_never_raises():
    # "" parses as {} per `json.loads(stdin or "{}")` — no hook_event_name, so
    # it isn't the SessionStart-source-filter branch; falls through to reading
    # state. Guarantee under test is exit 0 / no exception, not a specific body.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        r = hook("", f, open_state())
        assert "decision" not in r, r


def test_non_dict_json_stdin_never_raises():
    # non-dict payload ([1,2,3]) is coerced to {} by `main()`, same fallthrough
    # as empty stdin. Guarantee under test is exit 0 / no exception.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        r = hook("[1, 2, 3]", f, open_state())
        assert "decision" not in r, r


def test_corrupt_state_file_is_silent():
    # AC11: decide() now parses raw JSON itself before calling server.read(),
    # so corrupt state is silent — matching hook_stop.py — instead of
    # asserting a node that server.read()'s BLANK fallback invented.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, "{not valid json")
        assert r == {}, r


def test_corrupt_state_non_dict_json_is_silent():
    # valid JSON, but not an object => no "node" key => KeyError/TypeError caught
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, "[1, 2, 3]")
        assert r == {}, r


def test_corrupt_state_wrong_node_type_is_silent():
    # "node" present but not a valid node name => not in server.NODES => {}
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.json")
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        r = hook(payload, f, json.dumps({"node": "not-a-real-node"}))
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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print("hook_ctx ok")


if __name__ == "__main__":
    main()
