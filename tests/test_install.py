"""Tests for tmux_sentinel.install — hook injection and removal.

These replace the jq filters that setup.sh used to build as bash strings. That logic
edits the user's real agent configs, so it's worth testing directly rather than only
through the installer.
"""
import json
import tempfile
from pathlib import Path

from tmux_sentinel.install import (
    kiro_add, kiro_remove, kiro_has_hook,
    claude_add, claude_remove, claude_has_hook,
    _read_json, _write_json, _is_ours,
    KIRO_EVENTS, CLAUDE_EVENTS,
)

CMD = "PYTHONPATH=/repo python3 -S /repo/tmux_sentinel/hook.py"
OTHER = "echo 'someone elses hook'"


# --- recognising our own hook ------------------------------------------------------

def test_is_ours_matches_python_hook():
    assert _is_ours("PYTHONPATH=/x python3 -S /x/tmux_sentinel/hook.py")


def test_is_ours_matches_legacy_bash_hook():
    # Earlier versions installed a bash hook; removal must still find it.
    assert _is_ours("/home/u/tmux-sentinel/hooks/notify.sh")


def test_is_ours_rejects_other_hooks():
    assert not _is_ours(OTHER)
    assert not _is_ours("")


# --- Kiro: flat list per event -----------------------------------------------------

def test_kiro_add_to_empty_config():
    data = kiro_add({}, CMD)
    assert sorted(data["hooks"]) == sorted(KIRO_EVENTS)
    for event in KIRO_EVENTS:
        assert data["hooks"][event][0]["command"] == CMD


def test_kiro_add_is_idempotent():
    data = kiro_add(kiro_add({}, CMD), CMD)
    for event in KIRO_EVENTS:
        ours = [e for e in data["hooks"][event] if _is_ours(e["command"])]
        assert len(ours) == 1, f"{event} got a duplicate hook"


def test_kiro_add_preserves_other_hooks():
    data = {"hooks": {"agentSpawn": [{"command": OTHER}]}}
    data = kiro_add(data, CMD)
    commands = [e["command"] for e in data["hooks"]["agentSpawn"]]
    assert OTHER in commands
    assert CMD in commands


def test_kiro_add_preserves_unrelated_top_level_keys():
    data = kiro_add({"name": "my-agent", "model": "x"}, CMD)
    assert data["name"] == "my-agent"
    assert data["model"] == "x"


def test_kiro_remove_deletes_our_hook():
    data = kiro_remove(kiro_add({}, CMD))
    assert "hooks" not in data, "empty hooks dict should be dropped entirely"


def test_kiro_remove_keeps_other_hooks():
    data = {"hooks": {"agentSpawn": [{"command": OTHER}]}}
    data = kiro_remove(kiro_add(data, CMD))
    assert data["hooks"]["agentSpawn"] == [{"command": OTHER}]


def test_kiro_remove_when_absent_is_noop():
    original = {"hooks": {"agentSpawn": [{"command": OTHER}]}}
    assert kiro_remove(json.loads(json.dumps(original))) == original


def test_kiro_remove_upgrades_legacy_hook():
    # A config carrying only the old bash hook should end up clean.
    data = {"hooks": {"stop": [{"command": "/x/tmux-sentinel/hooks/notify.sh"}]}}
    assert "hooks" not in kiro_remove(data)


def test_kiro_add_replaces_legacy_hook():
    data = {"hooks": {"stop": [{"command": "/x/tmux-sentinel/hooks/notify.sh"}]}}
    data = kiro_add(data, CMD)
    stop = [e["command"] for e in data["hooks"]["stop"]]
    assert stop == [CMD], "legacy hook should be replaced, not duplicated"


def test_kiro_has_hook():
    assert not kiro_has_hook({})
    assert not kiro_has_hook({"hooks": {"stop": [{"command": OTHER}]}})
    assert kiro_has_hook(kiro_add({}, CMD))


# --- Claude Code: matcher groups ---------------------------------------------------

def test_claude_add_to_empty_config():
    data = claude_add({}, CMD)
    assert sorted(data["hooks"]) == sorted(CLAUDE_EVENTS)
    group = data["hooks"]["Stop"][0]
    assert group["matcher"] == ""
    assert group["hooks"][0] == {"type": "command", "command": CMD}


def test_claude_add_is_idempotent():
    data = claude_add(claude_add({}, CMD), CMD)
    for event in CLAUDE_EVENTS:
        ours = [
            h for g in data["hooks"][event]
            for h in g["hooks"] if _is_ours(h["command"])
        ]
        assert len(ours) == 1, f"{event} got a duplicate hook"


def test_claude_add_preserves_user_matcher_groups():
    # A user's own matcher-specific hook must survive untouched.
    data = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": OTHER}]}
    ]}}
    data = claude_add(data, CMD)
    groups = data["hooks"]["PreToolUse"]
    assert groups[0] == {"matcher": "Bash",
                         "hooks": [{"type": "command", "command": OTHER}]}
    assert any(h["command"] == CMD for g in groups for h in g["hooks"])


def test_claude_remove_deletes_our_hook():
    assert "hooks" not in claude_remove(claude_add({}, CMD))


def test_claude_remove_keeps_user_groups():
    data = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": OTHER}]}
    ]}}
    data = claude_remove(claude_add(data, CMD))
    assert data["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": OTHER}]}
    ]


def test_claude_remove_keeps_siblings_inside_a_shared_group():
    # Our hook sharing a group with another: the group stays, minus ours.
    data = {"hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": OTHER},
        {"type": "command", "command": CMD},
    ]}]}}
    data = claude_remove(data)
    assert data["hooks"]["Stop"] == [
        {"matcher": "", "hooks": [{"type": "command", "command": OTHER}]}
    ]


def test_claude_remove_when_absent_is_noop():
    original = {"hooks": {"Stop": [
        {"matcher": "", "hooks": [{"type": "command", "command": OTHER}]}
    ]}}
    assert claude_remove(json.loads(json.dumps(original))) == original


def test_claude_preserves_unrelated_settings():
    data = claude_add({"model": "opus", "permissions": {"deny": ["x"]}}, CMD)
    assert data["model"] == "opus"
    assert data["permissions"] == {"deny": ["x"]}
    data = claude_remove(data)
    assert data["model"] == "opus"
    assert data["permissions"] == {"deny": ["x"]}


def test_claude_has_hook():
    assert not claude_has_hook({})
    assert claude_has_hook(claude_add({}, CMD))


# --- file I/O ----------------------------------------------------------------------

def test_read_missing_file_returns_empty():
    assert _read_json(Path("/no/such/file.json")) == {}


def test_read_rejects_non_object_json():
    path = Path(tempfile.mkdtemp()) / "a.json"
    path.write_text("[1, 2, 3]")
    try:
        _read_json(path)
        assert False, "should reject a JSON array"
    except ValueError:
        pass


def test_write_then_read_roundtrip():
    path = Path(tempfile.mkdtemp()) / "nested" / "b.json"
    _write_json(path, {"hooks": {"stop": []}})
    assert _read_json(path) == {"hooks": {"stop": []}}


def test_write_leaves_no_temp_files():
    d = Path(tempfile.mkdtemp())
    path = d / "c.json"
    _write_json(path, {"x": 1})
    assert [p.name for p in d.iterdir()] == ["c.json"]


def test_write_preserves_original_on_failure():
    # An unserialisable value must not clobber a good file.
    path = Path(tempfile.mkdtemp()) / "d.json"
    _write_json(path, {"good": True})
    try:
        _write_json(path, {"bad": object()})
    except TypeError:
        pass
    assert _read_json(path) == {"good": True}


# --- listing agents by hook state -------------------------------------------------
#
# This filter was inverted on the first attempt — it listed hooked configs as
# "eligible" and vice versa, so setup silently installed nothing. Worth testing
# directly rather than only through the installer.

def test_list_kiro_agents_splits_by_hook_state():
    import tmux_sentinel.install as inst
    d = Path(tempfile.mkdtemp())
    (d / "unhooked.json").write_text(json.dumps({"name": "u"}))
    (d / "hooked.json").write_text(json.dumps(
        {"hooks": {"stop": [{"command": CMD}]}}))
    orig = inst.KIRO_AGENTS_DIR
    inst.KIRO_AGENTS_DIR = d
    try:
        eligible = [p.name for p in inst._list_kiro_agents(hooked=False)]
        hooked = [p.name for p in inst._list_kiro_agents(hooked=True)]
        assert eligible == ["unhooked.json"], eligible
        assert hooked == ["hooked.json"], hooked
    finally:
        inst.KIRO_AGENTS_DIR = orig


def test_list_kiro_agents_skips_unparseable_files():
    # A stray non-JSON file in the user's agents dir shouldn't break setup.
    import tmux_sentinel.install as inst
    d = Path(tempfile.mkdtemp())
    (d / "good.json").write_text(json.dumps({"name": "g"}))
    (d / "bad.json").write_text("not json at all {{{")
    orig = inst.KIRO_AGENTS_DIR
    inst.KIRO_AGENTS_DIR = d
    try:
        assert [p.name for p in inst._list_kiro_agents(hooked=False)] == ["good.json"]
    finally:
        inst.KIRO_AGENTS_DIR = orig


def test_list_kiro_agents_missing_dir_is_empty():
    import tmux_sentinel.install as inst
    orig = inst.KIRO_AGENTS_DIR
    inst.KIRO_AGENTS_DIR = Path("/no/such/agents/dir")
    try:
        assert inst._list_kiro_agents(hooked=False) == []
        assert inst._list_kiro_agents(hooked=True) == []
    finally:
        inst.KIRO_AGENTS_DIR = orig


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
