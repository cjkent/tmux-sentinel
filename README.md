# tmux-sentinel

A tmux-native tool for monitoring and switching between multiple AI agent sessions. It tracks agent status via lifecycle hooks (Kiro CLI and Claude Code) and surfaces that information through a window picker, status bar, and bell notifications.

Entirely vibe-coded using Claude Code. I haven't looked at the code and neither should you. Use at your own risk. Works on my machine ;)

## What It Does

When you run multiple AI agents (Kiro CLI or Claude Code) in separate tmux windows, tmux-sentinel gives you:

- **Status tracking** — know which agents are working, idle, waiting for input, or errored, without switching to each window
- **Window picker** — an fzf popup listing all windows across all sessions with agent status, working directory, git branch, and elapsed time, in one of three sort orders
- **Status bar summary** — a persistent indicator in `status-right` showing how many agents need attention
- **Bell notifications** — window tabs highlight red when an agent finishes or needs input

## How It Works

### Hook Script

`tmux_sentinel/hook.py` handles lifecycle events from both Kiro CLI and Claude Code. It receives a JSON payload on stdin and dispatches based on event name:

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

Each agent pane gets a JSON file at `~/.tmux-sentinel/status/<pane-id>.json`:

```json
{
  "status": "working",
  "cwd": "/Users/you/dev/project",
  "git_branch": "main",
  "timestamp": 1744634421
}
```

One file per pane, written atomically via temp file + rename.

Additional flag files per pane:
- `.error` — set when a tool fails, cleared on stop
- `.unseen` — set on stop, cleared when the user visits the window

Plus one global UI-state file, `~/.tmux-sentinel/preview` — present when the picker's preview pane should open showing.

### Daemon

`tmux_sentinel_daemon/` runs a small background process holding all agent state in
memory, listening on a Unix socket at `~/.tmux-sentinel/daemon.sock`. It's the
authoritative source while running; the status files remain as a fallback.

It starts itself — `bin/status_client.sh` spawns one if none is running, so the status
bar's next refresh brings it back. There's nothing to install or supervise.

Two things use it:
- **Hooks** forward their events to it (`hook_client.py`), so state updates need no file I/O.
- **The picker** fetches a whole snapshot in one round trip (`client.py`), avoiding a
  process-tree walk and a `capture-pane` per pane. This is the difference between a
  popup that opens in ~90ms and one that takes ~300ms.

It also polls, every 5s while an agent is working and every 10s otherwise, to catch
what hooks can't tell it: agents that exited, and turns that ended without a `Stop`
event (an interrupt, say). That correction is a screen-scrape driven by the patterns in
`manifests/`.

### Stale File Cleanup

Status files are cleaned up on every read (by the picker and status bar). A pane's files are removed if:
- The pane no longer exists in tmux, or
- The pane exists but no agent process is running in its process tree

Process detection uses a single `ps` snapshot + BFS tree walk from each pane's shell PID. It matches `kiro-cli chat` (excluding non-interactive `kiro-cli acp`) and `/claude` (excluding the otelcol sidecar).

### Window Picker

`tmux_sentinel/picker.py` is an fzf popup bound to a configurable key during setup. It:

1. Fetches a state snapshot from the daemon in one round trip, falling back to direct file + `ps` inspection if the daemon is down
2. Lists all panes across all tmux sessions
3. Cleans up status files for panes the daemon no longer tracks
4. Falls back to tmux's `pane_current_path` for non-agent panes
5. Aligns columns using Python string formatting (no external `column` command)
6. Colorizes status labels with ANSI codes: green=idle, blue=working, purple=waiting, red=error
7. Shows the session name as a column on every row rather than as a group header, so every row is a real fzf target — a query like `myproj waiting` narrows correctly, where a header row could only match itself. Row order depends on the sort mode (below)
8. Marks the focused pane with `►` in a leading marker column
9. Shows a red `●` dot in that same marker column for panes with unseen status changes (the focused pane is always seen, so the two never clash — keeping "where am I" and "what needs attention" vertically aligned)
10. On selection, focuses that exact pane (split panes are individually selectable, since targets are pane ids rather than `session:window`)

### Sort modes

The list can be ordered three ways. Each has its own launch key, and the prompt names
the active mode:

| Mode | Order | Cursor starts on |
|---|---|---|
| `unseen` | unseen first, then by what most wants attention (waiting, error, working, idle), then recency | first unseen row |
| `session` | session order, then window index — the original grouped order | the focused pane |
| `mru` | most recently *visited* first | top row, or the one below it if that's the pane you're in |

Each mode has its own key, set with `@sentinel-key-unseen`, `@sentinel-key-session` and
`@sentinel-key-mru` (see [tmux options](#tmux-options)). `unseen` is the default when the
picker is launched without `--mode`.

`mru` orders by when you last *visited* a pane. tmux has no last-visited timestamp —
`#{window_activity}` is last-*output* time, so an agent printing into a window you never
looked at would jump to the top. `sentinel.tmux` therefore installs three tmux hooks
(`after-select-window`, `after-select-pane`, `client-session-changed`) that record each
visit to `~/.tmux-sentinel/lru`, most recent first. Set `@sentinel-track-visits off` to
skip them.

Hooks are array options, and the index matters. An unindexed `set-hook` resets the whole
array for that event, silently deleting hooks another tool set — so we never use one.
Instead we reuse the slot already holding our command if there is one, and otherwise take
the first free index. That's idempotent across reloads, and it leaves other tools' hooks
alone (tmux-switch, for instance, sets two of these three). What no index can survive is
another tool's *unindexed* set on the same event, which is why the load-order note in
[Setup](#1-install-the-plugin) matters.

Panes with no recorded visit sort last, ordered among themselves by output time — so a
fresh install still gives a sensible list before any history accumulates.

Keys inside the picker:
- `enter` — focus the selected pane
- `alt-u` / `alt-s` / `alt-r` — switch to unseen / session / recent order. (Alt rather than Ctrl because `ctrl-u` and `ctrl-r` are fzf's own clear-query and toggle-sort.)
- `?` — toggle a preview of the highlighted pane, anchored to the bottom where the current output and any prompt are. Hidden by default, and the choice persists between popups (remembered in `~/.tmux-sentinel/preview`). Its width and line count are configurable.
- `ctrl-x` — close the highlighted pane (tmux closes the window with its last pane)

Elapsed time is shown only for working agents — it reflects time since the user's last prompt, useful for spotting stuck agents.

### Status Bar

`bin/status_client.sh` runs via `#()` in tmux's `status-right`, querying the daemon each poll:

- `● N unseen` (red background) — agents with completed turns you haven't looked at yet
- `⚠ N waiting` (magenta background) — agents waiting for you (approval or a question)
- `⚙ N working` (blue background) — agents currently processing

Counts reflect *other* windows only — the focused window is excluded, since you can already see it. When you focus a pane, its unseen flag is cleared.

### Bell Notifications

The hook rings the terminal bell (`\a`) when an agent's turn ends. Combined with tmux's bell monitoring (`monitor-bell on`, `bell-action other`), this highlights the window tab in red until you visit it.

## Configuration

Optional. Every setting has a default, so a partial file is fine and a missing one means defaults throughout. Settings cover the picker's column caps, the preview's width and length, and the daemon's poll intervals — see `config.toml.example` for the full annotated list. The picker reads the file on every open; changing a poll interval needs a daemon restart.

```bash
./bin/edit-config.sh
```

Opens `~/.tmux-sentinel/config.toml` in `$EDITOR`, creating it from the annotated example on first use, and reports any TOML syntax error on exit (an unparseable file is silently ignored in favour of defaults, so it's worth knowing).

The popup's **width and height are not in this file.** tmux fixes a popup's size when it opens the popup, before the picker process exists, so the geometry can't be read from inside Python. It's a tmux option instead, applied by `sentinel.tmux` when it builds the bindings:

```tmux
set -g @sentinel-popup-width  '85%'
set -g @sentinel-popup-height '70%'
```

Change it, reload your config, and the next popup is the new size.

The config editor can be bound to a key so it opens in a popup itself:

```tmux
bind -n M-, display-popup -w 80% -h 80% -E "/path/to/tmux-sentinel/bin/edit-config.sh"
```

## Setup

Prerequisites: Python 3.11+, fzf 0.30+, tmux 3.2+. (`nc` optional — see Dependencies.)
Both the plugin and `setup.sh` check these, since they otherwise fail at keypress with
cryptic errors rather than at install.

Setup is two steps, and they do different jobs:

| | What it does | Why separate |
|---|---|---|
| `sentinel.tmux` | keybinds, status bar, visit-tracking hooks | must be re-runnable on every tmux start |
| `./setup.sh` | agent lifecycle hooks, in *your agents'* config files | writes to other tools' configs, so it stays an explicit opt-in |

### 1. Install the plugin

With [TPM](https://github.com/tmux-plugins/tpm) or [tpack](https://github.com/joshmedeski/tpack) (a drop-in TPM replacement with a TUI) — the declaration is identical:

```tmux
set -g @plugin 'cjkent/tmux-sentinel'
```

Then `prefix + I` to install.

Without a plugin manager, clone it and source the entry point from `~/.tmux.conf`:

```tmux
run-shell /path/to/tmux-sentinel/sentinel.tmux
```

**Put it after your plugin manager's init line.** tmux hooks are array options, and an
unindexed `set-hook` resets the whole array for that event — so if another plugin loads
after us and sets one of the hooks we use, ours is silently deleted. Loading last avoids
that. (See the note under Sort modes about which hooks are involved.)

### 2. Install the agent hooks

```bash
./setup.sh
```

Kiro and Claude Code are both optional — setup skips whichever you don't have. It:

1. Checks dependencies and creates `~/.tmux-sentinel/status/`
2. Offers an fzf multi-select of your Kiro agent configs (`~/.kiro/agents/*.json`), everything pre-selected — Enter accepts all, Tab deselects, Esc skips
3. Backs up the selected configs and injects hooks for all 5 lifecycle events, replacing the old bash hook if an earlier version installed it
4. Offers to do the same for Claude Code (`~/.claude/settings.json`), creating the file if absent and leaving hooks of your own untouched
5. Offers to add the `sentinel.tmux` line to `~/.tmux.conf`, and to add the status-bar placeholder

To remove the agent hooks again:

```bash
./setup.sh --remove-hooks
```

**Without step 2 the picker still works** — agent detection doesn't need the hooks, since
the process tree walk finds agents and the screen-scrape classifies them. What you lose
is notification: no bell when a turn ends, no `[ERR]` status, unreliable unseen dots, and
elapsed time measured from when the daemon noticed rather than when you asked.

### tmux options

All optional; defaults shown. Set them before the `sentinel.tmux` line.

```tmux
set -g @sentinel-key-unseen    'C-Space'   # triage view; 'none' to disable
set -g @sentinel-key-session   'M-Space'   # grouped by session
set -g @sentinel-key-mru       'C-Tab'     # most recently visited
set -g @sentinel-key-table     'root'      # default table for keys that don't name one
set -g @sentinel-popup-width   '85%'
set -g @sentinel-popup-height  '70%'
set -g @sentinel-track-visits  'on'        # 'off' disables the visit-tracking hooks
set -g @sentinel-set-bell-options 'on'     # 'off' leaves your bell settings alone
set -g @sentinel-bell-style    'fg=red,bold'
set -g @sentinel-status-interval '5'
```

Use `none` rather than an empty string to disable a key: tmux reports an option set to
`''` identically to one that was never set, so an empty value just falls back to the
default.

**Key tables.** A key can name its own table with a `prefix:` or `root:` marker, and a
mode can take several keys separated by spaces:

```tmux
set -g @sentinel-key-unseen  'C-Space'         # default table (root)
set -g @sentinel-key-session 'prefix:e'        # prefix table: press the prefix, then e
set -g @sentinel-key-mru     'C-Tab prefix:u'  # both — a fast key and a fallback
```

The two tables differ in reliability, not only in speed. A root key reaches tmux only if
the terminal passes it through, and keys like `M-Space` or `C-Tab` are often claimed by
the terminal or the OS. A prefix key needs only the prefix itself, which is a plain
control byte, so tmux reads whatever follows. Giving a mode one of each means a terminal
that swallows your fast key does not leave the mode unreachable.

Display settings — column widths, preview size — live in `~/.tmux-sentinel/config.toml`
instead, because the picker reads them per-invocation and shouldn't pay a `show-option`
subprocess for each. See [Configuration](#configuration).

### Status bar

The plugin never overwrites `status-right`. Put a placeholder where you want the
segment and it's substituted on load:

```tmux
set -g status-right '#{sentinel_status} %H:%M'
```

With no placeholder, nothing is touched and no summary appears. `setup.sh` offers to
append one for you.

## File Structure

```
tmux-sentinel/
├── tmux_sentinel/             # Core package
│   ├── status.py              # Status file I/O, flags, cleanup
│   ├── process.py             # Agent process detection via ps tree walk
│   ├── tmux.py                # Tmux command wrappers
│   ├── formatting.py          # Column alignment, ANSI colors, elapsed time
│   ├── hook.py                # Hook entry point (Kiro CLI + Claude Code)
│   ├── config.py              # Optional user settings (config.toml)
│   ├── install.py             # Hook add/remove in agent JSON configs
│   └── picker.py              # fzf window picker entry point
├── tmux_sentinel_daemon/      # Long-running state daemon
│   ├── daemon.py              # Socket server + poll loop
│   ├── state.py               # In-memory per-pane state
│   ├── poll.py                # Process walk + screen-scrape correction
│   ├── manifests.py           # Loads the screen-scrape patterns
│   ├── client.py              # Picker's state-snapshot client
│   ├── hook_client.py         # Hook's event-forwarding client
│   └── status_format.py       # Status-bar string rendering
├── manifests/                 # Screen-scrape patterns, one file per agent
│   ├── claude.toml
│   └── kiro.toml
├── sentinel.tmux              # Plugin entry point (TPM/tpack); tmux-side config
├── setup.sh                   # Agent-hook installer (interactive)
├── config.toml.example        # Annotated default settings
├── bin/
│   ├── status_client.sh       # status-right client (lazy-starts the daemon)
│   ├── edit-config.sh         # Opens config.toml in $EDITOR
└── tests/
    ├── test_*.py              # Python tests (186)
    └── test-*.sh              # Bash tests (picker + setup)
```

## Dependencies

- **Python 3.11+** — stdlib only, no pip packages. (3.11 for `tomllib`, used to read the config file.)
- **fzf 0.30+** — fuzzy finder, used for the window picker popup and setup's multi-select.
- **nc** (optional) — the status bar uses it to query the daemon when available; falls back to a Python socket client otherwise.
- **tmux 3.2+** — obviously. 3.2 is where `display-popup` arrived, which the picker is built on.

## Further Reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — module-by-module design notes
- [DAEMON-DESIGN.md](DAEMON-DESIGN.md) — why the daemon exists and how it's structured
- [PORTABILITY.md](PORTABILITY.md) — what's specific to the author's setup, and what other users may hit
- [TODO.md](TODO.md) — ideas, known issues, and planned work
