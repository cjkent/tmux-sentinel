# Ideas & TODOs

## Planned changes (written up in detail elsewhere)

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

- **Portability: open items for other users** → [PORTABILITY.md](PORTABILITY.md)
  Audit of what's specific to this machine/setup. The two blockers are fixed (setup.sh
  aborted for anyone without Kiro; the status bar required `nc -U`, absent on some
  Linux). Still open: agent detection is process-name based, scrape patterns assume
  Claude Code's current English UI, emoji/glyph assumptions, and setup overwrites
  `status-right` wholesale.

## Ideas

- **Package as a tmux plugin (TPM)** — Roughly a day. The blocker isn't packaging, it's that `setup.sh` is interactive (10 `read` prompts plus an fzf multi-select) and edits other tools' config files, neither of which TPM can do: it runs a root-level `*.tmux` file non-interactively on every tmux start.

  What it needs:
  - **Split setup in two.** A non-interactive, idempotent `sentinel.tmux` for bindings, options, hooks and the status bar; `setup.sh` keeps the part that must stay opt-in — injecting hooks into `~/.kiro/agents/*.json` and `~/.claude/settings.json`. A plugin silently rewriting another tool's config on every tmux start isn't acceptable.
  - **Config via tmux options** (`@sentinel-*`, read with `show-option -gqv`), which is the TPM convention. This one change *simplifies* things: the plugin file re-runs on reload, so it can rebuild the bindings from options — which finally makes popup geometry an ordinary setting and retires `bin/set-popup-size.sh`. Keep `config.toml` for the Python-side display caps, which are read per-invocation and shouldn't pay a `show-option` subprocess each.
  - **Stop clobbering `status-right`** (`setup.sh:302` overwrites it wholesale). Convention is a placeholder the user positions themselves, e.g. `set -g status-right '#{sentinel_status} %H:%M'`, substituted into the existing value. Already tracked in [PORTABILITY.md](PORTABILITY.md); being a plugin forces it.
  - **Hook installation needs two independent fixes** (see the entry below). Neither matters for a hand-managed `~/.tmux.conf`, where you control load order and can see which indices are free; both matter under TPM, where you control neither.

  - **Dependency check that fails loudly.** Python 3.11+, fzf 0.30+, tmux 3.2+. There's no stdout at tmux start, so `display-message` a one-line warning rather than breaking silently.

  **Worth deciding first whether it's worth it.** TPM buys `prefix+I` install and auto-update, but hook injection still needs a manual `./setup.sh`, so it's a two-step install either way. If the goal is just "easier to try", a better README or a one-line installer gets most of the value for an hour rather than a day.

- **What a plugin-only install gives you (no `setup.sh`)** — Verified: agent detection is entirely hookless. The `ps` tree walk finds every agent and the screen-scrape classifies them, so the picker is fully functional on its own.

  Working: the picker (all panes and sessions, agent icons, `IDL`/`WRK`/`WAI`, cwd, branch, pane-precise switching, preview, `ctrl-x`), all three sort modes including `mru` (its hooks are tmux-side), the status-bar summary, stale cleanup, the daemon, `config.toml`.

  Lost or degraded, all of it downstream of the hooks:
  - **Bell on turn end** — only `hook.py` emits `\a`; nothing else can know a turn ended.
  - **`[ERR]` status** — needs `postToolUse`'s failure flag; no other source exists.
  - **Elapsed time** — hooks timestamp the *prompt*, the poll stamps *discovery*, so `WRK` elapsed counts from when the daemon noticed rather than when you asked.
  - **`unseen` reliability** — the poll only infers it from a `WORKING`→`IDLE` transition it happens to observe, so a turn that starts and finishes between polls is missed: the red dot and the status-bar count under-report.
  - **Latency** — 5-10s poll granularity instead of immediate.
  - **Agent cwd** — falls back to the shell's `pane_current_path` (the shallow-path issue above).
  - **`WAI` from a trailing `?`** — Kiro-only, and the response text comes from the hook.

  So *switching* works fully; *notification* doesn't. Which means `sentinel.tmux` should `display-message` a one-line nudge on first run ("run ./setup.sh to enable notifications") rather than leaving people to conclude the unseen dot is broken.

- **Make the LRU hook installation robust (needed before shipping as a plugin)** — tmux hooks are array options, and the three ways to set one each fail differently. Verified:

  | Form | Idempotent | Preserves other tools' hooks |
  |---|---|---|
  | `set-hook -g name cmd` (unindexed) | yes | **no — resets the whole array** |
  | `set-hook -ga name cmd` (append) | **no — one copy per config reload** | yes |
  | `set-hook -g name[10] cmd` (indexed) | yes | yes, *unless* someone collides on the index |

  We use the indexed form, which is the best of the three, but it has two distinct weaknesses:

  1. **The index is a guess, not a reservation.** Two plugins both choosing `[10]` means the second silently overwrites the first — no error, no log. Fix: don't hardcode. Scan the existing array; if an entry already holds our command (match on our script path) reuse that index, else take the first free one. That's idempotent, collision-free and non-destructive at once. Prototyped and confirmed: with `[0]` and `[1]` already occupied, ours landed at `[2]`, and re-running kept `[2]`.

  2. **No index survives someone else's unindexed set.** An unindexed `set-hook` resets the array wholesale, so a plugin loading after us wipes our hook whatever index we chose. This already happened: our hooks were placed above the `source-file .../lru.conf` line in `~/.tmux.conf`, and tmux-switch — which sets `after-select-window` and `client-session-changed` unindexed — deleted them on the next load. Only `after-select-pane` survived, because tmux-switch doesn't set that one. Fix: re-assert the hooks after all plugins have loaded, e.g. from a `client-attached` hook, with care not to redo the work on every attach.

  Note the failure is silent in both cases: the recent-mode ordering just quietly stops updating, with nothing in any log. Worth a self-check that warns once via `display-message` if the hook has gone missing.

  Also worth knowing why `-a` isn't the easy way out: it appends unconditionally, and a tmux config is sourced repeatedly (server start, `prefix+r`-style reload, TPM's `prefix+I`/`prefix+U`). Each pass adds another copy and nothing removes the old ones, so the hook *runs* once per copy — measured: 3 appends gave 3 script invocations for a single window switch. These hooks fire on every navigation, so that's waste on the hottest path, and it degrades invisibly because the LRU result stays correct (`lru_bump` dedupes).

- **Native OS notifications** — Fire a macOS notification (via `osascript`) when an agent hits a permission prompt or finishes, but only when the terminal doesn't have OS focus (so you're not pinged while already looking at it). Requires `focus-events on` in tmux or an `osascript` frontmost-app check.

- **Move picker row-building into the daemon** — After `-S` and the tmux/git optimizations, the popup's remaining ~110ms is mostly Python interpreter startup + imports (~70ms) rather than computation (~5ms Python work once the process is up). Moving row-building/formatting into the daemon (a new `render <pane_id>` command returning the finished fzf input) would let the popup-side script shrink to a thin connect-and-pipe client — but *something* still has to start a process to run fzf and pipe the daemon's response into it. To truly avoid paying interpreter startup, that thin client would need to be shell (`nc` + `fzf`, same pattern as `bin/status_client.sh`) rather than Python.
  Costs: (1) doesn't replace existing code, it *adds* a third path — you'd still keep the direct-fallback Python path for when the daemon is down, so `picker.py`'s current logic doesn't go away; (2) couples the daemon to fzf's display format (`\x1f`-separated columns, ANSI coloring) — a UI-shaping concern currently isolated to `picker.py`/`formatting.py`, now living in the always-running process; (3) a daemon bug in rendering could now affect more than just the picker.
  Not worth doing unless popup latency becomes a real complaint again — we already went from ~290ms to ~110ms this session, and this buys further ms at real architectural cost. Revisit if it does.

- **OpenCode support** — Process detection is trivial (~10 min: add a pattern to `process.py` and an icon to `_AGENT_ICONS`). The real unknown is whether OpenCode exposes lifecycle hooks: if it does, ~1-2hr to wire an event mapping like the Kiro/Claude aliases in `hook.py`; if not, it's scrape-only via a new `manifests/opencode.toml`, ~half a day and less reliable (no `Stop` event means no bell, no unseen flag, and status inferred entirely from the screen). Deferred — settle the hooks question first.

## TODOs

- **Daemon cwd can be the shell cwd, not the agent cwd** — For panes the daemon discovers by polling (agent already running at daemon start, or after restart/wake), it stores the shell's `pane_current_path` rather than the agent's reported cwd, because `ps`/tmux is all it has. The picker works around this by preferring the status file's cwd when present, but a daemon-only pane (no status file) still shows the shallower shell path. Possible fix: have the daemon reconcile cwd from the status file on discovery, or accept it. See ARCHITECTURE.md "Known issue — the `cwd`… can be wrong". Cosmetic only (navigation unaffected).


- **Colour unseen/current window names in the picker** — Colour the unseen rows' window name bold red and the current row's green, so they're findable at a glance when many windows are listed (the leading markers are small). The wrinkle that stalled this: injecting ANSI codes into the name *before* `align_columns` inflates the measured column width (`_display_width` counts every char), breaking alignment — so the colouring has to happen after alignment, in `_colorize_line`, which only sees the joined line. Worth folding into the picker-modes work, since that rewrites the same row/render path.

## Done

- **Picker multi-mode redesign** (`ca1d799`, `cc83671`, `74408a7`, `c8023b5`, `09fe491`) — Delivered [PICKER-MODES-PLAN.md](PICKER-MODES-PLAN.md) in full: session headers dropped for a session column, and three sort modes (`unseen` default, `session`, `mru`) with per-mode cursor placement, `--mode=X` launch keys and in-popup Alt-u/s/r switching.

  Two places the plan was wrong, both found by testing:
  - It assumed the focused pane sorts to the top in `mru`, so the cursor should start on row 2. `window_activity` is last-*output* time, so a chattier agent routinely outranks the pane you're in; the cursor is keyed to row 1 instead, stepping down only when row 1 *is* the focused pane.
  - `window_activity` turned out to be the wrong signal for `mru` altogether — it promoted windows that had never been visited. Visits are now recorded to `~/.tmux-sentinel/lru` by tmux hooks (`bin/lru_bump.sh`), with output time only as a fallback for never-visited panes.

  Also fixed en route: `switch_to_pane` ran `switch-client` before `select-window`, so the intermediate window counted as a visit and one switch recorded two.

- **User config file** (`config.toml`) — Settings now live in `~/.tmux-sentinel/config.toml`, parsed with stdlib `tomllib` (project baseline moved to Python 3.11+). Covers the picker's column caps, `cwd_head_segments`, and the preview's width and length, plus the daemon's poll intervals. Missing/partial/malformed files fall back to defaults. `tomllib` is imported lazily only when a config file exists, since importing it unconditionally cost ~11ms on every picker launch. Popup geometry deliberately stays in the tmux binding — tmux fixes a popup's size before Python starts — and `bin/set-popup-size.sh` edits it, persisting to `~/.tmux.conf` and re-binding the live server.

- **Promote idle panes to working via screen-scrape** (`bb0d863`) — Nothing could move a pane from `idle` to `working` except a hook, and the poll only scraped panes it already thought were working/waiting. A turn starting without a hook (resumed session, `/compact` continuation, dropped hook) showed IDL until its first `PreToolUse`. The poll now promotes on positive evidence of a live turn, and the idle poll interval dropped 30s → 10s.

- **Pane-precise picker selection** (`ef89d1d`) — Targets were `session:window`, which couldn't distinguish the panes of a split window: selecting either row left focus wherever it was. Targets are now globally-unique pane ids, `switch_to_pane()` selects window *then* pane, the `►` marker keys off the focused pane id, and `ctrl-x` closes the selected pane.

- **Detect Claude manual-mode footer as idle** (`e590ebb`) — A pane interrupted while in manual mode classified as neither idle nor waiting (its footer reads `⏸ manual mode on`, not `shift+tab to cycle`), so it stayed stuck on `working`.

- **Read `.git/HEAD` directly instead of shelling out to `git`** (`1288467`) — `git branch --show-current` cost ~24ms per pane and dominated picker latency. Now a sub-ms file read, walking up to the repo root and following the `gitdir:` pointer for worktrees/submodules. Picker generate went ~90ms → ~37ms cold; end-to-end ~130-150ms → ~88ms.

- **TOML screen manifests** — Screen-scrape patterns now live in `manifests/*.toml` (one per agent), loaded by `tmux_sentinel_daemon/manifests.py`. Adding an agent or updating a pattern is now a config change. Inspired by herdr.
