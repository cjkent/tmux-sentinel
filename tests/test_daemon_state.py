"""Tests for the daemon's in-memory state management."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmux_sentinel_daemon.state import DaemonState, PaneState
from tmux_sentinel.status import IDLE, WORKING, WAITING, ERROR


def test_apply_agent_spawn():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "SessionStart", "cwd": "/tmp"},
        pane_id="100",
    )
    ps = state.get("100")
    assert ps is not None
    assert ps.status == IDLE
    assert ps.cwd == "/tmp"
    assert ps.unseen is False
    print("  ✓ test_apply_agent_spawn")


def test_apply_user_prompt_submit():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "SessionStart", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    ps = state.get("100")
    assert ps.status == WORKING
    print("  ✓ test_apply_user_prompt_submit")


def test_apply_stop_idle():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp", "assistant_response": "Done."},
        pane_id="100",
    )
    ps = state.get("100")
    assert ps.status == IDLE
    assert ps.unseen is True
    print("  ✓ test_apply_stop_idle")


def test_apply_stop_waiting():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp", "assistant_response": "What do you think?"},
        pane_id="100",
    )
    ps = state.get("100")
    assert ps.status == WAITING
    assert ps.unseen is True
    print("  ✓ test_apply_stop_waiting")


def test_apply_stop_error():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "PostToolUse", "tool_response": {"success": False}},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp"},
        pane_id="100",
    )
    ps = state.get("100")
    assert ps.status == ERROR
    assert ps.has_error is False  # cleared after stop
    assert ps.unseen is True
    print("  ✓ test_apply_stop_error")


def test_pre_tool_use_preserves_timestamp():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    ts = state.get("100").timestamp
    state.apply_hook_event(
        {"hookEventName": "PreToolUse", "cwd": "/tmp"},
        pane_id="100",
    )
    assert state.get("100").timestamp == ts
    print("  ✓ test_pre_tool_use_preserves_timestamp")


def test_mark_seen():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp"},
        pane_id="100",
    )
    assert state.get("100").unseen is True
    state.mark_seen("100")
    assert state.get("100").unseen is False
    print("  ✓ test_mark_seen")


def test_any_working():
    state = DaemonState()
    assert state.any_working() is False
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    assert state.any_working() is True
    print("  ✓ test_any_working")


def test_remove_pane():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "SessionStart", "cwd": "/tmp"},
        pane_id="100",
    )
    assert state.get("100") is not None
    state.remove_pane("100")
    assert state.get("100") is None
    print("  ✓ test_remove_pane")


def test_kiro_event_names():
    state = DaemonState()
    state.apply_hook_event(
        {"hook_event_name": "agentSpawn", "cwd": "/tmp"},
        pane_id="200",
    )
    assert state.get("200").status == IDLE
    state.apply_hook_event(
        {"hook_event_name": "userPromptSubmit", "cwd": "/tmp"},
        pane_id="200",
    )
    assert state.get("200").status == WORKING
    print("  ✓ test_kiro_event_names")


if __name__ == "__main__":
    test_apply_agent_spawn()
    test_apply_user_prompt_submit()
    test_apply_stop_idle()
    test_apply_stop_waiting()
    test_apply_stop_error()
    test_pre_tool_use_preserves_timestamp()
    test_mark_seen()
    test_any_working()
    test_remove_pane()
    test_kiro_event_names()
    print("\nAll tests passed")
