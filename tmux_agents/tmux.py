"""
Tmux command wrappers for tmux-agents.

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
    fmt = "#{pane_id}|#{pane_pid}|#{session_name}|#{window_index}|#{window_name}|#{pane_current_path}|#{pane_title}"
    output = _run_tmux("list-panes", "-a", "-F", fmt)
    panes = []
    for line in output.splitlines():
        parts = line.split("|", 6)
        if len(parts) == 7:
            panes.append(PaneInfo(
                pane_id=parts[0].lstrip("%"),
                pane_pid=parts[1],
                session=parts[2],
                window_index=parts[3],
                window_name=parts[4],
                pane_current_path=parts[5],
                pane_title=parts[6],
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


def switch_to(session: str, window: str) -> None:
    """Switch the client to the given session and window."""
    _run_tmux("switch-client", "-t", session)
    _run_tmux("select-window", "-t", f"{session}:{window}")


def pane_pids() -> dict[str, str]:
    """Return {pane_id: pane_pid} for all panes. Pane IDs have no % prefix."""
    return {p.pane_id: p.pane_pid for p in list_panes()}


def capture_pane_tail(pane_id: str, lines: int = 5) -> str:
    """Capture the last N non-empty lines of a pane's visible content."""
    output = _run_tmux("capture-pane", "-p", "-t", f"%{pane_id}")
    non_empty = [l for l in output.splitlines() if l.strip()]
    return "\n".join(non_empty[-lines:]) if non_empty else ""
