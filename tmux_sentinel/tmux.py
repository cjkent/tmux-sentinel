"""
Tmux command wrappers for tmux-sentinel.

All functions shell out to the `tmux` binary and parse its output.
Returns empty/default values if tmux is not running or commands fail.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class PaneInfo:
    pane_id: str       # numeric, without % prefix
    pane_pid: str
    session: str
    window_index: str
    window_name: str
    pane_current_path: str
    pane_title: str = ""
    # Epoch seconds of the window's last activity. tmux tracks this for every
    # window, agent or not, which makes it the only recency signal that covers the
    # whole list — the daemon only has timestamps for panes running an agent.
    activity: int = 0


def _run_tmux(*args: str) -> str:
    """Run a tmux command and return stdout. Returns empty string on failure."""
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def list_panes() -> list[PaneInfo]:
    """List all panes across all sessions with metadata."""
    # pane_title stays last: it's free text (an agent's task summary) and may itself
    # contain "|", so it has to be the field the split stops at. Anything added later
    # goes before it.
    fmt = (
        "#{pane_id}|#{pane_pid}|#{session_name}|#{window_index}|#{window_name}"
        "|#{pane_current_path}|#{window_activity}|#{pane_title}"
    )
    output = _run_tmux("list-panes", "-a", "-F", fmt)
    panes = []
    for line in output.splitlines():
        parts = line.split("|", 7)
        if len(parts) == 8:
            panes.append(PaneInfo(
                pane_id=parts[0].lstrip("%"),
                pane_pid=parts[1],
                session=parts[2],
                window_index=parts[3],
                window_name=parts[4],
                pane_current_path=parts[5],
                activity=int(parts[6]) if parts[6].isdigit() else 0,
                pane_title=parts[7],
            ))
    return panes


def list_sessions() -> list[str]:
    """Return session names."""
    output = _run_tmux("list-sessions", "-F", "#{session_name}")
    return output.splitlines() if output else []


def focused_pane_id() -> str:
    """Return the pane ID (without %) of the currently focused pane."""
    output = _run_tmux("display-message", "-p", "#{pane_id}")
    return output.lstrip("%")


def current_session() -> str:
    return _run_tmux("display-message", "-p", "#{session_name}")


def current_window_index() -> str:
    return _run_tmux("display-message", "-p", "#{window_index}")


def current_session_window() -> tuple[str, str]:
    """Return (session_name, window_index) for the client, in one tmux call."""
    output = _run_tmux("display-message", "-p", "#{session_name}|#{window_index}")
    if "|" in output:
        session, _, window = output.partition("|")
        return session, window
    return output, ""


def current_session_window_pane() -> tuple[str, str, str]:
    """Return (session_name, window_index, pane_id) for the client, in one call.

    The pane id (without %) identifies the focused pane, which the picker needs to
    mark the current row precisely — a split window has several panes, and only one
    of them is focused.
    """
    output = _run_tmux(
        "display-message", "-p", "#{session_name}|#{window_index}|#{pane_id}"
    )
    parts = output.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2].lstrip("%")
    return output, "", ""


def switch_to_pane(pane_id: str) -> None:
    """Focus a specific pane, switching session and window as needed.

    Targets the pane by its id rather than "session:window": pane ids are globally
    unique and stable, so this survives window renumbering between the popup being
    drawn and the selection being made. Selecting the window alone would leave the
    focus on whichever pane of that window last had it — which is exactly the bug
    this replaces.
    """
    target = f"%{pane_id}"
    session = _run_tmux("display-message", "-p", "-t", target, "#{session_name}")
    if session:
        _run_tmux("switch-client", "-t", session)
    _run_tmux("select-window", "-t", target)
    _run_tmux("select-pane", "-t", target)


def kill_pane(pane_id: str) -> None:
    """Kill a single pane (tmux closes the window when its last pane goes)."""
    _run_tmux("kill-pane", "-t", f"%{pane_id}")


def pane_pids() -> dict[str, str]:
    """Return {pane_id: pane_pid} for all panes. Pane IDs have no % prefix."""
    return {p.pane_id: p.pane_pid for p in list_panes()}


def capture_pane_tail(pane_id: str, lines: int = 5) -> str:
    """Capture the last N non-empty lines of a pane's visible content."""
    output = _run_tmux("capture-pane", "-p", "-t", f"%{pane_id}")
    non_empty = [l for l in output.splitlines() if l.strip()]
    return "\n".join(non_empty[-lines:]) if non_empty else ""
