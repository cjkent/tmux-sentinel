#!/usr/bin/env bash
# tmux-sentinel setup
#
# Configures agent status tracking and the tmux wiring for it. Does four things:
#   1. Injects hook entries into Kiro agent JSON configs (all 5 lifecycle events)
#   2. Offers hooks for Claude Code and the plugin for OpenCode
#   3. Creates the status directory (~/.tmux-sentinel/status/)
#   4. Offers to add the sentinel.tmux line and a status-bar placeholder to ~/.tmux.conf
#
# Hooks are additive — existing hooks in agent configs are preserved.
# The injection is idempotent — running setup again won't duplicate hooks.
# Agent configs are backed up before modification.
#
# Usage:
#   ./setup.sh              — interactive setup (inject hooks, configure tmux)
#   ./setup.sh --remove-hooks — interactive removal of hooks from agent configs
set -euo pipefail

# --- Terminal colors for output ---

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }
step()  { echo -e "\n${BOLD}$1${NC}"; }

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_CMD="PYTHONPATH=$REPO_DIR python3 -S $REPO_DIR/tmux_sentinel/hook.py"
AGENTS_DIR="$HOME/.kiro/agents"
STATUS_DIR="$HOME/.tmux-sentinel/status"

# Multi-select picker, delegated to fzf rather than hand-rolled.
#
# This used to be ~60 lines of raw terminal handling — tput civis, single-byte reads,
# manual ANSI escape decoding for arrow keys, cursor-up redraws. fzf is already a hard
# dependency and does all of it natively.
#
# Everything starts selected via load:select-all, matching the old behaviour where
# pressing Enter accepts the lot (the common case).
#
# Sets PICKER_RESULT to a space-separated list of selected indices (0-based).
#
# The one subtlety: with --multi, Enter on an *empty* selection returns the item under
# the cursor rather than nothing, so "I want none of these" cannot be expressed by
# deselecting everything — it's Esc, which fzf reports as exit 130. Treating that as an
# empty result is what stops setup from hooking an agent the user just deselected.
run_picker() {
    local prompt="$1"; shift
    local labels=("$@")

    # Indices travel through fzf as a hidden first field, so the visible list stays
    # clean while the caller still gets positions back rather than having to re-match
    # labels (which could be ambiguous if two agents shared a name).
    local input="" i=0
    for label in "${labels[@]}"; do
        input+="${i}"$'\t'"${label}"$'\n'
        i=$((i + 1))
    done

    # `|| true` matters: Esc makes fzf exit non-zero, and under `set -e` that would
    # kill setup outright instead of being read as "the user chose nothing".
    local out status
    out=$(printf '%s' "$input" | fzf \
        --multi \
        --sync \
        --no-sort \
        --reverse \
        --delimiter=$'\t' \
        --with-nth=2 \
        --height="~100%" \
        --prompt="  " \
        --header="$prompt
  tab: toggle · ctrl-a: all · ctrl-d: none · enter: confirm · esc: skip" \
        --bind 'load:select-all' \
        --bind 'ctrl-a:select-all' \
        --bind 'ctrl-d:deselect-all' 2>/dev/null) && status=0 || status=$?

    PICKER_RESULT=""
    # 130 is Esc/interrupt. Anything non-zero means "no selection", and stdout must be
    # ignored — see the note above about Enter falling back to the cursor item.
    if [ "$status" -ne 0 ]; then
        return 0
    fi
    while IFS=$'\t' read -r idx _; do
        [ -n "$idx" ] && PICKER_RESULT+="$idx "
    done <<< "$out"
}

# All JSON manipulation lives in tmux_sentinel.install. It used to be jq filters
# assembled as bash strings, which meant regexes under four levels of escaping and no
# way to test them in isolation — and jq was a dependency needed for setup alone,
# since nothing else in the project used it.
INSTALL="PYTHONPATH=$REPO_DIR python3 -S -m tmux_sentinel.install"

# --- Remove hooks mode ---
# Strips tmux-sentinel hook entries from selected agent configs, leaving any hooks the
# user added themselves untouched.
if [ "${1:-}" = "--remove-hooks" ]; then
    echo -e "${BOLD}tmux-sentinel — remove hooks${NC}"
    echo ""

    HOOKED=()
    HOOKED_LABELS=()
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        HOOKED+=("$f")
        HOOKED_LABELS+=("$(basename "$f" .json)")
    done < <(eval "$INSTALL --list-kiro-hooked")

    if [ "${#HOOKED[@]}" -eq 0 ]; then
        info "No agents have tmux-sentinel hooks"
        exit 0
    fi

    run_picker "Select agents to remove hooks from:" "${HOOKED_LABELS[@]}"

    SELECTED=()
    for idx in $PICKER_RESULT; do
        SELECTED+=("${HOOKED[$idx]}")
    done
    REMOVED=0
    if [ "${#SELECTED[@]}" -gt 0 ]; then
        REMOVED=$(eval "$INSTALL --remove-kiro" "$(printf '%q ' "${SELECTED[@]}")")
    fi
    info "Removed hooks from $REMOVED agents"

    # Also remove from Claude Code settings
    CLAUDE_SETTINGS="$HOME/.claude/settings.json"
    if eval "$INSTALL --claude-has-hook"; then
        echo -n "  Also remove hooks from Claude Code settings? [Y/n] "
        read -r answer
        if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
            cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.backup.$(date +%Y%m%d%H%M%S)"
            eval "$INSTALL --remove-claude" \
                && info "Removed hooks from Claude Code settings" \
                || error "Could not update $CLAUDE_SETTINGS"
        fi
    fi

    # And the OpenCode plugin file, if we installed one.
    if eval "$INSTALL --opencode-has-plugin"; then
        echo -n "  Also remove the OpenCode plugin? [Y/n] "
        read -r answer
        if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
            eval "$INSTALL --remove-opencode" >/dev/null \
                && info "Removed the OpenCode plugin" \
                || error "Could not remove the OpenCode plugin"
        fi
    fi
    exit 0
fi

# --- Main setup ---
echo -e "${BOLD}tmux-sentinel setup${NC}"
echo ""

# 1. Check dependencies
#
# Versions are checked, not just presence: the features below fail at keypress with
# cryptic errors rather than at install time, which is a miserable way to find out.
step "Checking dependencies..."
for cmd in fzf tmux python3; do
    if command -v "$cmd" &>/dev/null; then
        info "$cmd found"
    else
        error "$cmd not found — install it first"
        exit 1
    fi
done

# True if $1 is a lower version than $2. sort -V orders versions properly, but its
# lowest entry equals $1 both when $1 < $2 and when they're equal, hence the != test.
ver_lt() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

# tmux 3.2+ for display-popup, which the picker is built on.
TMUX_VER=$(tmux -V 2>/dev/null | sed -n 's/^tmux \([0-9][0-9.]*\).*/\1/p')
if [ -n "$TMUX_VER" ] && ver_lt "$TMUX_VER" "3.2"; then
    error "tmux $TMUX_VER is too old — display-popup needs 3.2+"
    exit 1
fi
info "tmux ${TMUX_VER:-unknown} (3.2+ required)"

# fzf 0.60+ for the `exclude` action ctrl-x uses; older releases lack it and fzf
# rejects an unknown action outright, so the picker would not start at all.
FZF_VER=$(fzf --version 2>/dev/null | sed -n 's/^\([0-9][0-9.]*\).*/\1/p')
if [ -n "$FZF_VER" ] && ver_lt "$FZF_VER" "0.60"; then
    error "fzf $FZF_VER is too old — the picker needs 0.60+"
    exit 1
fi
info "fzf ${FZF_VER:-unknown} (0.60+ required)"

# Python 3.11+ for tomllib, used to read the config file.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    error "python3 $(python3 -V 2>&1 | awk '{print $2}') is too old — 3.11+ required (tomllib)"
    exit 1
fi
info "python3 $(python3 -V 2>&1 | awk '{print $2}') (3.11+ required)"

# nc is optional: the status bar prefers it for speed but falls back to Python.
# Probes behaviour rather than parsing --help, since help formats differ and a naive
# grep for "-U" misses the compact flag cluster that OpenBSD and nmap netcat print.
# See the same detection in bin/status_client.sh.
if command -v nc &>/dev/null; then
    # The probe is expected to fail — the socket path doesn't exist. Guard the
    # assignment with `|| true` so `set -e` doesn't treat that as fatal.
    NC_PROBE=$(nc -U /nonexistent/tmux-sentinel-probe </dev/null 2>&1) || true
    case "$NC_PROBE" in
        *"illegal option"*|*"invalid option"*|*"unrecognized option"*|*"usage:"*)
            warn "nc lacks -U — the status bar will use a slower Python fallback" ;;
        *)
            info "nc supports -U (status bar will use the fast path)" ;;
    esac
else
    warn "nc not found — the status bar will use a slower Python fallback"
fi

# 2. Create directories
step "Creating directories..."
mkdir -p "$STATUS_DIR"
info "Status directory: $STATUS_DIR"

# 3. Find eligible agents
#
# Kiro is optional: plenty of users run only Claude Code. Skip this section when
# there's no Kiro install rather than exiting, or a Claude-only user would get no
# Claude hooks, no status bar, and no keybind — the rest of setup lives below.
step "Finding Kiro agents..."
ELIGIBLE=()
ELIGIBLE_LABELS=()
if [ ! -d "$AGENTS_DIR" ]; then
    info "No Kiro install at $AGENTS_DIR — skipping (Claude Code is configured below)"
else
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        ELIGIBLE+=("$f")
        ELIGIBLE_LABELS+=("$(basename "$f" .json)")
    done < <(eval "$INSTALL --list-kiro-eligible")
fi

if [ ! -d "$AGENTS_DIR" ]; then
    :   # nothing to do; message already shown above
elif [ "${#ELIGIBLE[@]}" -eq 0 ]; then
    info "All agents already have tmux-sentinel hooks"
else
    run_picker "Select agents to add tmux-sentinel hooks to:" "${ELIGIBLE_LABELS[@]}"

    # Backup agent configs before modifying, then inject our hook into
        # all 5 lifecycle events. Adding is idempotent — an existing copy of our
        # hook is replaced rather than duplicated.
    SELECTED=()
    for idx in $PICKER_RESULT; do
        SELECTED+=("${ELIGIBLE[$idx]}")
    done

    if [ "${#SELECTED[@]}" -gt 0 ]; then
        BACKUP_DIR="$AGENTS_DIR.backup.$(date +%Y%m%d%H%M%S)"
        cp -r "$AGENTS_DIR/" "$BACKUP_DIR"
        info "Backup created at $BACKUP_DIR"

        UPDATED=$(eval "$INSTALL --add-kiro" "$(printf '%q ' "${SELECTED[@]}")" \
            "--command $(printf '%q' "$HOOK_CMD")")
        info "Hooked $UPDATED agents"
    else
        warn "No agents selected"
    fi
fi

# 4. Configure Claude Code hooks
step "Configuring Claude Code..."
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CC_EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse Stop"

if eval "$INSTALL --claude-has-hook"; then
    info "Claude Code hooks already configured"
else
    if [ -f "$CLAUDE_SETTINGS" ]; then
        PROMPT="  Add tmux-sentinel hooks to Claude Code? [Y/n] "
    else
        PROMPT="  Create Claude Code settings with tmux-sentinel hooks? [Y/n] "
    fi
    echo -n "$PROMPT"
    read -r answer
    if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
        # Back up only an existing file; --add-claude creates one if absent.
        [ -f "$CLAUDE_SETTINGS" ] \
            && cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.backup.$(date +%Y%m%d%H%M%S)"
        if eval "$INSTALL --add-claude --command $(printf '%q' "$HOOK_CMD")"; then
            info "Claude Code hooks configured in $CLAUDE_SETTINGS"
        else
            error "Could not update $CLAUDE_SETTINGS"
        fi
    fi
fi

# 5. Configure OpenCode
#
# OpenCode has no hook list to edit: it loads plugin files from a directory, so this
# installs one file. Offered only if OpenCode looks present, since writing into the
# config of a tool that is not installed would just leave a stray directory.
OPENCODE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
if command -v opencode >/dev/null 2>&1 || [ -d "$OPENCODE_DIR" ]; then
    step "Configuring OpenCode..."
    if eval "$INSTALL --opencode-has-plugin"; then
        # Re-run to refresh the file: it embeds this checkout's path, which goes stale if
        # the checkout is moved. Idempotent, so an unchanged file is left alone.
        RESULT=$(eval "$INSTALL --add-opencode --repo-dir $(printf '%q' "$REPO_DIR")" 2>&1) \
            || RESULT="failed"
        case "$RESULT" in
            unchanged) info "OpenCode plugin already configured" ;;
            updated)   info "OpenCode plugin refreshed" ;;
            *)         error "Could not update the OpenCode plugin: $RESULT" ;;
        esac
    else
        echo -n "  Add the tmux-sentinel plugin to OpenCode? [Y/n] "
        read -r answer
        if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
            if RESULT=$(eval "$INSTALL --add-opencode --repo-dir $(printf '%q' "$REPO_DIR")" 2>&1); then
                info "OpenCode plugin installed in $OPENCODE_DIR/plugins"
                info "Restart OpenCode to load it"
            else
                error "$RESULT"
            fi
        fi
    fi
fi

# --- tmux configuration ---
#
# Not done here any more. sentinel.tmux owns everything tmux-side — bindings, options,
# hooks, the status bar — because it has to be re-runnable on every tmux start for the
# plugin case, and duplicating that logic in two places would guarantee they drift.
#
# What's left for this script is the part a plugin must not do: writing into other
# tools' config files (agent hooks, and ~/.tmux.conf below), which is why each stays an
# explicit opt-in step.
step "Wiring up tmux..."

TMUX_CONF="$HOME/.tmux.conf"
PLUGIN_LINE="run-shell $REPO_DIR/sentinel.tmux"
MARKER="# tmux-sentinel"
CONF_BACKED_UP=0

# Patterns for uncommented lines, so a commented-out example never counts as set up.
RUN_RE='^[[:space:]]*run(-shell)?[[:space:]].*sentinel\.tmux'
TPM_RUN_RE='^[[:space:]]*run(-shell)?[[:space:]].*(tpm|tpack)'
INTERVAL_RE='^[[:space:]]*set(-option)?[[:space:]].*status-interval([[:space:]"]|$)'
RIGHT_RE='^[[:space:]]*set(-option)?[[:space:]].*status-right([[:space:]"]|$)'
STATUS_RE="$INTERVAL_RE|$RIGHT_RE|^[[:space:]]*[^#[:space:]].*sentinel_status"

# True if $TMUX_CONF has a line matching the ERE $1. A missing file does not match.
conf_has() { grep -qE "$1" "$TMUX_CONF" 2>/dev/null; }

# Line number of the first (head) or last (tail) line matching the ERE $1, or nothing.
conf_line() { grep -nE "$1" "$TMUX_CONF" 2>/dev/null | "$2" -1 | cut -d: -f1 || true; }

# Backs up $TMUX_CONF once per run, before the first change. Nothing to back up if absent.
backup_conf() {
    if [ "$CONF_BACKED_UP" = 0 ] && [ -f "$TMUX_CONF" ]; then
        cp "$TMUX_CONF" "$TMUX_CONF.backup.$(date +%Y%m%d%H%M%S)"
    fi
    CONF_BACKED_UP=1
}

# Replaces $TMUX_CONF with the file $1 and removes it. cat rather than mv, so the conf
# keeps its own file mode instead of mktemp's 0600.
replace_conf() { cat "$1" > "$TMUX_CONF"; rm -f "$1"; }

# Yes/no question defaulting to yes. End of input counts as no.
ask_yes() {
    local answer
    echo -n "$1"
    read -r answer || answer=n
    [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]
}

IS_TPM=0
if grep -qF "@plugin 'cjkent/tmux-sentinel'" "$TMUX_CONF" 2>/dev/null \
   || grep -qF '@plugin "cjkent/tmux-sentinel"' "$TMUX_CONF" 2>/dev/null; then
    IS_TPM=1
fi

# status-right: offered, never imposed. sentinel.tmux only substitutes a #{sentinel_status}
# placeholder on load, so without one the status bar shows nothing, which looks like a
# broken install. An existing status-right is kept, and our segment is appended to it.
if conf_has 'sentinel_status|status_client\.sh'; then
    info "status bar already shows the agent summary"
elif ask_yes "  Add status-interval 5 and a #{sentinel_status} placeholder to status-right in ~/.tmux.conf? [Y/n] "; then
    if conf_has "$RIGHT_RE"; then
        RIGHT_NEW='set -ga status-right " #{sentinel_status}"'
        warn "Your existing status-right was kept — #{sentinel_status} is appended to it"
    else
        RIGHT_NEW='set -g status-right "#{sentinel_status} %H:%M"'
    fi
    BLOCK=$(mktemp)
    {
        if ! grep -qF "$MARKER" "$TMUX_CONF" 2>/dev/null; then
            echo ""
            echo "$MARKER: agent summary in the status bar. sentinel.tmux fills in #{sentinel_status} on load."
        fi
        conf_has "$INTERVAL_RE" || echo "set -g status-interval 5"
        echo "$RIGHT_NEW"
    } > "$BLOCK"

    backup_conf
    TPM_LINE=$(conf_line "$TPM_RUN_RE" head)
    if [ "$IS_TPM" = 1 ] && [ -n "$TPM_LINE" ]; then
        # Above the TPM run line, so the options exist when sentinel.tmux is loaded by TPM.
        TMP=$(mktemp)
        awk -v n="$TPM_LINE" -v blk="$BLOCK" \
            'NR == n { while ((getline l < blk) > 0) print l } { print }' "$TMUX_CONF" > "$TMP"
        replace_conf "$TMP"
        info "Added to $TMUX_CONF, above the TPM run line"
    else
        cat "$BLOCK" >> "$TMUX_CONF"
        info "Added to $TMUX_CONF"
        if [ "$IS_TPM" = 1 ]; then
            warn "No TPM run line found in $TMUX_CONF — check the status lines load before tpm"
        fi
    fi
    rm -f "$BLOCK"
else
    warn "Skipped — add #{sentinel_status} to your status-right when you want it"
fi

if [ "$IS_TPM" = 1 ]; then
    info "TPM plugin entry found — tmux config is handled by sentinel.tmux"
elif conf_has "$RUN_RE"; then
    info "sentinel.tmux already sourced from $TMUX_CONF"
else
    echo "  tmux-sentinel's tmux config (keybinds, status bar, hooks) lives in"
    echo "  sentinel.tmux. Add it to $TMUX_CONF so it loads on every tmux start?"
    if ask_yes "  Add it? [Y/n] "; then
        # Appended last, after the status-right lines above, because sentinel.tmux
        # substitutes the placeholder when it loads. It also runs after any plugin manager:
        # an unindexed set-hook in another plugin resets the whole hook array for that
        # event, and whoever runs last wins.
        backup_conf
        {
            echo ""
            echo "$MARKER: loads keybinds, status bar and visit-tracking hooks."
            echo "# Keep this after any plugin manager (e.g. TPM's run script) and after"
            echo "# status-right — see the project README."
            echo "$PLUGIN_LINE"
        } >> "$TMUX_CONF"
        info "Added to $TMUX_CONF"
    else
        warn "Skipped — run 'tmux run-shell $REPO_DIR/sentinel.tmux' to load it manually"
    fi
fi

# sentinel.tmux must load after status-right is set. Non-TPM: the run-shell line must be
# the last sentinel line. TPM: status-right must come before the TPM run line.
if [ "$IS_TPM" = 1 ]; then
    RUN_LINE=$(conf_line "$TPM_RUN_RE" head)
else
    RUN_LINE=$(conf_line "$RUN_RE" head)
fi
LAST_STATUS=$(conf_line "$STATUS_RE" tail)
if [ -n "$RUN_LINE" ] && [ -n "$LAST_STATUS" ] && [ "$LAST_STATUS" -gt "$RUN_LINE" ]; then
    if [ "$IS_TPM" = 1 ]; then
        warn "A status bar setting in $TMUX_CONF comes after the TPM run line — move it above that line"
    else
        warn "The sentinel.tmux line in $TMUX_CONF comes before a status bar setting — it must load last"
        if ask_yes "  Move the run-shell line to the end of $TMUX_CONF? [Y/n] "; then
            backup_conf
            TMP=$(mktemp)
            awk -v n="$RUN_LINE" 'NR == n { moved = $0; next } { print } END { print moved }' \
                "$TMUX_CONF" > "$TMP"
            replace_conf "$TMP"
            info "Moved the run-shell line to the end of $TMUX_CONF"
        else
            warn "Left in place — the status bar will not show the summary until it is moved"
        fi
    fi
fi

# Load it now so this session is configured without needing a restart.
tmux run-shell "$REPO_DIR/sentinel.tmux" 2>/dev/null \
    && info "Loaded into the running tmux server" \
    || warn "Could not load into the running server (is tmux running?)"

step "Setup complete!"
echo ""
echo -e "  Agent hooks: installed (status tracking, bell, unseen flags)"
echo -e "  tmux config: ${BOLD}sentinel.tmux${NC}"
echo ""
echo -e "  Picker keys (defaults, all without the tmux prefix):"
echo -e "    ${BOLD}C-Space${NC}  unseen  — triage: what needs you, first"
echo -e "    ${BOLD}M-Space${NC}  session — grouped by session"
echo -e "    ${BOLD}C-Tab${NC}    recent  — most recently visited"
echo -e "  In the picker: ${BOLD}?${NC} preview · ${BOLD}ctrl-x${NC} close pane · ${BOLD}Alt-u/s/r${NC} switch mode"
echo ""
echo -e "  Change keys, popup size and more with ${BOLD}@sentinel-*${NC} options in $TMUX_CONF:"
echo "    set -g @sentinel-key-unseen  'C-Space'   # or 'none' to disable"
echo "    set -g @sentinel-popup-width '85%'"
echo -e "  Full list: see the Configuration section of the README."
echo ""
echo -e "  Display settings (column widths, preview): ${BOLD}bin/edit-config.sh${NC}"
echo ""
echo -e "  To remove hooks: ${BOLD}$(basename "$0") --remove-hooks${NC}"
