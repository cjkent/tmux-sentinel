# tmux-agents

A tmux-native tool for monitoring and switching between multiple AI agent sessions. It tracks agent status via lifecycle hooks (Kiro CLI and Claude Code) and surfaces that information through a window picker, status bar, and bell notifications — all within tmux.

## What It Does

When you run multiple AI agents (Kiro CLI or Claude Code) in separate tmux windows, tmux-agents gives you:

- **Status tracking** — know which agents are working, idle, waiting for input, or errored, without switching to each window
- **Window picker** (`Ctrl+b a`) — an fzf popup listing all windows across all sessions with agent status, working directory, git branch, and elapsed time
- **Status bar summary** — a persistent indicator in `status-right` showing how many agents need attention
- **Bell notifications** — window tabs highlight red when an agent finishes or needs input

## How It Works

### Hook Script

`tmux_agents/hook.py` handles lifecycle events from both Kiro CLI and Claude Code. It receives a JSON payload on stdin and dispatches based on event name:

| Kiro CLI Event | Claude Code Event | Action |
|---|---|---|
| `agentSpawn` | `SessionStart` | Write status `idle`, record cwd and git branch |
| `userPromptSubmit` | `UserPromptSubmit` | Write status `working`, set timestamp |
| `preToolUse` | `PreToolUse` | Keep `working` status, preserve the prompt timestamp |
| `postToolUse` | `PostToolUse` | If the tool failed, set an error flag for the turn |
| `stop` | `Stop` | Determine final status, ring bell, mark as unseen |

The final status on `stop` is determined by:
1. Last non-empty line of the response ends with `?` → **waiting** (Kiro CLI only; Claude Code doesn't provide response text)
2. Any tool failed during the turn → **error**
3. Otherwise → **idle**

### Status Files

Each agent pane gets a JSON file at `~/.tmux-agents/status/<pane-id>.json`:

```json
{
  "status": "working",
  "cwd": "/Users/you/dev/project",
  "git_branch": "main",
  "timestamp": 1744634421
}
```

One file per pane, no shared state, no concurrency issues. Written atomically via temp file + rename.

Additional flag files per pane:
- `.error` — set when a tool fails, cleared on stop
- `.unseen` — set on stop, cleared when the user visits the window

### Stale File Cleanup

Status files are cleaned up on every read (by the picker and status bar). A pane's files are removed if:
- The pane no longer exists in tmux, or
- The pane exists but no agent process is running in its process tree

Process detection uses a single `ps` snapshot + BFS tree walk from each pane's shell PID. It matches `kiro-cli chat` (excluding non-interactive `kiro-cli acp`) and `/claude` (excluding the otelcol sidecar).

### Window Picker

`tmux_agents/picker.py` is an fzf popup bound to `Ctrl+b a`. It:

1. Cleans up stale status files
2. Lists all windows across all tmux sessions
3. Reads status files for agent panes, falls back to tmux's `pane_current_path` for non-agent windows
4. Aligns columns using Python string formatting (no external `column` command)
5. Colorizes status labels with ANSI codes: green=idle, yellow=working, purple=waiting, red=error
6. Groups windows under session headers
7. Marks the current window with `►`
8. Shows a red `●` dot next to windows with unseen status changes
9. On selection, switches to that session and window

Elapsed time is shown only for working agents — it reflects time since the user's last prompt, useful for spotting stuck agents.

### Status Bar

`bin/status_client.sh` runs via `#()` in tmux's `status-right`, querying the daemon each poll:

- `● N unseen` (red background) — agents with completed turns you haven't looked at yet
- `⚠ N waiting` (magenta background) — agents waiting for you (approval or a question)
- `⚙ N working` (blue background) — agents currently processing

Counts reflect *other* windows only — the focused window is excluded, since you can already see it. When you focus a pane, its unseen flag is cleared.

### Bell Notifications

The hook rings the terminal bell (`\a`) when an agent's turn ends. Combined with tmux's bell monitoring (`monitor-bell on`, `bell-action other`), this highlights the window tab in red until you visit it.

## Setup

Prerequisites: Python 3.8+, `jq`, `fzf`, `tmux`.

```bash
./setup.sh
```

The setup script:

1. Checks that dependencies are installed
2. Creates `~/.tmux-agents/status/`
3. Presents an interactive checkbox picker of your Kiro agent configs (`~/.kiro/agents/*.json`)
4. Backs up the selected configs and injects hook entries for all 5 lifecycle events
5. Removes any old bash hooks if present
6. Offers to inject hooks into Claude Code settings (`~/.claude/settings.json`)
7. Configures tmux: bell monitoring, status bar, `Ctrl+b a` keybinding

To remove hooks from agent configs:

```bash
./setup.sh --remove-hooks
```

Note: tmux options set by setup don't persist across tmux restarts. Add them to `~/.tmux.conf` to make them permanent.

## File Structure

```
tmux-agents/
├── tmux_agents/               # Python package (active)
│   ├── __init__.py
│   ├── status.py              # Status file I/O, flags, cleanup
│   ├── process.py             # Agent process detection via ps tree walk
│   ├── tmux.py                # Tmux command wrappers
│   ├── formatting.py          # Column alignment, ANSI colors, elapsed time
│   ├── hook.py                # Hook entry point (Kiro CLI + Claude Code)
│   ├── statusbar.py           # Status bar polling entry point
│   └── picker.py              # fzf window picker entry point
├── setup.sh                   # Interactive installer (bash)
├── tests/
│   ├── test_*.py              # Python tests (46)
│   └── test-*.sh              # Bash tests (56, for legacy scripts)
├── hooks/                     # Old bash hook (preserved as fallback)
│   └── notify.sh
└── bin/                       # Old bash scripts (preserved as fallback)
    ├── picker.sh
    └── status-bar.sh
```

## Dependencies

- **Python 3.8+** — ships with macOS, available on Linux. Stdlib only, no pip packages.
- **fzf** — fuzzy finder, used for the window picker popup.
- **jq** — used by `setup.sh` for agent config manipulation.
- **tmux** — obviously.
