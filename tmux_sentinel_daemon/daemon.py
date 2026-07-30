"""
Long-running daemon for tmux-sentinel.

Holds all agent state in memory, listens on a Unix domain socket for hook
events and status queries, and periodically polls the process tree + screen-
scrapes panes to detect state changes that hooks miss.

Protocol (one connection per request, line-based text):
  - Hook event: client sends a JSON line → daemon responds "ok\n"
  - Status query: client sends "status <pane_id>\n" → daemon responds with
    the tmux format string (possibly empty) and closes

Lifecycle:
  - Lazy-started by the status bar client or manually via python3 -m tmux_sentinel_daemon
  - Exits on SIGTERM, when no tmux server is running, or immediately on startup
    if another instance is already running (see single-instance lock below)
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tmux_sentinel.status import (
    IDLE, WORKING, WAITING, ERROR, AgentStatus,
    list_statuses, read_status, write_status,
    set_unseen, clear_unseen, is_unseen,
    set_error_flag, clear_error_flag, has_error_flag,
    cleanup_stale, recreate_missing, STATUS_DIR,
)
from tmux_sentinel.process import get_agent_panes
from tmux_sentinel.tmux import (
    list_panes, pane_pids, focused_pane_id,
    capture_pane_tail, list_sessions,
)
from tmux_sentinel.hook import handle_event

from tmux_sentinel_daemon.state import DaemonState
from tmux_sentinel_daemon.poll import run_poll
from tmux_sentinel_daemon.status_format import format_status_output

SOCK_DIR = Path.home() / ".tmux-sentinel"
SOCK_PATH = SOCK_DIR / "daemon.sock"
PID_FILE = SOCK_DIR / "daemon.pid"
LOCK_FILE = SOCK_DIR / "daemon.lock"

# The idle interval bounds how long a hookless turn (resumed session, /compact
# continuation, dropped hook) can show as IDL before the poll's screen-scrape
# promotes it — see the IDLE branch in poll.run_poll. 30s was long enough to feel
# broken; 10s keeps the worst case brief while still being a cheap background rate
# when nothing is running.
POLL_INTERVAL_IDLE = 10.0
POLL_INTERVAL_ACTIVE = 5.0

log = logging.getLogger("tmux-sentinel-daemon")


def _acquire_singleton_lock() -> object:
    """Return an open file handle holding an exclusive, non-blocking flock on
    LOCK_FILE, or None if another daemon already holds it.

    flock is atomic at the kernel level, so this closes the race that let
    status_client.sh's lazy-start spawn multiple daemons when several tmux
    panes polled at once with no daemon running: each would independently
    see "no daemon" and start one, and since startup unconditionally deleted
    and rebound the socket, every loser became an orphan that never exited
    (the shutdown check only fires when tmux itself has no sessions, which
    is essentially never true for a laptop kept awake with tmux running).
    The file handle must be kept open for the lock to hold — do not close it
    while the daemon is meant to be running.
    """
    fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


async def handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: DaemonState,
) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not line:
            return
        text = line.decode().strip()

        if text.startswith("status "):
            pane_id = text[7:].strip()
            state.mark_seen(pane_id)
            response = format_status_output(state, pane_id)
            writer.write(response.encode() + b"\n")
        elif text == "dump":
            # Full per-pane state snapshot for the picker (JSON object keyed by pane_id)
            snapshot = {
                pane_id: {
                    "status": ps.status,
                    "cwd": ps.cwd,
                    "git_branch": ps.git_branch,
                    "timestamp": ps.timestamp,
                    "unseen": ps.unseen,
                    "agent_type": ps.agent_type,
                }
                for pane_id, ps in state.panes.items()
            }
            writer.write(json.dumps(snapshot).encode() + b"\n")
        elif text.startswith("{"):
            try:
                event = json.loads(text)
                pane_id = event.pop("_pane_id", "")
                if pane_id:
                    state.apply_hook_event(event, pane_id)
            except json.JSONDecodeError:
                pass
            writer.write(b"ok\n")
        else:
            writer.write(b"err unknown command\n")

        await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


async def poll_loop(state: DaemonState, shutdown_event: asyncio.Event) -> None:
    while not shutdown_event.is_set():
        interval = POLL_INTERVAL_ACTIVE if state.any_working() else POLL_INTERVAL_IDLE
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        run_poll(state)

        if not list_sessions():
            log.info("No tmux server running, shutting down")
            shutdown_event.set()
            break


async def run_daemon() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    SOCK_DIR.mkdir(parents=True, exist_ok=True)

    lock_fh = _acquire_singleton_lock()
    if lock_fh is None:
        log.info("Another daemon instance is already running, exiting")
        return

    if SOCK_PATH.exists():
        SOCK_PATH.unlink()

    state = DaemonState()
    run_poll(state)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    server = await asyncio.start_unix_server(
        lambda r, w: handle_connection(r, w, state),
        path=str(SOCK_PATH),
    )
    os.chmod(SOCK_PATH, 0o700)
    PID_FILE.write_text(str(os.getpid()))
    log.info("Daemon listening on %s (pid %d)", SOCK_PATH, os.getpid())

    poll_task = asyncio.create_task(poll_loop(state, shutdown_event))

    await shutdown_event.wait()
    server.close()
    await server.wait_closed()
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

    SOCK_PATH.unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)
    lock_fh.close()
    LOCK_FILE.unlink(missing_ok=True)
    log.info("Daemon shut down")


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
