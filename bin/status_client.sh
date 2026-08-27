#!/usr/bin/env bash
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

# Talking to a Unix socket from a shell isn't portable. Most nc builds do support it:
# openbsd-netcat (Debian/Ubuntu/Arch default) and nmap-ncat (Fedora/RHEL default) both
# have -U, as does BSD netcat on macOS. Only netcat-traditional lacks it entirely.
#
# nc is much preferred where available — ~11ms against ~54ms to start a Python
# interpreter — and this runs on every status-bar refresh, so it's worth detecting
# properly rather than assuming.
#
# Detection probes behaviour instead of parsing --help: help output formats differ
# (macOS lists flags one per line, OpenBSD and nmap print a compact cluster like
# "[-46bCDdFhklNnrStUuvZz]"), and a naive grep for "-U" misses the cluster form —
# which would send most Linux users down the slow path for no reason. Instead, run
# nc -U against a path that cannot exist: a build lacking the flag reports an invalid
# option, while one supporting it gets as far as failing to find the socket.
HAVE_NC_U=0
if command -v nc >/dev/null 2>&1; then
    nc_probe=$(nc -U /nonexistent/tmux-sentinel-probe </dev/null 2>&1)
    case "$nc_probe" in
        *"illegal option"*|*"invalid option"*|*"unrecognized option"*|*"usage:"*)
            HAVE_NC_U=0 ;;
        *)
            HAVE_NC_U=1 ;;
    esac
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
