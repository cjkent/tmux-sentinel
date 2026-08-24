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
from dataclasses import dataclass
from pathlib import Path

from tmux_sentinel.status import (
    IDLE, WORKING, WAITING, ERROR, STATUS_DIR,
    read_status, is_unseen, cleanup_stale, recreate_missing,
)
from tmux_sentinel.hook import _get_git_branch
from tmux_sentinel.process import get_kiro_panes, get_agent_types
from tmux_sentinel.tmux import (
    list_panes, list_sessions, pane_pids,
    current_session, current_window_index, current_session_window,
    current_session_window_pane, switch_to_pane, kill_pane, focused_pane_id, PaneInfo,
)
from tmux_sentinel.formatting import (
    elapsed, status_label, colorize_status, align_columns,
    RED, RESET,
)
from tmux_sentinel.config import get_int, get_str


_AGENT_ICONS = {"kiro": "👻", "claude": "🟠"}

# Column caps, all overridable from ~/.tmux-sentinel/config.toml. Names are the
# free-text column, so they get the tightest default: Claude's live task titles
# ramble ("Investigate the 5xx errors reported in the ticket…"), and the session
# column needs room of its own.
_MAX_TITLE_LEN = get_int("max_name_len", 28)
_MAX_SESSION_LEN = get_int("max_session_len", 20)
_MAX_CWD_LEN = get_int("max_cwd_len", 50)

# How many leading path segments to keep when eliding the middle of a long path.
# "~" is a segment but not a meaningful one, so a path starting with it keeps an
# extra segment to reach the same depth.
_CWD_HEAD_SEGMENTS = get_int("cwd_head_segments", 2)


def _truncate(text: str, limit: int) -> str:
    """Shorten text to limit characters, marking the elision with an ellipsis."""
    return text if len(text) <= limit else text[:limit] + "…"


def _truncate_path(path: str, limit: int) -> str:
    """Shorten a path by eliding its middle, keeping the head and the last segment.

    The leading segments identify the project (~/workplace/PVRF-820/…) and the last
    identifies the package (…/DvRcsCalculationServiceCDK); it's the middle that's
    boilerplate. Dropping the head, as a plain tail-truncation would, loses the
    project — which is the part you scan for. align_columns pads every column to its
    widest value, so one deep path would otherwise indent every other row.
    """
    if len(path) <= limit:
        return path
    segments = path.split("/")
    # Neither "~" nor the empty string from a leading "/" is a meaningful segment, so
    # keep one extra past it to reach the same real depth.
    head_count = _CWD_HEAD_SEGMENTS
    if segments[0] in ("~", ""):
        head_count += 1
    # Nothing to elide unless there's at least one segment between head and tail.
    if len(segments) <= head_count + 1:
        return _truncate(path, limit)
    head = "/".join(segments[:head_count])
    candidate = f"{head}/…/{segments[-1]}"
    # If the elided form still doesn't fit (a very long final segment), fall back to
    # dropping the front — at that point the tail is all that can be shown.
    if len(candidate) > limit:
        return "…" + path[-limit:]
    return candidate


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
            return _truncate(title, _MAX_TITLE_LEN)
    return pane.window_name


LRU_PATH = Path.home() / ".tmux-sentinel" / "lru"

# Rank given to a pane with no recorded visit. Larger than any real rank, so
# never-visited panes sort last in "recent" order.
_LRU_UNVISITED = 1 << 30


def _lru_ranks(path: Path = None) -> dict[str, int]:
    """Map pane id -> visit rank (0 = most recently visited).

    Populated by bin/lru_bump.sh via tmux hooks. tmux itself has no last-visited
    timestamp: #{window_activity} is last-*output* time, so an agent printing into a
    window you never looked at would otherwise jump to the top of the recent list.
    """
    target = path or LRU_PATH
    try:
        lines = target.read_text().splitlines()
    except OSError:
        return {}
    ranks: dict[str, int] = {}
    for line in lines:
        pane = line.strip().lstrip("%")
        if not pane:
            continue
        # First occurrence wins: the file is most-recent-first, and bump dedupes, but
        # a stale duplicate must not demote a pane. Rank counts entries rather than
        # lines, so a blank line doesn't consume a position.
        if pane not in ranks:
            ranks[pane] = len(ranks)
    return ranks


@dataclass
class _Record:
    """A display row plus the fields the sort modes order on."""
    row: list[str]
    target: str
    unseen: bool
    severity: int
    activity: int
    session_index: int
    window_index: int
    # 0 = most recently visited; _LRU_UNVISITED when never visited.
    lru_rank: int = _LRU_UNVISITED


# Triage order for the unseen mode: what most wants a human, first. Waiting outranks
# error because it's blocking on you right now, whereas an error has already happened.
_SEVERITY = {WAITING: 0, ERROR: 1, WORKING: 2, IDLE: 3}
_SEVERITY_NONE = 4          # a pane with no agent in it

MODE_UNSEEN = "unseen"
MODE_SESSION = "session"
MODE_MRU = "mru"
MODES = (MODE_UNSEEN, MODE_SESSION, MODE_MRU)

# Shown in the fzf prompt. "recent" reads better than "mru" for the latter.
_MODE_PROMPT = {MODE_UNSEEN: "unseen", MODE_SESSION: "session", MODE_MRU: "recent"}


def _as_int(value: str) -> int:
    """Parse a tmux index for sorting; window indices are numeric but arrive as text."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _sort_records(records: list[_Record], mode: str) -> list[_Record]:
    """Order rows for the given mode.

    Sorts are stable, so each key list only needs the fields that mode cares about —
    ties keep the order panes were discovered in, which is session order.
    """
    if mode == MODE_MRU:
        # Ordered by when you last *visited* a pane, which is what "recent" should
        # mean. Never-visited panes fall to the back, ordered among themselves by
        # last output — that's the best available signal for a pane the LRU cache has
        # never seen (a fresh install, or a pane created since the cache was written).
        return sorted(records, key=lambda r: (r.lru_rank, -r.activity))
    if mode == MODE_SESSION:
        # Reproduces the pre-modes ordering exactly: sessions in tmux's order, then
        # window index within each.
        return sorted(records, key=lambda r: (r.session_index, r.window_index))
    # Default: triage. Unseen first, then by what most wants attention, then recency.
    return sorted(records, key=lambda r: (not r.unseen, r.severity, -r.activity))


def _build_rows(
    panes: list[PaneInfo],
    cur_session: str,
    cur_window: str,
    status_dir: Path = None,
    agent_types: dict[str, str] = None,
    git_branches: dict[str, str] = None,
    daemon_state: dict = None,
    session_order: list[str] = None,
    focused_pane: str = "",
    mode: str = MODE_UNSEEN,
) -> tuple[list[list[str]], list[str]]:
    """
    Build display rows and corresponding targets for the picker.

    If daemon_state is provided (a dict keyed by pane_id from the daemon's
    'dump' command), agent panes use its already-computed status/unseen/branch,
    avoiding per-pane capture-pane and status-file reads. Panes not in
    daemon_state fall back to the status-file path.

    session_order gives the display order of sessions; if omitted it is fetched
    via list_sessions() (callers that already have it can pass it to save a call).

    focused_pane is the pane id (no %) of the focused pane. When given, the ►
    marker identifies that exact pane rather than every pane in the current
    window — a split window has several panes but only one is focused.

    Rows are flat — there are no session header rows. The session name is a column
    on every row instead, which keeps each row a real fzf target (so a query like
    "myproj waiting" narrows correctly, where a header row could only ever match
    itself) and leaves the row order free to change. Rows are still emitted in
    session order, so the display groups by session exactly as the headers did.

    Returns:
        (rows, targets) where rows[i] is a list of column values and
        targets[i] is a pane id (no %).
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
    records: list[_Record] = []
    # Read once rather than per pane; only the "recent" mode uses it, but the read is
    # a single small file so it isn't worth gating.
    lru = _lru_ranks()

    # Group panes by session
    sessions: dict[str, list[PaneInfo]] = {}
    for p in panes:
        sessions.setdefault(p.session, []).append(p)

    # Use session order from tmux
    if session_order is None:
        session_order = list_sessions()
    for session_index, session in enumerate(session_order):
        if session not in sessions:
            continue

        for p in sessions[session]:
            agent_icon = _AGENT_ICONS.get(at.get(p.pane_id, ""), "  ")
            # Prefer the focused pane id: in a split window, matching on
            # session+window alone would mark every pane of that window as current.
            if focused_pane:
                is_current = (p.pane_id == focused_pane)
            else:
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
                # No agent in this pane. display_status must be assigned here too, or
                # it would leak from whichever pane the loop looked at last and give
                # this row someone else's sort severity.
                display_status = ""
                icon_display = "[---]"
                agent_icon = "  "
                el = ""
                cwd = p.pane_current_path
                git_branch = gb.get(p.pane_id, "")
                branch = f"({git_branch})" if git_branch else ""

            short_cwd = _truncate_path(
                _shorten_path(cwd, home, home_symlinks), _MAX_CWD_LEN
            )
            # The leading marker column is shared: the focused pane shows ►, an
            # unseen (finished-but-unviewed) pane shows ●. These never clash — the
            # focused pane is always seen — so they occupy one column, keeping
            # "where am I" and "what needs attention" vertically aligned. It's its
            # own column now, so align_columns pads it rather than the name string
            # carrying the marker's width.
            if is_current:
                marker = "►"
            elif unseen:
                marker = "●"
            else:
                marker = " "
            name = _display_name(p, at.get(p.pane_id, ""))

            records.append(_Record(
                row=[
                    marker,
                    _truncate(p.session, _MAX_SESSION_LEN),
                    name,
                    agent_icon,
                    icon_display,
                    short_cwd,
                    branch,
                    el,
                ],
                # Target the pane, not "session:window": split panes share a window
                # index, so a window-level target can't distinguish them and tmux
                # would keep focus on whichever pane last had it.
                target=p.pane_id,
                unseen=unseen,
                severity=_SEVERITY.get(display_status, _SEVERITY_NONE),
                activity=p.activity,
                session_index=session_index,
                window_index=_as_int(p.window_index),
                lru_rank=lru.get(p.pane_id, _LRU_UNVISITED),
            ))

    for rec in _sort_records(records, mode):
        rows.append(rec.row)
        targets.append(rec.target)
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
        # The unseen dot sits alone in the leading marker column, so match the bare
        # glyph — it can't collide with anything else on the line.
        ("●", f"{RED}●{RESET}"),
    ]
    for old, new in replacements:
        line = line.replace(old, new)
    if is_current:
        line = f"{_BOLD}{line.replace(RESET, RESET + _BOLD)}{RESET}"
    return line


def _generate_list(mode: str = MODE_UNSEEN) -> str:
    """Generate the fzf input list.

    Fast path: if the daemon is running, use its in-memory state snapshot for
    agent panes (no process-tree walk, no per-pane capture-pane). Fall back to
    the direct file+ps path if the daemon is unavailable.
    """
    from tmux_sentinel_daemon.client import dump_state

    daemon_state = dump_state()
    if daemon_state is not None:
        return _generate_list_from_daemon(daemon_state, mode)
    return _generate_list_direct(mode)


def _generate_list_from_daemon(daemon_state: dict, mode: str = MODE_UNSEEN) -> str:
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
        cur_fut = ex.submit(current_session_window_pane)

        panes = panes_fut.result()

        # Only non-agent panes need a git lookup; the daemon supplies branch for
        # agent panes (computed from the agent's own cwd).
        git_futs = {
            p.pane_id: ex.submit(_get_git_branch, p.pane_current_path)
            for p in panes
            if p.pane_id not in daemon_state and p.pane_current_path
        }
        cur_sess, cur_win, cur_pane = cur_fut.result()
        session_order = sessions_fut.result()
        git_branches = {pid: fut.result() for pid, fut in git_futs.items()}

    # Agent types drive the icon column; the daemon reports type per pane.
    agent_types = {pid: st.get("agent_type", "claude") for pid, st in daemon_state.items()}

    rows, targets = _build_rows(
        panes, cur_sess, cur_win,
        agent_types=agent_types, git_branches=git_branches, daemon_state=daemon_state,
        session_order=session_order, focused_pane=cur_pane, mode=mode,
    )
    return _render(rows, targets)


def _generate_list_direct(mode: str = MODE_UNSEEN) -> str:
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
    cur_sess, cur_win, cur_pane = current_session_window_pane()
    agent_types = get_agent_types(pp, process_tree=tree)

    rows, targets = _build_rows(
        panes, cur_sess, cur_win, agent_types=agent_types,
        git_branches=git_branches, focused_pane=cur_pane, mode=mode,
    )
    return _render(rows, targets)


# Whether the preview pane is showing persists between popup invocations, so the
# choice sticks instead of resetting every time. fzf has no memory of its own, so
# it lives in a file alongside the other runtime state (status dir, socket, pid).
_PREVIEW_STATE_FILE = Path.home() / ".tmux-sentinel" / "preview"


def _preview_visible() -> bool:
    """True if the preview pane was left showing last time."""
    return _PREVIEW_STATE_FILE.exists()


def _toggle_preview_state() -> None:
    """Flip the remembered preview visibility."""
    if _PREVIEW_STATE_FILE.exists():
        _PREVIEW_STATE_FILE.unlink(missing_ok=True)
    else:
        _PREVIEW_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PREVIEW_STATE_FILE.touch()


def _render(rows: list[list[str]], targets: list[str]) -> str:
    """Align, colorize, and join rows into the fzf input string."""
    aligned = align_columns(rows)
    colorized = [_colorize_line(line) for line in aligned]
    sep = "\x1f"
    return "\n".join(f"{line}{sep}{target}" for line, target in zip(colorized, targets))


def _cursor_row(fzf_input: str, mode: str) -> int:
    """1-based row the cursor should rest on at launch, or 0 to leave it at the top.

    Per mode, because the useful starting point differs:
      session — the focused pane, answering "where am I" the instant it opens
      unseen  — the first row needing attention, other than the focused pane; falling
                back to the focused pane when nothing does
      mru     — the top row, i.e. the most recent; but if that's the pane you're
                already in, the one below it, since selecting your own pane is a no-op

    Row indices map straight to fzf item positions because --no-sort and --reverse
    keep the input order as the display order.
    """
    lines = fzf_input.splitlines()
    current = 0
    for i, line in enumerate(lines, start=1):
        if "►" in line:
            current = i
            break

    if mode == MODE_MRU:
        # Deliberately keyed to row 1, not to wherever the focused pane happens to be.
        # tmux's window_activity is last-*output* time, not last-focus time, so a
        # chattier agent elsewhere can outrank the pane you're sitting in — the focused
        # pane is often not the top row. Stepping past it wherever it appeared would
        # land the cursor on an arbitrary row and skip the most recent target.
        if current == 1 and len(lines) > 1:
            return 2
        return 1 if lines else 0
    if mode == MODE_UNSEEN:
        # Anything the sort ranked above "seen and quiet" counts, not the ● alone. A pane
        # blocked on an approval prompt sorts to the very top yet may carry no dot, so
        # keying on the dot alone sent the cursor past the one row that wanted a human.
        # The status labels are the same signal the sort uses, so the two agree.
        #
        # The focused pane is skipped: it can now match (a ► row may carry [WAI]), and
        # landing on the pane you are already in makes the keypress do nothing.
        for i, line in enumerate(lines, start=1):
            if i == current:
                continue
            if "●" in line or "[WAI]" in line or "[ERR]" in line:
                return i
        return current
    return current


def _parse_mode(argv: list[str]) -> str:
    """Read --mode=X from the arguments, falling back to the default.

    An unrecognised mode falls back rather than erroring: the picker is bound to a
    key, so a typo in a tmux.conf binding should still open a usable list.
    """
    for arg in argv:
        if arg.startswith("--mode="):
            value = arg.split("=", 1)[1]
            if value in MODES:
                return value
    return MODE_UNSEEN


def main() -> None:
    mode = _parse_mode(sys.argv[1:])

    # --list mode: output the list and exit (used by fzf reload)
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        sys.stdout.write(_generate_list(mode))
        return

    # --close mode: kill the selected pane (used by fzf ctrl-x). Targets are pane
    # ids now, so this closes exactly the pane on the highlighted row; tmux closes
    # the window itself once its last pane is gone.
    if len(sys.argv) > 1 and sys.argv[1] == "--close":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        if target and target.isdigit():
            kill_pane(target)
        return

    # --toggle-preview mode: remember the new preview visibility (used by fzf's ?).
    if len(sys.argv) > 1 and sys.argv[1] == "--toggle-preview":
        _toggle_preview_state()
        return

    fzf_input = _generate_list(mode)
    sep = "\x1f"
    current_pos = _cursor_row(fzf_input, mode)

    # Build the reload and close commands for fzf.
    # -S skips Python's site-init (no pip deps here, so it's safe) — trims a
    # few ms of interpreter startup per invocation by skipping Homebrew's
    # sitecustomize.py.
    script = f"PYTHONPATH={os.environ.get('PYTHONPATH', '.')} python3 -S {__file__}"
    close_cmd = f"{script} --close {{2}}"
    reload_cmd = f"{script} --list --mode={mode}"
    toggle_preview_cmd = f"{script} --toggle-preview"

    # Field 2 is the target pane id, stored without the "%" tmux wants, so the
    # placeholder gets a literal "%" in front of it. The pane's tail is the useful
    # part (current output, prompt, any approval request), so capture and tail it.
    #
    # The awk strips *trailing* blank lines. capture-pane pads its output to the full
    # pane height, so a pane holding two lines of output in a 30-row pane emits 30
    # lines — 2 real, 28 empty. Since the preview window is anchored to the bottom
    # ("follow" below), those blanks are what you'd be looking at: the preview reads
    # as empty even though the content is sitting just above. Buffering and replaying
    # up to the last non-blank line keeps interior blanks, which carry real structure
    # in agent output, and drops only the padding.
    preview_lines = get_int("preview_lines", 40)
    preview_width = get_str("preview_width", "50%")
    trim_trailing_blanks = (
        "awk '{ b[NR]=$0; if (NF) last=NR } END { for (i=1;i<=last;i++) print b[i] }'"
    )
    preview_cmd = (
        f"tmux capture-pane -ep -t %{{2}} 2>/dev/null"
        f" | {trim_trailing_blanks}"
        f" | tail -n {preview_lines}"
    )

    fzf_args = [
        "fzf",
        "--ansi",
        "--no-sort",
        "--reverse",
        # The prompt names the active mode, since the ordering is otherwise hard to
        # tell apart at a glance (unseen and mru coincide whenever nothing is unseen).
        f"--prompt={_MODE_PROMPT[mode]} > ",
        "--header=ctrl-x: close pane  ?: preview  "
        "M-u/M-s/M-r: unseen/session/recent",
        "--no-info",
        "--no-multi",
        "--cycle",
        f"--delimiter={sep}",
        "--with-nth=1",
        "--preview", preview_cmd,
        # Always declare the window hidden, then reveal it below if the remembered
        # state says so. State flows one way — file to fzf — so the two can't drift
        # into disagreeing about whether the preview is up.
        #
        # "follow" pins the view to the end of the output. A pane's useful context
        # is always at the bottom (latest output, the prompt, a pending approval),
        # whereas fzf otherwise shows the top — which for a long-lived pane is
        # whatever scrolled past 40 lines ago. Note the fzf actions for this
        # (preview-bottom) don't work bound to start/load: they race the async
        # preview render, so the window flag is the reliable way to do it.
        f"--preview-window=right:{preview_width},follow,hidden",
        "--bind", f"ctrl-x:execute-silent({close_cmd})+reload({reload_cmd})",
        "--bind", f"?:toggle-preview+execute-silent({toggle_preview_cmd})",
    ]

    # In-popup mode switching. Secondary to launching in the mode you want (the tmux
    # keybinds), so the cursor just lands at the top of the new order rather than being
    # recomputed — fzf can't run our per-mode cursor logic mid-session.
    #
    # M- rather than ctrl-, because ctrl-u and ctrl-r are fzf defaults (clear-query and
    # toggle-sort); rebinding those would cost more than it gains.
    for key, target_mode in (("alt-u", MODE_UNSEEN), ("alt-s", MODE_SESSION),
                             ("alt-r", MODE_MRU)):
        fzf_args += [
            "--bind",
            f"{key}:reload({script} --list --mode={target_mode})"
            f"+change-prompt({_MODE_PROMPT[target_mode]} > )+first",
        ]
    if _preview_visible():
        fzf_args += ["--bind", "start:show-preview"]
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

    # Extract target from selection. Every row is a selectable pane id.
    selection = result.stdout.strip()
    target = selection.split(sep)[-1] if sep in selection else ""
    if target and target.isdigit():
        switch_to_pane(target)


if __name__ == "__main__":
    main()
