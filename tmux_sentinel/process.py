"""
Process tree inspection for tmux-sentinel.

Detects which tmux panes have an interactive AI agent (Kiro CLI or Claude Code)
running as the pane's root process or as a descendant of it. Uses a single
`ps -eo pid,ppid,args` call to build a process tree, then checks each pane's
pane_pid (the root) and walks its descendants.

Kiro CLI matching: command line must contain both 'kiro-cli' and 'chat'.
This distinguishes interactive agents from non-interactive uses like
'kiro-cli acp' (background workers).

Claude Code matching: command line must contain '/claude' (path separator
ensures we match the binary, not unrelated strings), or argv[0] must be
exactly 'claude' (bare command). Excludes the otelcol
sidecar process.

OpenCode matching: the program (argv[0], or the script after node/bun) must be named
exactly 'opencode', and its first non-flag argument must not be a non-interactive
subcommand ('serve', 'run', 'acp', ...). Written from the documentation, not from a
running instance, so the process shape is unverified; see _opencode_interactive.

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
            # -ww means unlimited width. Without it, procps (the Linux ps) can clip the
            # args column to the screen width, and detection reads the *whole* command
            # line: an agent is identified by a substring of it, and the pane's own
            # agent is found by walking pids up the tree. Real agent command lines here
            # run to ~2000 characters, since they are a node interpreter path followed
            # by a deep path into the package — so anything clipped near 80 columns
            # would drop the distinguishing part and the pane would show [---].
            # Harmless on macOS, where BSD ps accepts -ww and already does not clip.
            ["ps", "-ww", "-eo", "pid,ppid,args"],
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


# Subcommands that do not run the interactive TUI. `opencode attach` is deliberately not
# listed: it opens the TUI against a remote server. Taken from the CLI documentation.
_OPENCODE_NON_INTERACTIVE = frozenset({
    "run", "serve", "web", "acp", "auth", "models", "export", "import",
    "upgrade", "uninstall", "stats", "github", "mcp", "agent", "session",
})

# A JS runtime in front of the script: `node /path/to/opencode`.
_JS_RUNTIMES = frozenset({"node", "bun", "bunx", "deno"})


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _opencode_interactive(args: str) -> bool:
    """True if this command line is the OpenCode TUI rather than any other opencode use.

    Only argv[0] counts, or the script argument when argv[0] is a JS runtime. Matching
    "opencode" anywhere in the line would also hit `vim ~/dev/opencode/README.md`, and
    a project directory passed to opencode itself.

    UNVERIFIED: written from the documentation. It assumes the TUI's argv[0] is, or ends
    in, `opencode`, which holds for the install script, Homebrew and npm shims alike, but
    the real `ps` output has not been seen.
    """
    tokens = args.split()
    if not tokens:
        return False
    i = 0
    if _basename(tokens[0]) in _JS_RUNTIMES:
        # Skip the runtime's own flags to reach the script.
        i = 1
        while i < len(tokens) and tokens[i].startswith("-"):
            i += 1
    if i >= len(tokens) or _basename(tokens[i]) != "opencode":
        return False
    for token in tokens[i + 1:]:
        if token.startswith("-"):
            continue
        return token not in _OPENCODE_NON_INTERACTIVE
    return True   # bare `opencode`, or only flags


def _agent_type_of(args: str) -> Optional[str]:
    """Return "kiro", "claude" or "opencode" for an agent's command line, else None."""
    # OpenCode first. Its test is strict (argv[0] only), while the Claude test is a loose
    # substring match, so `opencode --model anthropic/claude-sonnet` would otherwise be
    # taken for Claude Code on the strength of its model name.
    if _opencode_interactive(args):
        return "opencode"
    if "kiro-cli" in args and "chat" in args:
        return "kiro"
    # A bare `claude` (found on PATH) has no slash, so it is matched by the program name.
    # The name check is exact: `claude-foo` and `vim claude.md` do not match.
    tokens = args.split()
    program_is_claude = bool(tokens) and _basename(tokens[0]) == "claude"
    if ("/claude" in args or program_is_claude) and "otelcol" not in args:
        return "claude"
    return None


def _is_agent_process(args: str) -> bool:
    """Check if a command line belongs to an interactive AI agent."""
    return _agent_type_of(args) is not None


def _has_agent_descendant(root_pid: str, tree: dict[str, tuple[str, str]]) -> bool:
    """Return True if root_pid or any of its descendants is an interactive AI agent.

    The root counts: a pane started as `claude --continue` has the agent as its pane_pid.
    """
    root_entry = tree.get(root_pid)
    if root_entry and _is_agent_process(root_entry[1]):
        return True

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
    Return the set of pane IDs that have an AI agent running as the pane's root or a descendant.

    Detects Kiro CLI (kiro-cli chat), Claude Code (/path/to/claude) and OpenCode.

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
    """Return the agent type running as root_pid or under it, or None."""
    root_entry = tree.get(root_pid)
    if root_entry:
        root_type = _agent_type_of(root_entry[1])
        if root_type:
            return root_type

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
                agent_type = _agent_type_of(entry[1])
                if agent_type:
                    return agent_type
            queue.append(child)
    return None


def get_agent_types(
    pane_pids: dict[str, str],
    process_tree: Optional[dict[str, tuple[str, str]]] = None,
) -> dict[str, str]:
    """
    Return {pane_id: agent_type} for panes with an agent running.

    agent_type is "kiro", "claude" or "opencode".
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
