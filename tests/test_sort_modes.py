"""Tests for the picker's sort modes and per-mode cursor placement."""
from tmux_sentinel.status import IDLE, WORKING, WAITING, ERROR
from tmux_sentinel.picker import (
    _Record, _sort_records, _cursor_row, _parse_mode,
    _SEVERITY, _SEVERITY_NONE,
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


def test_mru_mode_is_most_recent_first():
    recs = [_rec(target="old", activity=100),
            _rec(target="new", activity=300),
            _rec(target="mid", activity=200)]
    assert [r.target for r in _sort_records(recs, MODE_MRU)] == ["new", "mid", "old"]


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


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
