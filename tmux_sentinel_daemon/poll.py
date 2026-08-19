"""
Periodic polling logic for the daemon.

Performs the same process tree walk and screen-scrape that statusbar.py does,
but updates the in-memory DaemonState instead of writing files.
"""
from __future__ import annotations

import re

from tmux_sentinel.status import IDLE, WORKING, WAITING
from tmux_sentinel.process import get_agent_panes, get_agent_types, _get_process_tree
from tmux_sentinel.tmux import list_panes, pane_pids, capture_pane_tail, focused_pane_id, _run_tmux
from tmux_sentinel.hook import _get_git_branch

from tmux_sentinel_daemon.state import DaemonState
from tmux_sentinel_daemon.manifests import load_all_manifests, classify

_MANIFESTS = load_all_manifests()


def _detect_pane_state(pane_id: str, agent_type: str = "claude") -> str | None:
    """Screen-scrape a pane to detect its actual state."""
    tail = capture_pane_tail(pane_id, lines=10)
    if not tail:
        return None
    rules = _MANIFESTS.get(agent_type)
    if not rules:
        return None
    return classify(tail, rules)


# Evidence that a turn is running *right now*, used to promote idle -> working.
# Deliberately stricter than the manifest's idle-veto pattern: that one may match
# leftover text from a finished turn ("Crunched for 1m 9s"), which is fine for
# vetoing but would wrongly promote an idle pane.
#
# A live turn renders a status line of the form "<glyph> <verb>… (<elapsed> · …)",
# e.g. "✳ Working… (30s · ↓ 1.2k tokens)". Matching is deliberately verb-agnostic:
# Claude Code rotates the gerund freely (Working, Crunching, Churning, Pondering,
# and others it may add), so enumerating them would silently rot — a verb we hadn't
# listed would leave a working pane stuck showing IDL until its first tool call.
#
# What identifies the line instead is its *shape*: anchored at the start of a line,
# a single non-space glyph, one word ending in the elision character, then a
# parenthesised number. The anchor matters — without it the pattern also matches
# prose that merely quotes it (a pane discussing this very regex would trip it).
# The "…(" is what makes it *live*: a completed turn reads "Crunched for 1m 9s",
# with no ellipsis and no parenthesis.
#
# A third form covers background agents. The main agent can sit idle at an empty
# prompt while a background agent works, and its footer then looks completely idle —
# so without this the pane reads IDL even though work is happening. Agents are listed
# below the footer, one per row, with a right-aligned status:
#
#     ⏺ main
#     ◯ kairos-V2331008326  Analyse this oncall ticket…    1m 3s · ↓ 132.0k tokens
#
# A running agent's row ends in an elapsed time; a finished one ends in the literal
# "idle". The row itself persists either way, so the trailing status is the only thing
# that distinguishes them.
#
# Deliberately not keyed to the ◯/⏺ glyph: those mark which entry is *focused* in the
# list, not which agent is running, and they swap as you move between agents. An
# earlier version of this pattern assumed ◯ meant "running" and was wrong.
#
# The name is required to be a non-space run without spaces, which keeps this from
# matching ordinary transcript lines (those start with ⏺ too, but are followed by
# prose). The timer is bare rather than parenthesised, which is why the two patterns
# above can't see it.
_WORKING_MARKER = re.compile(
    r"^[ \t]*\S[ \t]+\w+…[ \t]*\(\d"
    r"|esc to interrupt"
    r"|^[ \t]*[◯⏺][ \t]+\S+[ \t]{2,}.*?\b\d+[hms]\b[ \t]*(?:·|$)",
    re.MULTILINE,
)


def _has_working_marker(tail: str) -> bool:
    """True if the captured text shows a turn actively in progress."""
    return bool(_WORKING_MARKER.search(tail or ""))


_WINDOW_NAME_MAP = {"claude": "claude", "kiro": "kiro"}
_RENAME_TRIGGERS = {"toolbox-exec"}


def _fix_window_names(agent_types: dict[str, str], panes: list) -> None:
    """Rename windows stuck on 'toolbox-exec' (or similar) to the agent name."""
    pane_windows = {p.pane_id: p.window_name for p in panes}
    for pane_id, agent_type in agent_types.items():
        window_name = pane_windows.get(pane_id, "")
        if window_name in _RENAME_TRIGGERS:
            new_name = _WINDOW_NAME_MAP.get(agent_type)
            if new_name:
                _run_tmux("set-option", "-t", f"%{pane_id}", "-w", "automatic-rename", "off")
                _run_tmux("rename-window", "-t", f"%{pane_id}", new_name)


def run_poll(state: DaemonState) -> None:
    """Run one poll cycle: process tree walk + screen-scrape."""
    import time

    all_pane_pids = pane_pids()
    process_tree = _get_process_tree()
    live_agent_panes = get_agent_panes(all_pane_pids, process_tree=process_tree)

    state.focused_pane = focused_pane_id()

    panes = list_panes()
    pane_paths = {p.pane_id: p.pane_current_path for p in panes}

    agent_types = get_agent_types(all_pane_pids, process_tree=process_tree)
    _fix_window_names(agent_types, panes)

    stale_panes = set(state.panes.keys()) - live_agent_panes
    for pane_id in stale_panes:
        state.remove_pane(pane_id)

    now = int(time.time())
    for pane_id in live_agent_panes:
        agent_type = agent_types.get(pane_id, "claude")
        ps = state.get(pane_id)
        if ps is None:
            cwd = pane_paths.get(pane_id, "")
            branch = _get_git_branch(cwd) if cwd else ""
            ps = state.ensure(pane_id)
            ps.cwd = cwd
            ps.git_branch = branch
            ps.timestamp = now
            ps.agent_type = agent_type
            actual = _detect_pane_state(pane_id, agent_type)
            ps.status = actual if actual else IDLE
            continue

        ps.agent_type = agent_type

        if ps.status in (WORKING, WAITING):
            actual = _detect_pane_state(pane_id, agent_type)
            if actual == WAITING:
                ps.status = WAITING
            elif actual == IDLE:
                ps.status = IDLE
                ps.unseen = True
            elif actual is None and ps.status == WAITING:
                ps.status = WORKING
                # Back to working means no completed-and-unseen result stands.
                ps.unseen = False

        elif ps.status == IDLE:
            # Nothing else promotes idle -> working: only a hook does, and a turn
            # can begin without one reaching us (resumed session, /compact
            # continuation, agent-initiated turn, or a dropped hook). Such a pane
            # would sit on IDL until its first PreToolUse. The live spinner /
            # elapsed-timer marker is positive evidence of a turn in progress, so
            # promote on it — and since the turn is running, no finished-but-unseen
            # result stands.
            if _has_working_marker(capture_pane_tail(pane_id, lines=10)):
                ps.status = WORKING
                ps.timestamp = now
                ps.unseen = False

    if state.focused_pane:
        state.mark_seen(state.focused_pane)
