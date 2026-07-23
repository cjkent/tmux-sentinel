#!/bin/bash
# tmux-agents setup
#
# Configures tmux and Kiro CLI for agent status tracking. Does three things:
#   1. Injects hook entries into Kiro agent JSON configs (all 5 lifecycle events)
#   2. Configures tmux options (bell monitoring, status bar, keybinding)
#   3. Creates the status directory (~/.tmux-agents/status/)
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
HOOK_CMD="PYTHONPATH=$REPO_DIR python3 -S $REPO_DIR/tmux_agents/hook.py"
AGENTS_DIR="$HOME/.kiro/agents"
STATUS_DIR="$HOME/.tmux-agents/status"

# Interactive checkbox picker with keyboard navigation.
# Renders a list of items with [x] checkboxes. All items start checked.
# Controls: ↑↓ navigate, space toggle, a select all, n select none, enter confirm.
# Sets PICKER_RESULT to space-separated list of selected indices (0-based).
# Uses tput to hide cursor during redraws and single-shot printf for flicker-free rendering.
run_picker() {
    local prompt="$1"; shift
    local labels=("$@")
    local count=${#labels[@]}
    local checked=() cursor=0

    for ((i=0; i<count; i++)); do checked[$i]=1; done

    # Hide cursor, restore on exit
    tput civis 2>/dev/null
    trap 'tput cnorm 2>/dev/null' RETURN

    echo "  $prompt"

    # Initial draw
    local buf=""
    for ((i=0; i<count; i++)); do
        local m="[ ]" p="  "
        [ "${checked[$i]}" -eq 1 ] && m="[x]"
        [ "$i" -eq "$cursor" ] && p="▸ "
        buf+="${p}${m} ${labels[$i]}"$'\n'
    done
    buf+="  ↑↓ move · space toggle · a all · n none · enter confirm"
    printf '%s\n' "$buf"

    while true; do
        IFS= read -rsn1 key
        case "$key" in
            $'\x1b')
                read -rsn2 seq
                case "$seq" in
                    '[A') ((cursor > 0)) && ((cursor--)) || true ;;
                    '[B') ((cursor < count - 1)) && ((cursor++)) || true ;;
                esac ;;
            ' ') [ "${checked[$cursor]}" -eq 1 ] && checked[$cursor]=0 || checked[$cursor]=1 ;;
            'a') for ((i=0; i<count; i++)); do checked[$i]=1; done ;;
            'n') for ((i=0; i<count; i++)); do checked[$i]=0; done ;;
            '') break ;;
            *) continue ;;
        esac

        # Redraw: move up, rebuild buffer, print in one shot
        printf '\033[%dA' "$((count + 1))"
        buf=""
        for ((i=0; i<count; i++)); do
            local m="[ ]" p="  "
            [ "${checked[$i]}" -eq 1 ] && m="[x]"
            [ "$i" -eq "$cursor" ] && p="▸ "
            buf+="\033[2K${p}${m} ${labels[$i]}"$'\n'
        done
        buf+="\033[2K  ↑↓ move · space toggle · a all · n none · enter confirm"
        printf '%b\n' "$buf"
    done

    PICKER_RESULT=""
    for ((i=0; i<count; i++)); do
        [ "${checked[$i]}" -eq 1 ] && PICKER_RESULT+="$i "
    done
}

# Check if an agent config already has our hooks in all 5 events.
# Matches hooks whose command path contains "tmux-agents/...notify.sh".
# Returns 0 (true) if all 5 events have our hook, 1 (false) otherwise.
has_hooks() {
    local f="$1"
    local count
    count=$(jq '[.hooks.agentSpawn, .hooks.userPromptSubmit, .hooks.preToolUse, .hooks.postToolUse, .hooks.stop | select(. != null) | map(select(.command | test("tmux_agents.*hook\\.py"))) | select(length > 0)] | length' "$f" 2>/dev/null)
    [ "$count" -eq 5 ]
}

# --- Remove hooks mode ---
# Strips tmux-agents hook entries from selected agent configs.
# Uses jq to filter out entries whose command contains "notify.sh",
# then cleans up empty hook arrays and objects.
if [ "${1:-}" = "--remove-hooks" ]; then
    echo -e "${BOLD}tmux-agents — remove hooks${NC}"
    echo ""

    HOOKED=()
    HOOKED_LABELS=()
    for f in "$AGENTS_DIR"/*.json; do
        [ -f "$f" ] || continue
        jq empty "$f" 2>/dev/null || continue
        jq -e '.hooks' "$f" >/dev/null 2>&1 || continue
        if jq -e '[.hooks[][] | select(.command | test("notify.sh|hook.py"))] | length > 0' "$f" >/dev/null 2>&1; then
            HOOKED+=("$f")
            HOOKED_LABELS+=("$(basename "$f" .json)")
        fi
    done

    if [ "${#HOOKED[@]}" -eq 0 ]; then
        info "No agents have tmux-agents hooks"
        exit 0
    fi

    run_picker "Select agents to remove hooks from:" "${HOOKED_LABELS[@]}"

    EVENTS="agentSpawn userPromptSubmit preToolUse postToolUse stop"
    REMOVED=0
    for idx in $PICKER_RESULT; do
        f="${HOOKED[$idx]}"
        jq_filter='.'
        for evt in $EVENTS; do
            jq_filter+=" | if .hooks.${evt} then .hooks.${evt} = [.hooks.${evt}[] | select(.command | test(\"notify.sh|hook.py\") | not)] else . end"
            jq_filter+=" | if .hooks.${evt} == [] then del(.hooks.${evt}) else . end"
        done
        jq_filter+=' | if .hooks == {} then del(.hooks) else . end'
        result=$(jq "$jq_filter" "$f" 2>/dev/null) && echo "$result" > "$f" && ((REMOVED++)) || true
    done
    info "Removed hooks from $REMOVED agents"

    # Also remove from Claude Code settings
    CLAUDE_SETTINGS="$HOME/.claude/settings.json"
    if [ -f "$CLAUDE_SETTINGS" ] && jq -e '.hooks // {} | to_entries[] | .value[]?.hooks[]? | select(.command | test("tmux_agents.*hook\\.py"))' "$CLAUDE_SETTINGS" >/dev/null 2>&1; then
        echo -n "  Also remove hooks from Claude Code settings? [Y/n] "
        read -r answer
        if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
            CC_EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse Stop"
            jq_filter='.'
            for evt in $CC_EVENTS; do
                jq_filter+=" | if .hooks.${evt} then .hooks.${evt} = [.hooks.${evt}[] | .hooks = [.hooks[] | select(.command | test(\"tmux_agents.*hook\\\\.py\") | not)] | select(.hooks | length > 0)] else . end"
                jq_filter+=" | if .hooks.${evt} == [] then del(.hooks.${evt}) else . end"
            done
            jq_filter+=' | if .hooks == {} then del(.hooks) else . end'
            result=$(jq "$jq_filter" "$CLAUDE_SETTINGS" 2>/dev/null) && echo "$result" > "$CLAUDE_SETTINGS"
            info "Removed hooks from Claude Code settings"
        fi
    fi
    exit 0
fi

# --- Main setup ---
echo -e "${BOLD}tmux-agents setup${NC}"
echo ""

# 1. Check dependencies
step "Checking dependencies..."
for cmd in jq fzf tmux; do
    if command -v "$cmd" &>/dev/null; then
        info "$cmd found"
    else
        error "$cmd not found — install it first"
        exit 1
    fi
done

# 2. Create directories
step "Creating directories..."
mkdir -p "$STATUS_DIR"
info "Status directory: $STATUS_DIR"

# 3. Find eligible agents
step "Finding Kiro agents..."
if [ ! -d "$AGENTS_DIR" ]; then
    warn "No agents directory at $AGENTS_DIR"
    exit 0
fi

ELIGIBLE=()
ELIGIBLE_LABELS=()
for f in "$AGENTS_DIR"/*.json; do
    [ -f "$f" ] || continue
    jq empty "$f" 2>/dev/null || continue
    has_hooks "$f" && continue
    ELIGIBLE+=("$f")
    ELIGIBLE_LABELS+=("$(basename "$f" .json)")
done

if [ "${#ELIGIBLE[@]}" -eq 0 ]; then
    info "All agents already have tmux-agents hooks"
else
    run_picker "Select agents to add tmux-agents hooks to:" "${ELIGIBLE_LABELS[@]}"

    # Backup agent configs before modifying, then inject our hook into
        # all 5 lifecycle events. The jq filter is idempotent — it only adds
        # our hook if it's not already present (checked via regex on the command path).
    SELECTED=()
    for idx in $PICKER_RESULT; do
        SELECTED+=("${ELIGIBLE[$idx]}")
    done

    if [ "${#SELECTED[@]}" -gt 0 ]; then
        BACKUP_DIR="$AGENTS_DIR.backup.$(date +%Y%m%d%H%M%S)"
        cp -r "$AGENTS_DIR/" "$BACKUP_DIR"
        info "Backup created at $BACKUP_DIR"

        EVENTS="agentSpawn userPromptSubmit preToolUse postToolUse stop"
        UPDATED=0
        for f in "${SELECTED[@]}"; do
            jq_filter='. | if .hooks == null then .hooks = {} else . end'
            for evt in $EVENTS; do
                jq_filter+=" | if .hooks.${evt} then .hooks.${evt} = [.hooks.${evt}[] | select(.command | test(\"tmux-agents/.*notify\\\\.sh\") | not)] else . end"
                jq_filter+=" | if (.hooks.${evt} // [] | map(select(.command | test(\"tmux_agents.*hook\\\\.py\"))) | length) == 0"
                jq_filter+=" then .hooks.${evt} = (.hooks.${evt} // []) + [{\"command\": \"${HOOK_CMD}\", \"description\": \"tmux-agents status tracking\"}]"
                jq_filter+=" else . end"
            done
            result=$(jq "$jq_filter" "$f" 2>/dev/null) && echo "$result" > "$f" && ((UPDATED++)) || true
        done
        info "Hooked $UPDATED agents"
    else
        warn "No agents selected"
    fi
fi

# 4. Configure Claude Code hooks
step "Configuring Claude Code..."
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CC_EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse Stop"

if [ -f "$CLAUDE_SETTINGS" ]; then
    if jq -e '.hooks // {} | to_entries[] | .value[]?.hooks[]? | select(.command | test("tmux_agents.*hook\\.py"))' "$CLAUDE_SETTINGS" >/dev/null 2>&1; then
        info "Claude Code hooks already configured"
    else
        echo -n "  Add tmux-agents hooks to Claude Code? [Y/n] "
        read -r answer
        if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
            cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.backup.$(date +%Y%m%d%H%M%S)"

            jq_filter='. | if .hooks == null then .hooks = {} else . end'
            for evt in $CC_EVENTS; do
                jq_filter+=" | .hooks.${evt} = (.hooks.${evt} // [])"
                jq_filter+=" | if ([.hooks.${evt}[] | .hooks[]? | select(.command | test(\"tmux_agents.*hook\\\\.py\"))] | length) == 0"
                jq_filter+=" then .hooks.${evt} += [{\"matcher\": \"\", \"hooks\": [{\"type\": \"command\", \"command\": \"${HOOK_CMD}\"}]}]"
                jq_filter+=" else . end"
            done

            result=$(jq "$jq_filter" "$CLAUDE_SETTINGS" 2>/dev/null) && echo "$result" > "$CLAUDE_SETTINGS"
            info "Claude Code hooks configured in $CLAUDE_SETTINGS"
        fi
    fi
else
    echo -n "  Create Claude Code settings with tmux-agents hooks? [Y/n] "
    read -r answer
    if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
        mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
        echo '{}' > "$CLAUDE_SETTINGS"

        jq_filter='. | .hooks = {}'
        for evt in $CC_EVENTS; do
            jq_filter+=" | .hooks.${evt} = [{\"matcher\": \"\", \"hooks\": [{\"type\": \"command\", \"command\": \"${HOOK_CMD}\"}]}]"
        done

        result=$(jq "$jq_filter" "$CLAUDE_SETTINGS" 2>/dev/null) && echo "$result" > "$CLAUDE_SETTINGS"
        info "Created $CLAUDE_SETTINGS with hooks"
    fi
fi

# Configure tmux options for agent monitoring.
# These are runtime settings — they don't persist across tmux restarts.
# To make them permanent, add them to ~/.tmux.conf.
step "Configuring tmux..."
# Bell monitoring: when an agent finishes, it rings the bell (\a).
# monitor-bell + bell-action other = highlight the window tab for bells in OTHER windows.
# window-status-bell-style = how the highlighted tab looks (red + bold).
tmux set -g monitor-bell on 2>/dev/null && info "monitor-bell on" || warn "Could not set monitor-bell"
tmux set -g bell-action other 2>/dev/null && info "bell-action other" || warn "Could not set bell-action"
tmux set -g window-status-bell-style 'fg=red,bold' 2>/dev/null && info "window-status-bell-style set" || warn "Could not set bell style"
tmux set -g status-interval 5 2>/dev/null && info "status-interval 5s" || warn "Could not set status-interval"
# status-right: run the status bar script every status-interval seconds.
# The #() syntax tells tmux to execute the command and insert its output.
tmux set -g status-right "#($REPO_DIR/bin/status_client.sh '#{pane_id}') %H:%M" 2>/dev/null && info "status-right configured" || warn "Could not set status-right"
# Bind Ctrl+b a to open the agent picker popup
tmux bind-key a display-popup -w 70% -h 50% -E "PYTHONPATH=$REPO_DIR python3 -S $REPO_DIR/tmux_agents/picker.py" 2>/dev/null && info "Ctrl+b a bound to agent picker" || warn "Could not bind key"

step "Setup complete!"
echo ""
echo -e "  Picker:     ${BOLD}Ctrl+b a${NC}"
echo -e "  Status bar: agent summary on the right"
echo -e "  Bell:       window tab highlights red on done/waiting/error"
echo ""
echo -e "  To remove hooks: ${BOLD}$(basename "$0") --remove-hooks${NC}"
