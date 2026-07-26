"""
Lightweight hook client for the daemon.

Connects to the Unix socket, sends the JSON event with the pane ID attached,
and disconnects. If the daemon isn't running, exits silently — the daemon
reconstructs state from the process tree on its own.

Usage:
    python3 -S -m tmux_sentinel_daemon.hook_client
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

SOCK_PATH = Path.home() / ".tmux-sentinel" / "daemon.sock"


def send_event(event: dict, pane_id: str) -> bool:
    """Send a hook event to the daemon. Returns True on success."""
    event["_pane_id"] = pane_id
    payload = json.dumps(event) + "\n"

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(str(SOCK_PATH))
        sock.sendall(payload.encode())
        sock.recv(64)
        sock.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def main() -> None:
    tmux_pane = os.environ.get("TMUX_PANE", "")
    if not tmux_pane:
        return
    pane_id = tmux_pane.lstrip("%")

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    send_event(event, pane_id)


if __name__ == "__main__":
    main()
