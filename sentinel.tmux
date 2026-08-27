#!/usr/bin/env bash
# tmux-sentinel plugin entry point.
#
# TPM runs this on every tmux start and on prefix+I / prefix+U, non-interactively and
# with nowhere to print. So everything here must be idempotent, silent on success, and
# incapable of destroying configuration the user or another plugin owns.
#
# Install with TPM:
#     set -g @plugin 'cjkent/tmux-sentinel'
# Or without TPM, from ~/.tmux.conf:
#     run-shell /path/to/tmux-sentinel/sentinel.tmux
#
# Agent lifecycle hooks are NOT installed here. They live in other tools' config files
# (~/.kiro/agents/*.json, ~/.claude/settings.json), and a plugin rewriting those on
# every tmux start would be indefensible. Run ./setup.sh once for those.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- option helpers ----------------------------------------------------------------

# Read a user option, falling back to a default. -q keeps tmux quiet about unset
# options; -v gives the bare value.
opt() {
    local value
    value="$(tmux show-option -gqv "$1" 2>/dev/null)"
    if [ -z "$value" ]; then printf '%s' "$2"; else printf '%s' "$value"; fi
}

# Warn the user in the only channel available at tmux start. Deliberately terse: this
# lands in their message area, not a log.
warn() { tmux display-message "tmux-sentinel: $1" 2>/dev/null || true; }

# Install a hook at our own array slot: reuse the index already holding our command
# (idempotent across reloads), else take the first free one (collision-free, and never
# disturbs another tool's).
#
# tmux hooks are array options and the three ways to set one all misbehave differently:
#   * unindexed  — resets the whole array, silently deleting other tools' hooks
#   * -a append  — never idempotent: one copy per reload, and the hook then runs once
#                  per copy on every event
#   * indexed    — safe, but a hardcoded index is a guess; two plugins picking the same
#                  one means the second silently wins
set_own_hook() {
    local event="$1" mark="$2" cmd="$3" existing idx used
    existing="$(tmux show-hooks -g 2>/dev/null | grep "^${event}\[" || true)"
    idx="$(printf '%s\n' "$existing" | grep -F -- "$mark" \
           | head -1 | sed -E 's/^[^[]*\[([0-9]+)\].*/\1/')"
    if [ -z "$idx" ]; then
        used="$(printf '%s\n' "$existing" | sed -E 's/^[^[]*\[([0-9]+)\].*/\1/')"
        idx=0
        while printf '%s\n' "$used" | grep -qx "$idx"; do idx=$((idx + 1)); done
    fi
    tmux set-hook -g "${event}[${idx}]" "$cmd" 2>/dev/null || true
}

# The nested `tmux display -p` is required rather than inlining #{pane_id}: tmux expands
# a hook command's format strings against the current client, not the event's target, so
# the inline form reports the wrong pane for most navigation.
install_visit_hooks() {
    local cmd="run-shell -b \"$REPO_DIR/bin/lru_bump.sh \\\"\$(tmux display -p '#{pane_id}')\\\"\""
    local event
    for event in after-select-window after-select-pane client-session-changed; do
        set_own_hook "$event" "lru_bump.sh" "$cmd"
    done
}

# --hooks-only: reinstall just the visit hooks and exit. Invoked from a client-attached
# hook (see below), which is the only way to survive a plugin that loads after us and
# sets one of these events *unindexed* — that resets the whole array, so no choice of
# index protects us. A client attaches only after the config has finished sourcing, so
# by then every plugin has had its turn.
#
# Deliberately does not re-register the client-attached hook itself: that would recurse.
if [ "${1:-}" = "--hooks-only" ]; then
    install_visit_hooks
    exit 0
fi

# --- dependency checks -------------------------------------------------------------
#
# These features fail at keypress with cryptic errors rather than at install, so it's
# worth saying something up front. Missing dependencies disable the plugin rather than
# leaving half of it wired up.

ver_lt() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found — not loading"
    exit 0
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    warn "python3 3.11+ required (found $(python3 -V 2>&1 | awk '{print $2}')) — not loading"
    exit 0
fi
if ! command -v fzf >/dev/null 2>&1; then
    warn "fzf not found — not loading"
    exit 0
fi
FZF_VER="$(fzf --version 2>/dev/null | sed -n 's/^\([0-9][0-9.]*\).*/\1/p')"
if [ -n "$FZF_VER" ] && ver_lt "$FZF_VER" "0.60"; then
    warn "fzf 0.60+ required (found $FZF_VER) — not loading"
    exit 0
fi
TMUX_VER="$(tmux -V 2>/dev/null | sed -n 's/^tmux \([0-9][0-9.]*\).*/\1/p')"
if [ -n "$TMUX_VER" ] && ver_lt "$TMUX_VER" "3.2"; then
    warn "tmux 3.2+ required for display-popup (found $TMUX_VER) — not loading"
    exit 0
fi

# --- bell notification -------------------------------------------------------------
#
# The agent hooks ring the terminal bell when a turn ends; these turn that into a
# highlighted window tab. Only set when the user hasn't expressed a preference, so we
# don't override a deliberate choice. bell-action in particular is a matter of taste.

if [ "$(opt @sentinel-set-bell-options on)" = "on" ]; then
    tmux set -g monitor-bell on 2>/dev/null || true
    tmux set -g bell-action other 2>/dev/null || true
    tmux set -g window-status-bell-style "$(opt @sentinel-bell-style 'fg=red,bold')" 2>/dev/null || true
fi

# --- status bar --------------------------------------------------------------------
#
# Substitution, not assignment. The user writes a placeholder where they want the
# segment:
#
#     set -g status-right '#{sentinel_status} %H:%M'
#
# and it's replaced here with the real command. Overwriting status-right outright — as
# setup.sh used to — destroys whatever the user had built, which is not a plugin's
# business. If the placeholder is absent we do nothing at all, so a user who hasn't
# opted in keeps their bar untouched.
#
# Idempotent by construction: after substitution the placeholder is gone, so a second
# run finds nothing to do. On a config reload the user's conf reinstates the
# placeholder first, and we substitute again.
STATUS_CMD="#($REPO_DIR/bin/status_client.sh '#{pane_id}')"
for scope in status-right status-left; do
    current="$(tmux show-option -gqv "$scope" 2>/dev/null)"
    case "$current" in
        *'#{sentinel_status}'*)
            tmux set -g "$scope" "${current//'#{sentinel_status}'/$STATUS_CMD}" 2>/dev/null || true
            # The segment is only refreshed as often as the status bar redraws.
            if [ "$(tmux show-option -gqv status-interval 2>/dev/null)" -gt 5 ] 2>/dev/null; then
                tmux set -g status-interval "$(opt @sentinel-status-interval 5)" 2>/dev/null || true
            fi
            ;;
    esac
done

# --- visit tracking hooks ----------------------------------------------------------
#
# Records which pane you actually visited, for the picker's "recent" mode. tmux has no
# last-visited timestamp — #{window_activity} is last-*output* time, so an agent
# printing into a window you never looked at would top a recency list.
if [ "$(opt @sentinel-track-visits on)" = "on" ]; then
    install_visit_hooks
    # Re-assert once a client attaches. Choosing a free index protects us from another
    # plugin picking the same index, but nothing protects us from one setting the same
    # event *unindexed*, which wipes the array outright. Attach happens after the whole
    # config is sourced, so this runs last regardless of plugin load order.
    set_own_hook client-attached "sentinel.tmux --hooks-only" \
        "run-shell -b \"$REPO_DIR/sentinel.tmux --hooks-only\""
fi

# --- keybindings -------------------------------------------------------------------
#
# One key per sort mode. Rebuilt from options on every load, which is what makes popup
# geometry an ordinary setting rather than something baked into the binding: change
# @sentinel-popup-width, reload, and the next popup is the new size.
#
# Set a key option to "none" to skip that binding, for users who only want one or two.
# It has to be an explicit sentinel rather than an empty string: `show-option -gqv`
# returns nothing for both "set to empty" and "never set", so an empty value is
# indistinguishable from unset and would just fall back to the default.

POPUP_W="$(opt @sentinel-popup-width 85%)"
POPUP_H="$(opt @sentinel-popup-height 70%)"
# "root" binds without the prefix; "prefix" requires it first.
# Default table for keys that don't name one themselves.
KEY_TABLE="$(opt @sentinel-key-table root)"

# Each key may name its own table with a "prefix:" or "root:" marker:
#
#     set -g @sentinel-key-unseen  'C-Space'            # default table
#     set -g @sentinel-key-session 'prefix:e'           # prefix table
#     set -g @sentinel-key-mru     'C-Tab prefix:u'     # both: root key + fallback
#
# The two tables differ in reliability, not only in speed. A root key reaches tmux only
# if the terminal passes it through: Option-Space and Ctrl-Tab both need terminal
# support and are often claimed by the terminal or the OS. A prefix key needs only the
# prefix itself, which is a plain control byte, so tmux reads whatever follows. Mixing
# the two lets a fast root key coexist with a dependable prefix fallback.
bind_mode() {
    local key="$1" mode="$2" table="$KEY_TABLE"
    [ -n "$key" ] && [ "$key" != "none" ] || return 0
    case "$key" in
        prefix:*) table=prefix; key="${key#prefix:}" ;;
        root:*)   table=root;   key="${key#root:}" ;;
    esac
    [ -n "$key" ] || return 0
    local cmd="PYTHONPATH=$REPO_DIR python3 -S $REPO_DIR/tmux_sentinel/picker.py --mode=$mode"
    # Branch rather than splatting a possibly-empty flags array: under `set -u`,
    # expanding an empty array is a fatal "unbound variable" in bash 3.2, which is
    # what macOS ships as /bin/bash.
    if [ "$table" = "root" ]; then
        tmux bind-key -n "$key" display-popup -w "$POPUP_W" -h "$POPUP_H" -E "$cmd" \
            2>/dev/null || warn "could not bind '$key' (check the key name)"
    else
        tmux bind-key "$key" display-popup -w "$POPUP_W" -h "$POPUP_H" -E "$cmd" \
            2>/dev/null || warn "could not bind '$key' (check the key name)"
    fi
}

# Each mode accepts a space-separated list, so one mode can have both a root key and a
# prefix fallback. This is what removes the need for a hand-written extra binding.
#
# Space is the separator rather than a comma because no tmux key name contains a space —
# the space key itself is spelled "Space". A comma would have made the literal "," key
# impossible to bind, since it would split into empty fields.
bind_modes() {
    local mode="$1" list="$2" key
    # Deliberately unquoted: word splitting on whitespace is the point here.
    for key in $list; do
        bind_mode "$key" "$mode"
    done
}

bind_modes unseen  "$(opt @sentinel-key-unseen  'C-Space')"
bind_modes session "$(opt @sentinel-key-session 'M-Space')"
bind_modes mru     "$(opt @sentinel-key-mru     'C-Tab')"

exit 0
