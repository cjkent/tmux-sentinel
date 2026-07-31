# Ideas & TODOs

## Planned changes (written up in detail elsewhere)

- **Picker multi-mode redesign** → [PICKER-MODES-PLAN.md](PICKER-MODES-PLAN.md)
  Drop session header rows, make the session name a column on every row, and sort
  the flat list by one of three modes: `unseen` (default triage view), `session`
  (today's grouped order), `mru` (most-recently-used first). Primary interaction is
  three per-mode tmux launch keybinds; in-popup switching is a nice-to-have. Also
  widens the popup and adds a per-mode cursor resting position. Fully specced and
  settled — no open questions. Groundwork already landed in `ef89d1d` (pane-precise
  targets, single `►` marker), which the plan notes so it isn't re-derived.

- **Staleness guard for stranded "working" state** → [STALE-WORKING-GUARD-PLAN.md](STALE-WORKING-GUARD-PLAN.md)
  Backstop for panes stranded on `working` when a turn ends without a `Stop` hook
  (interrupt into a footer no manifest rule covers). **Includes a warning worth
  reading before building:** the naive version can flip a genuinely-working agent to
  idle, because `ps.timestamp` is prompt-time (turn length, not idle time) and the
  on-screen marker can be absent while working (e.g. scrolled-back pane). The plan's
  prerequisite is a separate `last_activity` field updated on every hook event; the
  guard is unsafe without it. Note: `poll.py` now has `_has_working_marker()`
  (added in `bb0d863`), so the guard should reuse that rather than define its own —
  and its stricter-pattern reasoning applies there too.

## Ideas

- **Native OS notifications** — Fire a macOS notification (via `osascript`) when an agent hits a permission prompt or finishes, but only when the terminal doesn't have OS focus (so you're not pinged while already looking at it). Requires `focus-events on` in tmux or an `osascript` frontmost-app check.

- **Move picker row-building into the daemon** — After `-S` and the tmux/git optimizations, the popup's remaining ~110ms is mostly Python interpreter startup + imports (~70ms) rather than computation (~5ms Python work once the process is up). Moving row-building/formatting into the daemon (a new `render <pane_id>` command returning the finished fzf input) would let the popup-side script shrink to a thin connect-and-pipe client — but *something* still has to start a process to run fzf and pipe the daemon's response into it. To truly avoid paying interpreter startup, that thin client would need to be shell (`nc` + `fzf`, same pattern as `bin/status_client.sh`) rather than Python.
  Costs: (1) doesn't replace existing code, it *adds* a third path — you'd still keep the direct-fallback Python path for when the daemon is down, so `picker.py`'s current logic doesn't go away; (2) couples the daemon to fzf's display format (`\x1f`-separated columns, ANSI coloring) — a UI-shaping concern currently isolated to `picker.py`/`formatting.py`, now living in the always-running process; (3) a daemon bug in rendering could now affect more than just the picker.
  Not worth doing unless popup latency becomes a real complaint again — we already went from ~290ms to ~110ms this session, and this buys further ms at real architectural cost. Revisit if it does.

- **OpenCode support** — Process detection is trivial (~10 min: add a pattern to `process.py` and an icon to `_AGENT_ICONS`). The real unknown is whether OpenCode exposes lifecycle hooks: if it does, ~1-2hr to wire an event mapping like the Kiro/Claude aliases in `hook.py`; if not, it's scrape-only via a new `manifests/opencode.toml`, ~half a day and less reliable (no `Stop` event means no bell, no unseen flag, and status inferred entirely from the screen). Deferred — settle the hooks question first.

## TODOs

- **Daemon cwd can be the shell cwd, not the agent cwd** — For panes the daemon discovers by polling (agent already running at daemon start, or after restart/wake), it stores the shell's `pane_current_path` rather than the agent's reported cwd, because `ps`/tmux is all it has. The picker works around this by preferring the status file's cwd when present, but a daemon-only pane (no status file) still shows the shallower shell path. Possible fix: have the daemon reconcile cwd from the status file on discovery, or accept it. See ARCHITECTURE.md "Known issue — the `cwd`… can be wrong". Cosmetic only (navigation unaffected).

- **Make the picker's display caps user-configurable** — The column caps are module constants in `picker.py`: `_MAX_SESSION_LEN` (20), `_MAX_TITLE_LEN` (28), `_MAX_CWD_LEN` (50), and `_CWD_HEAD_SEGMENTS` (2, how many leading path segments survive middle-elision). Sensible defaults for one setup aren't sensible for all — someone on a narrow terminal wants tighter caps, someone on an ultrawide wants none, and the right `_CWD_HEAD_SEGMENTS` depends on how deeply nested your repos are (Brazil workspaces want 2; a flat `~/src/*` layout wants 1). Needs a config mechanism, of which the project currently has none — options are a TOML file next to `manifests/` (reusing the existing hand-rolled parser), tmux user options read via `show-option -gqv @sentinel-max-cwd`, or plain env vars. tmux options are the most tmux-native and need no new parsing, but are clumsy for many values; a config file is cleaner but is a new subsystem. Decide the mechanism before adding the first setting, since it'll set the pattern.

- **Colour unseen/current window names in the picker** — Colour the unseen rows' window name bold red and the current row's green, so they're findable at a glance when many windows are listed (the leading markers are small). The wrinkle that stalled this: injecting ANSI codes into the name *before* `align_columns` inflates the measured column width (`_display_width` counts every char), breaking alignment — so the colouring has to happen after alignment, in `_colorize_line`, which only sees the joined line. Worth folding into the picker-modes work, since that rewrites the same row/render path.

## Done

- **Promote idle panes to working via screen-scrape** (`bb0d863`) — Nothing could move a pane from `idle` to `working` except a hook, and the poll only scraped panes it already thought were working/waiting. A turn starting without a hook (resumed session, `/compact` continuation, dropped hook) showed IDL until its first `PreToolUse`. The poll now promotes on positive evidence of a live turn, and the idle poll interval dropped 30s → 10s.

- **Pane-precise picker selection** (`ef89d1d`) — Targets were `session:window`, which couldn't distinguish the panes of a split window: selecting either row left focus wherever it was. Targets are now globally-unique pane ids, `switch_to_pane()` selects window *then* pane, the `►` marker keys off the focused pane id, and `ctrl-x` closes the selected pane.

- **Detect Claude manual-mode footer as idle** (`e590ebb`) — A pane interrupted while in manual mode classified as neither idle nor waiting (its footer reads `⏸ manual mode on`, not `shift+tab to cycle`), so it stayed stuck on `working`.

- **Read `.git/HEAD` directly instead of shelling out to `git`** (`1288467`) — `git branch --show-current` cost ~24ms per pane and dominated picker latency. Now a sub-ms file read, walking up to the repo root and following the `gitdir:` pointer for worktrees/submodules. Picker generate went ~90ms → ~37ms cold; end-to-end ~130-150ms → ~88ms.

- **TOML screen manifests** — Screen-scrape patterns now live in `manifests/*.toml` (one per agent), loaded by `tmux_sentinel_daemon/manifests.py`. Adding an agent or updating a pattern is now a config change. Inspired by herdr.
