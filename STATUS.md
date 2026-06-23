# tmux-agents Build Status

## P0 Deliverables

| # | Deliverable | Status | Notes |
|---|---|---|---|
| 1 | Hook script (`hooks/notify.sh`) | ✅ Done | 13 tests |
| 2 | Status files (JSON per pane) | ✅ Done | Part of #1 |
| 3 | Window picker (`bin/picker.sh`) | ✅ Done | 12 tests |
| 4 | Bell notifications | ✅ Done | Added to hook stop handler |
| 5 | Status bar alert (`bin/status-bar.sh`) | ✅ Done | 11 tests |
| 6 | Stale file cleanup | ✅ Done | Built into picker + status-bar. 6 tests |
| 7 | Setup script (`setup.sh`) | ✅ Done | 16 tests |

**Total: 58 tests, all passing**

## Progress Log

_(newest first)_

- **All P0 deliverables complete** — 58 tests across 5 test suites, all passing.
- **#7 Setup script** — Interactive TUI checkbox picker for agent selection. Injects hooks, configures tmux (bell, status bar, keybinding). Supports --remove-hooks. 16 tests.
- **#5+#6 Status bar + stale cleanup** — Polling script for status-right shows cross-session summary with color-coded backgrounds. Stale cleanup built into both picker and status-bar (validate-on-read). 17 tests.
- **#3+#4 Picker + bell** — fzf popup with session grouping, column alignment, Ctrl+b a. Bell rings on stop. 12 tests.
- **#1+#2 Hook script + status files** — `hooks/notify.sh` handles all 5 events, writes JSON to `~/.tmux-agents/status/<pane-id>.json`. Fixed jq boolean false handling. 13 tests.
