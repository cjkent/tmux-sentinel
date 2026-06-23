"""
Process tree inspection for tmux-agents.

Detects which tmux panes have an interactive AI agent (Kiro CLI or Claude Code)
running as a descendant process. Uses a single `ps -eo pid,ppid,args` call to
build a process tree, then walks descendants from each pane's shell PID.

Kiro CLI matching: command line must contain both 'kiro-cli' and 'chat'.
This distinguishes interactive agents from non-interactive uses like
'kiro-cli acp' (background workers).

Claude Code matching: command line must contain '/claude' (path separator
ensures we match the binary, not unrelated strings). Excludes the otelcol
sidecar process.

Note: on macOS, the pane's shell process name persists as "kiro-cli-term"
even after kiro exits. We don't rely on the shell name — we check for actual
child processes in the tree.
"""
from __future__ import annotations

import subprocess
from typing import Optional


def _get_process_tree() -> dict[str, tuple[str, str]]:
    """Return {pid: (ppid, args)} from ps. All values are strings."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,args"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    tree: dict[str, tuple[str, str]] = {}
    for line in result.stdout.strip().splitlines()[1:]:  # skip header
        parts = line.split(None, 2)
        if len(parts) >= 3:
            tree[parts[0]] = (parts[1], parts[2])
    return tree


def _is_agent_process(args: str) -> bool:
    """Check if a command line belongs to an interactive AI agent."""
    if "kiro-cli" in args and "chat" in args:
        return True
    if "/claude" in args and "otelcol" not in args:
        return True
    return False


def _has_agent_descendant(root_pid: str, tree: dict[str, tuple[str, str]]) -> bool:
    """Walk the process tree from root_pid, return True if any descendant is an interactive AI agent."""
    # Build children map for efficient traversal
    children: dict[str, list[str]] = {}
    for pid, (ppid, _) in tree.items():
        children.setdefault(ppid, []).append(pid)

    # BFS from root
    queue = [root_pid]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for child in children.get(current, []):
            entry = tree.get(child)
            if entry and _is_agent_process(entry[1]):
                return True
            queue.append(child)
    return False


def get_agent_panes(
    pane_pids: dict[str, str],
    process_tree: Optional[dict[str, tuple[str, str]]] = None,
) -> set[str]:
    """
    Return the set of pane IDs that have an AI agent running as a descendant.

    Detects both Kiro CLI (kiro-cli chat) and Claude Code (/path/to/claude).

    Args:
        pane_pids: {pane_id: pane_pid} mapping from tmux
        process_tree: optional pre-built tree for testing; if None, calls ps
    """
    if process_tree is None:
        process_tree = _get_process_tree()
    return {
        pane_id
        for pane_id, pane_pid in pane_pids.items()
        if _has_agent_descendant(pane_pid, process_tree)
    }


def _agent_type_for(root_pid: str, tree: dict[str, tuple[str, str]]) -> Optional[str]:
    """Return the agent type running under root_pid, or None."""
    children: dict[str, list[str]] = {}
    for pid, (ppid, _) in tree.items():
        children.setdefault(ppid, []).append(pid)

    queue = [root_pid]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for child in children.get(current, []):
            entry = tree.get(child)
            if entry:
                args = entry[1]
                if "kiro-cli" in args and "chat" in args:
                    return "kiro"
                if "/claude" in args and "otelcol" not in args:
                    return "claude"
            queue.append(child)
    return None


def get_agent_types(
    pane_pids: dict[str, str],
    process_tree: Optional[dict[str, tuple[str, str]]] = None,
) -> dict[str, str]:
    """
    Return {pane_id: agent_type} for panes with an agent running.

    agent_type is "kiro" or "claude".
    """
    if process_tree is None:
        process_tree = _get_process_tree()
    result = {}
    for pane_id, pane_pid in pane_pids.items():
        agent_type = _agent_type_for(pane_pid, process_tree)
        if agent_type:
            result[pane_id] = agent_type
    return result


# Backward compatibility
get_kiro_panes = get_agent_panes
_has_kiro_descendant = _has_agent_descendant
