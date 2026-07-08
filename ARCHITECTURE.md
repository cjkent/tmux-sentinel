# Architecture

## Overview

tmux-agents monitors AI agent sessions (Kiro CLI and Claude Code) running in tmux panes. It consists of two Python packages (`tmux_agents/` and `tmux_agents_daemon/`) plus a shell script. No pip dependencies — everything uses the Python standard library. The only external tools are `tmux`, `fzf`, `nc` (netcat), and `jq` (jq is only used by `setup.sh`, not by Python code).

## Components

### Daemon (`tmux_agents_daemon/`)

A long-running asyncio process that holds all agent state in memory and serves it over a Unix domain socket at `~/.tmux-agents/daemon.sock`.

```
tmux status-right → bin/status_client.sh → nc -U daemon.sock → daemon → response
agent hook fires  → hook_client.py       → nc -U daemon.sock → daemon → "ok"
```

**Modules:**

- `daemon.py` — asyncio server: socket listener, connection handler, poll loop scheduling, lifecycle (SIGTERM, tmux-gone detection)
- `state.py` — `DaemonState` class holding per-pane status in memory, processes hook events
- `poll.py` — periodic process tree walk + screen-scrape, updates in-memory state
- `status_format.py` — builds tmux format strings from daemon state
- `hook_client.py` — fire-and-forget socket client for sending hook events to the daemon

**Lifecycle:**
- Lazy-started by `bin/status_client.sh` on first tmux poll (or manually via `python3 -m tmux_agents_daemon`)
- Exits on SIGTERM or when no tmux server is running (checked periodically)
- PID file at `~/.tmux-agents/daemon.pid`
- If it crashes, the status client gets no response and restarts it on the next poll

**Protocol (one connection per request, line-based text):**
- Hook event: client sends a JSON line with `_pane_id` field → daemon responds `ok\n`
- Status query: client sends `status <pane_id>\n` → daemon responds with tmux format string (possibly empty)

### Status Bar Client (`bin/status_client.sh`)

Shell script called by tmux every `status-interval` seconds via `#()`. Sends `status <pane_id>` to the daemon socket using `nc -U`. If the daemon isn't running, starts it and retries.

```
set -g status-right "#(bin/status_client.sh '#{pane_id}') %H:%M"
```

### Hook Entry Points

Two parallel hooks fire on each agent lifecycle event:

1. `tmux_agents/hook.py` — writes status JSON to `~/.tmux-agents/status/<pane-id>.json` (used by the picker)
2. `tmux_agents_daemon/hook_client.py` — sends the event to the daemon socket (used by the status bar)

Both are configured in `~/.claude/settings.json` (Claude Code) and `~/.kiro/agents/*.json` (Kiro CLI).

Five events are handled:
- `agentSpawn` / `SessionStart` → `idle` status
- `userPromptSubmit` / `UserPromptSubmit` → `working` status with current timestamp
- `preToolUse` / `PreToolUse` → keep `working`, preserve the prompt's timestamp
- `postToolUse` / `PostToolUse` → set error flag if the tool failed
- `stop` / `Stop` → determine final status (idle/waiting/error), mark unseen, ring bell

**Known limitation:** Kiro CLI has no hook for cancellation or tool approval prompts. If the user cancels mid-turn or the agent is waiting for tool approval, the `stop` hook never fires and status stays as `working`. This is corrected by screen-scrape detection in the daemon's poll loop.

### Picker (`tmux_agents/picker.py`)

Called by tmux when the user presses `Ctrl+b a`. Runs inside a `display-popup` overlay.

```
Ctrl+b a → tmux display-popup → picker.py → fzf → tmux switch-client/select-window
```

**Agent detection is based on status files, not process detection.** Only panes where the hook has fired (creating a status file) are shown as agents. This prevents false positives from processes that run kiro-cli non-interactively.

For agents with `working` status, the picker screen-scrapes the pane to detect approval prompts or stale idle state.

The display and the selection target are separated by a unit separator character (U+001F). fzf's `--with-nth=1` shows only the display part; the target (`session:window`) is extracted after selection.

## Shared Modules (`tmux_agents/`)

### status.py

All status file I/O. Reads and writes JSON files, manages `.error` and `.unseen` flag files, and handles stale file cleanup.

Status files are written atomically: write to a `.tmp` file, then rename. This prevents partial reads.

### process.py

Determines which tmux panes have an **interactive** AI agent running. Takes a single `ps -eo pid,ppid,args` snapshot, builds a parent→children map, and BFS from each pane's shell PID. Matches:
- `kiro-cli` + `chat` (excludes `kiro-cli acp` and other non-interactive uses)
- `/claude` (excludes `otelcol` sidecar)

The process tree is passed as a parameter for testability — tests provide a fake tree dict instead of calling `ps`.

### tmux.py

Thin wrappers around `tmux` CLI commands. Each function runs `subprocess.run(["tmux", ...])` and parses the output. Returns empty/default values if tmux isn't running.

Key functions:
- `list_panes()` → all panes with metadata (ID, PID, session, window, current path)
- `focused_pane_id()` → which pane the user is looking at
- `switch_to(session, window)` → switch client and select window
- `pane_pids()` → `{pane_id: pane_pid}` mapping for process detection
- `capture_pane_tail(pane_id, lines)` → last N non-empty lines of visible content

### formatting.py

Pure functions for display formatting:
- `elapsed(timestamp)` → human-readable duration ("5s", "3m", "1h30m")
- `status_label(status)` → text label ("[IDL]", "[WRK]", etc.)
- `colorize_status(label, status)` → wrap in ANSI color codes
- `align_columns(rows)` → pad columns to equal width across rows

## Screen-Scrape Detection

Hooks don't cover cancellation or tool approval prompts. The daemon's poll loop and the picker use `tmux capture-pane` to read the last few lines of a pane's visible content:

| Pattern | Detected state | Action |
|---|---|---|
| `requires approval`, `Allow once`, `shift+tab to approve`, etc. | waiting | Daemon/picker corrects status to waiting |
| `ask a question or describe a task` | idle | Kiro CLI idle prompt detected |
| `shift+tab to cycle` or `? for shortcuts` (without `esc to interrupt`) | idle | Claude Code idle footer detected |

This runs only for panes whose status is `working` — it's a correction mechanism, not the primary state source.

## Data Flow

```
Agent runs in tmux pane
  │
  ├─ Agent fires hook events
  │   ├─ hook.py writes status JSON + flag files (for picker)
  │   └─ hook_client.py sends event to daemon socket (for status bar)
  │
  ├─ Daemon poll loop (5s active / 30s idle):
  │   ├─ Process tree walk: discover/remove agents
  │   ├─ Screen-scrape "working" panes to detect approval/idle
  │   └─ Clear .unseen for focused pane
  │
  ├─ Every status-interval seconds:
  │   └─ bin/status_client.sh queries daemon
  │       └─ Daemon responds with tmux format string
  │
  └─ User presses Ctrl+b a:
      └─ picker.py
          ├─ Reads status files
          ├─ Screen-scrapes "working" agents
          └─ fzf selection → switch window
```

## Color Scheme

| Context | Status | Color |
|---|---|---|
| Picker label | idle | Green |
| Picker label | working | Blue |
| Picker label | waiting | Purple |
| Picker label | error | Red |
| Picker unseen marker | ● | Red |
| Status bar | unseen | Red background |
| Status bar | waiting | Magenta background |
| Status bar | working | Blue background |

## Testing

Tests use stdlib only (no pytest). Each test file has a `__main__` block that runs all `test_*` functions and prints results.

```bash
for t in tests/test_*.py; do PYTHONPATH=. python3 "$t"; done
```

Tests for `status.py`, `hook.py`, `picker.py` use temp directories to isolate status file I/O. Tests for `process.py` use fake process tree dicts. Tests for the daemon use real Unix sockets in temp directories. Tests for `formatting.py` are pure unit tests.
