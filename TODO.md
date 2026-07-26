# Ideas & TODOs

## Ideas

- **Native OS notifications** — Fire a macOS notification (via `osascript`) when an agent hits a permission prompt or finishes, but only when the terminal doesn't have OS focus (so you're not pinged while already looking at it). Requires `focus-events on` in tmux or an `osascript` frontmost-app check.

- **Read `.git/HEAD` directly instead of shelling out to `git`** — After the picker's tmux calls were collapsed, `git branch --show-current` (~24ms/pane) is the dominant remaining subprocess cost in the fast path. The current branch can be read from `.git/HEAD` as a sub-ms file read (`ref: refs/heads/<branch>`). Edge cases to handle: walk up parent dirs to find `.git`; detached HEAD (raw SHA → empty/short SHA); and `.git`-as-a-file (`gitdir: …`) for worktrees/submodules — this last one matters since Brazil workspaces and worktrees are common. ~20-30 lines of pure file I/O in `_get_git_branch`. Only worth it if picker latency still bothers at scale; ~56ms is already fairly snappy.

- **Move picker row-building into the daemon** — After `-S` and the tmux/git optimizations, the popup's remaining ~110ms is mostly Python interpreter startup + imports (~70ms) rather than computation (~5ms Python work once the process is up). Moving row-building/formatting into the daemon (a new `render <pane_id>` command returning the finished fzf input) would let the popup-side script shrink to a thin connect-and-pipe client — but *something* still has to start a process to run fzf and pipe the daemon's response into it. To truly avoid paying interpreter startup, that thin client would need to be shell (`nc` + `fzf`, same pattern as `bin/status_client.sh`) rather than Python.
  Costs: (1) doesn't replace existing code, it *adds* a third path — you'd still keep the direct-fallback Python path for when the daemon is down, so `picker.py`'s current logic doesn't go away; (2) couples the daemon to fzf's display format (`\x1f`-separated columns, ANSI coloring) — a UI-shaping concern currently isolated to `picker.py`/`formatting.py`, now living in the always-running process; (3) a daemon bug in rendering could now affect more than just the picker.
  Not worth doing unless popup latency becomes a real complaint again — we already went from ~290ms to ~110ms this session, and this buys further ms at real architectural cost. Revisit if it does.

## TODOs

- **Daemon cwd can be the shell cwd, not the agent cwd** — For panes the daemon discovers by polling (agent already running at daemon start, or after restart/wake), it stores the shell's `pane_current_path` rather than the agent's reported cwd, because `ps`/tmux is all it has. The picker works around this by preferring the status file's cwd when present, but a daemon-only pane (no status file) still shows the shallower shell path. Possible fix: have the daemon reconcile cwd from the status file on discovery, or accept it. See ARCHITECTURE.md "Known issue — the `cwd`… can be wrong". Cosmetic only (navigation unaffected).

## Done

- **TOML screen manifests** — Screen-scrape patterns now live in `manifests/*.toml` (one per agent), loaded by `tmux_sentinel_daemon/manifests.py`. Adding an agent or updating a pattern is now a config change. Inspired by herdr.
