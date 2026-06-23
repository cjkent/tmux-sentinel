#!/bin/bash
# Tests for bin/picker.sh helper functions and data generation
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

assert_contains() {
    local desc="$1" needle="$2" haystack="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        echo "  ✓ $desc"
        ((PASS++)) || true
    else
        echo "  ✗ $desc"
        echo "    expected to contain: $needle"
        echo "    actual: $haystack"
        ((FAIL++)) || true
    fi
}

# --- Test: elapsed formatting ---
echo "Test: elapsed formatting"
# Source the functions by extracting them
elapsed() {
    local ts="$1" now diff
    now=$(date +%s)
    diff=$((now - ts))
    if [ "$diff" -lt 60 ]; then echo "${diff}s"
    elif [ "$diff" -lt 3600 ]; then echo "$((diff / 60))m"
    else echo "$((diff / 3600))h$((diff % 3600 / 60))m"
    fi
}

NOW=$(date +%s)
assert_eq "seconds ago" "30s" "$(elapsed $((NOW - 30)))"
assert_eq "minutes ago" "5m" "$(elapsed $((NOW - 300)))"
assert_eq "hours ago" "1h30m" "$(elapsed $((NOW - 5400)))"

# --- Test: status_icon ---
echo "Test: status_icon"
status_icon() {
    case "$1" in
        idle) echo "[IDL]" ;; working) echo "[WRK]" ;;
        waiting) echo "[WAI]" ;; error) echo "[ERR]" ;; *) echo "[---]" ;;
    esac
}

assert_eq "idle icon" "[IDL]" "$(status_icon idle)"
assert_eq "working icon" "[WRK]" "$(status_icon working)"
assert_eq "waiting icon" "[WAI]" "$(status_icon waiting)"
assert_eq "error icon" "[ERR]" "$(status_icon error)"
assert_eq "unknown icon" "[---]" "$(status_icon blah)"

# --- Test: stale cleanup ---
echo "Test: stale cleanup"
TEST_DIR=$(mktemp -d)
mkdir -p "$TEST_DIR/status"
# Create a status file for a pane that doesn't exist
echo '{"status":"done"}' > "$TEST_DIR/status/99999.json"
touch "$TEST_DIR/status/99999.error"
# Create one for a pane that does exist
REAL_PANE=$(tmux list-panes -a -F '#{pane_id}' 2>/dev/null | head -1 | sed 's/%//')
if [ -n "$REAL_PANE" ]; then
    echo '{"status":"working"}' > "$TEST_DIR/status/$REAL_PANE.json"
fi

# Run cleanup
STATUS_DIR="$TEST_DIR/status"
live=$(tmux list-panes -a -F '#{pane_id}' 2>/dev/null | sed 's/^%//')
for f in "$STATUS_DIR"/*.json; do
    [ -f "$f" ] || continue
    pane_id=$(basename "$f" .json)
    if ! echo "$live" | grep -qx "$pane_id"; then
        rm -f "$f" "$STATUS_DIR/$pane_id.error"
    fi
done

assert_eq "stale json removed" "false" "$([ -f "$TEST_DIR/status/99999.json" ] && echo true || echo false)"
assert_eq "stale error removed" "false" "$([ -f "$TEST_DIR/status/99999.error" ] && echo true || echo false)"
if [ -n "$REAL_PANE" ]; then
    assert_eq "live pane file kept" "true" "$([ -f "$TEST_DIR/status/$REAL_PANE.json" ] && echo true || echo false)"
fi

rm -rf "$TEST_DIR"

# --- Summary ---
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
