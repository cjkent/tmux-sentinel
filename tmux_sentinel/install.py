"""
Hook installation and removal for tmux-sentinel.

Edits the JSON config files of the supported agents to add or remove the tmux-sentinel
lifecycle hook. Called by setup.sh, which keeps the interactive parts and the tmux
configuration — this module only does the JSON.

Written in Python rather than jq for two reasons: jq was otherwise unused by the
project, so it was a dependency users had to install for setup alone; and the filters
had to be assembled as bash strings, which meant regexes buried under four levels of
escaping (test(\\"tmux_sentinel.*hook\\\\\\\\.py\\")) that could not be tested in
isolation.

The two agents use different shapes for the same idea:

  Kiro (~/.kiro/agents/*.json) — a flat list per event:
      {"hooks": {"agentSpawn": [{"command": "...", "description": "..."}]}}

  Claude Code (~/.claude/settings.json) — matcher groups, each with its own list:
      {"hooks": {"SessionStart": [{"matcher": "", "hooks": [
          {"type": "command", "command": "..."}]}]}}

Every operation is idempotent: adding twice leaves one hook, removing when absent is a
no-op. Files are written atomically (temp file + rename) so an interrupted run cannot
leave a user with a truncated agent config.

Usage:
    python3 -m tmux_sentinel.install --list-kiro
    python3 -m tmux_sentinel.install --add-kiro <file>... --command CMD
    python3 -m tmux_sentinel.install --remove-kiro <file>...
    python3 -m tmux_sentinel.install --add-claude --command CMD
    python3 -m tmux_sentinel.install --remove-claude
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

KIRO_EVENTS = ["agentSpawn", "userPromptSubmit", "preToolUse", "postToolUse", "stop"]
CLAUDE_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
KIRO_AGENTS_DIR = Path.home() / ".kiro" / "agents"

# Recognises our own hook however it's invoked, so we don't add a second copy or miss
# one on removal. Matches the current Python entry point and the old bash hook that
# earlier versions installed.
_OURS = re.compile(r"tmux_sentinel.*hook\.py|tmux-sentinel/.*notify\.sh")


def _is_ours(command: str) -> bool:
    return bool(_OURS.search(command or ""))


def _read_json(path: Path) -> dict:
    """Read a JSON object, returning {} for a missing file. Invalid JSON raises."""
    try:
        with open(path, "rb") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _write_json(path: Path, data: dict) -> None:
    """Write JSON atomically, so an interrupted run can't truncate a live config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        # Leave the original untouched if anything goes wrong mid-write.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- Kiro: flat list of {"command": ..., "description": ...} per event -------------

def kiro_has_hook(data: dict) -> bool:
    """True if any event already carries our hook."""
    hooks = data.get("hooks") or {}
    return any(
        _is_ours(entry.get("command", ""))
        for entries in hooks.values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
    )


def kiro_add(data: dict, command: str) -> dict:
    """Add our hook to every lifecycle event, replacing any older copy of it."""
    hooks = data.setdefault("hooks", {})
    for event in KIRO_EVENTS:
        entries = [
            e for e in hooks.get(event, []) or []
            if not (isinstance(e, dict) and _is_ours(e.get("command", "")))
        ]
        entries.append({
            "command": command,
            "description": "tmux-sentinel status tracking",
        })
        hooks[event] = entries
    return data


def kiro_remove(data: dict) -> dict:
    """Remove our hook, tidying up empty structures the way the jq version did."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [
            e for e in entries
            if not (isinstance(e, dict) and _is_ours(e.get("command", "")))
        ]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    return data


# --- Claude Code: matcher groups, each holding its own list of hooks --------------

def claude_has_hook(data: dict) -> bool:
    hooks = data.get("hooks") or {}
    return any(
        _is_ours(inner.get("command", ""))
        for groups in hooks.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
        for inner in group.get("hooks", []) or []
        if isinstance(inner, dict)
    )


def claude_add(data: dict, command: str) -> dict:
    """Add our hook to every event that doesn't already have it.

    Existing matcher groups are left alone — a user may have their own hooks with
    specific matchers, and ours wants to run for everything, so it gets its own group
    with an empty matcher.
    """
    hooks = data.setdefault("hooks", {})
    for event in CLAUDE_EVENTS:
        groups = hooks.get(event) or []
        already = any(
            _is_ours(inner.get("command", ""))
            for group in groups if isinstance(group, dict)
            for inner in group.get("hooks", []) or [] if isinstance(inner, dict)
        )
        if not already:
            groups = list(groups) + [{
                "matcher": "",
                "hooks": [{"type": "command", "command": command}],
            }]
        hooks[event] = groups
    return data


def claude_remove(data: dict) -> dict:
    """Remove our hook, dropping groups and events left empty."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            inner = [
                h for h in group.get("hooks", []) or []
                if not (isinstance(h, dict) and _is_ours(h.get("command", "")))
            ]
            # A group that only held our hook goes entirely; one with others stays.
            if inner:
                group["hooks"] = inner
                kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    return data


# --- CLI ---------------------------------------------------------------------------

def _list_kiro_agents(hooked: bool) -> list[Path]:
    """Kiro agent configs, filtered by whether they already carry our hook.

    hooked=True  -> configs we've already hooked (candidates for removal)
    hooked=False -> configs without our hook (candidates for installation)

    Unreadable or non-JSON files are skipped rather than reported: the directory is the
    user's, and setup shouldn't fail because something unrelated lives there.
    """
    if not KIRO_AGENTS_DIR.is_dir():
        return []
    result = []
    for path in sorted(KIRO_AGENTS_DIR.glob("*.json")):
        try:
            data = _read_json(path)
        except (ValueError, json.JSONDecodeError, OSError):
            continue
        if kiro_has_hook(data) == hooked:
            result.append(path)
    return result


def main(argv: list[str] = None) -> int:
    p = argparse.ArgumentParser(description="Install or remove tmux-sentinel hooks.")
    p.add_argument("--command", help="the hook command to install")
    p.add_argument("--list-kiro-eligible", action="store_true",
                   help="list Kiro configs without our hook, one per line")
    p.add_argument("--list-kiro-hooked", action="store_true",
                   help="list Kiro configs with our hook, one per line")
    p.add_argument("--add-kiro", nargs="+", metavar="FILE")
    p.add_argument("--remove-kiro", nargs="+", metavar="FILE")
    p.add_argument("--add-claude", action="store_true")
    p.add_argument("--remove-claude", action="store_true")
    p.add_argument("--claude-has-hook", action="store_true",
                   help="exit 0 if Claude settings already have our hook, else 1")
    args = p.parse_args(argv)

    if args.list_kiro_eligible:
        for path in _list_kiro_agents(hooked=False):
            print(path)
        return 0

    if args.list_kiro_hooked:
        for path in _list_kiro_agents(hooked=True):
            print(path)
        return 0

    if args.claude_has_hook:
        try:
            return 0 if claude_has_hook(_read_json(CLAUDE_SETTINGS)) else 1
        except (ValueError, json.JSONDecodeError, OSError):
            return 1

    if args.add_kiro or args.remove_kiro:
        if args.add_kiro and not args.command:
            p.error("--add-kiro requires --command")
        count = 0
        for name in args.add_kiro or args.remove_kiro:
            path = Path(name)
            try:
                data = _read_json(path)
            except (ValueError, json.JSONDecodeError, OSError) as e:
                print(f"skipped {path}: {e}", file=sys.stderr)
                continue
            data = (kiro_add(data, args.command) if args.add_kiro
                    else kiro_remove(data))
            _write_json(path, data)
            count += 1
        print(count)
        return 0

    if args.add_claude or args.remove_claude:
        if args.add_claude and not args.command:
            p.error("--add-claude requires --command")
        try:
            data = _read_json(CLAUDE_SETTINGS)
        except (ValueError, json.JSONDecodeError, OSError) as e:
            print(f"cannot read {CLAUDE_SETTINGS}: {e}", file=sys.stderr)
            return 1
        data = (claude_add(data, args.command) if args.add_claude
                else claude_remove(data))
        _write_json(CLAUDE_SETTINGS, data)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
