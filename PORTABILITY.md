# Portability notes

An audit of what in this codebase is specific to the author's machine or setup, and
what other users would hit. The two blockers found have been fixed; the rest are
recorded here rather than fixed, because each is a judgement call about how far to go
for other people's setups.

Findings were verified by reading the code and, where possible, by running it — noted
per item. First audited at commit `76e51ae`; re-audited at `ce370bc`, after the plugin
work added `sentinel.tmux`, `install.py` and `bin/lru_bump.sh`.

## Fixed

- **`setup.sh` aborted for anyone without Kiro.** An `exit 0` fired when
  `~/.kiro/agents` was missing, and it sat *before* the Claude Code hooks, the tmux
  config, the status bar, and the keybind. A Claude-Code-only user — probably most
  users — got nothing installed, after a message that looked like success. The Kiro
  section is now skipped rather than fatal. Verified by running setup against a fake
  `$HOME` with no Kiro: it now installs all 5 Claude hooks and completes.

- **`bin/status_client.sh` required `nc -U`.** Talking to a Unix socket from a shell
  isn't portable. In practice most builds are fine — openbsd-netcat (Debian/Ubuntu/Arch
  default) and nmap-ncat (Fedora/RHEL default) both support `-U`, as does BSD netcat on
  macOS; only netcat-traditional lacks it. But with no fallback, those users got a
  status bar that silently showed nothing while the daemon ran fine. There's now a
  Python socket fallback, with `nc` preferred where available (~11ms vs ~54ms, and this
  runs on every status-bar refresh). Both paths verified byte-identical.

  The detection needed a second pass. The first version grepped `nc --help` for `-U`,
  which only matches macOS's one-flag-per-line format: OpenBSD and nmap print a compact
  cluster like `[-46bCDdFhklNnrStUuvZz]`, so **most Linux users would have been sent
  down the slow Python path despite having a perfectly good `nc`**. Detection now probes
  behaviour instead — run `nc -U` against a path that cannot exist, and check whether it
  complains about an invalid option (no support) or about the missing socket (supported).
  Verified against simulated netcat-traditional, openbsd-netcat, and nmap-ncat.

  Fixing this also caught a latent bug: the old `if [ $? -ne 0 ]` tested the exit status
  of the enclosing `if`, not of the query.

- **No version checks.** tmux 3.2+ (`display-popup`), fzf 0.30+ (the `--bind` event
  names and `--preview-window` flags the picker uses), and Python 3.11+ (`tomllib`)
  are all required, and all failed at *keypress* with cryptic errors rather than at
  install. `setup.sh` now gates on each. (The first version-comparison I wrote was
  inverted — it passed 3.1 and rejected 3.3 — so the `ver_lt` helper is deliberately
  explicit about the equality case.)

- **`ps` could clip the command line on Linux.** `_get_process_tree` ran
  `ps -eo pid,ppid,args`. procps — the Linux `ps` — can clip the `args` column to the
  screen width, and detection depends on the *whole* line: an agent is recognised by a
  substring of it, and a pane's own agent by walking pids up the tree. Measured on this
  machine, real agent command lines run to ~2000 characters (a node interpreter path
  followed by a deep path into the package), so a clip near 80 columns would drop the
  distinguishing part and every agent pane would show `[---]`. Now `ps -ww -eo …`;
  `-ww` means unlimited width. Verified harmless on macOS: BSD `ps` accepts it and still
  finds all 7 agents here.

- **Two scripts hardcoded `#!/bin/bash`.** `setup.sh` and `bin/status_client.sh` assumed
  bash lives at `/bin/bash`, which is true on macOS and mainstream Linux but not on
  NixOS or the BSDs. Both now use `#!/usr/bin/env bash`, matching the other three
  scripts.

## Open — worth knowing, not yet addressed

### Agent detection is process-name based

`tmux_sentinel/process.py:47,49,116-119` matches command lines containing
`kiro-cli`+`chat`, or `/claude` (excluding `otelcol`). Verified by reading.

A user whose Claude binary is wrapped in a shell function, invoked via an alias, or
launched under a different name won't be detected: their panes show `[---]` and get no
status tracking at all. The failure is silent and looks like the tool not working.

Now that `config.toml` exists, the match patterns could move there. Until then it's
worth documenting the assumption in the README.

### Screen-scrape patterns assume Claude Code's current English UI

`manifests/claude.toml`. Verified by reading; the patterns are strings like
`ask a question or describe a task`, `shift+tab to cycle`, `manual mode on`,
`requires approval`, `esc to interrupt`.

Consequences for others: a non-English locale matches nothing, so panes never
classify as idle or waiting; and any Claude Code UI change breaks detection until the
manifest is updated. This is inherent to screen-scraping and is *why* the patterns
live in editable TOML rather than in code — but nothing tells a user that, or that
they're expected to adapt them. The README should say so.

Related: `_WORKING_MARKER` in `tmux_sentinel_daemon/poll.py` was deliberately made
verb-agnostic (commit `877828e`) for exactly this reason — it matches the *shape* of
the status line rather than enumerating Claude's rotating gerunds, which would rot.
The manifest patterns have no equivalent protection.

### Glyph and font assumptions

`tmux_sentinel/picker.py:52` uses emoji for agent icons (`👻` Kiro, `🟠` Claude), and
the picker uses `►`, `●`, `…` plus box-drawing in places. Verified by reading.

Fine in any modern terminal; garbled in a bare Linux console, over some SSH setups, or
without emoji font coverage. Emoji are also double-width, which `_display_width`
handles — but only for East-Asian-wide characters, so a terminal that renders them
single-width would misalign the columns. Candidates for `config.toml`, with an
ASCII-only fallback set.

### Old LTS distros fall below the dependency floor

Not a defect — the version gates in `sentinel.tmux` and `setup.sh` catch it and say so —
but worth knowing who gets turned away. The floors are Python 3.11 (`tomllib`), fzf 0.30
and tmux 3.2.

| Distro | Python | fzf | tmux | Verdict |
|---|---|---|---|---|
| Ubuntu 22.04 LTS | 3.10 | 0.29 | 3.2a | **refused** on two counts |
| Ubuntu 24.04 LTS | 3.12 | 0.44 | 3.4 | fine |
| Debian 12 | 3.11 | 0.38 | 3.3a | fine |
| Fedora / Arch | current | current | current | fine |

Ubuntu 22.04 is supported until 2027, so this is a real population. Both blockers are
avoidable by the user (a newer Python, fzf from a release binary), and neither is worth
lowering the floor for: `tomllib` is the reason the config file needs no dependency, and
the fzf floor is the `--bind` event names the picker relies on.

### Tests reference the author's paths

`tests/test_picker.py:333-362` hardcodes `/Users/cjkent` in `_shorten_path`
assertions. Verified: **these are not a portability problem** — `_shorten_path` takes
`home` as a parameter, so the tests pass on any machine. They just read oddly to a
contributor. `tests/test_picker.py:113` does use the real `os.path.expanduser("~")`,
which is environment-coupled but correct anywhere.

### ~~Assumes the user wants their tmux config edited~~ — fixed

`setup.sh` used to overwrite `status-right` wholesale, destroying whatever the user had
built. Fixed when the tmux configuration moved into `sentinel.tmux`: the plugin only
ever *substitutes* a `#{sentinel_status}` placeholder, and does nothing at all if the
user hasn't added one. `setup.sh` offers to append the placeholder with `set -ga`, which
composes with the existing value rather than replacing it, and asks first.

## Checked and OK

- **No hardcoded personal paths in any code.** `setup.sh` derives `REPO_DIR` from its
  own location, and the absolute paths in the author's `~/.tmux.conf` are *generated*
  from that — correct by construction, not baked in. Verified by grep across all
  tracked `.py`/`.sh`/`.toml`.
- **No macOS-only tools.** No `osascript`, `pbcopy`, `gsed`, `stat -f`, `date -r`.
- **No `sed -i`** anywhere, which is the classic BSD-vs-GNU trap (`sed -i ''` on macOS
  vs `sed -i` on GNU).
- **No bash 4+ constructs.** No `mapfile`/`readarray`, associative arrays, or `${v^^}`.
  All shell scripts parse under macOS's `/bin/bash` 3.2, which was verified explicitly
  — `mapfile` had in fact been used and was removed for this reason.
- **`ps -eo pid,ppid,args`** (`tmux_sentinel/process.py`) is POSIX-portable.
- **Amazon-internal vocabulary** (`DvRcs*`, `PVRF-*`, `mainline`, `/Volumes/workplace`)
  appears only in test fixtures and one explanatory comment at `picker.py:104-107`.
  None of it is in logic.
- **`~/workplace` symlink handling is generic** — `_home_symlink_targets` reads
  whatever symlinks exist in `$HOME`; it isn't a hardcoded list.
- **Python is stdlib-only**, no pip dependencies.
