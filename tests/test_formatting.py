"""Tests for tmux_sentinel.formatting module."""
import time

from tmux_sentinel.formatting import (
    elapsed, status_label, colorize_status, align_columns,
    GREEN, BLUE, PURPLE, RED, RESET,
)


def test_elapsed_seconds():
    now = int(time.time())
    assert elapsed(now - 30) == "30s"


def test_elapsed_minutes():
    now = int(time.time())
    assert elapsed(now - 300) == "5m"


def test_elapsed_hours():
    now = int(time.time())
    assert elapsed(now - 5400) == "1h30m"


def test_elapsed_future():
    """Future timestamps should show 0s, not negative."""
    assert elapsed(int(time.time()) + 100) == "0s"


def test_status_labels():
    assert status_label("idle") == "[IDL]"
    assert status_label("working") == "[WRK]"
    assert status_label("waiting") == "[WAI]"
    assert status_label("error") == "[ERR]"
    assert status_label("unknown") == "[---]"


def test_colorize_status():
    result = colorize_status("[IDL]", "idle")
    assert result == f"{GREEN}[IDL]{RESET}"
    result = colorize_status("[ERR]", "error")
    assert result == f"{RED}[ERR]{RESET}"


def test_colorize_unknown():
    result = colorize_status("[---]", "unknown")
    assert result == "[---]"  # no color for unknown


def test_align_columns_basic():
    rows = [
        ["a", "short", "x"],
        ["ab", "very long value", "y"],
    ]
    result = align_columns(rows)
    assert len(result) == 2
    # First columns should be padded to same width
    assert result[0].startswith("a ")
    assert result[1].startswith("ab")
    # Last column should not be padded
    assert result[0].endswith("x")
    assert result[1].endswith("y")


def test_align_columns_empty():
    assert align_columns([]) == []


def test_align_columns_single_column():
    rows = [["hello"], ["world"]]
    result = align_columns(rows)
    assert result == ["hello", "world"]


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
