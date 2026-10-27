"""Tests for tmux_sentinel.process module."""
from tmux_sentinel.process import get_kiro_panes, _has_kiro_descendant, get_agent_panes, _has_agent_descendant, _is_agent_process


def _make_tree(*entries):
    """Build a process tree dict from (pid, ppid, comm) tuples."""
    return {pid: (ppid, comm) for pid, ppid, comm in entries}


def test_kiro_direct_child():
    tree = _make_tree(
        ("100", "1", "tmux"),
        ("200", "100", "zsh"),
        ("300", "200", "kiro-cli chat"),
    )
    assert _has_kiro_descendant("200", tree) is True


def test_kiro_grandchild():
    tree = _make_tree(
        ("100", "1", "tmux"),
        ("200", "100", "zsh"),
        ("300", "200", "/bin/zsh"),
        ("400", "300", "kiro-cli chat --tui"),
    )
    assert _has_kiro_descendant("200", tree) is True


def test_no_kiro():
    tree = _make_tree(
        ("100", "1", "tmux"),
        ("200", "100", "zsh"),
        ("300", "200", "vim"),
    )
    assert _has_kiro_descendant("200", tree) is False


def test_kiro_in_different_subtree():
    """kiro-cli under a different root should not match."""
    tree = _make_tree(
        ("100", "1", "tmux"),
        ("200", "100", "zsh"),       # our pane
        ("300", "200", "vim"),
        ("400", "1", "other-shell"),  # different subtree
        ("500", "400", "kiro-cli chat"),
    )
    assert _has_kiro_descendant("200", tree) is False


def test_get_kiro_panes():
    tree = _make_tree(
        ("100", "1", "tmux"),
        ("200", "100", "zsh"),
        ("300", "200", "kiro-cli chat"),
        ("400", "100", "zsh"),
        ("500", "400", "vim"),
    )
    pane_pids = {"0": "200", "1": "400"}
    result = get_kiro_panes(pane_pids, process_tree=tree)
    assert result == {"0"}


def test_empty_tree():
    assert get_kiro_panes({"0": "999"}, process_tree={}) == set()


def test_kiro_cli_chat_matches():
    """kiro-cli-chat should also match (contains both 'kiro-cli' and 'chat')."""
    tree = _make_tree(
        ("200", "1", "zsh"),
        ("300", "200", "/usr/local/bin/kiro-cli-chat chat --tui"),
    )
    assert _has_kiro_descendant("200", tree) is True


def test_kiro_acp_does_not_match():
    """kiro-cli acp (non-interactive) should NOT match."""
    tree = _make_tree(
        ("200", "1", "zsh"),
        ("300", "200", "kiro-cli acp --trust-all-tools"),
    )
    assert _has_kiro_descendant("200", tree) is False


# --- Claude Code tests ---

def test_claude_direct_child():
    tree = _make_tree(
        ("200", "1", "zsh"),
        ("300", "200", "/Users/user/.toolbox/tools/claude-code/2.1.131/claude"),
    )
    assert _has_agent_descendant("200", tree) is True


def test_claude_bin_path():
    tree = _make_tree(
        ("200", "1", "zsh"),
        ("300", "200", "/Users/user/.toolbox/tools/claude-code/2.1.131/bin/claude"),
    )
    assert _has_agent_descendant("200", tree) is True


def test_otelcol_not_matched():
    """The otelcol sidecar should NOT match even though its path contains claude-code."""
    tree = _make_tree(
        ("200", "1", "zsh"),
        ("300", "200", "/Users/user/.toolbox/tools/claude-code/2.1.131/otelcol-contrib --config yaml:..."),
    )
    assert _has_agent_descendant("200", tree) is False


def test_mixed_kiro_and_claude():
    """Both Kiro and Claude Code panes detected."""
    tree = _make_tree(
        ("100", "1", "tmux"),
        ("200", "100", "zsh"),
        ("300", "200", "kiro-cli chat"),
        ("400", "100", "zsh"),
        ("500", "400", "/usr/local/bin/claude"),
        ("600", "100", "zsh"),
        ("700", "600", "vim"),
    )
    pane_pids = {"0": "200", "1": "400", "2": "600"}
    result = get_agent_panes(pane_pids, process_tree=tree)
    assert result == {"0", "1"}


def test_is_agent_process():
    assert _is_agent_process("kiro-cli chat") is True
    assert _is_agent_process("kiro-cli chat --tui") is True
    assert _is_agent_process("kiro-cli acp") is False
    assert _is_agent_process("/usr/local/bin/claude") is True
    assert _is_agent_process("/Users/u/.toolbox/tools/claude-code/2.1/claude") is True
    assert _is_agent_process("otelcol-contrib --config yaml:claude-code") is False
    assert _is_agent_process("vim") is False


# --- OpenCode ---
#
# Written from the documentation, not from a running instance: these pin the matching
# rules, not the real `ps` output.

def test_opencode_tui_is_an_agent():
    from tmux_sentinel.process import _agent_type_of
    for args in (
        "opencode",
        "/Users/x/.opencode/bin/opencode",
        "/opt/homebrew/bin/opencode -c",
        "opencode /Users/x/dev/project",
        "opencode attach http://localhost:4096",     # a TUI against a remote server
        "node /usr/lib/node_modules/opencode-ai/bin/opencode",
        "bun /x/.bin/opencode --model anthropic/claude",
    ):
        assert _agent_type_of(args) == "opencode", args


def test_opencode_non_interactive_uses_are_not_agents():
    from tmux_sentinel.process import _agent_type_of
    for args in (
        "opencode serve --port 4096",
        "/opt/homebrew/bin/opencode run 'fix the bug'",
        "opencode acp",
        "opencode web",
        "opencode models",
        "opencode auth login",
    ):
        assert _agent_type_of(args) is None, args


def test_opencode_is_not_matched_by_an_incidental_path():
    # Only argv[0] counts. A path that merely contains the word must not.
    from tmux_sentinel.process import _agent_type_of
    for args in (
        "vim /home/x/opencode/README.md",
        "less /Users/x/dev/opencode",
        "cd /Users/x/dev/opencode-notes",
        "zsh",
        "",
    ):
        assert _agent_type_of(args) is None, args


def test_opencode_with_a_claude_model_is_not_taken_for_claude_code():
    # The Claude test is a loose substring match on "/claude", which a model name such as
    # anthropic/claude-sonnet contains.
    from tmux_sentinel.process import _agent_type_of
    assert _agent_type_of("opencode --model anthropic/claude-sonnet-4") == "opencode"
    assert _agent_type_of("/opt/homebrew/bin/opencode -m anthropic/claude-opus") == "opencode"


def test_existing_agents_are_still_recognised():
    from tmux_sentinel.process import _agent_type_of
    assert _agent_type_of("kiro-cli chat") == "kiro"
    assert _agent_type_of("/usr/local/bin/claude --resume") == "claude"
    assert _agent_type_of("/x/claude otelcol") is None


def test_opencode_pane_is_found_and_typed():
    from tmux_sentinel.process import get_agent_types
    tree = _make_tree(("100", "1", "tmux"), ("200", "100", "zsh"),
                      ("300", "200", "/Users/x/.opencode/bin/opencode"))
    assert get_agent_panes({"p1": "200"}, process_tree=tree) == {"p1"}
    assert get_agent_types({"p1": "200"}, process_tree=tree) == {"p1": "opencode"}


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"  ✓ {name}")
    print("\nAll tests passed")
