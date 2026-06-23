"""
Status file management for tmux-agents.

Each agent pane has a JSON status file at ~/.tmux-agents/status/<pane-id>.json
containing: status, cwd, git_branch, timestamp.

Additional flag files:
  <pane-id>.error  — set when a tool fails, cleared on stop
  <pane-id>.unseen — set on stop, cleared when the user visits the window
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Valid status values
IDLE = "idle"
WORKING = "working"
WAITING = "waiting"
ERROR = "error"

STATUS_DIR = Path.home() / ".tmux-agents" / "status"


@dataclass
class AgentStatus:
    status: str
    cwd: str
    git_branch: str
    timestamp: int

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "cwd": self.cwd,
            "git_branch": self.git_branch,
            "timestamp": self.timestamp,
        }


def ensure_status_dir(status_dir: Path = STATUS_DIR) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)


def status_file(pane_id: str, status_dir: Path = STATUS_DIR) -> Path:
    return status_dir / f"{pane_id}.json"


def error_file(pane_id: str, status_dir: Path = STATUS_DIR) -> Path:
    return status_dir / f"{pane_id}.error"


def unseen_file(pane_id: str, status_dir: Path = STATUS_DIR) -> Path:
    return status_dir / f"{pane_id}.unseen"


def write_status(
    pane_id: str,
    status: str,
    cwd: str = "",
    git_branch: str = "",
    timestamp: int = 0,
    status_dir: Path = STATUS_DIR,
) -> None:
    """Write a JSON status file for a pane. Creates the directory if needed."""
    ensure_status_dir(status_dir)
    data = AgentStatus(status, cwd, git_branch, timestamp)
    path = status_file(pane_id, status_dir)
    # Write atomically via temp file to avoid partial reads by the status bar
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data.to_dict(), indent=2) + "\n")
    tmp.rename(path)


def read_status(pane_id: str, status_dir: Path = STATUS_DIR) -> Optional[AgentStatus]:
    """Read a pane's status file. Returns None if the file doesn't exist or is invalid."""
    path = status_file(pane_id, status_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return AgentStatus(
            status=data.get("status", ""),
            cwd=data.get("cwd", ""),
            git_branch=data.get("git_branch", ""),
            timestamp=data.get("timestamp", 0),
        )
    except (json.JSONDecodeError, KeyError):
        return None


def list_statuses(status_dir: Path = STATUS_DIR) -> list[tuple[str, AgentStatus]]:
    """Return (pane_id, AgentStatus) for all status files."""
    results = []
    if not status_dir.exists():
        return results
    for f in status_dir.glob("*.json"):
        pane_id = f.stem
        s = read_status(pane_id, status_dir)
        if s is not None:
            results.append((pane_id, s))
    return results


def set_error_flag(pane_id: str, status_dir: Path = STATUS_DIR) -> None:
    error_file(pane_id, status_dir).touch()


def clear_error_flag(pane_id: str, status_dir: Path = STATUS_DIR) -> None:
    error_file(pane_id, status_dir).unlink(missing_ok=True)


def has_error_flag(pane_id: str, status_dir: Path = STATUS_DIR) -> bool:
    return error_file(pane_id, status_dir).exists()


def set_unseen(pane_id: str, status_dir: Path = STATUS_DIR) -> None:
    unseen_file(pane_id, status_dir).touch()


def clear_unseen(pane_id: str, status_dir: Path = STATUS_DIR) -> None:
    unseen_file(pane_id, status_dir).unlink(missing_ok=True)


def is_unseen(pane_id: str, status_dir: Path = STATUS_DIR) -> bool:
    return unseen_file(pane_id, status_dir).exists()


def cleanup_stale(live_pane_ids: set[str], status_dir: Path = STATUS_DIR) -> None:
    """Remove status/error/unseen files for panes not in live_pane_ids."""
    if not status_dir.exists():
        return
    for f in status_dir.glob("*.json"):
        pane_id = f.stem
        if pane_id not in live_pane_ids:
            f.unlink(missing_ok=True)
            error_file(pane_id, status_dir).unlink(missing_ok=True)
            unseen_file(pane_id, status_dir).unlink(missing_ok=True)


def recreate_missing(
    live_pane_ids: set[str],
    pane_paths: dict[str, str],
    status_dir: Path = STATUS_DIR,
) -> None:
    """Create idle status files for kiro panes that are missing one.

    After laptop sleep/wake, cleanup may have removed status files while kiro
    was still running. This recreates them using the pane's current path from
    tmux, so the picker and status bar show correct info immediately.
    """
    import time
    from tmux_agents.hook import _get_git_branch

    now = int(time.time())
    for pane_id in live_pane_ids:
        if not status_file(pane_id, status_dir).exists():
            cwd = pane_paths.get(pane_id, "")
            branch = _get_git_branch(cwd) if cwd else ""
            write_status(pane_id, IDLE, cwd, branch, now, status_dir=status_dir)
