"""Tests for the OpenCode plugin's translation of events.

The plugin is JavaScript, so it runs under Node with a stand-in for OpenCode's context
(tests/opencode_plugin_driver.mjs). That checks our mapping and the sub-session filter.
It does NOT check OpenCode's real payloads, which are undocumented: the event types come
from the docs and the field names inside them are assumptions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmux_sentinel.install import opencode_render

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "opencode_plugin_driver.mjs")
REPO = "/fake/repo dir"          # a space, to prove the path survives being embedded


def _run(scenario: dict) -> dict:
    tmp = tempfile.mkdtemp()
    try:
        plugin = os.path.join(tmp, "plugin.mjs")
        with open(plugin, "w") as f:
            f.write(opencode_render(REPO))
        sc, out = os.path.join(tmp, "s.json"), os.path.join(tmp, "o.json")
        with open(sc, "w") as f:
            json.dump(scenario, f)
        res = subprocess.run(["node", DRIVER, plugin, sc, out],
                             capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr
        with open(out) as f:
            return json.load(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ev(type_, **props):
    return {"hook": "event", "event": {"type": type_, "properties": props}}


def _names(result: dict) -> list[str]:
    """Event names sent, once per send (each send runs hook.py then hook_client)."""
    return [c["payload"]["hook_event_name"] for c in result["calls"] if "hook.py" in c["cmd"]]


def test_main_session_lifecycle_maps_to_our_events():
    r = _run({"steps": [
        _ev("session.created", info={"id": "s1"}),
        _ev("session.status", sessionID="s1", status={"type": "busy"}),
        {"hook": "tool.execute.before", "input": {"sessionID": "s1", "tool": "bash"}},
        _ev("session.idle", sessionID="s1"),
    ]})
    assert _names(r) == ["agentSpawn", "userPromptSubmit", "preToolUse", "stop"]


def test_busy_is_reported_once_per_turn():
    r = _run({"steps": [
        _ev("session.status", sessionID="s1", status={"type": "busy"}),
        _ev("session.status", sessionID="s1", status={"type": "busy"}),
        _ev("session.status", sessionID="s1", status={"type": "busy"}),
        _ev("session.idle", sessionID="s1"),
        _ev("session.status", sessionID="s1", status={"type": "busy"}),   # next turn
    ]})
    assert _names(r) == ["userPromptSubmit", "stop", "userPromptSubmit"]


def test_permission_events_map_to_waiting_and_back():
    r = _run({"steps": [
        _ev("permission.asked", sessionID="s1"),
        _ev("permission.replied", sessionID="s1"),
        _ev("permission.updated", sessionID="s1"),     # the name older releases used
    ]})
    assert _names(r) == ["permissionRequest", "permissionReplied", "permissionRequest"]


def test_session_error_sets_the_error_flag():
    r = _run({"steps": [_ev("session.error", sessionID="s1")]})
    first = [c for c in r["calls"] if "hook.py" in c["cmd"]][0]["payload"]
    assert first["hook_event_name"] == "postToolUse"
    assert first["tool_response"] == {"success": False}


def test_subagent_sessions_are_ignored():
    # A child session runs in the same pane and fires the same events. Its activity is
    # not the main session's, so none of it may be reported.
    r = _run({"steps": [
        _ev("session.created", info={"id": "s1"}),
        _ev("session.created", info={"id": "c1", "parentID": "s1"}),
        _ev("session.status", sessionID="c1", status={"type": "busy"}),
        {"hook": "tool.execute.before", "input": {"sessionID": "c1", "tool": "read"}},
        _ev("permission.asked", sessionID="c1"),
        _ev("session.idle", sessionID="c1"),
    ]})
    assert _names(r) == ["agentSpawn"]        # only the main session's own creation


def test_a_child_learned_from_session_updated_is_also_ignored():
    r = _run({"steps": [
        _ev("session.updated", info={"id": "c2", "parentID": "s1"}),
        _ev("session.idle", sessionID="c2"),
    ]})
    assert _names(r) == []


def test_unknown_sessions_are_treated_as_the_main_one():
    # Fails towards reporting: a missed notification is worse than an extra one.
    r = _run({"steps": [_ev("session.idle", sessionID="never-seen")]})
    assert _names(r) == ["stop"]


def test_events_without_a_session_id_still_work():
    r = _run({"steps": [{"hook": "event", "event": {"type": "session.idle"}}]})
    assert _names(r) == ["stop"]


def test_each_send_runs_the_file_hook_and_the_daemon_client():
    r = _run({"steps": [_ev("session.idle", sessionID="s1")]})
    cmds = [c["cmd"] for c in r["calls"]]
    assert any("hook.py" in c for c in cmds)
    assert any("tmux_sentinel_daemon.hook_client" in c for c in cmds)
    for c in r["calls"]:
        assert c["env"]["PYTHONPATH"] == REPO
        assert c["payload"]["cwd"] == "/proj"


def test_the_repo_path_with_a_space_is_embedded_intact():
    r = _run({"steps": [_ev("session.idle", sessionID="s1")]})
    hook = [c for c in r["calls"] if "hook.py" in c["cmd"]][0]["cmd"]
    assert f"{REPO}/tmux_sentinel/hook.py" in hook


def test_the_bell_from_the_hook_is_written_by_the_plugin():
    r = _run({"steps": [_ev("session.idle", sessionID="s1")]})
    assert r["bells"] == 1


def test_nothing_is_registered_outside_tmux():
    r = _run({"noTmux": True, "steps": []})
    assert r["hookNames"] == []


def test_only_one_function_is_exported():
    # OpenCode treats every exported function as a plugin, so a helper export would be
    # loaded as one.
    assert _run({"steps": []})["exports"] == 1


def test_registers_the_documented_hooks():
    assert sorted(_run({"steps": []})["hookNames"]) == ["event", "tool.execute.before"]


if __name__ == "__main__":
    if shutil.which("node") is None:
        print("node not found; skipping the OpenCode plugin tests")
        sys.exit(0)
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
