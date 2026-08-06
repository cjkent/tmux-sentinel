#!/bin/bash
# Status bar client for tmux-sentinel daemon.
# Queries the daemon for status output; lazy-starts it if not running.
#
# Usage in tmux.conf:
#   set -g status-right '#(~/.tmux-sentinel/bin/status_client.sh "#{pane_id}") %H:%M'

SOCK="$HOME/.tmux-sentinel/daemon.sock"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PANE_ID="${1#%}"

if [ -z "$PANE_ID" ]; then
    exit 0
fi

# Talking to a Unix socket from a shell isn't portable: `nc -U` works with BSD netcat
# (macOS) and OpenBSD netcat, but GNU netcat-traditional — still the default on some
# Linux distros — has no -U flag at all. Without a fallback the status bar just shows
# nothing there, with no clue why.
#
# nc is preferred when it works because it's ~11ms against ~54ms for starting a Python
# interpreter, and this runs on every status-bar refresh.
HAVE_NC_U=0
if command -v nc >/dev/null 2>&1 && nc -h 2>&1 | grep -q -- '-U'; then
    HAVE_NC_U=1
fi

query() {
    if [ "$HAVE_NC_U" -eq 1 ]; then
        printf 'status %s\n' "$PANE_ID" | nc -U "$SOCK" 2>/dev/null
    else
        PYTHONPATH="$REPO_DIR" python3 -S -c '
import socket, sys
sock_path, pane_id = sys.argv[1], sys.argv[2]
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect(sock_path)
    s.sendall(f"status {pane_id}\n".encode())
    chunks = []
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    s.close()
    sys.stdout.write(b"".join(chunks).decode())
except OSError:
    pass
' "$SOCK" "$PANE_ID" 2>/dev/null
    fi
}

RESULT=$(query)

if [ -z "$RESULT" ] && [ ! -S "$SOCK" ]; then
    PYTHONPATH="$REPO_DIR" python3 -S -m tmux_sentinel_daemon </dev/null >/dev/null 2>&1 &
    sleep 0.3
    RESULT=$(query)
fi

printf '%s' "$RESULT"
