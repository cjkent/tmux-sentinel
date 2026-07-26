"""Tests for TOML screen-scrape manifest loading and classification."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmux_sentinel_daemon.manifests import load_all_manifests, classify

_RULES = load_all_manifests()["claude"]


def _c(tail):
    return classify(tail, _RULES)


def test_loads_claude_and_kiro():
    manifests = load_all_manifests()
    assert "claude" in manifests
    assert "kiro" in manifests
    assert len(manifests["claude"]) > 0
    print("  ✓ test_loads_claude_and_kiro")


def test_working_early_turn_no_token_counter():
    # Early in a turn CC shows an elapsed timer but no token counter yet.
    # Must NOT be classified idle (regression: showed [IDL] while working).
    tail = "✽ Working… (3s)\n❯\n  ⏵⏵ auto mode on (shift+tab to cycle)"
    assert _c(tail) is None
    print("  ✓ test_working_early_turn_no_token_counter")


def test_working_with_token_counter():
    tail = "✽ Working… (57s · ↓ 432 tokens)\n❯\n  ⏵⏵ auto mode on (shift+tab to cycle)"
    assert _c(tail) is None
    print("  ✓ test_working_with_token_counter")


def test_working_minutes_elapsed():
    tail = "✽ Crunching… (5m 7s · ↑ 1.2k tokens)\n  ? for shortcuts"
    assert _c(tail) is None
    print("  ✓ test_working_minutes_elapsed")


def test_working_old_style_esc_to_interrupt():
    tail = "esc to interrupt\n❯\n  ⏵⏵ auto mode on (shift+tab to cycle)"
    assert _c(tail) is None
    print("  ✓ test_working_old_style_esc_to_interrupt")


def test_idle_mode_footer():
    tail = "❯\n  ⏵⏵ auto mode on (shift+tab to cycle)"
    assert _c(tail) == "idle"
    print("  ✓ test_idle_mode_footer")


def test_idle_shortcuts_footer():
    tail = "❯\n  ? for shortcuts"
    assert _c(tail) == "idle"
    print("  ✓ test_idle_shortcuts_footer")


def test_waiting_approval_prompt():
    tail = "Do you want to proceed?\n❯ 1. Yes\n  2. No"
    assert _c(tail) == "waiting"
    print("  ✓ test_waiting_approval_prompt")


def test_waiting_question_selection():
    tail = "Which option?\n  1. A\n  2. B\nEnter to select · Esc to cancel"
    assert _c(tail) == "waiting"
    print("  ✓ test_waiting_question_selection")


def test_unknown_returns_none():
    tail = "some random build output\nnothing agent-like here"
    assert _c(tail) is None
    print("  ✓ test_unknown_returns_none")


if __name__ == "__main__":
    test_loads_claude_and_kiro()
    test_working_early_turn_no_token_counter()
    test_working_with_token_counter()
    test_working_minutes_elapsed()
    test_working_old_style_esc_to_interrupt()
    test_idle_mode_footer()
    test_idle_shortcuts_footer()
    test_waiting_approval_prompt()
    test_waiting_question_selection()
    test_unknown_returns_none()
    print("\nAll tests passed")
