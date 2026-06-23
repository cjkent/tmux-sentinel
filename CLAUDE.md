# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

tmux-agents is a Python package that monitors multiple AI agent sessions (Kiro CLI and Claude Code) running in tmux. It tracks agent status via lifecycle hooks and surfaces information through a window picker, status bar, and bell notifications.

## Commands

### Run all tests
```bash
for t in tests/test_*.py; do PYTHONPATH=. python3 "$t"; done
```

### Run a single test file
```bash
PYTHONPATH=. python3 tests/test_hook.py
```

### Start the daemon manually
```bash
PYTHONPATH=. python3 -m tmux_agents_daemon
```

### Setup (installs hooks into Kiro agent configs and Claude Code settings, configures tmux)
```bash
./setup.sh
```

## Architecture

Two packages, stdlib only (no pip dependencies):

### `tmux_agents/` — shared modules and legacy entry points

- `hook.py` — called by Kiro CLI and Claude Code on lifecycle events, writes status JSON to `~/.tmux-agents/status/<pane-id>.json`
- `picker.py` — called on `Ctrl+b a`, shows fzf popup of all windows with agent status
- `status.py` — status file I/O, flag files (`.error`, `.unseen`), stale cleanup
- `process.py` — detects which panes have an interactive agent running (`kiro-cli chat` or `claude`) via ps tree walk
- `tmux.py` — thin wrappers around tmux CLI commands
- `formatting.py` — column alignment, ANSI colors, elapsed time (pure functions)

### `tmux_agents_daemon/` — long-running daemon (replaces the old statusbar.py polling)

- `daemon.py` — asyncio server listening on `~/.tmux-agents/daemon.sock`, handles hook events and status queries
- `state.py` — `DaemonState` class, holds per-pane agent status in memory
- `poll.py` — periodic process tree walk + screen-scrape, updates in-memory state
- `status_format.py` — builds tmux format strings from daemon state
- `hook_client.py` — fire-and-forget client that sends hook events to the daemon socket

### `bin/`

- `status_client.sh` — shell script for tmux `status-right`, queries daemon via `nc -U`, lazy-starts it if not running

**Key design decisions:**
- Status files are written atomically (temp file + rename)
- Agent detection in picker uses status files (not process detection) to avoid false positives from non-interactive uses
- The daemon only reports on other windows, never the focused pane
- Hook event normalization: Claude Code's PascalCase events (`SessionStart`, `Stop`) are mapped to Kiro's camelCase equivalents internally
- The daemon clears on-disk `.unseen` flags when a pane is queried, so the picker stays in sync

**Daemon polling and screen-scraping (`poll.py`):**

The daemon polls on a timer (5s when any agent is working, 30s when idle) and does:

| Purpose | Mechanism | Why needed |
|---------|-----------|------------|
| Clean up after exited agents | Process tree walk (`ps`) | Status becomes stale when an agent exits without a hook firing |
| Detect interrupted agents | Screen-scrape (`capture-pane`) | Claude Code's `Stop` hook doesn't fire on user interrupt; Kiro CLI lacks a cancellation hook entirely. Detects idle prompt to correct stale `working` status |
| Detect permission prompts | Screen-scrape (`capture-pane`) | Neither agent has a hook for "waiting for user to approve a tool" |
| Discover new agents | Process tree walk (`ps`) | After laptop sleep/wake or daemon restart, picks up already-running agents |

For Claude Code, idle detection relies on the footer text: "esc to interrupt" is present while working, absent when idle. Two footer variants are matched: `shift+tab to cycle` (acceptEdits mode) and `? for shortcuts` (default mode).

Claude Code's plan confirmation prompt ("Would you like to proceed?" with `shift+tab to approve with this feedback` footer) is detected via `_APPROVAL_PATTERN` since it's a "waiting for user" state.

**Daemon protocol (Unix socket, one connection per request):**
- Hook event: client sends JSON line with `_pane_id` field → daemon responds `ok\n`
- Status query: client sends `status <pane_id>\n` → daemon responds with tmux format string (possibly empty)

**Daemon lifecycle:**
- Lazy-started by `bin/status_client.sh` on first tmux poll, or manually
- Exits on SIGTERM or when no tmux server is running
- PID file at `~/.tmux-agents/daemon.pid`

**Hook configuration:**
- Kiro CLI: hooks are injected into `~/.kiro/agents/*.json`
- Claude Code: hooks are injected into `~/.claude/settings.json` (both old hook.py and daemon hook_client)

## Testing

Tests use stdlib only (no pytest). Each test file has a `__main__` block that runs `test_*` functions. Tests use temp directories for status file isolation and fake process tree dicts for process.py tests. Test pane IDs use high numbers (e.g. 99990) to avoid collisions with real tmux panes.
