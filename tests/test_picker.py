"""Tests for tmux_agents.picker module."""
import tempfile
from pathlib import Path

from tmux_agents.status import IDLE, WORKING, WAITING, write_status, set_unseen
from tmux_agents.tmux import PaneInfo
from tmux_agents.picker import _build_rows, _colorize_line


def _make_pane(pane_id="99990", session="test", window_index="0", window_name="zsh", path="/tmp"):
    return PaneInfo(
        pane_id=pane_id, pane_pid="1000", session=session,
        window_index=window_index, window_name=window_name, pane_current_path=path,
    )


def _setup():
    d = Path(tempfile.mkdtemp()) / "status"
    d.mkdir(parents=True)
    return d


def _rows(d, panes, cur_session="other", cur_window="99"):
    import tmux_agents.picker as pm
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
    assert len(rows) == 2
    assert targets[0] == "test:"
    assert targets[1] == "test:0"
    assert "[WRK]" in rows[1][2]


def test_build_rows_non_agent():
    d = _setup()
    rows, _ = _rows(d, [_make_pane(path="/home/user/projects")])
    assert len(rows) == 2
    assert "[---]" in rows[1][2]
    assert "/home/user/projects" in rows[1][3]


def test_build_rows_current_window_marker():
    d = _setup()
    panes = [_make_pane(), _make_pane(pane_id="99991", window_index="1", window_name="vim")]
    rows, _ = _rows(d, panes, "test", "0")
    assert rows[1][0].startswith("►")
    assert rows[2][0].startswith("  ")


def test_build_rows_unseen_marker():
    d = _setup()
    write_status("99990", IDLE, "/tmp", "", 1000, status_dir=d)
    set_unseen("99990", status_dir=d)
    rows, _ = _rows(d, [_make_pane()])
    assert "●" in rows[1][2]


def test_build_rows_seen_no_marker():
    d = _setup()
    write_status("99990", IDLE, "/tmp", "", 1000, status_dir=d)
    # No unseen flag
    rows, _ = _rows(d, [_make_pane()])
    assert "●" not in rows[1][2]


def test_colorize_line():
    line = "  0: zsh  [IDL]  ●  ~/dev  (main)"
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
    assert rows[1][5] != ""
    # Idle row should have empty elapsed
    assert rows[2][5] == ""


def _rows_daemon(d, panes, daemon_state, cur_session="other", cur_window="99"):
    import tmux_agents.picker as pm
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
    assert "[WRK]" in rows[1][2]
    assert rows[1][5] != ""  # elapsed shown for working


def test_daemon_state_prefers_status_file_cwd():
    # When a status file exists, its cwd wins over the daemon's (agent cwd vs shell cwd).
    d = _setup()
    write_status("99990", WORKING, "/deep/agent/path", "main", 1000, status_dir=d)
    ds = {"99990": {"status": WORKING, "cwd": "/shallow", "git_branch": "other",
                    "timestamp": 1000, "unseen": False, "agent_type": "claude"}}
    rows, _ = _rows_daemon(d, [_make_pane()], ds, "test", "0")
    assert "/deep/agent/path" in rows[1][3]
    assert "(main)" in rows[1][4]


def test_daemon_state_unseen_marker():
    d = _setup()
    ds = {"99990": {"status": IDLE, "cwd": "/tmp", "git_branch": "",
                    "timestamp": 1000, "unseen": True, "agent_type": "claude"}}
    rows, _ = _rows_daemon(d, [_make_pane()], ds)
    assert "●" in rows[1][2]


def test_generate_list_from_daemon_calls_cleanup_stale():
    # Regression: an agent exits (daemon correctly stops tracking the pane),
    # but its status file lingers on disk. Without cleanup, the picker's
    # status-file fallback would still show it as an agent ([IDL], no orange
    # badge). _generate_list_from_daemon must clear stale files using the
    # daemon's own pane set as the liveness truth (it has no ps walk of its
    # own to derive one).
    import tmux_agents.picker as pm

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


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
