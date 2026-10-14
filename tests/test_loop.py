"""Tests for suppressing the unseen flag on intermediate /loop iterations."""
from __future__ import annotations

import json
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmux_sentinel.loop import loop_continues, _TAIL_BYTES
from tmux_sentinel_daemon.state import DaemonState
from tmux_sentinel.status import IDLE, WAITING


# --- transcript builders, shaped like a real dynamic /loop session ---

def _tool_use(name, **inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def _text(s="done"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": s}]}}


def _prompt(s):
    return {"type": "user", "message": {"content": s}}


def _tool_result():
    return {"type": "user", "message": {"content": [{"type": "tool_result"}]}}


_TURN_END = {"type": "system", "subtype": "turn_duration"}


def _write(records, raw_lines=()):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for r in records:
        f.write(json.dumps(r) + "\n")
    for line in raw_lines:
        f.write(line + "\n")
    f.close()
    return f.name


def _check(records, **kw):
    path = _write(records, **kw)
    try:
        return loop_continues(path)
    finally:
        os.unlink(path)


# A finished earlier iteration, so every case also proves the scan stops at the
# previous turn's boundary rather than reading back into it.
_EARLIER_ITERATION = [
    _prompt("/loop check the CR"),
    _tool_use("ScheduleWakeup", delaySeconds=270),
    _tool_result(), _text("still building"), _TURN_END,
]


def test_iteration_that_scheduled_another_continues():
    assert _check([
        _prompt("/loop check the CR"),
        _tool_use("ScheduleWakeup", delaySeconds=270, noop=True),
        _tool_result(), _text("still building"),
    ]) is True
    print("  ✓ test_iteration_that_scheduled_another_continues")


def test_final_iteration_with_stop_true_does_not_continue():
    assert _check(_EARLIER_ITERATION + [
        _prompt("/loop check the CR"),
        _tool_use("ScheduleWakeup", stop=True),
        _tool_result(), _text("passed, stopping"),
    ]) is False
    print("  ✓ test_final_iteration_with_stop_true_does_not_continue")


def test_final_iteration_without_a_call_does_not_continue():
    assert _check(_EARLIER_ITERATION + [
        _prompt("/loop check the CR"), _text("passed"),
    ]) is False
    print("  ✓ test_final_iteration_without_a_call_does_not_continue")


def test_scan_stops_at_the_previous_turn():
    # The earlier iteration scheduled a wakeup; this turn did not. Reading back past
    # the turn boundary would wrongly report a loop still running.
    assert _check(_EARLIER_ITERATION + [_prompt("an ordinary prompt"), _text("ok")]) is False
    print("  ✓ test_scan_stops_at_the_previous_turn")


def test_ordinary_turn_is_unaffected():
    assert _check([_prompt("fix the bug"), _tool_use("Bash", command="ls"),
                   _tool_result(), _text("fixed")]) is False
    print("  ✓ test_ordinary_turn_is_unaffected")


def test_last_call_in_a_turn_decides():
    assert _check([
        _tool_use("ScheduleWakeup", delaySeconds=270),
        _tool_use("ScheduleWakeup", stop=True), _text("changed my mind"),
    ]) is False
    print("  ✓ test_last_call_in_a_turn_decides")


def test_prose_mentioning_the_tool_is_not_a_call():
    # The cheap substring filter must not be fooled by text that merely names it.
    assert _check([_prompt("why did ScheduleWakeup fire turn_duration"),
                   _text("because ScheduleWakeup is a tool")]) is False
    print("  ✓ test_prose_mentioning_the_tool_is_not_a_call")


def test_fails_towards_notifying():
    assert loop_continues("") is False
    assert loop_continues("/nonexistent/transcript.jsonl") is False
    assert _check([_text("x")], raw_lines=["{not json ScheduleWakeup"]) is False
    print("  ✓ test_fails_towards_notifying")


def test_long_transcript_reads_only_the_tail():
    # Padding larger than the window pushes everything before the tail out of reach;
    # the call near the end must still be found, from a mid-line window start.
    filler = [_text("x" * 2000) for _ in range(_TAIL_BYTES // 2000 + 50)]
    assert _check(filler + [_tool_use("ScheduleWakeup", delaySeconds=270),
                            _tool_result(), _text("waiting")]) is True
    print("  ✓ test_long_transcript_reads_only_the_tail")


# --- the daemon's use of it ---

def _stop(state, transcript, response=""):
    event = {"hookEventName": "Stop", "cwd": "/tmp", "transcript_path": transcript}
    if response:
        event["assistant_response"] = response
    state.apply_hook_event(event, pane_id="100")
    return state.get("100")


def test_daemon_stop_mid_loop_is_not_unseen():
    path = _write([_tool_use("ScheduleWakeup", delaySeconds=270), _text("waiting")])
    try:
        ps = _stop(DaemonState(), path)
        assert ps.status == IDLE and ps.unseen is False
    finally:
        os.unlink(path)
    print("  ✓ test_daemon_stop_mid_loop_is_not_unseen")


def test_daemon_stop_at_loop_end_is_unseen():
    path = _write([_tool_use("ScheduleWakeup", stop=True), _text("done")])
    try:
        assert _stop(DaemonState(), path).unseen is True
    finally:
        os.unlink(path)
    print("  ✓ test_daemon_stop_at_loop_end_is_unseen")


def test_daemon_question_mid_loop_is_still_unseen():
    # A loop cannot answer a question for you, so that one is worth the interruption.
    path = _write([_tool_use("ScheduleWakeup", delaySeconds=270), _text("which one?")])
    try:
        ps = _stop(DaemonState(), path, response="Which branch should I use?")
        assert ps.status == WAITING and ps.unseen is True
    finally:
        os.unlink(path)
    print("  ✓ test_daemon_question_mid_loop_is_still_unseen")


def test_daemon_stop_without_a_transcript_is_unseen():
    # Kiro sends no transcript_path; behaviour there must not change.
    assert _stop(DaemonState(), "").unseen is True
    print("  ✓ test_daemon_stop_without_a_transcript_is_unseen")


if __name__ == "__main__":
    test_iteration_that_scheduled_another_continues()
    test_final_iteration_with_stop_true_does_not_continue()
    test_final_iteration_without_a_call_does_not_continue()
    test_scan_stops_at_the_previous_turn()
    test_ordinary_turn_is_unaffected()
    test_last_call_in_a_turn_decides()
    test_prose_mentioning_the_tool_is_not_a_call()
    test_fails_towards_notifying()
    test_long_transcript_reads_only_the_tail()
    test_daemon_stop_mid_loop_is_not_unseen()
    test_daemon_stop_at_loop_end_is_unseen()
    test_daemon_question_mid_loop_is_still_unseen()
    test_daemon_stop_without_a_transcript_is_unseen()
    print("\nAll tests passed")
