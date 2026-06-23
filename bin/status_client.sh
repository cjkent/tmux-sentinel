#!/bin/bash
# Status bar client for tmux-agents daemon.
# Queries the daemon for status output; lazy-starts it if not running.
#
# Usage in tmux.conf:
#   set -g status-right '#(~/.tmux-agents/bin/status_client.sh "#{pane_id}") %H:%M'

SOCK="$HOME/.tmux-agents/daemon.sock"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PANE_ID="${1#%}"

if [ -z "$PANE_ID" ]; then
    exit 0
fi

RESULT=$(echo "status $PANE_ID" | nc -U "$SOCK" 2>/dev/null)

if [ $? -ne 0 ] || [ -z "$RESULT" ] && [ ! -S "$SOCK" ]; then
    PYTHONPATH="$REPO_DIR" python3 -m tmux_agents_daemon </dev/null >/dev/null 2>&1 &
    sleep 0.3
    RESULT=$(echo "status $PANE_ID" | nc -U "$SOCK" 2>/dev/null)
fi

printf '%s' "$RESULT"
