# Plan: staleness guard for stranded "working" state

## Problem
Interrupting an agent mid-turn (Escape) cancels the turn **without firing the
`Stop` hook**. The last event the daemon saw was `working`, so the pane is stranded
on `working` indefinitely. The poll-loop screen-scrape (`poll.py`) is the only
safety net, and it can only rescue panes whose footer matches a manifest rule.

The `manual mode on` manifest rule (commit `e590ebb`) fixes the *known* footers.
The guard is a backstop for the whole class: **any interrupt into a footer we don't
have a rule for strands a pane on `working` forever.** It catches that without
enumerating every footer.

## Why the naive guard is NOT safe (must-read before building)
The obvious guard — "working for > N seconds with no on-screen working-marker →
idle" — can flip a **genuinely working** agent to idle. Two ways:

1. **The timer gives almost no protection.** `ps.timestamp` is set at
   `userPromptSubmit` and deliberately *not* refreshed by `preToolUse`/`postToolUse`
   (that's the "elapsed = time since your prompt" semantic the picker shows). So
   `now - ps.timestamp` is **turn length, not idle time.** Any genuine turn longer
   than the threshold (a 5-min build, a long agent loop) satisfies the timer
   continuously — so the *entire* safety rests on condition (2).

2. **The marker can be absent while genuinely working.** `capture-pane -p` captures
   the *visible* region. If you **scroll back / enter copy-mode** to read something
   while the agent churns, the bottom spinner line (`esc to interrupt` / `(\d+[hms]`)
   may not be in the captured lines → no marker → guard flips a live agent to idle.
   This is a normal thing to do, so it's a realistic false positive. (Verify tmux's
   exact copy-mode capture behaviour at build time.)

   Also: the guard leans entirely on the manifest's assertion that `(\d+[hms]` is
   present "for the whole turn." True today, not guaranteed across CC UI changes.

## The fix that makes the guard safe: a separate `last_activity` field
Stop leaning on the display timestamp. Add `PaneState.last_activity: int`, updated on
**every** hook event (`agentSpawn`, `userPromptSubmit`, `preToolUse`, `postToolUse`,
`stop`), while `timestamp` stays prompt-time for the picker's elapsed display.

Then "stuck" is defined honestly: **no hook activity for N seconds AND no on-screen
working-marker.** A live agent firing tool events keeps resetting `last_activity`, so
it never trips regardless of scroll/copy-mode state. The marker check demotes to a
fast-path; `last_activity` is the real guard.

With `last_activity` in place, a genuinely-working agent cannot be flipped to idle in
any realistic case. **Without it, the guard is unsafe and must not be added.**

## Implementation
1. **`state.py`** — add `last_activity: int = 0` to `PaneState`; set it in every
   branch of `apply_hook_event` (alongside the existing per-event updates).
2. **`poll.py`** — in the `if ps.status in (WORKING, WAITING)` block, add the
   `actual is None and ps.status == WORKING` case:
   ```python
   elif actual is None and ps.status == WORKING:
       tail = capture_pane_tail(pane_id, lines=10)
       if not _has_working_marker(tail) and now - ps.last_activity > STALE_WORKING_SECS:
           ps.status = IDLE
           ps.unseen = True
   ```
   with a helper mirroring the manifest exclusion:
   ```python
   _WORKING_MARKER = re.compile(r'esc to interrupt|\(\d+[hms]')
   def _has_working_marker(tail: str) -> bool:
       return bool(_WORKING_MARKER.search(tail))
   ```
3. **`daemon.py` `dump`** — no change needed (guard is internal to poll).

## Design calls
- **Threshold `STALE_WORKING_SECS`.** With `last_activity` doing the real work, this
  is just debounce for the gap between events and spinner re-render. 60–120s.
- **`unseen = True`?** Set it, matching the normal idle transition (a turn
  "finished," you may not have seen it). Arguable — an interrupt is user-initiated,
  so maybe not unseen. Minor.
- **Cost.** One extra `capture_pane_tail` per poll *only* for panes stuck
  marker-less on working — normally zero (working panes show the marker and
  short-circuit).

## Relationship to the manifest rule
- **Manifest rule** (`e590ebb`): precise, instant, but only covers enumerated
  footers.
- **Guard**: general (any unknown footer), but coarse — waits out the timer and can
  only ever conclude "idle" (can't tell idle from waiting).
- Keep both: rule for known states, guard as a floor so nothing stays stuck forever.

## Tests
- `test_daemon_state.py`: assert `last_activity` is set/refreshed by each hook event.
- `test_poll.py` (or equivalent): a pane stuck on `working` with no marker and
  `last_activity` older than the threshold → flips to idle+unseen; a pane with a
  live marker, or recent `last_activity`, is left untouched.
