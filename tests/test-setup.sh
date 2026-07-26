#!/bin/bash
# Tests for setup.sh hook injection and removal logic
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  ✓ $desc"
        ((PASS++)) || true
    else
        echo "  ✗ $desc"
        echo "    expected: $expected"
        echo "    actual:   $actual"
        ((FAIL++)) || true
    fi
}

HOOK_CMD="PYTHONPATH=$SCRIPT_DIR python3 $SCRIPT_DIR/tmux_sentinel/hook.py"
EVENTS="agentSpawn userPromptSubmit preToolUse postToolUse stop"

# --- Test: inject hooks into a bare agent config ---
echo "Test: inject hooks into bare config"
TEST_DIR=$(mktemp -d)
cat > "$TEST_DIR/test-agent.json" << 'EOF'
{
  "name": "test",
  "tools": ["shell"]
}
EOF

jq_filter='. | if .hooks == null then .hooks = {} else . end'
for evt in $EVENTS; do
    jq_filter+=" | if .hooks.${evt} then .hooks.${evt} = [.hooks.${evt}[] | select(.command | test(\"tmux-sentinel/.*notify\\\\.sh\") | not)] else . end"
    jq_filter+=" | if (.hooks.${evt} // [] | map(select(.command | test(\"tmux_sentinel.*hook\\\\.py\"))) | length) == 0"
    jq_filter+=" then .hooks.${evt} = (.hooks.${evt} // []) + [{\"command\": \"${HOOK_CMD}\", \"description\": \"tmux-sentinel status tracking\"}]"
    jq_filter+=" else . end"
done
result=$(jq "$jq_filter" "$TEST_DIR/test-agent.json") && echo "$result" > "$TEST_DIR/test-agent.json"

for evt in $EVENTS; do
    count=$(jq ".hooks.${evt} | length" "$TEST_DIR/test-agent.json")
    assert_eq "hook added for $evt" "1" "$count"
done
assert_eq "hook command correct" "$HOOK_CMD" "$(jq -r '.hooks.agentSpawn[0].command' "$TEST_DIR/test-agent.json")"

# --- Test: idempotent — running again doesn't duplicate ---
echo "Test: idempotent injection"
result=$(jq "$jq_filter" "$TEST_DIR/test-agent.json") && echo "$result" > "$TEST_DIR/test-agent.json"
for evt in $EVENTS; do
    count=$(jq ".hooks.${evt} | length" "$TEST_DIR/test-agent.json")
    assert_eq "still 1 hook for $evt" "1" "$count"
done

# --- Test: preserves existing hooks ---
echo "Test: preserves existing hooks"
cat > "$TEST_DIR/existing.json" << 'EOF'
{
  "name": "existing",
  "hooks": {
    "agentSpawn": [{"command": "/some/other/hook.sh", "description": "other"}]
  }
}
EOF
result=$(jq "$jq_filter" "$TEST_DIR/existing.json") && echo "$result" > "$TEST_DIR/existing.json"
assert_eq "existing hook preserved" "/some/other/hook.sh" "$(jq -r '.hooks.agentSpawn[0].command' "$TEST_DIR/existing.json")"
assert_eq "our hook added" "$HOOK_CMD" "$(jq -r '.hooks.agentSpawn[1].command' "$TEST_DIR/existing.json")"

# --- Test: remove hooks ---
echo "Test: remove hooks"
remove_filter='.'
for evt in $EVENTS; do
    remove_filter+=" | if .hooks.${evt} then .hooks.${evt} = [.hooks.${evt}[] | select(.command | test(\"notify.sh|hook.py\") | not)] else . end"
    remove_filter+=" | if .hooks.${evt} == [] then del(.hooks.${evt}) else . end"
done
remove_filter+=' | if .hooks == {} then del(.hooks) else . end'

# Remove from the bare config (only our hooks)
result=$(jq "$remove_filter" "$TEST_DIR/test-agent.json") && echo "$result" > "$TEST_DIR/test-agent.json"
assert_eq "hooks object removed from bare" "null" "$(jq -r '.hooks // null' "$TEST_DIR/test-agent.json")"

# Remove from config with existing hooks (should keep the other hook)
result=$(jq "$remove_filter" "$TEST_DIR/existing.json") && echo "$result" > "$TEST_DIR/existing.json"
assert_eq "other hook preserved after removal" "/some/other/hook.sh" "$(jq -r '.hooks.agentSpawn[0].command' "$TEST_DIR/existing.json")"
assert_eq "our hook removed" "1" "$(jq '.hooks.agentSpawn | length' "$TEST_DIR/existing.json")"

rm -rf "$TEST_DIR"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
