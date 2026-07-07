# Ideas & TODOs

## Ideas

- **Native OS notifications** — Fire a macOS notification (via `osascript`) when an agent hits a permission prompt or finishes, but only when the terminal doesn't have OS focus (so you're not pinged while already looking at it). Requires `focus-events on` in tmux or an `osascript` frontmost-app check.

## TODOs

_(none right now)_

## Done

- **TOML screen manifests** — Screen-scrape patterns now live in `manifests/*.toml` (one per agent), loaded by `tmux_agents_daemon/manifests.py`. Adding an agent or updating a pattern is now a config change. Inspired by herdr.
