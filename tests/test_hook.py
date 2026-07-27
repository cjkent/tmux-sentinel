"""Tests for tmux_sentinel.hook module."""
import os
import tempfile
from pathlib import Path

from tmux_sentinel.status import (
    IDLE, WORKING, WAITING, ERROR,
    read_status, has_error_flag, is_unseen,
)
from tmux_sentinel.hook import handle_event, _get_git_branch

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


# --- _get_git_branch: reads .git/HEAD directly (no `git` subprocess) ---

def _make_repo(root: Path, head_content: str, as_worktree_pointer: bool = False):
    """Create a fake repo layout under root. Returns the repo dir path."""
    if as_worktree_pointer:
        # .git is a FILE pointing at the real git dir (worktree/submodule case)
        real_git = root / "realgit"
        real_git.mkdir(parents=True)
        (real_git / "HEAD").write_text(head_content)
        (root / ".git").write_text(f"gitdir: {real_git}\n")
    else:
        git_dir = root / ".git"
        git_dir.mkdir(parents=True)
        (git_dir / "HEAD").write_text(head_content)
    return root


def test_git_branch_on_branch():
    root = Path(tempfile.mkdtemp())
    _make_repo(root, "ref: refs/heads/main\n")
    assert _get_git_branch(str(root)) == "main"


def test_git_branch_from_subdirectory():
    # Walks up to the repo root, like the git CLI.
    root = Path(tempfile.mkdtemp())
    _make_repo(root, "ref: refs/heads/feature/x\n")
    sub = root / "src" / "pkg"
    sub.mkdir(parents=True)
    assert _get_git_branch(str(sub)) == "feature/x"


def test_git_branch_detached_head():
    # Detached HEAD stores a bare SHA — no branch name.
    root = Path(tempfile.mkdtemp())
    _make_repo(root, "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0\n")
    assert _get_git_branch(str(root)) == ""


def test_git_branch_worktree_pointer():
    # .git is a file "gitdir: <path>" rather than a directory.
    root = Path(tempfile.mkdtemp())
    _make_repo(root, "ref: refs/heads/release\n", as_worktree_pointer=True)
    assert _get_git_branch(str(root)) == "release"


def test_git_branch_not_a_repo():
    root = Path(tempfile.mkdtemp())  # no .git anywhere up to fs root
    assert _get_git_branch(str(root)) == ""


def test_git_branch_nonexistent_dir():
    assert _get_git_branch("/no/such/dir/exists/here") == ""
    assert _get_git_branch("") == ""


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
