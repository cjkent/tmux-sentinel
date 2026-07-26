"""Tests for the daemon's socket protocol handling."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tmux_sentinel_daemon.daemon import handle_connection
from tmux_sentinel_daemon.state import DaemonState
from tmux_sentinel.status import WORKING


async def _run_server_with_request(state: DaemonState, request: bytes) -> bytes:
    """Start a real Unix socket server, send a request, return the response."""
    with tempfile.TemporaryDirectory() as td:
        sock_path = os.path.join(td, "test.sock")

        server = await asyncio.start_unix_server(
            lambda r, w: handle_connection(r, w, state),
            path=sock_path,
        )

        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(request)
        await writer.drain()
        writer.write_eof()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        server.close()
        await server.wait_closed()
        return response


def test_status_query_empty():
    state = DaemonState()

    async def _run():
        resp = await _run_server_with_request(state, b"status 100\n")
        assert resp == b"\n"

    asyncio.run(_run())
    print("  ✓ test_status_query_empty")


def test_status_query_with_working():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )

    async def _run():
        resp = await _run_server_with_request(state, b"status 200\n")
        text = resp.decode()
        assert "working" in text

    asyncio.run(_run())
    print("  ✓ test_status_query_with_working")


def test_hook_event():
    state = DaemonState()
    event = {"hookEventName": "SessionStart", "cwd": "/tmp", "_pane_id": "100"}

    async def _run():
        resp = await _run_server_with_request(state, json.dumps(event).encode() + b"\n")
        assert resp == b"ok\n"

    asyncio.run(_run())
    assert state.get("100") is not None
    assert state.get("100").cwd == "/tmp"
    print("  ✓ test_hook_event")


def test_status_query_marks_seen():
    state = DaemonState()
    state.apply_hook_event(
        {"hookEventName": "UserPromptSubmit", "cwd": "/tmp"},
        pane_id="100",
    )
    state.apply_hook_event(
        {"hookEventName": "Stop", "cwd": "/tmp"},
        pane_id="100",
    )
    assert state.get("100").unseen is True

    async def _run():
        await _run_server_with_request(state, b"status 100\n")

    asyncio.run(_run())
    assert state.get("100").unseen is False
    print("  ✓ test_status_query_marks_seen")


def test_unknown_command():
    state = DaemonState()

    async def _run():
        resp = await _run_server_with_request(state, b"garbage\n")
        assert b"err" in resp

    asyncio.run(_run())
    print("  ✓ test_unknown_command")


if __name__ == "__main__":
    test_status_query_empty()
    test_status_query_with_working()
    test_hook_event()
    test_status_query_marks_seen()
    test_unknown_command()
    print("\nAll tests passed")
