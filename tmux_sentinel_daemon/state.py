"""
In-memory state for the daemon.

Tracks per-pane agent status, unseen/error flags, and the focused pane.
Replaces the on-disk status files as the authoritative source of truth
while the daemon is running.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from tmux_sentinel.status import IDLE, WORKING, WAITING, ERROR, AgentStatus, clear_unseen
from tmux_sentinel.hook import _get_git_branch


@dataclass
class PaneState:
    status: str = IDLE
    cwd: str = ""
    git_branch: str = ""
    timestamp: int = 0
    has_error: bool = False
    unseen: bool = False
    agent_type: str = "claude"


class DaemonState:
    def __init__(self) -> None:
        self.panes: dict[str, PaneState] = {}
        self.focused_pane: str = ""

    def get(self, pane_id: str) -> PaneState | None:
        return self.panes.get(pane_id)

    def ensure(self, pane_id: str) -> PaneState:
        if pane_id not in self.panes:
            self.panes[pane_id] = PaneState()
        return self.panes[pane_id]

    def any_working(self) -> bool:
        return any(p.status == WORKING for p in self.panes.values())

    def mark_seen(self, pane_id: str) -> None:
        ps = self.panes.get(pane_id)
        if ps:
            ps.unseen = False
        clear_unseen(pane_id)

    def remove_pane(self, pane_id: str) -> None:
        self.panes.pop(pane_id, None)

    def apply_hook_event(self, event: dict, pane_id: str) -> None:
        """Process a hook event and update in-memory state."""
        event_name = event.get("hook_event_name") or event.get("hookEventName") or "unknown"

        _EVENT_ALIASES = {
            "SessionStart": "agentSpawn",
            "UserPromptSubmit": "userPromptSubmit",
            "PreToolUse": "preToolUse",
            "PostToolUse": "postToolUse",
            "Stop": "stop",
        }
        event_name = _EVENT_ALIASES.get(event_name, event_name)

        cwd = event.get("cwd", "")
        now = int(time.time())
        ps = self.ensure(pane_id)

        if event_name == "agentSpawn":
            ps.status = IDLE
            ps.cwd = cwd
            ps.git_branch = _get_git_branch(cwd)
            ps.timestamp = now
            ps.has_error = False
            ps.unseen = False

        elif event_name == "userPromptSubmit":
            ps.status = WORKING
            ps.cwd = cwd
            ps.git_branch = _get_git_branch(cwd)
            ps.timestamp = now
            # A new turn is starting, so any prior "finished but unseen" result
            # is now moot — and you're clearly at the pane if you're prompting
            # it. Leaving unseen set would show WRK + a red dot together.
            ps.unseen = False

        elif event_name == "preToolUse":
            ps.status = WORKING
            if cwd:
                ps.cwd = cwd
            ps.unseen = False

        elif event_name == "postToolUse":
            tool_response = event.get("tool_response", {})
            if tool_response.get("success") is False:
                ps.has_error = True

        elif event_name == "stop":
            import re
            response = event.get("assistant_response", "")
            branch = _get_git_branch(cwd) if cwd else ps.git_branch

            if response:
                last_line = ""
                for line in response.splitlines():
                    if line.strip():
                        last_line = line.strip()
                if re.search(r"\?\s*$", last_line):
                    ps.status = WAITING
                elif ps.has_error:
                    ps.status = ERROR
                else:
                    ps.status = IDLE
            else:
                ps.status = ERROR if ps.has_error else IDLE

            ps.cwd = cwd or ps.cwd
            ps.git_branch = branch
            ps.timestamp = now
            ps.has_error = False
            ps.unseen = True
