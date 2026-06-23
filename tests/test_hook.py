"""Tests for tmux_agents.hook module."""
import tempfile
from pathlib import Path

from tmux_agents.status import (
    IDLE, WORKING, WAITING, ERROR,
    read_status, has_error_flag, is_unseen,
)
from tmux_agents.hook import handle_event

_test_dir = None


def _setup():
    global _test_dir
    _test_dir = Path(tempfile.mkdtemp()) / "status"
    _test_dir.mkdir(parents=True)


def _ev(event, pane_id="42"):
    handle_event(event, pane_id, status_dir=_test_dir)


def _read(pane_id="42"):
    return read_status(pane_id, status_dir=_test_dir)


def test_agent_spawn():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    s = _read()
    assert s is not None
    assert s.status == IDLE
    assert s.cwd == "/tmp"


def test_user_prompt_submit():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "userPromptSubmit", "cwd": "/tmp"})
    assert _read().status == WORKING


def test_pre_tool_use_preserves_timestamp():
    _setup()
    _ev({"hook_event_name": "userPromptSubmit", "cwd": "/tmp"})
    ts = _read().timestamp
    _ev({"hook_event_name": "preToolUse", "tool_name": "shell", "cwd": "/tmp"})
    s = _read()
    assert s.status == WORKING
    assert s.timestamp == ts


def test_post_tool_use_success():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "postToolUse", "tool_response": {"success": True}})
    assert not has_error_flag("42", status_dir=_test_dir)


def test_post_tool_use_failure():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "postToolUse", "tool_response": {"success": False}})
    assert has_error_flag("42", status_dir=_test_dir)


def test_stop_idle():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "stop", "assistant_response": "Here is the result.", "cwd": "/tmp"})
    assert _read().status == IDLE
    assert is_unseen("42", status_dir=_test_dir)


def test_stop_waiting():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "stop", "assistant_response": "What would you like to do?", "cwd": "/tmp"})
    assert _read().status == WAITING


def test_stop_error():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "postToolUse", "tool_response": {"success": False}})
    _ev({"hook_event_name": "stop", "assistant_response": "Something went wrong.", "cwd": "/tmp"})
    assert _read().status == ERROR
    assert not has_error_flag("42", status_dir=_test_dir)


def test_stop_clears_error_flag():
    _setup()
    _ev({"hook_event_name": "agentSpawn", "cwd": "/tmp"})
    _ev({"hook_event_name": "postToolUse", "tool_response": {"success": False}})
    _ev({"hook_event_name": "stop", "assistant_response": "Done.", "cwd": "/tmp"})
    assert not has_error_flag("42", status_dir=_test_dir)


# --- Claude Code tests ---

def test_cc_session_start():
    _setup()
    _ev({"hook_event_name": "SessionStart", "cwd": "/tmp"})
    s = _read()
    assert s is not None
    assert s.status == IDLE
    assert s.cwd == "/tmp"


def test_cc_user_prompt_submit():
    _setup()
    _ev({"hook_event_name": "SessionStart", "cwd": "/tmp"})
    _ev({"hook_event_name": "UserPromptSubmit", "cwd": "/tmp"})
    assert _read().status == WORKING


def test_cc_pre_tool_use():
    _setup()
    _ev({"hook_event_name": "UserPromptSubmit", "cwd": "/tmp"})
    ts = _read().timestamp
    _ev({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/tmp"})
    s = _read()
    assert s.status == WORKING
    assert s.timestamp == ts


def test_cc_post_tool_use_no_error():
    """Claude Code PostToolUse has no tool_response.success field — should not set error."""
    _setup()
    _ev({"hook_event_name": "SessionStart", "cwd": "/tmp"})
    _ev({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_result": "file created"})
    assert not has_error_flag("42", status_dir=_test_dir)


def test_cc_stop_idle():
    """Claude Code Stop has no assistant_response — defaults to IDLE."""
    _setup()
    _ev({"hook_event_name": "SessionStart", "cwd": "/tmp"})
    _ev({"hook_event_name": "Stop", "cwd": "/tmp", "stop_reason": "end_turn"})
    assert _read().status == IDLE
    assert is_unseen("42", status_dir=_test_dir)


def test_cc_stop_with_error_flag():
    """Claude Code Stop with a prior error flag → ERROR status."""
    _setup()
    _ev({"hook_event_name": "SessionStart", "cwd": "/tmp"})
    _ev({"hook_event_name": "postToolUse", "tool_response": {"success": False}})
    _ev({"hook_event_name": "Stop", "cwd": "/tmp", "stop_reason": "end_turn"})
    assert _read().status == ERROR
    assert not has_error_flag("42", status_dir=_test_dir)


def test_cc_hookEventName_field():
    """Claude Code may use hookEventName (camelCase field) instead of hook_event_name."""
    _setup()
    _ev({"hookEventName": "SessionStart", "cwd": "/tmp"})
    s = _read()
    assert s is not None
    assert s.status == IDLE


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
