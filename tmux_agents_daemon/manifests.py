"""
Screen-scrape manifest loader.

Loads pattern files from the manifests/ directory and provides a function
to classify pane state based on the captured terminal text.

The manifest format is a small, TOML-flavoured subset — enough to express
the rules we need without depending on tomllib (which only exists in Python
3.11+; this project targets stdlib-only and must run under system python 3.9):

    # comment
    [[rule]]
    state   = "waiting"
    pattern = '''some|regex'''
    exclude = '''another|regex'''      # optional

Values may be wrapped in ''' triple quotes ''', "double quotes", or 'single
quotes'. Each rule spans one [[rule]] header followed by key = value lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MANIFESTS_DIR = Path(__file__).resolve().parent.parent / "manifests"


@dataclass
class Rule:
    state: str
    pattern: re.Pattern
    exclude: re.Pattern | None


def _unquote(value: str) -> str:
    """Strip surrounding triple/single/double quotes from a manifest value."""
    value = value.strip()
    for q in ("'''", '"""', '"', "'"):
        if len(value) >= 2 * len(q) and value.startswith(q) and value.endswith(q):
            return value[len(q):-len(q)]
    return value


def _parse_manifest(text: str) -> list[dict]:
    """Parse the TOML-subset manifest text into a list of rule dicts."""
    entries: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[rule]]":
            current = {}
            entries.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[key.strip()] = _unquote(value)
    return entries


def load_manifest(path: Path) -> list[Rule]:
    """Load rules from a manifest file."""
    entries = _parse_manifest(path.read_text())
    rules = []
    for entry in entries:
        state = entry["state"]
        pattern = re.compile(entry["pattern"])
        exclude = re.compile(entry["exclude"]) if "exclude" in entry else None
        rules.append(Rule(state=state, pattern=pattern, exclude=exclude))
    return rules


def load_all_manifests(manifests_dir: Path = MANIFESTS_DIR) -> dict[str, list[Rule]]:
    """Load all manifests, keyed by agent name (filename without extension)."""
    result = {}
    if not manifests_dir.exists():
        return result
    for path in sorted(manifests_dir.glob("*.toml")):
        name = path.stem
        result[name] = load_manifest(path)
    return result


def classify(text: str, rules: list[Rule]) -> str | None:
    """Apply rules to captured text. Returns the state of the first match, or None."""
    for rule in rules:
        if rule.pattern.search(text):
            if rule.exclude and rule.exclude.search(text):
                continue
            return rule.state
    return None
