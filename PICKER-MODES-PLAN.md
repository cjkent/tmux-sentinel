# Plan: flatten the picker into a sortable, multi-mode row model

## Goal
Replace session-header grouping with a **flat list** where session is a column on
every row. Expose three sort **modes**, switched live via fzf keybinds. Wider/taller
popup.

## Decisions locked
- **No headers anywhere.** Grouping becomes a *sort*, not a layout. Every row is a
  real fzf target (so `session waiting` narrows correctly).
- **Session is always a column** — preserves searchability.
- **Three modes:** `unseen` (default), `session`, `mru`.
- **Primary interaction is three per-mode launch keybinds** (one tmux bind per
  mode; specific keys chosen at build time). In-popup mode switching is a
  nice-to-have, not the main path — which lets us drop the cursor-recompute
  machinery (see §5).
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
- **Already landed (`ef89d1d`), inherited by this work — do not re-derive:**
  - **Targets are pane ids** (no `%`), not `session:window` — split panes are
    individually selectable. Selection calls `switch_to_pane(pane_id)`; the row
    model carries `pane_id` as its target unchanged.
  - **`is_current` keys off the focused pane id** (`_build_rows(..., focused_pane=)`,
    fed by `current_session_window_pane()`), not session+window — so exactly one row
    carries `►` even in a split window. The per-mode cursor logic in §5 depends on
    that uniqueness (it locates the single `►` row).
  - Rows are already **pane-level**, not window-level: a split window contributes one
    row per pane. Sorting in §2 therefore orders *panes*; `window_index` is a sort
    key, not an identity.

## 2. Sorting (mode-driven)
Each record carries: `unseen` (bool), `severity` (waiting=0, error=1, working=2,
idle=3, none=4), `activity` (`#{window_activity}`), `session_order_index`,
`window_index`.

```
unseen  : (not unseen, severity, -activity)     # dots on top, then triage
session : (session_order_index, window_index)   # exactly today's grouped order
mru     : (-activity)                            # most-recently-used first
```

`session` reproduces the current ordering precisely — nothing lost by dropping
headers.

## 3. `window_activity` plumbing (`tmux.py`)
Add `#{window_activity}` to `list_panes`'s format string + an `activity: int` field
on `PaneInfo`. Only cross-the-board recency signal — works for agent *and* plain
panes (the daemon only has timestamps for agent panes). ~3 lines.

## 4. `--mode` arg + launch keybinds (`main`, `_generate_list`)
- Thread `mode` through `_generate_list(mode)` → daemon/direct → `_build_rows`.
  Default `"unseen"`.
- `main()` grows a launch `--mode=X` arg; `--list` grows `--mode=X` too.
- **Primary path — three tmux keybinds, one per mode** (keys TBD at build time):
  ```
  <key1> : picker.py --mode=unseen    (primary triage)
  <key2> : picker.py --mode=session
  <key3> : picker.py --mode=mru
  ```
  Each launch renders its mode with the correct cursor via the plain launch path
  (§5) — no reload involved.
- **Nice-to-have — in-popup switching** via stateless fzf keybinds (mode lives in
  each reload string). Cursor lands at top-of-list after an in-popup switch (no
  recompute — see §5); acceptable since this isn't the primary path:
  ```
  ctrl-u : reload(… --list --mode=unseen)  + change-prompt(unseen > )
  ctrl-g : reload(… --list --mode=session) + change-prompt(session > )
  ctrl-r : reload(… --list --mode=mru)     + change-prompt(recent > )
  ```
  `--header` lists the three keys. (ctrl-u/g/r aren't fzf defaults; easy to retune.)

## 5. Cursor position (per-mode, correct at launch)
Because the primary path is three per-mode *launches* (§4), the cursor only needs to
be correct **at launch** — the fiddly post-reload recompute is dropped. Targets:
- `session` → **current window** (the `►` row)
- `unseen` → **first unseen** row (fall back to current window if none)
- `mru` → **row after the current window** (option C: current sits at top with `►`,
  cursor parks on the first switchable target below it)

Mechanism:
- **Launch:** compute the target row in `main()` and bind `start:pos(N)` with fzf
  `--sync`. `--sync` holds the UI until the initial list is read, so `start` fires
  *after* the buffer is populated (fixing the old empty-buffer bug that forced
  `load` before) and fires only at startup.
- **In-popup switch (nice-to-have):** no recompute — cursor lands at top of the new
  list. The `transform`/`--cursor-row` machinery from the earlier draft is
  **dropped** (in-popup switching isn't the primary path, so top-of-list is fine).
- **Caveat to verify during build** (not asserted now): confirm `--sync` +
  `start:pos(N)` behaves as documented on fzf 0.74 via a 5-second scripted test.
  Fallback: keep `load:pos(N)` (works today; the earlier empty-buffer issue only bit
  with the old bind ordering).

The MRU cursor is just "find the `►` row, +1" — same computation the other modes
already need. No list filtering special-cased into `_build_rows`.

## 6. Popup size
`-w 70% -h 50%` → `-w 85% -h 70%` in `setup.sh`'s `PICKER_CMD`. **Leaving
`~/.tmux.conf` alone** (hand-edited) — bump the two picker lines manually.

## 7. Tests
- `test_picker.py`: rewrite row-shape assertions (no header rows; marker=col 0,
  session=col 1). Add one test per mode asserting order (unseen-on-top, session
  grouping, mru by activity). Update `_colorize_line` sample lines.
  - Keep the existing split-pane tests from `ef89d1d`
    (`test_split_panes_get_distinct_targets`,
    `test_split_panes_only_focused_pane_is_current`,
    `test_current_marker_falls_back_to_window_without_focused_pane`) — they guard the
    single-`►` invariant the §5 cursor logic relies on. Their target assertions use
    pane ids, so they survive the row-model refactor; only row-index expectations may
    shift once header rows are gone.
- `test_formatting.py`: `align_columns` is generic — existing tests should pass; add
  a truncation check if truncation lands in a shared helper.

## Consequences folded in
- `ctrl-x` kills the **pane** under the cursor (`kill_pane`); tmux closes the window
  with its last pane. Header reads "ctrl-x: close pane". Already done in `ef89d1d`;
  session-kill dropped — use tmux commands or tmux-palette for that rare case.
- Session column width mitigated by the wider popup + 20-char cap + `align_columns`
  padding to actual content, not the cap.
- Because rows are pane-level, a split window shows one row per pane. The session
  column repeats across them, which is expected — the `{idx}: {name}` column
  distinguishes them.
