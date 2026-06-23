# Daemon Refactor Design

## Problem

The current architecture spawns a full Python process every 5 seconds for the status bar, plus subprocesses (ps, tmux, git). This drains battery on a laptop.

## Solution

A long-running Python daemon holds all state in memory. Hooks and the status bar communicate with it via a Unix domain socket at `~/.tmux-agents/daemon.sock`.

## Components

### Daemon (`tmux_agents_daemon/daemon.py`)

Single async process (asyncio) that:

- Listens on the Unix socket for hook events and status queries
- Maintains agent state in memory (replaces JSON status files)
- Runs the process tree walk + screen-scrape on an internal timer (~30s when idle, ~5s when anything is working)
- Reconstructs state from process tree on startup (handles missed events)
- Manages unseen/error flags in memory
- Clears unseen for a pane when it sees that pane in a status query (replaces the "clear on visit" file logic)

### Hook client (`tmux_agents_daemon/hook_client.py`)

Connects to the socket, writes the JSON event, closes. If connection fails, exits silently. The daemon reconstructs state on its own, so dropped events are tolerable.

### Status bar client (`bin/status_client.sh`)

```bash
#!/bin/bash
SOCK=~/.tmux-agents/daemon.sock
RESULT=$(echo "$1" | nc -U "$SOCK" 2>/dev/null)
if [ -z "$RESULT" ] && [ $? -ne 0 ]; then
    PYTHONPATH=/path/to/repo python3 -m tmux_agents.daemon &
    sleep 0.2
    RESULT=$(echo "$1" | nc -U "$SOCK" 2>/dev/null)
fi
printf '%s' "$RESULT"
```

tmux config:
```
set -g status-right '#(~/.tmux-agents/status_client.sh "#{pane_id}") %H:%M'
```

### Picker (`picker.py`)

Queries the daemon for current state instead of reading files and running ps. Falls back to direct detection if daemon is unavailable.

## Protocol

Simple line-based text over the Unix socket:

- **Hook event:** Client sends a JSON line (same payload as today). Daemon responds with `ok\n` and closes.
- **Status query:** Client sends `status <pane_id>\n`. Daemon responds with the tmux format string (possibly empty) and closes.

One connection per request (no persistent connections). The daemon handles multiple concurrent clients via asyncio.

## Lifecycle

- **Start:** Lazy-started by the status bar client on first poll (or manually via `python3 -m tmux_agents.daemon`).
- **Stop:** Exits when it detects no tmux server is running (checked periodically). Also exits on SIGTERM.
- **Crash recovery:** Status bar client gets no response, restarts the daemon on next poll. Up to one poll cycle of missing output (acceptable).

## Development approach

The daemon code lives in a separate package (`tmux_agents_daemon/`) alongside the existing `tmux_agents/`. The existing code continues to work unchanged — both can run side-by-side until the daemon is proven and the cutover happens.

- `tmux_agents_daemon/daemon.py` — the server
- `tmux_agents_daemon/hook_client.py` — lightweight hook sender
- `bin/status_client.sh` — status bar shell script

The daemon may reuse shared modules from `tmux_agents/` (process.py, tmux.py, formatting.py) via import.

## Migration (later)

Once the daemon is stable:

- Switch hooks in agent configs and Claude Code settings to use the daemon hook client
- Switch `status-right` to the new shell script
- Update picker to query the daemon
- Remove the old statusbar.py polling path
- Status files become optional (daemon reads them on first start for continuity, then manages everything in memory)
