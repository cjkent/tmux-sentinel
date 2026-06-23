"""
Periodic polling logic for the daemon.

Performs the same process tree walk and screen-scrape that statusbar.py does,
but updates the in-memory DaemonState instead of writing files.
"""
from __future__ import annotations

import re

from tmux_agents.status import IDLE, WORKING, WAITING
from tmux_agents.process import get_agent_panes, get_agent_types, _get_process_tree
from tmux_agents.tmux import list_panes, pane_pids, capture_pane_tail, focused_pane_id, _run_tmux
from tmux_agents.hook import _get_git_branch

from tmux_agents_daemon.state import DaemonState

_IDLE_PATTERN = re.compile(r"ask a question or describe a task")
_CC_WORKING_PATTERN = re.compile(r"esc to interrupt")
_APPROVAL_PATTERN = re.compile(
    r"requires approval"
    r"|\(f\) Approve all pending"
    r"|Yes, single permission"
    r"|Trust, always allow"
    r"|No \(Tab to edit\)"
    r"|Allow once"
    r"|Allow always"
    r"|Do you want to proceed\?"
    r"|shift\+tab to approve"
)
_CC_PROMPT_PATTERN = re.compile(r"shift\+tab to cycle|\? for shortcuts")
_CC_QUESTION_PATTERN = re.compile(r"Enter to select .* Esc to cancel|Esc to cancel .* Tab to amend")


def _detect_pane_state(pane_id: str) -> str | None:
    """Screen-scrape a pane to detect its actual state."""
    tail = capture_pane_tail(pane_id, lines=10)
    if not tail:
        return None
    if _APPROVAL_PATTERN.search(tail):
        return WAITING
    if _CC_QUESTION_PATTERN.search(tail):
        return WAITING
    if _IDLE_PATTERN.search(tail):
        return IDLE
    if _CC_PROMPT_PATTERN.search(tail) and not _CC_WORKING_PATTERN.search(tail):
        return IDLE
    return None


_WINDOW_NAME_MAP = {"claude": "claude", "kiro": "kiro"}
_RENAME_TRIGGERS = {"toolbox-exec"}


def _fix_window_names(all_pane_pids: dict[str, str], panes: list, process_tree) -> None:
    """Rename windows stuck on 'toolbox-exec' (or similar) to the agent name."""
    agent_types = get_agent_types(all_pane_pids, process_tree=process_tree)
    pane_windows = {p.pane_id: p.window_name for p in panes}
    for pane_id, agent_type in agent_types.items():
        window_name = pane_windows.get(pane_id, "")
        if window_name in _RENAME_TRIGGERS:
            new_name = _WINDOW_NAME_MAP.get(agent_type)
            if new_name:
                _run_tmux("set-option", "-t", f"%{pane_id}", "-w", "automatic-rename", "off")
                _run_tmux("rename-window", "-t", f"%{pane_id}", new_name)


def run_poll(state: DaemonState) -> None:
    """Run one poll cycle: process tree walk + screen-scrape."""
    import time

    all_pane_pids = pane_pids()
    process_tree = _get_process_tree()
    live_agent_panes = get_agent_panes(all_pane_pids, process_tree=process_tree)

    state.focused_pane = focused_pane_id()

    panes = list_panes()
    pane_paths = {p.pane_id: p.pane_current_path for p in panes}

    _fix_window_names(all_pane_pids, panes, process_tree)

    stale_panes = set(state.panes.keys()) - live_agent_panes
    for pane_id in stale_panes:
        state.remove_pane(pane_id)

    now = int(time.time())
    for pane_id in live_agent_panes:
        ps = state.get(pane_id)
        if ps is None:
            cwd = pane_paths.get(pane_id, "")
            branch = _get_git_branch(cwd) if cwd else ""
            ps = state.ensure(pane_id)
            ps.cwd = cwd
            ps.git_branch = branch
            ps.timestamp = now
            actual = _detect_pane_state(pane_id)
            ps.status = actual if actual else IDLE
            continue

        if ps.status in (WORKING, WAITING):
            actual = _detect_pane_state(pane_id)
            if actual == WAITING:
                ps.status = WAITING
            elif actual == IDLE:
                ps.status = IDLE
                ps.unseen = True
            elif actual is None and ps.status == WAITING:
                ps.status = WORKING

    if state.focused_pane:
        state.mark_seen(state.focused_pane)
