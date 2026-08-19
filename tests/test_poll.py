"""Tests for tmux_sentinel_daemon.poll — screen-scrape state correction."""
from tmux_sentinel.status import IDLE, WORKING, WAITING
from tmux_sentinel_daemon.poll import _has_working_marker
from tmux_sentinel_daemon.state import DaemonState


# --- _has_working_marker: live turn vs leftover text from a finished one ---

def test_marker_live_working_with_timer():
    assert _has_working_marker("✽ Working… (30s · ↓ 1.2k tokens)")


def test_marker_live_alternate_spinner_verbs():
    # Claude Code rotates the gerund; the timer is what makes it live.
    assert _has_working_marker("✻ Crunching… (5m 7s)")
    assert _has_working_marker("✶ Churning… (12s)")


def test_marker_is_verb_agnostic():
    # The gerund must NOT be enumerated: Claude Code rotates it freely and adds new
    # ones, and an unrecognised verb would leave a working pane stuck showing IDL
    # until its first tool call. Detection keys off the line's shape instead.
    assert _has_working_marker("✽ Frobnicating… (3s)")
    assert _has_working_marker("⠂ Reticulating… (1h2m · ↓ 4k tokens)")


def test_marker_tolerates_leading_whitespace():
    assert _has_working_marker("  ✻ Working… (35s · ↓ 1.1k tokens)")


_BG_FOOTER = "  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents\n  ⏺ main\n"
_BG_RUNNING = _BG_FOOTER + "  ◯ kairos-V123  Analyse this oncall ticket…    1m 3s · ↓ 132.0k tokens"
# A finished agent's row ends in the literal "idle"; the row itself persists.
_BG_FINISHED = _BG_FOOTER + "  ◯ kairos-V123  Analyse this oncall ticket…    idle"
_BG_FINISHED_BLANK = _BG_FOOTER + "  ◯ kairos-V123  Analyse this oncall ticket…"


def test_marker_detects_running_background_agent():
    # The main agent can sit idle at an empty prompt — footer and all — while a
    # background agent works. Without this the pane reads IDL despite work happening.
    # Note the timer is bare ("1m 3s"), not parenthesised, so the spinner patterns
    # can't see it.
    assert _has_working_marker(_BG_RUNNING)


def test_marker_rejects_finished_background_agent():
    # The row *persists* after the agent finishes; the trailing elapsed time is
    # replaced by "idle". Keying off the glyph alone would strand the pane on WORKING.
    assert not _has_working_marker(_BG_FINISHED)
    assert not _has_working_marker(_BG_FINISHED_BLANK)


def test_marker_ignores_the_focus_glyph():
    # ◯ vs ⏺ marks which entry is *focused* in the agent list, not which is running,
    # and they swap as you move between agents. An earlier version of this pattern
    # assumed ◯ meant "running" and was wrong.
    assert _has_working_marker(_BG_RUNNING.replace("◯", "⏺"))
    assert not _has_working_marker(_BG_FINISHED.replace("◯", "⏺"))


def test_marker_rejects_transcript_lines_with_durations():
    # Ordinary transcript output also starts with ⏺ and can mention a duration; only
    # the agent-list rows (name, then a wide gap, then the status) should match.
    assert not _has_working_marker("⏺ Bash(cd /x && sleep 3s)")
    assert not _has_working_marker("⏺ Done in 5s — all tests pass")
    assert not _has_working_marker("⏺ Teammate @kairos-V123 finished")
    assert not _has_working_marker("  ⏺ main")


def test_background_agent_vetoes_the_idle_rule():
    # poll.py demotes a WORKING pane whenever the scrape says idle, so if the manifest
    # still called this idle the pane would flip-flop and raise a spurious unseen flag
    # on every cycle.
    from tmux_sentinel_daemon.manifests import load_all_manifests, classify
    rules = load_all_manifests()["claude"]
    assert classify(_BG_RUNNING, rules) is None
    assert classify(_BG_FINISHED, rules) == IDLE


def test_poll_promotes_pane_with_running_background_agent():
    state = DaemonState()
    state.ensure("7").status = IDLE
    _run_poll_with({"7": _BG_RUNNING}, state)
    assert state.get("7").status == WORKING


def test_poll_leaves_pane_idle_when_background_agent_finished():
    state = DaemonState()
    state.ensure("7").status = IDLE
    _run_poll_with({"7": _BG_FINISHED}, state)
    assert state.get("7").status == IDLE


def test_marker_rejects_pattern_quoted_in_prose():
    # A pane showing text *about* the spinner format (a conversation discussing this
    # very regex, say) must not read as working. The match is anchored to the start
    # of a line, so mid-line prose can't trip it.
    assert not _has_working_marker("some prose about Working… (30s) inline in a sentence")
    assert not _has_working_marker("a live line reads Working… (30s · ↓ 1.2k tokens)")


def test_marker_esc_to_interrupt():
    assert _has_working_marker("some output\n  esc to interrupt\n")


def test_marker_rejects_finished_turn_text():
    # Past-tense summaries linger on screen after a turn ends — these must NOT
    # read as working, or an idle pane would be wrongly promoted.
    assert not _has_working_marker("✻ Crunched for 1m 9s")
    assert not _has_working_marker("✻ Churned for 1h 1m 55s")


def test_marker_rejects_stale_bare_timer():
    # A bare parenthesised duration in scrollback (tool output, HTTP timings) is
    # not evidence of a live turn.
    assert not _has_working_marker("⎿ Received 1KB (200 OK) in (12s)")


def test_marker_rejects_idle_footers():
    assert not _has_working_marker("⏵⏵ auto mode on (shift+tab to cycle) · ← for agents")
    assert not _has_working_marker("⏸ manual mode on · ← for agents")


def test_marker_rejects_empty():
    assert not _has_working_marker("")
    assert not _has_working_marker(None)


# --- the idle -> working promotion in run_poll ---
#
# Only a hook moves a pane into WORKING, but a turn can start without one
# reaching the daemon (resumed session, /compact continuation, dropped hook).
# The poll promotes such a pane when the screen shows a live turn.

def _run_poll_with(monkey_tails, state):
    """Run one poll cycle with tmux/process calls stubbed out.

    monkey_tails maps pane_id -> captured text.
    """
    import tmux_sentinel_daemon.poll as pm

    saved = {
        name: getattr(pm, name)
        for name in (
            "pane_pids", "_get_process_tree", "get_agent_panes", "focused_pane_id",
            "list_panes", "get_agent_types", "_fix_window_names",
            "capture_pane_tail", "_get_git_branch",
        )
    }
    panes = list(monkey_tails)
    try:
        pm.pane_pids = lambda: {p: "1000" for p in panes}
        pm._get_process_tree = lambda: {}
        pm.get_agent_panes = lambda pp, process_tree=None: set(panes)
        pm.focused_pane_id = lambda: ""          # nothing focused: no mark_seen
        pm.list_panes = lambda: []
        pm.get_agent_types = lambda pp, process_tree=None: {p: "claude" for p in panes}
        pm._fix_window_names = lambda *a, **k: None
        pm.capture_pane_tail = lambda pane_id, lines=10: monkey_tails.get(pane_id, "")
        pm._get_git_branch = lambda cwd: ""
        pm.run_poll(state)
    finally:
        for name, fn in saved.items():
            setattr(pm, name, fn)


def test_poll_promotes_idle_pane_showing_live_turn():
    state = DaemonState()
    ps = state.ensure("7")
    ps.status = IDLE
    ps.unseen = True  # a stale unseen flag from the previous turn
    _run_poll_with({"7": "✽ Working… (30s · ↓ 1.2k tokens)"}, state)
    assert state.get("7").status == WORKING
    # A turn is running, so no finished-but-unseen result stands.
    assert state.get("7").unseen is False


def test_poll_leaves_genuinely_idle_pane_alone():
    state = DaemonState()
    state.ensure("7").status = IDLE
    _run_poll_with({"7": "⏵⏵ auto mode on (shift+tab to cycle) · ← for agents"}, state)
    assert state.get("7").status == IDLE


def test_poll_does_not_promote_on_finished_turn_text():
    state = DaemonState()
    state.ensure("7").status = IDLE
    _run_poll_with({"7": "✻ Crunched for 1m 9s\n❯ \n"}, state)
    assert state.get("7").status == IDLE


def test_poll_promotion_sets_timestamp():
    # Elapsed time in the picker is measured from this timestamp; a promoted pane
    # has no prompt event to inherit one from, so the poll must set it.
    state = DaemonState()
    ps = state.ensure("7")
    ps.status = IDLE
    ps.timestamp = 0
    _run_poll_with({"7": "✶ Working… (5s)"}, state)
    assert state.get("7").timestamp > 0


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
