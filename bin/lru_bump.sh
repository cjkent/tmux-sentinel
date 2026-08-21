#!/usr/bin/env bash
# Record that a pane was just visited, for the picker's "recent" sort mode.
#
# tmux has no last-*visited* timestamp. #{window_activity} is last-*output* time, so an
# agent printing to a window you haven't looked at promotes it to the top of a recency
# list — which is wrong for a "where was I?" switcher. The only way to know what you
# actually visited is to record it, which is what this does, driven by tmux hooks.
#
# Entries are pane ids without the "%" prefix, most-recently-visited first, one per
# line. Pane ids match the picker's row targets directly, and are stable for the life
# of the pane (unlike session:window, which shifts when windows are renumbered).
#
# Installed by setup.sh as:
#   set-hook -g after-select-window[N]   'run-shell -b "…/lru_bump.sh \"$(tmux display -p \"#{pane_id}\")\""'
#   set-hook -g after-select-pane[N]     …
#   set-hook -g client-session-changed[N] …
#
# The nested `tmux display -p` is deliberate and not redundant: tmux expands format
# strings in a hook's command against the first attached session rather than the
# event's target, so #{pane_id} written directly into the hook would report the wrong
# pane for most navigation. Running display inside the hook's subshell gets the pane
# the event actually fired for. (Same trick tmux-switch uses, for the same reason.)
set -euo pipefail

entry="${1:-}"
entry="${entry#%}"
# Ignore anything that isn't a bare pane id: a failed `tmux display` would otherwise
# write an empty or malformed line into the cache.
case "$entry" in
    ''|*[!0-9]*) exit 0 ;;
esac

cache="${TMUX_SENTINEL_LRU:-$HOME/.tmux-sentinel/lru}"
max_lines=500

mkdir -p "$(dirname "$cache")"
tmp="$(mktemp "$cache.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

# Written to a temp file and moved into place: hooks fire concurrently (a session
# switch triggers several), and a half-written cache would scramble the order.
{
    printf '%s\n' "$entry"
    if [ -f "$cache" ]; then
        grep -Fxv -- "$entry" "$cache" | head -n $((max_lines - 1)) || true
    fi
} > "$tmp"

mv "$tmp" "$cache"
trap - EXIT
