"""Tests for the picker's sort modes and per-mode cursor placement."""
from tmux_sentinel.status import IDLE, WORKING, WAITING, ERROR
from tmux_sentinel.picker import (
    _Record, _sort_records, _cursor_row, _parse_mode, _lru_ranks,
    _SEVERITY, _SEVERITY_NONE, _LRU_UNVISITED,
    MODE_UNSEEN, MODE_SESSION, MODE_MRU,
)

SEP = "\x1f"


def _rec(**kw):
    base = dict(row=[], target="t", unseen=False, severity=_SEVERITY[IDLE],
                activity=0, session_index=0, window_index=0)
    base.update(kw)
    return _Record(**base)


def _lines(*rows):
    """Build an fzf input block; each row gets the hidden target field."""
    return "\n".join(f"{r}{SEP}x" for r in rows)


# --- ordering ----------------------------------------------------------------------

def test_session_mode_reproduces_grouped_order():
    # Must match the pre-modes ordering exactly: session order, then window index.
    recs = [
        _rec(target="b1", session_index=1, window_index=0),
        _rec(target="a2", session_index=0, window_index=2),
        _rec(target="a1", session_index=0, window_index=1),
    ]
    assert [r.target for r in _sort_records(recs, MODE_SESSION)] == ["a1", "a2", "b1"]


def test_session_mode_sorts_window_index_numerically():
    # Indices arrive from tmux as strings; "10" must not sort before "9".
    recs = [_rec(target="w10", window_index=10), _rec(target="w9", window_index=9)]
    assert [r.target for r in _sort_records(recs, MODE_SESSION)] == ["w9", "w10"]


def test_mru_mode_orders_by_visit_not_output():
    # The whole point: a pane that merely *printed* recently must not outrank one you
    # actually visited. window_activity is last-output time, so ordering on it promotes
    # chatty agents you never looked at.
    recs = [_rec(target="visited", lru_rank=0, activity=1),
            _rec(target="noisy", lru_rank=_LRU_UNVISITED, activity=999)]
    assert [r.target for r in _sort_records(recs, MODE_MRU)] == ["visited", "noisy"]


def test_mru_mode_orders_visited_panes_by_recency_of_visit():
    recs = [_rec(target="third", lru_rank=2),
            _rec(target="first", lru_rank=0),
            _rec(target="second", lru_rank=1)]
    assert [r.target for r in _sort_records(recs, MODE_MRU)] == ["first", "second", "third"]


def test_mru_mode_falls_back_to_output_for_never_visited():
    # A fresh install has no visit history; output time is the only signal left, and
    # unvisited panes still sort behind anything visited.
    recs = [_rec(target="old", lru_rank=_LRU_UNVISITED, activity=100),
            _rec(target="new", lru_rank=_LRU_UNVISITED, activity=300),
            _rec(target="mid", lru_rank=_LRU_UNVISITED, activity=200)]
    assert [r.target for r in _sort_records(recs, MODE_MRU)] == ["new", "mid", "old"]


# --- LRU cache parsing -------------------------------------------------------------

def test_lru_ranks_reads_most_recent_first():
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "lru"
    f.write_text("42\n17\n8\n")
    assert _lru_ranks(f) == {"42": 0, "17": 1, "8": 2}


def test_lru_ranks_strips_percent_and_blanks():
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "lru"
    f.write_text("%42\n\n  17  \n")
    assert _lru_ranks(f) == {"42": 0, "17": 1}


def test_lru_ranks_first_occurrence_wins():
    # bump dedupes, but a stale duplicate must not demote a pane.
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "lru"
    f.write_text("5\n9\n5\n")
    assert _lru_ranks(f)["5"] == 0


def test_lru_ranks_missing_file_is_empty():
    from pathlib import Path
    assert _lru_ranks(Path("/no/such/lru")) == {}


def test_unseen_mode_puts_unseen_first():
    # Unseen wins even when a seen row carries a more urgent status.
    recs = [_rec(target="seen", unseen=False, severity=_SEVERITY[WAITING]),
            _rec(target="unseen", unseen=True, severity=_SEVERITY[IDLE])]
    assert [r.target for r in _sort_records(recs, MODE_UNSEEN)] == ["unseen", "seen"]


def test_unseen_mode_orders_by_severity():
    # Waiting outranks error: it's blocking on you now, an error already happened.
    recs = [
        _rec(target="none", severity=_SEVERITY_NONE),
        _rec(target="idle", severity=_SEVERITY[IDLE]),
        _rec(target="working", severity=_SEVERITY[WORKING]),
        _rec(target="error", severity=_SEVERITY[ERROR]),
        _rec(target="waiting", severity=_SEVERITY[WAITING]),
    ]
    assert [r.target for r in _sort_records(recs, MODE_UNSEEN)] == [
        "waiting", "error", "working", "idle", "none"]


def test_unseen_mode_breaks_severity_ties_by_recency():
    recs = [_rec(target="older", activity=100), _rec(target="newer", activity=200)]
    assert [r.target for r in _sort_records(recs, MODE_UNSEEN)] == ["newer", "older"]


def test_unknown_mode_falls_back_to_unseen():
    # A typo in a tmux keybinding should still give a usable list.
    recs = [_rec(target="seen"), _rec(target="unseen", unseen=True)]
    assert ([r.target for r in _sort_records(recs, "bogus")]
            == [r.target for r in _sort_records(recs, MODE_UNSEEN)])


def test_sorts_are_stable_for_equal_keys():
    # Ties keep discovery order, which is session order — so equal rows don't shuffle
    # between invocations.
    recs = [_rec(target=str(i)) for i in range(6)]
    assert [r.target for r in _sort_records(recs, MODE_UNSEEN)] == [str(i) for i in range(6)]


# --- cursor placement --------------------------------------------------------------

def test_session_cursor_lands_on_focused_pane():
    assert _cursor_row(_lines("   a", "   b", "►  c"), MODE_SESSION) == 3


def test_unseen_cursor_lands_on_first_unseen():
    assert _cursor_row(_lines("   a", "●  b", "►  c"), MODE_UNSEEN) == 2


def test_unseen_cursor_falls_back_to_focused_pane():
    assert _cursor_row(_lines("   a", "►  b"), MODE_UNSEEN) == 2


def test_mru_cursor_skips_focused_pane_only_at_the_top():
    # Keyed to row 1, not to wherever ► is: window_activity is last-*output* time, so
    # a chattier agent elsewhere often outranks the pane you're sitting in. Stepping
    # past ► wherever it appeared would land on an arbitrary row.
    assert _cursor_row(_lines("►  a", "   b", "   c"), MODE_MRU) == 2
    assert _cursor_row(_lines("   a", "►  b", "   c"), MODE_MRU) == 1


def test_mru_cursor_handles_single_row():
    # Nothing below the focused pane to move to.
    assert _cursor_row(_lines("►  a"), MODE_MRU) == 1


def test_cursor_handles_empty_list():
    # 0 means "leave it at the top"; fzf's pos() is 1-based so 0 is never bound.
    for mode in (MODE_UNSEEN, MODE_SESSION, MODE_MRU):
        assert _cursor_row("", mode) == 0


def test_cursor_zero_when_nothing_to_anchor_on():
    assert _cursor_row(_lines("   a", "   b"), MODE_UNSEEN) == 0
    assert _cursor_row(_lines("   a"), MODE_SESSION) == 0


# --- CLI ---------------------------------------------------------------------------

def test_parse_mode():
    assert _parse_mode(["--list", "--mode=mru"]) == MODE_MRU
    assert _parse_mode(["--mode=session"]) == MODE_SESSION
    assert _parse_mode([]) == MODE_UNSEEN
    assert _parse_mode(["--mode=nonsense"]) == MODE_UNSEEN
    assert _parse_mode(["--close", "42"]) == MODE_UNSEEN


# --- switch ordering ---------------------------------------------------------------

def test_switch_to_pane_selects_window_before_switching_client():
    """The window must be selected before the client switches sessions.

    switch-client first would leave the client momentarily on whatever window that
    session last had active, and anything watching navigation — including this
    project's own recent-mode hooks — records that intermediate window as a visit.
    That produced two entries per switch when only one window was visited.
    """
    import tmux_sentinel.tmux as tm
    calls = []
    orig = tm._run_tmux
    tm._run_tmux = lambda *a: (calls.append(a), "sess")[1]
    try:
        tm.switch_to_pane("42")
    finally:
        tm._run_tmux = orig
    verbs = [c[0] for c in calls]
    assert "select-window" in verbs and "switch-client" in verbs
    assert verbs.index("select-window") < verbs.index("switch-client"), verbs
    assert verbs.index("select-pane") < verbs.index("switch-client"), verbs


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
