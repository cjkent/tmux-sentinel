"""
Formatting utilities for tmux-agents.

Handles column alignment, ANSI color codes, elapsed time formatting,
and status label rendering. Used by both the picker and status bar.
"""
from __future__ import annotations

import time

# ANSI color codes
GREEN = "\033[32m"
BLUE = "\033[34m"
PURPLE = "\033[35m"
RED = "\033[31m"
RESET = "\033[0m"

# Status label colors
STATUS_COLORS = {
    "idle": GREEN,
    "working": BLUE,
    "waiting": PURPLE,
    "error": RED,
}


def elapsed(timestamp: int) -> str:
    """Format a unix timestamp as elapsed duration (e.g. '5s', '3m', '1h30m')."""
    diff = int(time.time()) - timestamp
    if diff < 0:
        diff = 0
    if diff < 60:
        return f"{diff}s"
    if diff < 3600:
        return f"{diff // 60}m"
    return f"{diff // 3600}h{diff % 3600 // 60}m"


def status_label(status: str) -> str:
    """Return a plain-text status label like [IDL], [WRK], etc."""
    labels = {
        "idle": "[IDL]",
        "working": "[WRK]",
        "waiting": "[WAI]",
        "error": "[ERR]",
    }
    return labels.get(status, "[---]")


def colorize_status(label: str, status: str) -> str:
    """Wrap a status label in ANSI color codes based on the status value."""
    color = STATUS_COLORS.get(status, "")
    if color:
        return f"{color}{label}{RESET}"
    return label


def colorize(text: str, color: str) -> str:
    """Wrap text in an ANSI color code."""
    return f"{color}{text}{RESET}"


def _display_width(s: str) -> int:
    """Return the display width of a string, accounting for wide characters."""
    import unicodedata
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def align_columns(rows: list[list[str]]) -> list[str]:
    """
    Pad columns to equal width across all rows, returning aligned strings.

    Each row is a list of column values. Columns are left-aligned and
    separated by two spaces. The last column is not padded.
    """
    if not rows:
        return []
    # Find max display width for each column (excluding the last)
    num_cols = max(len(row) for row in rows)
    widths = [0] * num_cols
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], _display_width(val))
    # Format each row
    result = []
    for row in rows:
        parts = []
        for i, val in enumerate(row):
            if i < len(row) - 1:
                pad = widths[i] - _display_width(val)
                parts.append(val + " " * pad)
            else:
                parts.append(val)  # don't pad last column
        result.append("  ".join(parts))
    return result
