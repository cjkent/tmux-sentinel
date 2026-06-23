"""
Hook entry point for tmux-agents.

Reads a JSON event payload from stdin and updates the pane's status file.
Supports both Kiro CLI (camelCase events) and Claude Code (PascalCase events).

Kiro CLI events: agentSpawn, userPromptSubmit, preToolUse, postToolUse, stop
Claude Code events: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop

Usage (in agent config or Claude Code settings):
    python3 -m tmux_agents.hook

Exit silently if not running inside a tmux pane ($TMUX_PANE unset).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from tmux_agents.status import (
    IDLE, WORKING, WAITING, ERROR,
    write_status, read_status, set_error_flag, clear_error_flag,
    has_error_flag, set_unseen, ensure_status_dir,
)


def _get_git_branch(cwd: str) -> str:
    """Detect the current git branch for a directory."""
    if not cwd or not os.path.isdir(cwd):
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


_EVENT_ALIASES = {
    "SessionStart": "agentSpawn",
    "UserPromptSubmit": "userPromptSubmit",
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "Stop": "stop",
}


def handle_event(event: dict, pane_id: str, status_dir: Path = None) -> None:
    """Dispatch a hook event to the appropriate handler.

    Handles both Kiro CLI (camelCase) and Claude Code (PascalCase) event names.
    """
    from tmux_agents.status import STATUS_DIR as DEFAULT_DIR
    sd = status_dir or DEFAULT_DIR

    event_name = event.get("hook_event_name") or event.get("hookEventName") or "unknown"
    event_name = _EVENT_ALIASES.get(event_name, event_name)
    cwd = event.get("cwd", "")
    now = int(time.time())

    if event_name == "agentSpawn":
        # Agent session started — mark as idle
        branch = _get_git_branch(cwd)
        write_status(pane_id, IDLE, cwd, branch, now, status_dir=sd)

    elif event_name == "userPromptSubmit":
        # User sent a prompt — mark as working. The timestamp is set here and
        # preserved through preToolUse events, so elapsed time in the picker
        # shows time since the user's prompt (useful for spotting stuck agents).
        branch = _get_git_branch(cwd)
        write_status(pane_id, WORKING, cwd, branch, now, status_dir=sd)

    elif event_name == "preToolUse":
        # A tool is about to run — keep "working" status but preserve the
        # timestamp from userPromptSubmit (don't reset it).
        existing = read_status(pane_id, status_dir=sd)
        if existing:
            write_status(
                pane_id,
                WORKING,
                cwd or existing.cwd,
                existing.git_branch,
                existing.timestamp,
                status_dir=sd,
            )

    elif event_name == "postToolUse":
        # Kiro CLI: tool_response.success is a JSON boolean.
        # Claude Code: no success boolean (tool_result is a string).
        tool_response = event.get("tool_response", {})
        if tool_response.get("success") is False:
            set_error_flag(pane_id, status_dir=sd)

    elif event_name == "stop":
        # Agent turn complete — determine final status:
        #   1. Response ends with ? → waiting (agent asking a question)
        #   2. Any tool failed during the turn → error
        #   3. Otherwise → idle (turn completed normally)
        # Kiro CLI provides assistant_response; Claude Code does not.
        response = event.get("assistant_response", "")
        branch = _get_git_branch(cwd)

        if response:
            last_line = ""
            for line in response.splitlines():
                if line.strip():
                    last_line = line.strip()
            if re.search(r"\?\s*$", last_line):
                write_status(pane_id, WAITING, cwd, branch, now, status_dir=sd)
            elif has_error_flag(pane_id, status_dir=sd):
                write_status(pane_id, ERROR, cwd, branch, now, status_dir=sd)
            else:
                write_status(pane_id, IDLE, cwd, branch, now, status_dir=sd)
        else:
            if has_error_flag(pane_id, status_dir=sd):
                write_status(pane_id, ERROR, cwd, branch, now, status_dir=sd)
            else:
                write_status(pane_id, IDLE, cwd, branch, now, status_dir=sd)

        clear_error_flag(pane_id, status_dir=sd)
        set_unseen(pane_id, status_dir=sd)
        sys.stdout.write("\a")
        sys.stdout.flush()


def main() -> None:
    # Exit silently if not in a tmux pane
    tmux_pane = os.environ.get("TMUX_PANE", "")
    if not tmux_pane:
        return
    pane_id = tmux_pane.lstrip("%")

    ensure_status_dir()

    # Read JSON event from stdin
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        event = {}

    handle_event(event, pane_id)


if __name__ == "__main__":
    main()
