"""
Format the tmux status bar output from daemon state.

Produces colored segments showing unseen/waiting/working counts for
other windows (the focused window is excluded — you can already see it).
"""
from __future__ import annotations

from tmux_sentinel.status import WORKING, WAITING

from tmux_sentinel_daemon.state import DaemonState


def format_status_output(state: DaemonState, requesting_pane: str) -> str:
    """Build the tmux format string for the status bar.

    Counts agents in other panes (excluding requesting_pane which is the
    focused pane) and returns colored segments.
    """
    unseen_count = 0
    working_count = 0
    permission_count = 0

    for pane_id, ps in state.panes.items():
        if pane_id == requesting_pane:
            continue
        if ps.status == WORKING:
            working_count += 1
        elif ps.status == WAITING:
            permission_count += 1
        elif ps.unseen:
            unseen_count += 1

    parts = []
    if unseen_count > 0:
        parts.append(f"#[bg=red,fg=white,bold] ● {unseen_count} unseen ")
    if permission_count > 0:
        parts.append(f"#[bg=magenta,fg=white,bold] ⚠ {permission_count} waiting ")
    if working_count > 0:
        parts.append(f"#[bg=blue,fg=white] ⚙ {working_count} working ")
    if parts:
        return "".join(parts) + "#[bg=default,fg=default,none]"
    return ""
