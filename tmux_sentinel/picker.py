"""
Window picker for tmux-sentinel.

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

from tmux_sentinel.status import (
    WORKING, WAITING, STATUS_DIR,
    read_status, is_unseen, cleanup_stale, recreate_missing,
)
from tmux_sentinel.hook import _get_git_branch
from tmux_sentinel.process import get_kiro_panes, get_agent_types
from tmux_sentinel.tmux import (
    list_panes, list_sessions, pane_pids,
    current_session, current_window_index, current_session_window, switch_to, PaneInfo,
)
from tmux_sentinel.formatting import (
    elapsed, status_label, colorize_status, align_columns,
    RED, RESET,
)


_AGENT_ICONS = {"kiro": "👻", "claude": "🟠"}

_MAX_TITLE_LEN = 40


def _home_symlink_targets(home: str) -> dict[str, str]:
    """Map resolved-symlink-target -> '~/name' for symlinked directories
    directly under $HOME (e.g. ~/workplace -> /Volumes/workplace).

    Lets _shorten_path show '~/workplace/foo' instead of the raw
    '/Volumes/workplace/foo' a pane's cwd resolves to, since tmux/the shell
    report the real path, not the symlinked one the user actually navigates.
    """
    targets: dict[str, str] = {}
    try:
        with os.scandir(home) as entries:
            for entry in entries:
                if entry.is_symlink():
                    try:
                        target = os.path.realpath(entry.path)
                    except OSError:
                        continue
                    if os.path.isdir(target):
                        targets[target] = f"~/{entry.name}"
    except OSError:
        pass
    return targets


def _shorten_path(path: str, home: str, home_symlinks: dict[str, str]) -> str:
    """Shorten a path using $HOME, then the longest matching home symlink."""
    if path.startswith(home):
        return "~" + path[len(home):]
    best = None
    for target, alias in home_symlinks.items():
        if path == target or path.startswith(target + "/"):
            if best is None or len(target) > len(best[0]):
                best = (target, alias)
    if best:
        target, alias = best
        return alias + path[len(target):]
    return path


def _display_name(pane: PaneInfo, agent_type: str) -> str:
    """Return the name to show for a pane: an agent's live task title when
    available, otherwise the tmux window name.

    Claude Code sets pane_title via OSC escape codes as "<spinner-glyph>
    <task summary>" while it works. We only trust the title for panes we
    already know are running an agent (same detection that drives the icon
    column), since a plain shell's title is just whatever the last command
    happened to set (often a hash-looking string from the prompt).
    """
    if agent_type in _AGENT_ICONS and pane.pane_title:
        title = pane.pane_title.strip()
        # Strip a single leading spinner glyph (non-alphanumeric char + space).
        if title and not title[0].isalnum():
            title = title[1:].strip()
        # Reject a bare hex-looking hash (idle/default title, not a task).
        elif title and len(title) >= 8 and all(c in "0123456789abcdef" for c in title.lower()):
            title = ""
        if title:
            return title[:_MAX_TITLE_LEN] + ("…" if len(title) > _MAX_TITLE_LEN else "")
    return pane.window_name


def _build_rows(
    panes: list[PaneInfo],
    cur_session: str,
    cur_window: str,
    status_dir: Path = None,
    agent_types: dict[str, str] = None,
    git_branches: dict[str, str] = None,
    daemon_state: dict = None,
    session_order: list[str] = None,
) -> tuple[list[list[str]], list[str]]:
    """
    Build display rows and corresponding targets for the picker.

    If daemon_state is provided (a dict keyed by pane_id from the daemon's
    'dump' command), agent panes use its already-computed status/unseen/branch,
    avoiding per-pane capture-pane and status-file reads. Panes not in
    daemon_state fall back to the status-file path.

    session_order gives the display order of sessions; if omitted it is fetched
    via list_sessions() (callers that already have it can pass it to save a call).

    Returns:
        (rows, targets) where rows[i] is a list of column values and
        targets[i] is "session:window" or "" for session headers.
    """
    from tmux_sentinel.status import STATUS_DIR as DEFAULT_DIR
    sd = status_dir or DEFAULT_DIR
    at = agent_types or {}
    gb = git_branches or {}
    ds = daemon_state or {}
    home = os.path.expanduser("~")
    home_symlinks = _home_symlink_targets(home)
    rows: list[list[str]] = []
    targets: list[str] = []

    # Group panes by session
    sessions: dict[str, list[PaneInfo]] = {}
    for p in panes:
        sessions.setdefault(p.session, []).append(p)

    # Use session order from tmux
    if session_order is None:
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
            unseen = False
            daemon_pane = ds.get(p.pane_id)
            if daemon_pane is not None:
                # Fast path: daemon supplies status/unseen/timestamp (no capture-pane).
                # For cwd/branch, prefer the status file when present: the hook writes
                # the agent's own cwd, whereas the daemon falls back to the shell's cwd
                # for panes it discovered by polling rather than via a SessionStart hook.
                display_status = daemon_pane["status"]
                icon = status_label(display_status)
                unseen = daemon_pane.get("unseen", False)
                icon_display = icon
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
                    from tmux_sentinel_daemon.poll import _detect_pane_state
                    actual = _detect_pane_state(p.pane_id, at.get(p.pane_id, "claude"))
                    if actual is not None:
                        display_status = actual
                icon = status_label(display_status)
                unseen = is_unseen(p.pane_id, status_dir=sd)
                # Agents detected as waiting for approval are "unseen" unless it's the current window
                if display_status == WAITING and status.status == WORKING and not is_current:
                    unseen = True
                icon_display = icon
                el = elapsed(status.timestamp) if display_status == WORKING else ""
                cwd = status.cwd
                branch = f"({status.git_branch})" if status.git_branch else ""
            else:
                icon_display = "[---]"
                agent_icon = "  "
                el = ""
                cwd = p.pane_current_path
                git_branch = gb.get(p.pane_id, "")
                branch = f"({git_branch})" if git_branch else ""

            short_cwd = _shorten_path(cwd, home, home_symlinks)
            # The leading marker column is shared: a current window shows ►, an
            # unseen (finished-but-unviewed) window shows ●. These never clash —
            # the current window is always seen — so they occupy one column,
            # keeping "where am I" and "what needs attention" vertically aligned.
            if is_current:
                marker = "► "
            elif unseen:
                marker = "● "
            else:
                marker = "  "
            name = _display_name(p, at.get(p.pane_id, ""))

            rows.append([
                f"{marker}{p.window_index}: {name}",
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
    from tmux_sentinel.formatting import GREEN
    is_current = "►" in line
    replacements = [
        ("►", f"{GREEN}►{RESET}"),
        ("[IDL]", "\033[32m[IDL]\033[0m"),
        ("[WRK]", "\033[34m[WRK]\033[0m"),
        ("[WAI]", "\033[35m[WAI]\033[0m"),
        ("[ERR]", "\033[31m[ERR]\033[0m"),
        # The unseen dot now leads the row (in the shared marker column) rather
        # than trailing the status label, so match it at the start of the line.
        ("● ", f"{RED}●{RESET} "),
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
    from tmux_sentinel_daemon.client import dump_state

    daemon_state = dump_state()
    if daemon_state is not None:
        return _generate_list_from_daemon(daemon_state)
    return _generate_list_direct()


def _generate_list_from_daemon(daemon_state: dict) -> str:
    """Build the list from the daemon's state snapshot (fast path)."""
    from concurrent.futures import ThreadPoolExecutor
    from tmux_sentinel.hook import _get_git_branch

    # The daemon's keys are exactly the panes it currently sees an agent
    # running in (populated via its own ps walk + hook events). Remove status
    # files for any pane not in that set, so an exited agent's last-known
    # status doesn't linger and get picked up by the status-file fallback
    # below. This is the fast-path equivalent of _generate_list_direct's
    # cleanup_stale call — pure file I/O, no subprocess, so it's free here.
    cleanup_stale(set(daemon_state.keys()))

    # The three independent tmux queries and (once panes are known) the git
    # lookups all shell out; run them concurrently so their latencies overlap.
    with ThreadPoolExecutor() as ex:
        panes_fut = ex.submit(list_panes)
        sessions_fut = ex.submit(list_sessions)
        cur_fut = ex.submit(current_session_window)

        panes = panes_fut.result()

        # Only non-agent panes need a git lookup; the daemon supplies branch for
        # agent panes (computed from the agent's own cwd).
        git_futs = {
            p.pane_id: ex.submit(_get_git_branch, p.pane_current_path)
            for p in panes
            if p.pane_id not in daemon_state and p.pane_current_path
        }
        cur_sess, cur_win = cur_fut.result()
        session_order = sessions_fut.result()
        git_branches = {pid: fut.result() for pid, fut in git_futs.items()}

    # Agent types drive the icon column; the daemon reports type per pane.
    agent_types = {pid: st.get("agent_type", "claude") for pid, st in daemon_state.items()}

    rows, targets = _build_rows(
        panes, cur_sess, cur_win,
        agent_types=agent_types, git_branches=git_branches, daemon_state=daemon_state,
        session_order=session_order,
    )
    return _render(rows, targets)


def _generate_list_direct() -> str:
    """Build the list via direct file + ps inspection (daemon-unavailable fallback)."""
    from concurrent.futures import ThreadPoolExecutor
    from tmux_sentinel.process import _get_process_tree
    from tmux_sentinel.hook import _get_git_branch
    from tmux_sentinel.status import read_status

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

    # Position the fzf cursor on the current window at launch, so "where am I"
    # is answered the instant the popup opens (and it's the natural point to
    # navigate away from). The current row is the only one carrying ►; its
    # 1-based line index is the fzf item position. With --reverse + --no-sort
    # the input order is the display order, so the index maps directly.
    current_pos = 0
    for i, line in enumerate(fzf_input.splitlines(), start=1):
        if "►" in line:
            current_pos = i
            break

    # Build the reload and close commands for fzf.
    # -S skips Python's site-init (no pip deps here, so it's safe) — trims a
    # few ms of interpreter startup per invocation by skipping Homebrew's
    # sitecustomize.py.
    script = f"PYTHONPATH={os.environ.get('PYTHONPATH', '.')} python3 -S {__file__}"
    close_cmd = f"{script} --close {{2}}"
    reload_cmd = f"{script} --list"

    fzf_args = [
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
    ]
    # Place the cursor on the current window at startup. Bind to `load`, not
    # `start`: with input piped over stdin, `start` fires before fzf has
    # finished reading the list, so pos() would land in an empty buffer and
    # leave the cursor at the top. `load` fires once the input is fully read.
    # pos(N) is 1-based; only bind when we found the current row (pos(0) is invalid).
    if current_pos:
        fzf_args += ["--bind", f"load:pos({current_pos})"]

    # Run fzf
    try:
        result = subprocess.run(
            fzf_args,
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
