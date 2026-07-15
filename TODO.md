# Ideas & TODOs

## Ideas

- **Native OS notifications** — Fire a macOS notification (via `osascript`) when an agent hits a permission prompt or finishes, but only when the terminal doesn't have OS focus (so you're not pinged while already looking at it). Requires `focus-events on` in tmux or an `osascript` frontmost-app check.

- **Read `.git/HEAD` directly instead of shelling out to `git`** — After the picker's tmux calls were collapsed, `git branch --show-current` (~24ms/pane) is the dominant remaining subprocess cost in the fast path. The current branch can be read from `.git/HEAD` as a sub-ms file read (`ref: refs/heads/<branch>`). Edge cases to handle: walk up parent dirs to find `.git`; detached HEAD (raw SHA → empty/short SHA); and `.git`-as-a-file (`gitdir: …`) for worktrees/submodules — this last one matters since Brazil workspaces and worktrees are common. ~20-30 lines of pure file I/O in `_get_git_branch`. Only worth it if picker latency still bothers at scale; ~56ms is already fairly snappy.

## TODOs

- **Daemon cwd can be the shell cwd, not the agent cwd** — For panes the daemon discovers by polling (agent already running at daemon start, or after restart/wake), it stores the shell's `pane_current_path` rather than the agent's reported cwd, because `ps`/tmux is all it has. The picker works around this by preferring the status file's cwd when present, but a daemon-only pane (no status file) still shows the shallower shell path. Possible fix: have the daemon reconcile cwd from the status file on discovery, or accept it. See ARCHITECTURE.md "Known issue — the `cwd`… can be wrong". Cosmetic only (navigation unaffected).

## Done

- **TOML screen manifests** — Screen-scrape patterns now live in `manifests/*.toml` (one per agent), loaded by `tmux_agents_daemon/manifests.py`. Adding an agent or updating a pattern is now a config change. Inspired by herdr.
