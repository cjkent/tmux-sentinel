#!/usr/bin/env bash
# Open the tmux-sentinel config file in $EDITOR.
#
# Creates the file from config.toml.example on first use, so you start from a
# documented template rather than an empty buffer.
#
# Picker settings are read on every open, so edits apply immediately. Daemon poll
# intervals need a daemon restart. Popup geometry is not in this file at all — tmux
# fixes a popup's size before the picker starts — set @sentinel-popup-width/-height.
#
# Bind it with (note the popup needs -E so the editor gets a terminal):
#   bind -n M-, display-popup -w 80% -h 80% -E "/path/to/tmux-sentinel/bin/edit-config.sh"
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.tmux-sentinel"
CONFIG="$CONFIG_DIR/config.toml"
EXAMPLE="$REPO_DIR/config.toml.example"

mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG" ]; then
    if [ -f "$EXAMPLE" ]; then
        cp "$EXAMPLE" "$CONFIG"
        echo "Created $CONFIG from the example template."
    else
        # Example missing (partial checkout?) — still give the editor something.
        printf '# tmux-sentinel settings — see config.toml.example\n' >"$CONFIG"
    fi
fi

# VISUAL wins over EDITOR by convention, since it's the one meant for full-screen
# editors. vi is the last resort: POSIX requires it, so it's the safest fallback.
editor="${VISUAL:-${EDITOR:-vi}}"

# Word-split deliberately: EDITOR may carry flags, e.g. "code -w" or "emacs -nw".
# shellcheck disable=SC2086
$editor "$CONFIG"

# Report syntax errors rather than letting a typo silently fall back to defaults on
# the next read. Uses the same parser the tool uses, so the verdict matches.
if ! PYTHONPATH="$REPO_DIR" python3 -S -c "
import sys, tomllib
try:
    with open('$CONFIG', 'rb') as f:
        tomllib.load(f)
except tomllib.TOMLDecodeError as e:
    print(f'  Config has a syntax error: {e}', file=sys.stderr)
    print('  tmux-sentinel will ignore the file and use defaults until it is fixed.', file=sys.stderr)
    sys.exit(1)
" 2>&1; then
    printf '\nPress Enter to close...'
    read -r _
fi
