# Ideas & TODOs

## Ideas

- **Native OS notifications** — Fire a macOS notification (via `osascript`) when an agent hits a permission prompt or finishes, but only when the terminal doesn't have OS focus (so you're not pinged while already looking at it). Requires `focus-events on` in tmux or an `osascript` frontmost-app check.

## TODOs

- **Daemon cwd can be the shell cwd, not the agent cwd** — For panes the daemon discovers by polling (agent already running at daemon start, or after restart/wake), it stores the shell's `pane_current_path` rather than the agent's reported cwd, because `ps`/tmux is all it has. The picker works around this by preferring the status file's cwd when present, but a daemon-only pane (no status file) still shows the shallower shell path. Possible fix: have the daemon reconcile cwd from the status file on discovery, or accept it. See ARCHITECTURE.md "Known issue — the `cwd`… can be wrong". Cosmetic only (navigation unaffected).

## Done

- **TOML screen manifests** — Screen-scrape patterns now live in `manifests/*.toml` (one per agent), loaded by `tmux_agents_daemon/manifests.py`. Adding an agent or updating a pattern is now a config change. Inspired by herdr.
