#!/usr/bin/env bash
# Let M-Space and C-Tab reach tmux from iTerm2.
#
# Two iTerm2 settings stop them, and both are easy to miss because iTerm2 swallows the
# key without saying so:
#
#   * Left Option key set to "Normal". Option-Space then types a space (or composes a
#     character) instead of sending Esc-Space, which is what tmux reads as M-Space. Set
#     to "Esc+", it sends the Meta form.
#   * A key binding on Ctrl-Tab. iTerm2 uses it to switch tabs, so the key never reaches
#     the terminal at all. Removing the binding gives the key to tmux.
#
# Only the LEFT Option key is changed. The right one keeps composing characters, which
# on a UK or similar layout is how # and the like are typed.
#
# Run it from a terminal other than iTerm2, with iTerm2 quit. iTerm2 keeps its settings
# in memory and rewrites the file when it quits, so an edit made while it runs is lost.
#
# This does not edit tmux.conf. C-Tab also needs tmux told to expect extended keys:
#     set -s extended-keys on
#     set -as terminal-features 'xterm*:extkeys'
#
# Usage:
#     bin/iterm2-keys.sh            apply, after backing the file up
#     bin/iterm2-keys.sh --check    report what would change, change nothing
#     bin/iterm2-keys.sh --plist F  work on file F (testing); skips the running check
set -uo pipefail

PB=/usr/libexec/PlistBuddy
CTRL_TAB="0x9-0x40000"   # iTerm2's key for Ctrl-Tab: keycode 0x9, modifier mask 0x40000

CHECK=0
PLIST=""
EXPLICIT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=1 ;;
        --plist) shift; PLIST="${1:-}"; EXPLICIT=1 ;;
        -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "$(uname)" != "Darwin" ]; then
    echo "iTerm2 is macOS only; nothing to do." >&2
    exit 0
fi
[ -x "$PB" ] || { echo "PlistBuddy not found at $PB" >&2; exit 1; }

# --- find the settings file --------------------------------------------------------
#
# iTerm2 can keep its settings in a folder of the user's choice (Settings > General >
# Settings > "Load settings from a custom folder"). When it does, the copy under
# ~/Library/Preferences is ignored, so editing that one would change nothing.
if [ -z "$PLIST" ]; then
    custom_on="$(defaults read com.googlecode.iterm2 LoadPrefsFromCustomFolder 2>/dev/null || echo 0)"
    if [ "$custom_on" = "1" ]; then
        folder="$(defaults read com.googlecode.iterm2 PrefsCustomFolder 2>/dev/null || true)"
        folder="${folder/#\~/$HOME}"
        PLIST="$folder/com.googlecode.iterm2.plist"
    else
        PLIST="$HOME/Library/Preferences/com.googlecode.iterm2.plist"
    fi
fi
[ -f "$PLIST" ] || { echo "settings file not found: $PLIST" >&2; exit 1; }

if [ "$EXPLICIT" -eq 0 ] && [ "$CHECK" -eq 0 ] && pgrep -x iTerm2 >/dev/null 2>&1; then
    echo "iTerm2 is running. Quit it (Cmd-Q), run this from another terminal, then reopen it." >&2
    echo "It rewrites its settings file on quit, so an edit made now would be lost." >&2
    exit 1
fi

echo "settings file: $PLIST"

# --- helpers -----------------------------------------------------------------------

# A PlistBuddy read that may fail: a missing key is an answer here, not an error.
pb_get() { "$PB" -c "Print $1" "$PLIST" 2>/dev/null; }

CHANGES=0
note() { echo "  $*"; CHANGES=$((CHANGES + 1)); }

pb_set_int() {   # path value : set, creating the key if the profile never stored it
    "$PB" -c "Set $1 $2" "$PLIST" >/dev/null 2>&1 \
        || "$PB" -c "Add $1 integer $2" "$PLIST" >/dev/null 2>&1
}

# --- the changes -------------------------------------------------------------------

backup=""
prepare() {
    [ -n "$backup" ] && return
    backup="$PLIST.sentinel-backup-$(date +%Y%m%d-%H%M%S)"
    cp -p "$PLIST" "$backup"
    echo "  backup: $backup"
}

# 1. Ctrl-Tab bindings, in the global map and in each profile's own map.
remove_ctrl_tab() {   # label path
    if [ -n "$(pb_get "$2")" ]; then
        note "$1: removing the Ctrl-Tab binding"
        if [ "$CHECK" -eq 0 ]; then
            prepare
            "$PB" -c "Delete $2" "$PLIST" >/dev/null 2>&1
        fi
    fi
}
remove_ctrl_tab "global key map" ":GlobalKeyMap:$CTRL_TAB"

# 2. Left Option key, per profile.
i=0
while name="$(pb_get ":New\\ Bookmarks:$i:Name")" && [ -n "$name" ]; do
    remove_ctrl_tab "profile \"$name\"" ":New\\ Bookmarks:$i:Keyboard\\ Map:$CTRL_TAB"

    # 0 = Normal, 1 = Meta, 2 = Esc+. Meta sets the high bit, which UTF-8 terminals
    # misread, so 2 is the one tmux wants.
    current="$(pb_get ":New\\ Bookmarks:$i:Option\\ Key\\ Sends")"
    if [ "$current" != "2" ]; then
        note "profile \"$name\": left Option key ${current:-unset} -> Esc+"
        if [ "$CHECK" -eq 0 ]; then
            prepare
            pb_set_int ":New\\ Bookmarks:$i:Option\\ Key\\ Sends" 2
        fi
    fi
    i=$((i + 1))
done

if [ "$i" -eq 0 ]; then
    echo "  no profiles found in the file; is this an iTerm2 settings file?" >&2
    exit 1
fi

# --- finish ------------------------------------------------------------------------

if [ "$CHANGES" -eq 0 ]; then
    echo "Already set up. Nothing changed."
    exit 0
fi

if [ "$CHECK" -eq 1 ]; then
    echo "$CHANGES change(s) needed. Run again without --check to apply."
    exit 0
fi

# cfprefsd caches preferences. When iTerm2 reads the standard location, a stale cache
# could serve the old values. Restarting it is harmless; launchd starts it again at once.
if [ "$EXPLICIT" -eq 0 ]; then
    killall cfprefsd 2>/dev/null || true
fi

echo "$CHANGES change(s) applied. Reopen iTerm2, then check each key:"
echo "  run  cat -v  and press the key, then Ctrl-C"
echo "    M-Space should print   ^[ "
echo "    C-Tab   should print   ^[[9;5u   (needs the tmux lines in the file header)"
