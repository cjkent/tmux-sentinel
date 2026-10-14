"""
Detect whether a finishing turn is one iteration of a running /loop.

Every /loop iteration is a full turn, so every iteration fires a Stop hook, and a
Stop hook marks the pane unseen and rings the bell. For a loop that checks something
every five minutes that is a notification per check, when the only one worth having is
the one for the final iteration.

The hook cannot ask Claude Code whether another iteration is coming, but the
transcript records it. An iteration that will be followed by another ends its turn
having called ScheduleWakeup; the last one either calls it with stop=true or does not
call it at all. Observed on a live dynamic loop, one turn is:

    assistant  tool_use ScheduleWakeup      <- the signal
    user       tool_result
    assistant  text                         <- the closing message
    attachment hook_success                 <- the Stop hook runs here
    system     stop_hook_summary
    system     turn_duration                <- the turn boundary

The Stop hook runs before that turn's own turn_duration is written, so scanning back
from the end of the file, the first turn_duration met belongs to the previous turn and
marks where this turn began.

Only the dynamic form of /loop is recognised. A fixed-interval loop may schedule
through cron instead, and has not been observed.
"""
from __future__ import annotations

import json
import os

# How much of the transcript's tail to read. Transcripts grow without bound (one
# loop session here is 60MB), so reading the whole file on every Stop would cost more
# than the hook is worth. A turn that calls ScheduleWakeup does so near its end, so a
# window this size covers the turn unless its tool output was enormous.
_TAIL_BYTES = 1 << 20


def _tail_lines(path: str) -> list[str]:
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - _TAIL_BYTES)
        f.seek(start)
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    # A window that starts mid-file starts mid-line; the first fragment is not JSON.
    return lines[1:] if start else lines


def loop_continues(transcript_path: str) -> bool:
    """True if this turn scheduled another iteration of a /loop.

    Fails towards False. A missing path, an unreadable or odd transcript, or a window
    that never reaches this turn's start all mean "do not suppress", because one
    notification too many is a nuisance and one too few is a silently lost result.
    """
    if not transcript_path:
        return False
    try:
        lines = _tail_lines(transcript_path)
    except OSError:
        return False

    for line in reversed(lines):
        # Cheap filter first: most records are neither, and parsing them all is waste.
        if "ScheduleWakeup" not in line and "turn_duration" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") == "system" and rec.get("subtype") == "turn_duration":
            return False  # reached the previous turn without a ScheduleWakeup
        content = rec.get("message", {}).get("content")
        if rec.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in reversed(content):
            if block.get("type") == "tool_use" and block.get("name") == "ScheduleWakeup":
                # stop=true is the loop's own way of ending itself.
                return not block.get("input", {}).get("stop")
    return False
