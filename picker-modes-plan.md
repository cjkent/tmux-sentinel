# Plan: flatten the picker into a sortable, multi-mode row model

## Goal
Replace session-header grouping with a **flat list** where session is a column on
every row. Expose three sort **modes**, switched live via fzf keybinds. Wider/taller
popup.

## Decisions locked
- **No headers anywhere.** Grouping becomes a *sort*, not a layout. Every row is a
  real fzf target (so `session waiting` narrows correctly).
- **Session is always a column** — preserves searchability.
- **Three modes:** `unseen` (default), `by-session`, `mru`.
- **Current window included in all three modes.** Its `►` is a passive "you are
  here" anchor.
- **Cursor is per-mode** (§5). MRU follows option C: current at top, cursor parked
  on row 2.

## 1. Row model refactor (`_build_rows`)
Flat list of **records** (display + sort fields + target), sorted by mode, then
flattened to columns. Column order — marker, session, name, rest:

| # | column | source | cap |
|---|--------|--------|-----|
| 0 | marker | `►` current / `●` unseen / `` | — |
| 1 | session | `p.session` | 20, truncate tail + `…` |
| 2 | `{idx}: {name}` | `_display_name` | name → 28 |
| 3 | agent icon | `_AGENT_ICONS` | — |
| 4 | status | `[WRK]` etc. | — |
| 5 | cwd | `_shorten_path` | — |
| 6 | branch | `(branch)` | — |
| 7 | elapsed | `elapsed()` | — |

- Marker becomes its **own** column (today it's fused into the name string); so
  `align_columns` pads it and `_colorize_line` still finds `►`/`●`.
- `_MAX_TITLE_LEN`: 40 → 28. New 20-char session truncation.
- Daemon/file/no-agent branches unchanged in logic — they populate a record instead
  of appending columns inline.

## 2. Sorting (mode-driven)
Each record carries: `unseen` (bool), `severity` (waiting=0, error=1, working=2,
idle=3, none=4), `activity` (`#{window_activity}`), `session_order_index`,
`window_index`.

```
unseen     : (not unseen, severity, -activity)     # dots on top, then triage
by-session : (session_order_index, window_index)   # exactly today's grouped order
mru        : (-activity)                            # most-recently-used first
```

`by-session` reproduces the current ordering precisely — nothing lost by dropping
headers.

## 3. `window_activity` plumbing (`tmux.py`)
Add `#{window_activity}` to `list_panes`'s format string + an `activity: int` field
on `PaneInfo`. Only cross-the-board recency signal — works for agent *and* plain
panes (the daemon only has timestamps for agent panes). ~3 lines.

## 4. `--mode` arg + live switching (`main`, `_generate_list`)
- Thread `mode` through `_generate_list(mode)` → daemon/direct → `_build_rows`.
  Default `"unseen"`.
- `--list` grows `--mode=X`.
- Stateless keybinds (mode lives in each reload string):
  ```
  ctrl-u : reload(… --list --mode=unseen)     + change-prompt(unseen > )
  ctrl-g : reload(… --list --mode=by-session) + change-prompt(session > )
  ctrl-r : reload(… --list --mode=mru)        + change-prompt(recent > )
  ```
  `--header` lists the three keys. (ctrl-u/g/r aren't fzf defaults; easy to retune.)

## 5. Cursor position (per-mode, correct at launch *and* after reload)
Targets:
- `by-session` → **current window** (the `►` row)
- `unseen` → **first unseen** row (fall back to current window if none)
- `mru` → **row after the current window** (option C: current sits at top with `►`,
  cursor parks on the first switchable target below it)

Mechanism:
- **Launch:** add fzf `--sync` and bind `start:pos(N)` (not `load`). `--sync` holds
  the UI until the initial list is read, so `start` fires *after* the buffer is
  populated (fixing the old empty-buffer bug) and fires **only** at startup — never
  on reload.
- **Mode switch:** use fzf `transform` so each reload carries its own freshly-computed
  `pos(M)`, via a cheap `picker.py --cursor-row --mode=X` (one sub-ms daemon dump).
  Correct for the list being loaded.
- **Caveat to verify during build** (not asserted now): confirm `--sync` +
  `start:pos(N)` behaves as documented on fzf 0.74 via a 5-second scripted test.
  Fallback: compute position via `transform` on a `load` guard.

The MRU cursor is just "find the `►` row, +1" — same computation the other modes
already need. No list filtering special-cased into `_build_rows`.

## 6. Popup size
`-w 70% -h 50%` → `-w 85% -h 70%` in `setup.sh`'s `PICKER_CMD`. **Leaving
`~/.tmux.conf` alone** (hand-edited) — bump the two picker lines manually.

## 7. Tests
- `test_picker.py`: rewrite row-shape assertions (no header rows; marker=col 0,
  session=col 1). Add one test per mode asserting order (unseen-on-top, session
  grouping, mru by activity). Update `_colorize_line` sample lines.
- `test_formatting.py`: `align_columns` is generic — existing tests should pass; add
  a truncation check if truncation lands in a shared helper.

## Consequences folded in
- `ctrl-x` now only kills the window under the cursor; `--header` hint drops
  "/session." (Session-kill dropped — use tmux commands or tmux-palette for that
  rare case.)
- Session column width mitigated by the wider popup + 20-char cap + `align_columns`
  padding to actual content, not the cap.
