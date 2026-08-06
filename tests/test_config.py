"""Tests for tmux_sentinel.config — optional user settings."""
import tempfile
from pathlib import Path

from tmux_sentinel.config import load, get_int, get_str, get_float
import tmux_sentinel.config as cfg


def _write(text):
    """Write a config file and point the module's cache at it."""
    path = Path(tempfile.mkdtemp()) / "config.toml"
    path.write_text(text)
    cfg._cache = load(path)
    return path


def _clear():
    cfg._cache = {}


def test_missing_file_yields_defaults():
    _clear()
    assert get_int("max_cwd_len", 50) == 50
    assert get_str("preview_width", "50%") == "50%"
    assert get_float("poll_interval_idle", 10.0) == 10.0


def test_missing_file_does_not_raise():
    # A nonexistent path is normal — most users never write a config file.
    assert load(Path("/no/such/dir/config.toml")) == {}


def test_values_override_defaults():
    _write('max_cwd_len = 25\npreview_width = "70%"\npoll_interval_idle = 30.5\n')
    try:
        assert get_int("max_cwd_len", 50) == 25
        assert get_str("preview_width", "50%") == "70%"
        assert get_float("poll_interval_idle", 10.0) == 30.5
    finally:
        _clear()


def test_partial_file_keeps_other_defaults():
    _write("max_cwd_len = 25\n")
    try:
        assert get_int("max_cwd_len", 50) == 25
        assert get_int("max_name_len", 28) == 28
    finally:
        _clear()


def test_unknown_keys_ignored():
    _write("nonsense_setting = 1\n")
    try:
        assert get_int("max_cwd_len", 50) == 50
    finally:
        _clear()


def test_malformed_file_falls_back_to_defaults():
    # A typo in the config shouldn't stop the picker from opening.
    _write("this is not valid toml [[[\n")
    try:
        assert get_int("max_cwd_len", 50) == 50
    finally:
        _clear()


def test_wrong_type_falls_back_to_default():
    _write('max_cwd_len = "not a number"\npreview_width = 42\n')
    try:
        assert get_int("max_cwd_len", 50) == 50
        assert get_str("preview_width", "50%") == "50%"
    finally:
        _clear()


def test_bools_rejected_for_numeric_settings():
    # Python treats True as 1; a bool is never a sensible size or interval.
    _write("max_cwd_len = true\npoll_interval_idle = false\n")
    try:
        assert get_int("max_cwd_len", 50) == 50
        assert get_float("poll_interval_idle", 10.0) == 10.0
    finally:
        _clear()


def test_int_accepted_for_float_setting():
    _write("poll_interval_idle = 20\n")
    try:
        assert get_float("poll_interval_idle", 10.0) == 20.0
    finally:
        _clear()


def test_empty_string_falls_back():
    _write('preview_width = ""\n')
    try:
        assert get_str("preview_width", "50%") == "50%"
    finally:
        _clear()


def test_tomllib_not_imported_without_a_config_file():
    # Importing tomllib costs interpreter startup time that every picker launch
    # would pay, so it must stay lazy: no config file means no import.
    import subprocess, sys, os
    repo = Path(__file__).resolve().parent.parent
    code = (
        "import sys;"
        "from pathlib import Path;"
        "import tmux_sentinel.config as c;"
        "c.CONFIG_PATH = Path('/no/such/config.toml');"
        "c.load(force=True);"
        "print('tomllib' in sys.modules)"
    )
    env = dict(os.environ, PYTHONPATH=str(repo))
    out = subprocess.run([sys.executable, "-S", "-c", code],
                         capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "False", out.stdout + out.stderr


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
