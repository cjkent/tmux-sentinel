"""
Periodic polling logic for the daemon.

Performs the same process tree walk and screen-scrape that statusbar.py does,
but updates the in-memory DaemonState instead of writing files.
"""
from __future__ import annotations

from tmux_sentinel.status import IDLE, WORKING, WAITING
from tmux_sentinel.process import get_agent_panes, get_agent_types, _get_process_tree
from tmux_sentinel.tmux import list_panes, pane_pids, capture_pane_tail, focused_pane_id, _run_tmux
from tmux_sentinel.hook import _get_git_branch

from tmux_sentinel_daemon.state import DaemonState
from tmux_sentinel_daemon.manifests import load_all_manifests, classify

_MANIFESTS = load_all_manifests()


def _detect_pane_state(pane_id: str, agent_type: str = "claude") -> str | None:
    """Screen-scrape a pane to detect its actual state."""
    tail = capture_pane_tail(pane_id, lines=10)
    if not tail:
        return None
    rules = _MANIFESTS.get(agent_type)
    if not rules:
        return None
    return classify(tail, rules)


_WINDOW_NAME_MAP = {"claude": "claude", "kiro": "kiro"}
_RENAME_TRIGGERS = {"toolbox-exec"}


def _fix_window_names(agent_types: dict[str, str], panes: list) -> None:
    """Rename windows stuck on 'toolbox-exec' (or similar) to the agent name."""
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

    agent_types = get_agent_types(all_pane_pids, process_tree=process_tree)
    _fix_window_names(agent_types, panes)

    stale_panes = set(state.panes.keys()) - live_agent_panes
    for pane_id in stale_panes:
        state.remove_pane(pane_id)

    now = int(time.time())
    for pane_id in live_agent_panes:
        agent_type = agent_types.get(pane_id, "claude")
        ps = state.get(pane_id)
        if ps is None:
            cwd = pane_paths.get(pane_id, "")
            branch = _get_git_branch(cwd) if cwd else ""
            ps = state.ensure(pane_id)
            ps.cwd = cwd
            ps.git_branch = branch
            ps.timestamp = now
            ps.agent_type = agent_type
            actual = _detect_pane_state(pane_id, agent_type)
            ps.status = actual if actual else IDLE
            continue

        ps.agent_type = agent_type

        if ps.status in (WORKING, WAITING):
            actual = _detect_pane_state(pane_id, agent_type)
            if actual == WAITING:
                ps.status = WAITING
            elif actual == IDLE:
                ps.status = IDLE
                ps.unseen = True
            elif actual is None and ps.status == WAITING:
                ps.status = WORKING
                # Back to working means no completed-and-unseen result stands.
                ps.unseen = False

    if state.focused_pane:
        state.mark_seen(state.focused_pane)
