"""Tests for tmux_sentinel.picker module."""
import tempfile
from pathlib import Path

from tmux_sentinel.status import IDLE, WORKING, WAITING, write_status, set_unseen
from tmux_sentinel.tmux import PaneInfo
from tmux_sentinel.picker import (
    _build_rows, _colorize_line, _display_name,
    _shorten_path, _home_symlink_targets,
)


# Column positions in a _build_rows row. Named so a column-order change means
# editing this block, not every assertion below.
COL_MARKER = 0
COL_SESSION = 1
COL_NAME = 2
COL_ICON = 3
COL_STATUS = 4
COL_CWD = 5
COL_BRANCH = 6
COL_ELAPSED = 7


def _make_pane(pane_id="99990", session="test", window_index="0", window_name="zsh", path="/tmp", title=""):
    return PaneInfo(
        pane_id=pane_id, pane_pid="1000", session=session,
        window_index=window_index, window_name=window_name, pane_current_path=path,
        pane_title=title,
    )


def _setup():
    d = Path(tempfile.mkdtemp()) / "status"
    d.mkdir(parents=True)
    return d


def _rows(d, panes, cur_session="other", cur_window="99"):
    import tmux_sentinel.picker as pm
    orig = pm.list_sessions
    pm.list_sessions = lambda: sorted({p.session for p in panes})
    try:
        return _build_rows(panes, cur_session, cur_window, status_dir=d)
    finally:
        pm.list_sessions = orig


def test_build_rows_with_agent():
    d = _setup()
    write_status("99990", WORKING, "/home/user/dev", "main", 1000, status_dir=d)
    rows, targets = _rows(d, [_make_pane()], "test", "0")
    # Flat rows: no session header, so one pane means exactly one row.
    assert len(rows) == 1
    # Targets are pane ids, so split panes are individually selectable.
    assert targets[0] == "99990"
    assert "[WRK]" in rows[0][COL_STATUS]


def test_build_rows_has_no_session_header():
    # Headers are gone: the session is a column on every row instead, which keeps
    # every row a real fzf target.
    d = _setup()
    rows, targets = _rows(d, [_make_pane(session="myproj")])
    assert len(rows) == 1
    assert rows[0][COL_SESSION] == "myproj"
    assert all(t for t in targets), "no row should carry an empty (header) target"
    assert not any("──" in c for row in rows for c in row)


def test_build_rows_name_column_omits_window_index():
    # The window index isn't actionable from the picker — you can't type it to jump
    # anywhere, and it only adds noise to fzf matching.
    d = _setup()
    rows, _ = _rows(d, [_make_pane(window_index="7", window_name="zsh")])
    assert rows[0][COL_NAME] == "zsh"


def test_build_rows_session_column_repeats_per_row():
    d = _setup()
    panes = [
        _make_pane(pane_id="99990", window_index="0"),
        _make_pane(pane_id="99991", window_index="1"),
    ]
    rows, _ = _rows(d, panes)
    assert [r[COL_SESSION] for r in rows] == ["test", "test"]


def test_build_rows_truncates_long_session_name():
    d = _setup()
    long_name = "a-very-long-session-name-well-past-the-cap"
    rows, _ = _rows(d, [_make_pane(session=long_name)])
    cell = rows[0][COL_SESSION]
    assert cell.endswith("…")
    assert len(cell) == 21  # 20 chars + the ellipsis


def test_build_rows_non_agent():
    d = _setup()
    rows, _ = _rows(d, [_make_pane(path="/home/user/projects")])
    assert len(rows) == 1
    assert "[---]" in rows[0][COL_STATUS]
    assert "/home/user/projects" in rows[0][COL_CWD]


def test_build_rows_elides_middle_of_long_cwd():
    # Deep Brazil paths would otherwise pad every other row. The leading segments
    # name the project and the last names the package, so the middle is what goes.
    # Built from the real $HOME so _shorten_path collapses it to "~" first.
    import os
    d = _setup()
    deep = os.path.join(
        os.path.expanduser("~"), "workplace/Thing/src/nested/DvRcsSomeVeryLongServiceName"
    )
    rows, _ = _rows(d, [_make_pane(path=deep)])
    cell = rows[0][COL_CWD]
    assert cell == "~/workplace/Thing/…/DvRcsSomeVeryLongServiceName"


def test_build_rows_current_window_marker():
    d = _setup()
    panes = [_make_pane(), _make_pane(pane_id="99991", window_index="1", window_name="vim")]
    rows, _ = _rows(d, panes, "test", "0")
    assert rows[0][COL_MARKER] == "►"
    assert rows[1][COL_MARKER].strip() == ""


def test_build_rows_unseen_marker():
    d = _setup()
    write_status("99990", IDLE, "/tmp", "", 1000, status_dir=d)
    set_unseen("99990", status_dir=d)
    rows, _ = _rows(d, [_make_pane()])
    # The unseen dot sits alone in the leading marker column.
    assert rows[0][COL_MARKER] == "●"


def test_build_rows_seen_no_marker():
    d = _setup()
    write_status("99990", IDLE, "/tmp", "", 1000, status_dir=d)
    # No unseen flag
    rows, _ = _rows(d, [_make_pane()])
    assert rows[0][COL_MARKER].strip() == ""


def test_build_rows_current_window_not_unseen_marker():
    # The focused pane shares the marker column with the unseen dot, but ► must
    # win — the focused pane is by definition seen, so it never shows ●.
    d = _setup()
    write_status("99990", IDLE, "/tmp", "", 1000, status_dir=d)
    set_unseen("99990", status_dir=d)
    rows, _ = _rows(d, [_make_pane()], "test", "0")
    assert rows[0][COL_MARKER] == "►"


def _rows_focused(d, panes, focused_pane, cur_session="test", cur_window="0"):
    """Build rows with an explicit focused pane id (split-window aware)."""
    import tmux_sentinel.picker as pm
    orig = pm.list_sessions
    pm.list_sessions = lambda: sorted({p.session for p in panes})
    try:
        return _build_rows(
            panes, cur_session, cur_window, status_dir=d, focused_pane=focused_pane
        )
    finally:
        pm.list_sessions = orig


def test_split_panes_get_distinct_targets():
    # Two panes in the SAME window: a window-level target ("session:window") can't
    # tell them apart, so selecting either one used to leave focus wherever it was.
    d = _setup()
    panes = [
        _make_pane(pane_id="99990", window_index="0"),
        _make_pane(pane_id="99991", window_index="0"),
    ]
    _, targets = _rows_focused(d, panes, focused_pane="99990")
    assert targets[0] == "99990"
    assert targets[1] == "99991"
    assert targets[0] != targets[1]


def test_split_panes_only_focused_pane_is_current():
    # Both panes share session+window, so only the focused pane id may carry ►.
    d = _setup()
    panes = [
        _make_pane(pane_id="99990", window_index="0"),
        _make_pane(pane_id="99991", window_index="0"),
    ]
    rows, _ = _rows_focused(d, panes, focused_pane="99991")
    assert rows[0][COL_MARKER] != "►"
    assert rows[1][COL_MARKER] == "►"


def test_current_marker_falls_back_to_window_without_focused_pane():
    # With no focused_pane supplied, fall back to session+window matching.
    d = _setup()
    rows, _ = _rows(d, [_make_pane()], "test", "0")
    assert rows[0][COL_MARKER] == "►"


def test_colorize_line():
    line = "●  test  zsh  [IDL]  ~/dev  (main)"
    result = _colorize_line(line)
    assert "\033[32m[IDL]\033[0m" in result
    assert "\033[31m●\033[0m" in result


def test_colorize_all_statuses():
    for label, code in [("[IDL]", "32"), ("[WRK]", "34"), ("[WAI]", "35"), ("[ERR]", "31")]:
        result = _colorize_line(f"test {label} end")
        assert f"\033[{code}m{label}\033[0m" in result


def test_elapsed_only_for_working():
    d = _setup()
    write_status("99990", WORKING, "/tmp", "", 1000, status_dir=d)
    write_status("99991", IDLE, "/tmp", "", 1000, status_dir=d)
    panes = [_make_pane(), _make_pane(pane_id="99991", window_index="1")]
    rows, _ = _rows(d, panes)
    # Working row should have elapsed time (non-empty last column)
    assert rows[0][COL_ELAPSED] != ""
    # Idle row should have empty elapsed
    assert rows[1][COL_ELAPSED] == ""


def _rows_daemon(d, panes, daemon_state, cur_session="other", cur_window="99"):
    import tmux_sentinel.picker as pm
    orig = pm.list_sessions
    pm.list_sessions = lambda: sorted({p.session for p in panes})
    try:
        return _build_rows(panes, cur_session, cur_window, status_dir=d, daemon_state=daemon_state)
    finally:
        pm.list_sessions = orig


def test_daemon_state_fast_path():
    # A pane present in daemon_state uses its status without a status file.
    d = _setup()
    ds = {"99990": {"status": WORKING, "cwd": "/srv/app", "git_branch": "dev",
                    "timestamp": 1000, "unseen": False, "agent_type": "claude"}}
    rows, _ = _rows_daemon(d, [_make_pane()], ds, "test", "0")
    assert "[WRK]" in rows[0][COL_STATUS]
    assert rows[0][COL_ELAPSED] != ""  # elapsed shown for working


def test_daemon_state_prefers_status_file_cwd():
    # When a status file exists, its cwd wins over the daemon's (agent cwd vs shell cwd).
    d = _setup()
    write_status("99990", WORKING, "/deep/agent/path", "main", 1000, status_dir=d)
    ds = {"99990": {"status": WORKING, "cwd": "/shallow", "git_branch": "other",
                    "timestamp": 1000, "unseen": False, "agent_type": "claude"}}
    rows, _ = _rows_daemon(d, [_make_pane()], ds, "test", "0")
    assert "/deep/agent/path" in rows[0][COL_CWD]
    assert "(main)" in rows[0][COL_BRANCH]


def test_daemon_state_unseen_marker():
    d = _setup()
    ds = {"99990": {"status": IDLE, "cwd": "/tmp", "git_branch": "",
                    "timestamp": 1000, "unseen": True, "agent_type": "claude"}}
    rows, _ = _rows_daemon(d, [_make_pane()], ds)
    # Unseen dot sits alone in the leading marker column.
    assert rows[0][COL_MARKER] == "●"


def test_generate_list_from_daemon_calls_cleanup_stale():
    # Regression: an agent exits (daemon correctly stops tracking the pane),
    # but its status file lingers on disk. Without cleanup, the picker's
    # status-file fallback would still show it as an agent ([IDL], no orange
    # badge). _generate_list_from_daemon must clear stale files using the
    # daemon's own pane set as the liveness truth (it has no ps walk of its
    # own to derive one).
    import tmux_sentinel.picker as pm

    pane = _make_pane()
    calls = []
    orig_cleanup_stale = pm.cleanup_stale
    orig_list_panes, orig_list_sessions = pm.list_panes, pm.list_sessions
    orig_csw = pm.current_session_window
    pm.cleanup_stale = lambda live_ids: calls.append(set(live_ids))
    pm.list_panes = lambda: [pane]
    pm.list_sessions = lambda: [pane.session]
    pm.current_session_window = lambda: (pane.session, pane.window_index)
    try:
        pm._generate_list_from_daemon({"other-pane": {}})
    finally:
        pm.cleanup_stale = orig_cleanup_stale
        pm.list_panes = orig_list_panes
        pm.list_sessions = orig_list_sessions
        pm.current_session_window = orig_csw

    assert calls == [{"other-pane"}]


def test_display_name_uses_claude_title_when_agent():
    pane = _make_pane(window_name="claude", title="✳ Fix the flaky test")
    assert _display_name(pane, "claude") == "Fix the flaky test"


def test_display_name_falls_back_to_window_name_for_non_agent():
    # Same title string, but the pane isn't a known agent — must not use it.
    pane = _make_pane(window_name="zsh", title="✳ Fix the flaky test")
    assert _display_name(pane, "") == "zsh"


def test_display_name_rejects_bare_hash_title():
    # Idle/default shell title (no spinner glyph, just a hex-looking hash)
    # must not be shown as if it were a task.
    pane = _make_pane(window_name="claude", title="80a9972fcf5b")
    assert _display_name(pane, "claude") == "claude"


def test_display_name_falls_back_when_no_title():
    pane = _make_pane(window_name="claude", title="")
    assert _display_name(pane, "claude") == "claude"


def test_display_name_truncates_long_title():
    from tmux_sentinel.picker import _MAX_TITLE_LEN
    long_title = "✳ " + "x" * 60
    pane = _make_pane(window_name="claude", title=long_title)
    name = _display_name(pane, "claude")
    assert name.endswith("…")
    assert len(name) == _MAX_TITLE_LEN + 1  # cap + ellipsis


def test_display_name_works_for_kiro_too():
    pane = _make_pane(window_name="kiro", title="⠐ Refactor the parser")
    assert _display_name(pane, "kiro") == "Refactor the parser"


def test_shorten_path_home_prefix():
    assert _shorten_path("/Users/cjkent/dev/tmux-sentinel", "/Users/cjkent", {}) == "~/dev/tmux-sentinel"


def test_shorten_path_home_symlink():
    syms = {"/Volumes/workplace": "~/workplace"}
    got = _shorten_path("/Volumes/workplace/PVRF-820/src", "/Users/cjkent", syms)
    assert got == "~/workplace/PVRF-820/src"


def test_shorten_path_exact_symlink_target():
    syms = {"/Volumes/workplace": "~/workplace"}
    assert _shorten_path("/Volumes/workplace", "/Users/cjkent", syms) == "~/workplace"


def test_shorten_path_prefers_longest_symlink_match():
    # A sibling path that merely starts with the same characters must not match.
    syms = {"/Volumes/work": "~/work", "/Volumes/workplace": "~/workplace"}
    got = _shorten_path("/Volumes/workplace/foo", "/Users/cjkent", syms)
    assert got == "~/workplace/foo"


def test_shorten_path_no_match_passes_through():
    syms = {"/Volumes/workplace": "~/workplace"}
    assert _shorten_path("/tmp/scratch", "/Users/cjkent", syms) == "/tmp/scratch"


def test_shorten_path_similar_prefix_not_matched():
    # /Volumes/workplace2 must not be treated as inside /Volumes/workplace.
    syms = {"/Volumes/workplace": "~/workplace"}
    assert _shorten_path("/Volumes/workplace2/foo", "/Users/cjkent", syms) == "/Volumes/workplace2/foo"


def test_truncate_path_leaves_short_paths_alone():
    from tmux_sentinel.picker import _truncate_path
    assert _truncate_path("~/dev/tmux-sentinel", 50) == "~/dev/tmux-sentinel"
    assert _truncate_path("~/tmp", 50) == "~/tmp"


def test_truncate_path_keeps_project_and_package():
    # "~" isn't a meaningful segment, so it keeps three to reach two real ones.
    from tmux_sentinel.picker import _truncate_path
    p = "~/workplace/PVRF-820/src/DvRcsCalculationServiceCDK"
    assert _truncate_path(p, 50) == "~/workplace/PVRF-820/…/DvRcsCalculationServiceCDK"


def test_truncate_path_absolute_keeps_two_real_segments():
    # A leading "/" splits to an empty first segment — it must not count as one of
    # the two meaningful ones, or "workplace" would be lost here.
    from tmux_sentinel.picker import _truncate_path
    p = "/Volumes/workplace/Foo/src/Bar/baz/deeply/nested/thing"
    assert _truncate_path(p, 50) == "/Volumes/workplace/…/thing"


def test_truncate_path_no_middle_to_elide():
    from tmux_sentinel.picker import _truncate_path
    # Only head + tail, nothing between: falls back to a plain tail truncation.
    p = "~/aaaaaaaaaaaaaaaaaaaaaaaaaaa/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    out = _truncate_path(p, 50)
    assert len(out) <= 51
    assert "…" in out


def test_truncate_path_absurdly_long_final_segment():
    # The elided form still wouldn't fit, so fall back to dropping the front.
    from tmux_sentinel.picker import _truncate_path
    p = "~/a/b/c/ThisFinalSegmentIsAbsurdlyLongAndCannotPossiblyFitInFifty"
    out = _truncate_path(p, 50)
    assert out.startswith("…")
    assert len(out) <= 51


def test_truncate_path_edge_cases():
    from tmux_sentinel.picker import _truncate_path
    assert _truncate_path("", 50) == ""
    assert _truncate_path("/", 50) == "/"
    assert _truncate_path("~", 50) == "~"


def test_home_symlink_targets_finds_dir_symlink():
    import os
    home = Path(tempfile.mkdtemp())
    real_target = home / "elsewhere"
    real_target.mkdir()
    (home / "linked").symlink_to(real_target)
    (home / "not_a_symlink").mkdir()

    targets = _home_symlink_targets(str(home))
    # Compare against the resolved path, since e.g. macOS /var -> /private/var
    # means realpath() may not equal the path we built the symlink from.
    assert targets == {os.path.realpath(real_target): "~/linked"}


def test_home_symlink_targets_ignores_broken_symlink():
    home = Path(tempfile.mkdtemp())
    (home / "broken").symlink_to(home / "does-not-exist")

    targets = _home_symlink_targets(str(home))
    assert targets == {}


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
