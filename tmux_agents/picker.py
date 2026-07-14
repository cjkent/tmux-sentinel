"""
Window picker for tmux-agents.

An fzf-based popup showing all tmux windows across all sessions with agent
status metadata. Bound to Ctrl+b a via setup.sh.

Agent status comes from status files written by the hook (hook.py). Only panes
that have a status file are shown as agents — panes without a status file show
as plain windows with [---]. This prevents false positives from processes that
run kiro-cli non-interactively (e.g. KiRoom server workers).

For agents with "working" status, the picker screen-scrapes the pane to detect:
  - Approval prompts → shown as [WAI] with unseen marker
  - Idle input prompt → shown as [IDL] (handles cancelled agents)

Pipeline:
  1. Clean up stale status files (panes where kiro-cli chat is no longer running)
  2. Build display rows with columns: marker, index, name, status, cwd, branch, elapsed
  3. Align columns using Python string formatting
  4. Colorize status labels with ANSI codes
  5. Pipe to fzf for interactive selection
  6. Switch to the selected session/window

Usage:
    python3 path/to/picker.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tmux_agents.status import (
    WORKING, WAITING, STATUS_DIR,
    read_status, is_unseen, cleanup_stale, recreate_missing,
)
from tmux_agents.hook import _get_git_branch
from tmux_agents.process import get_kiro_panes, get_agent_types
from tmux_agents.tmux import (
    list_panes, list_sessions, pane_pids,
    current_session, current_window_index, switch_to, PaneInfo,
)
from tmux_agents.formatting import (
    elapsed, status_label, colorize_status, align_columns,
    RED, RESET,
)


_AGENT_ICONS = {"kiro": "👻", "claude": "🟠"}



def _build_rows(
    panes: list[PaneInfo],
    cur_session: str,
    cur_window: str,
    status_dir: Path = None,
    agent_types: dict[str, str] = None,
    git_branches: dict[str, str] = None,
    daemon_state: dict = None,
) -> tuple[list[list[str]], list[str]]:
    """
    Build display rows and corresponding targets for the picker.

    If daemon_state is provided (a dict keyed by pane_id from the daemon's
    'dump' command), agent panes use its already-computed status/unseen/branch,
    avoiding per-pane capture-pane and status-file reads. Panes not in
    daemon_state fall back to the status-file path.

    Returns:
        (rows, targets) where rows[i] is a list of column values and
        targets[i] is "session:window" or "" for session headers.
    """
    from tmux_agents.status import STATUS_DIR as DEFAULT_DIR
    sd = status_dir or DEFAULT_DIR
    at = agent_types or {}
    gb = git_branches or {}
    ds = daemon_state or {}
    home = os.path.expanduser("~")
    rows: list[list[str]] = []
    targets: list[str] = []

    # Group panes by session
    sessions: dict[str, list[PaneInfo]] = {}
    for p in panes:
        sessions.setdefault(p.session, []).append(p)

    # Use session order from tmux
    session_order = list_sessions()
    for session in session_order:
        if session not in sessions:
            continue
        # Session header
        rows.append([f"── {session} ──", "", "", "", "", "", ""])
        targets.append(f"{session}:")

        for p in sessions[session]:
            agent_icon = _AGENT_ICONS.get(at.get(p.pane_id, ""), "  ")
            is_current = (p.session == cur_session and p.window_index == cur_window)
            daemon_pane = ds.get(p.pane_id)
            if daemon_pane is not None:
                # Fast path: daemon supplies status/unseen/timestamp (no capture-pane).
                # For cwd/branch, prefer the status file when present: the hook writes
                # the agent's own cwd, whereas the daemon falls back to the shell's cwd
                # for panes it discovered by polling rather than via a SessionStart hook.
                display_status = daemon_pane["status"]
                icon = status_label(display_status)
                unseen = daemon_pane.get("unseen", False)
                icon_display = f"{icon} ●" if unseen else f"{icon}  "
                el = elapsed(daemon_pane["timestamp"]) if display_status == WORKING else ""
                file_status = read_status(p.pane_id, status_dir=sd)
                if file_status:
                    cwd = file_status.cwd or daemon_pane.get("cwd", "") or p.pane_current_path
                    branch = f"({file_status.git_branch})" if file_status.git_branch else ""
                else:
                    cwd = daemon_pane.get("cwd", "") or p.pane_current_path
                    branch = f"({daemon_pane['git_branch']})" if daemon_pane.get("git_branch") else ""
            elif (status := read_status(p.pane_id, status_dir=sd)):
                display_status = status.status
                # Screen-scrape working agents to detect approval prompts or stale state
                if status.status == WORKING:
                    from tmux_agents_daemon.poll import _detect_pane_state
                    actual = _detect_pane_state(p.pane_id, at.get(p.pane_id, "claude"))
                    if actual is not None:
                        display_status = actual
                icon = status_label(display_status)
                unseen = is_unseen(p.pane_id, status_dir=sd)
                # Agents detected as waiting for approval are "unseen" unless it's the current window
                if display_status == WAITING and status.status == WORKING and not is_current:
                    unseen = True
                icon_display = f"{icon} ●" if unseen else f"{icon}  "
                el = elapsed(status.timestamp) if display_status == WORKING else ""
                cwd = status.cwd
                branch = f"({status.git_branch})" if status.git_branch else ""
            else:
                icon_display = "[---]  "
                agent_icon = "  "
                el = ""
                cwd = p.pane_current_path
                git_branch = gb.get(p.pane_id, "")
                branch = f"({git_branch})" if git_branch else ""

            short_cwd = cwd.replace(home, "~", 1) if cwd.startswith(home) else cwd
            marker = "► " if (p.session == cur_session and p.window_index == cur_window) else "  "

            rows.append([
                f"{marker}{p.window_index}: {p.window_name}",
                agent_icon,
                icon_display,
                short_cwd,
                branch,
                el,
            ])
            targets.append(f"{p.session}:{p.window_index}")

    return rows, targets


_BOLD = "\033[1m"

def _colorize_line(line: str) -> str:
    """Apply ANSI colors to status labels and unseen markers in a line."""
    from tmux_agents.formatting import GREEN
    is_current = "►" in line
    replacements = [
        ("►", f"{GREEN}►{RESET}"),
        ("[IDL]", "\033[32m[IDL]\033[0m"),
        ("[WRK]", "\033[34m[WRK]\033[0m"),
        ("[WAI]", "\033[35m[WAI]\033[0m"),
        ("[ERR]", "\033[31m[ERR]\033[0m"),
        (" ●", f" {RED}●{RESET}"),
    ]
    for old, new in replacements:
        line = line.replace(old, new)
    if is_current:
        line = f"{_BOLD}{line.replace(RESET, RESET + _BOLD)}{RESET}"
    return line


def _generate_list() -> str:
    """Generate the fzf input list.

    Fast path: if the daemon is running, use its in-memory state snapshot for
    agent panes (no process-tree walk, no per-pane capture-pane). Fall back to
    the direct file+ps path if the daemon is unavailable.
    """
    from tmux_agents_daemon.client import dump_state

    daemon_state = dump_state()
    if daemon_state is not None:
        return _generate_list_from_daemon(daemon_state)
    return _generate_list_direct()


def _generate_list_from_daemon(daemon_state: dict) -> str:
    """Build the list from the daemon's state snapshot (fast path)."""
    from concurrent.futures import ThreadPoolExecutor
    from tmux_agents.hook import _get_git_branch

    panes = list_panes()
    cur_sess = current_session()
    cur_win = current_window_index()

    # Agent types drive the icon column; the daemon reports type per pane.
    agent_types = {pid: st.get("agent_type", "claude") for pid, st in daemon_state.items()}

    # Only non-agent panes need a git lookup; the daemon supplies branch for
    # agent panes (computed from the agent's own cwd). Lookups run in parallel.
    with ThreadPoolExecutor() as ex:
        git_futs = {
            p.pane_id: ex.submit(_get_git_branch, p.pane_current_path)
            for p in panes
            if p.pane_id not in daemon_state and p.pane_current_path
        }
        git_branches = {pid: fut.result() for pid, fut in git_futs.items()}

    rows, targets = _build_rows(
        panes, cur_sess, cur_win,
        agent_types=agent_types, git_branches=git_branches, daemon_state=daemon_state,
    )
    return _render(rows, targets)


def _generate_list_direct() -> str:
    """Build the list via direct file + ps inspection (daemon-unavailable fallback)."""
    from concurrent.futures import ThreadPoolExecutor
    from tmux_agents.process import _get_process_tree
    from tmux_agents.hook import _get_git_branch
    from tmux_agents.status import read_status

    pp = pane_pids()
    panes = list_panes()

    # Run ps and all git branch lookups in parallel
    with ThreadPoolExecutor() as ex:
        tree_fut = ex.submit(_get_process_tree)
        git_futs = {}
        for p in panes:
            if not read_status(p.pane_id) and p.pane_current_path:
                git_futs[p.pane_id] = ex.submit(_get_git_branch, p.pane_current_path)
        tree = tree_fut.result()
        git_branches = {pid: fut.result() for pid, fut in git_futs.items()}

    live_kiro = get_kiro_panes(pp, process_tree=tree)
    cleanup_stale(live_kiro)

    pane_paths = {p.pane_id: p.pane_current_path for p in panes}
    recreate_missing(live_kiro, pane_paths)
    cur_sess = current_session()
    cur_win = current_window_index()
    agent_types = get_agent_types(pp, process_tree=tree)

    rows, targets = _build_rows(panes, cur_sess, cur_win, agent_types=agent_types, git_branches=git_branches)
    return _render(rows, targets)


def _render(rows: list[list[str]], targets: list[str]) -> str:
    """Align, colorize, and join rows into the fzf input string."""
    aligned = align_columns(rows)
    colorized = [_colorize_line(line) for line in aligned]
    sep = "\x1f"
    return "\n".join(f"{line}{sep}{target}" for line, target in zip(colorized, targets))


def main() -> None:
    # --list mode: output the list and exit (used by fzf reload)
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        sys.stdout.write(_generate_list())
        return

    # --close mode: kill a window or session (used by fzf ctrl-x)
    if len(sys.argv) > 1 and sys.argv[1] == "--close":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        if ":" in target:
            session, window = target.split(":", 1)
            if window:
                subprocess.run(["tmux", "kill-window", "-t", f"{session}:{window}"],
                               capture_output=True)
            else:
                subprocess.run(["tmux", "kill-session", "-t", session],
                               capture_output=True)
        return

    fzf_input = _generate_list()
    sep = "\x1f"

    # Build the reload and close commands for fzf
    script = f"PYTHONPATH={os.environ.get('PYTHONPATH', '.')} python3 {__file__}"
    close_cmd = f"{script} --close {{2}}"
    reload_cmd = f"{script} --list"

    # Run fzf
    try:
        result = subprocess.run(
            [
                "fzf",
                "--ansi",
                "--no-sort",
                "--reverse",
                "--prompt=Switch to > ",
                "--header=ctrl-x: close window/session",
                "--no-info",
                "--no-multi",
                "--cycle",
                f"--delimiter={sep}",
                "--with-nth=1",
                "--bind", f"ctrl-x:execute-silent({close_cmd})+reload({reload_cmd})",
            ],
            input=fzf_input,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("fzf not found", file=sys.stderr)
        return

    if result.returncode != 0 or not result.stdout.strip():
        return

    # Extract target from selection
    selection = result.stdout.strip()
    target = selection.split(sep)[-1] if sep in selection else ""
    if ":" in target:
        session, window = target.split(":", 1)
        if window:
            switch_to(session, window)


if __name__ == "__main__":
    main()
