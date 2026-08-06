#!/usr/bin/env bash
# Change the size of the agent picker popup.
#
# The popup's geometry is an argument to tmux's display-popup, fixed before the
# picker process starts — so it can't live in ~/.tmux-sentinel/config.toml with the
# other settings. It has to be in the tmux binding itself. This script edits those
# bindings for you: it updates ~/.tmux.conf so the change persists, and re-binds the
# running server so it takes effect now, without a tmux restart.
set -uo pipefail

CONF="${TMUX_CONF:-$HOME/.tmux.conf}"

if [ ! -f "$CONF" ]; then
    echo "No $CONF — run setup.sh first to bind the picker." >&2
    exit 1
fi

# Find the picker bindings, whatever key they use and whether they need the prefix.
# Avoids mapfile/readarray: macOS ships bash 3.2 as /bin/bash, which lacks both.
FIRST=$(grep -m1 'display-popup.*picker\.py' "$CONF")
if [ -z "$FIRST" ]; then
    echo "No picker binding found in $CONF — run setup.sh first." >&2
    exit 1
fi

# Current size, read from the first binding, so the prompts default to reality
# rather than to whatever this script happens to hardcode.
CUR_W=$(printf '%s\n' "$FIRST" | sed -n 's/.*-w \([0-9]*%*\).*/\1/p')
CUR_H=$(printf '%s\n' "$FIRST" | sed -n 's/.*-h \([0-9]*%*\).*/\1/p')
: "${CUR_W:=85%}"
: "${CUR_H:=70%}"

echo "Picker popup size — press Enter to keep the current value."
read -r -p "  Width  [$CUR_W]: " W
read -r -p "  Height [$CUR_H]: " H
W="${W:-$CUR_W}"
H="${H:-$CUR_H}"

# Accept "80" as shorthand for "80%": percentages are almost always what's wanted,
# and a bare number would otherwise mean 80 columns.
[[ "$W" =~ ^[0-9]+$ ]] && W="$W%"
[[ "$H" =~ ^[0-9]+$ ]] && H="$H%"
for v in "$W" "$H"; do
    if ! [[ "$v" =~ ^[0-9]+%?$ ]]; then
        echo "Invalid size '$v' — use e.g. 85% or 120." >&2
        exit 1
    fi
done

# Rewrite the size flags in place, leaving each binding's key and table alone.
tmp=$(mktemp)
sed -E "/display-popup.*picker\.py/{
    s/-w [0-9]+%?/-w $W/
    s/-h [0-9]+%?/-h $H/
}" "$CONF" >"$tmp" && mv "$tmp" "$CONF"

echo "Updated $CONF:"
grep -n 'display-popup.*picker\.py' "$CONF" | sed 's/^/  /'

# Apply to the running server too, so the new size is live immediately.
#
# The binding lines are already valid tmux commands, so feed them straight back via
# source-file. That avoids re-parsing them in the shell — the quoted -E command
# contains spaces and quotes that word-splitting or eval would mangle, and lets tmux
# own the syntax it defined.
if tmux info &>/dev/null; then
    live=$(mktemp)
    grep 'display-popup.*picker\.py' "$CONF" >"$live"
    if tmux source-file "$live" 2>/dev/null; then
        echo "Re-bound $(wc -l <"$live" | tr -d ' ') picker key(s) in the running server."
    else
        echo "Could not re-bind the running server; the new size applies on restart." >&2
    fi
    rm -f "$live"
else
    echo "(No tmux server running — the new size applies next time it starts.)"
fi
