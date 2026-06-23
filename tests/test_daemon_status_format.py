"""Tests for daemon status bar formatting."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmux_agents_daemon.state import DaemonState
from tmux_agents_daemon.status_format import format_status_output
from tmux_agents.status import IDLE, WORKING, WAITING


def test_empty_state():
    state = DaemonState()
    result = format_status_output(state, "1")
    assert result == ""
    print("  ✓ test_empty_state")


def test_working_in_other_pane():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    result = format_status_output(state, "200")
    assert "working" in result
    assert "⚙" in result
    print("  ✓ test_working_in_other_pane")


def test_focused_pane_excluded():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    result = format_status_output(state, "100")
    assert result == ""
    print("  ✓ test_focused_pane_excluded")


def test_unseen_finished():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp"},
        pane_id="100",
    )
    result = format_status_output(state, "200")
    assert "finished" in result
    assert "●" in result
    print("  ✓ test_unseen_finished")


def test_waiting_waiting():
    state = DaemonState()
    ps = state.ensure("100")
    ps.status = WAITING
    result = format_status_output(state, "200")
    assert "waiting" in result
    assert "⚠" in result
    print("  ✓ test_waiting_waiting")


def test_multiple_categories():
    state = DaemonState()
    # One working
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    # One finished+unseen
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="101",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp"},
        pane_id="101",
    )
    # One waiting
    ps = state.ensure("102")
    ps.status = WAITING

    result = format_status_output(state, "200")
    assert "working" in result
    assert "finished" in result
    assert "waiting" in result
    print("  ✓ test_multiple_categories")


if __name__ == "__main__":
    test_empty_state()
    test_working_in_other_pane()
    test_focused_pane_excluded()
    test_unseen_finished()
    test_waiting_waiting()
    test_multiple_categories()
    print("\nAll tests passed")
