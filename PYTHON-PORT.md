# Python Port Plan

## Why

Bash is getting brittle — process tree walking via ps+awk, column alignment via token replacement, duplicated cleanup logic, JSON via jq subshells. Python fixes all of these with stdlib.

## Structure

```
tmux-sentinel/
├── tmux_sentinel/
│   ├── __init__.py
│   ├── status.py        # Read/write status files, cleanup stale
│   ├── process.py       # Detect kiro-cli in pane process trees
│   ├── tmux.py          # Wrapper around tmux commands (list-panes, display-message, etc.)
│   ├── hook.py          # Entry point for the Kiro CLI hook (replaces notify.sh)
│   ├── picker.py        # Entry point for the fzf popup (replaces picker.sh)
│   ├── statusbar.py     # Entry point for status-right polling (replaces status-bar.sh)
│   └── formatting.py    # Column alignment, ANSI colors, elapsed time
├── setup.sh             # Keep as bash — it's interactive TUI + tmux/jq config
├── tests/
│   ├── test_status.py
│   ├── test_process.py
│   ├── test_hook.py
│   ├── test_picker.py
│   ├── test_statusbar.py
│   └── test_formatting.py
└── README.md
```

## Entry Points

Three shell-callable entry points, invoked the same way as today:

- `python3 -m tmux_sentinel.hook` — called by Kiro CLI hooks (reads JSON from stdin)
- `python3 -m tmux_sentinel.picker` — called by tmux display-popup
- `python3 -m tmux_sentinel.statusbar` — called by tmux status-right via `#()`

## Module Responsibilities

### status.py
- `read_status(pane_id) -> dict | None` — read a pane's JSON status file
- `write_status(pane_id, status, cwd, git_branch, timestamp)` — write JSON atomically
- `list_statuses() -> list[dict]` — read all status files
- `set_unseen(pane_id)` / `clear_unseen(pane_id)` / `is_unseen(pane_id)`
- `set_error_flag(pane_id)` / `clear_error_flag(pane_id)` / `has_error_flag(pane_id)`
- `cleanup_stale(live_kiro_panes: set[str])` — remove files for dead/non-kiro panes
- Constants: `STATUS_DIR`, status values

### process.py
- `get_kiro_panes() -> set[str]` — return pane IDs that have kiro-cli as a descendant
- Uses `psutil` if available, falls back to parsing `ps -eo pid,ppid,comm`
- Single `ps` call, build parent→children map, walk from each pane pid

### tmux.py
- `list_panes() -> list[dict]` — pane_id, pane_pid, session, window_index, window_name
- `list_sessions() -> list[str]`
- `focused_pane() -> str` — current pane ID
- `switch_to(session, window)` — switch client + select window
- `display_message(msg, duration_ms)` — for debugging
- All functions shell out to `tmux` and parse output

### hook.py (`__main__` entry point)
- Read JSON from stdin
- Dispatch on `hook_event_name`
- Same logic as current notify.sh but using status.py functions
- Detect git branch via `subprocess.run(["git", ...])`

### picker.py (`__main__` entry point)
- Call `cleanup_stale()`
- Build display lines with proper column alignment (no `column` command needed — use Python string formatting)
- Colorize with ANSI codes directly (no sed post-processing)
- Pipe to `fzf` via `subprocess.Popen`
- Parse selection, call `tmux.switch_to()`

### statusbar.py (`__main__` entry point)
- Clear unseen flag for focused pane
- Call `cleanup_stale()`
- Count working/unseen agents
- Output tmux-formatted string

### formatting.py
- `elapsed(timestamp) -> str` — "5s", "3m", "1h30m"
- `status_label(status) -> str` — "[IDL]", "[WRK]", etc.
- `colorize(text, color) -> str` — wrap in ANSI codes
- `align_columns(rows) -> list[str]` — pad columns to equal width

## What Stays as Bash

- `setup.sh` — interactive TUI, jq manipulation of agent configs, tmux option setting. This is inherently scripty and works fine as bash.

## Testing

- Use `pytest` with stdlib only (no external deps)
- Mock `subprocess.run` for tmux/git/ps calls
- Test status file I/O with `tmp_path` fixture
- Test formatting functions as pure unit tests
- Test hook dispatch with fake JSON payloads

## Dependencies

- Python 3.8+ (ships with macOS, available on Linux)
- No pip packages — stdlib only (`json`, `subprocess`, `os`, `time`, `pathlib`)
- `fzf` — still used as external process for the picker

## Migration Steps

1. Create `tmux_sentinel/` package with `status.py`, `process.py`, `tmux.py`, `formatting.py`
2. Port `hook.py` + tests — verify with real Kiro agent
3. Port `statusbar.py` + tests — verify status bar works
4. Port `picker.py` + tests — verify popup works
5. Update `setup.sh` to point hooks/status-right/keybinding at Python entry points
6. Remove old bash scripts
7. Run full test suite
