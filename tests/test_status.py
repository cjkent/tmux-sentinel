"""Tests for tmux_sentinel.status module."""
import json
import tempfile
from pathlib import Path

from tmux_sentinel.status import (
    IDLE, WORKING, WAITING, ERROR,
    write_status, read_status, list_statuses,
    set_error_flag, clear_error_flag, has_error_flag,
    set_unseen, clear_unseen, is_unseen,
    cleanup_stale,
)


def _tmp_dir():
    return Path(tempfile.mkdtemp()) / "status"


def test_write_and_read():
    d = _tmp_dir()
    write_status("42", WORKING, "/tmp", "main", 1000, status_dir=d)
    s = read_status("42", status_dir=d)
    assert s is not None
    assert s.status == WORKING
    assert s.cwd == "/tmp"
    assert s.git_branch == "main"
    assert s.timestamp == 1000


def test_read_missing():
    d = _tmp_dir()
    d.mkdir(parents=True)
    assert read_status("99", status_dir=d) is None


def test_read_invalid_json():
    d = _tmp_dir()
    d.mkdir(parents=True)
    (d / "42.json").write_text("not json")
    assert read_status("42", status_dir=d) is None


def test_list_statuses():
    d = _tmp_dir()
    write_status("1", IDLE, "/a", "", 100, status_dir=d)
    write_status("2", WORKING, "/b", "dev", 200, status_dir=d)
    results = list_statuses(status_dir=d)
    assert len(results) == 2
    ids = {pane_id for pane_id, _ in results}
    assert ids == {"1", "2"}


def test_error_flag():
    d = _tmp_dir()
    d.mkdir(parents=True)
    assert not has_error_flag("42", status_dir=d)
    set_error_flag("42", status_dir=d)
    assert has_error_flag("42", status_dir=d)
    clear_error_flag("42", status_dir=d)
    assert not has_error_flag("42", status_dir=d)


def test_unseen_flag():
    d = _tmp_dir()
    d.mkdir(parents=True)
    assert not is_unseen("42", status_dir=d)
    set_unseen("42", status_dir=d)
    assert is_unseen("42", status_dir=d)
    clear_unseen("42", status_dir=d)
    assert not is_unseen("42", status_dir=d)


def test_cleanup_stale():
    d = _tmp_dir()
    write_status("1", IDLE, "", "", 0, status_dir=d)
    write_status("2", WORKING, "", "", 0, status_dir=d)
    set_error_flag("2", status_dir=d)
    set_unseen("2", status_dir=d)
    # Only pane 1 is live
    cleanup_stale({"1"}, status_dir=d)
    assert read_status("1", status_dir=d) is not None
    assert read_status("2", status_dir=d) is None
    assert not has_error_flag("2", status_dir=d)
    assert not is_unseen("2", status_dir=d)


def test_write_atomic():
    """Verify write uses atomic rename (tmp file shouldn't linger)."""
    d = _tmp_dir()
    write_status("42", IDLE, "", "", 0, status_dir=d)
    tmp_files = list(d.glob("*.tmp"))
    assert len(tmp_files) == 0


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
