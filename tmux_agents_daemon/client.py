"""
Query client for the daemon.

Used by the picker to fetch the full per-pane state snapshot over the socket,
avoiding a redundant process-tree walk and per-pane capture-pane calls.
Returns None if the daemon is unavailable, so callers can fall back to direct
detection.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

SOCK_PATH = Path.home() / ".tmux-agents" / "daemon.sock"


def dump_state(timeout: float = 1.0) -> dict | None:
    """Fetch the daemon's full per-pane state snapshot.

    Returns a dict keyed by pane_id with status/cwd/git_branch/timestamp/unseen,
    or None if the daemon is unreachable or returns something unparseable.
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(SOCK_PATH))
        sock.sendall(b"dump\n")
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return None

    try:
        return json.loads(b"".join(chunks).decode())
    except (json.JSONDecodeError, ValueError):
        return None
