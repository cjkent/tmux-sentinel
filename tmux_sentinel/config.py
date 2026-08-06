"""
User configuration for tmux-sentinel.

Settings live in ~/.tmux-sentinel/config.toml. The file is optional — every value
has a default, so an absent, partial, or malformed file is fine and unknown keys
are ignored.

Parsed with the stdlib tomllib (Python 3.11+), so the format is real TOML rather
than an approximation of it.

Usage:
    from tmux_sentinel.config import get_int
    width = get_int("max_cwd_len", 50)
"""
from __future__ import annotations

from pathlib import Path

CONFIG_PATH = Path.home() / ".tmux-sentinel" / "config.toml"

# Parsed once per process. The picker and hooks are short-lived; the daemon is
# restarted to pick up changes, so there's no staleness worth handling.
_cache: dict | None = None


def load(path: Path = None, force: bool = False) -> dict:
    """Load and cache the config file.

    A missing, unreadable, or malformed file yields an empty dict: a typo in a
    config file shouldn't stop the picker from opening.
    """
    global _cache
    if _cache is not None and not force and path is None:
        return _cache
    target = path or CONFIG_PATH
    values: dict = {}
    # Import tomllib lazily and only when there's a file to parse. Importing it
    # unconditionally costs ~11ms of interpreter startup, which every picker launch
    # would pay even though most users never write a config file — a 12% regression
    # on a popup we deliberately tuned down to ~88ms. A missing-file stat is ~10µs.
    if target.exists():
        try:
            import tomllib
            with open(target, "rb") as f:
                values = tomllib.load(f)
        except (OSError, ValueError):
            # ValueError covers tomllib.TOMLDecodeError: a malformed config file
            # shouldn't stop the picker from opening.
            values = {}
    if path is None:
        _cache = values
    return values


def get_str(key: str, default: str) -> str:
    """Return a string setting, or the default if unset or not a string."""
    value = load().get(key)
    return value if isinstance(value, str) and value else default


def get_int(key: str, default: int) -> int:
    """Return an integer setting, or the default if unset or not a number.

    Rejects bools, which are ints in Python but never a sensible size or count.
    """
    value = load().get(key)
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def get_float(key: str, default: float) -> float:
    """Return a float setting, or the default if unset or not a number."""
    value = load().get(key)
    if isinstance(value, bool):
        return default
    return float(value) if isinstance(value, (int, float)) else default
